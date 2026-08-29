#!/usr/bin/env python3
"""Shared libmelee arena runner.

The runner owns Dolphin, the Melee console, controllers, and menu navigation.
Team bots are separate modules and only implement Bot.act().
"""

from __future__ import annotations

import argparse
import configparser
import importlib.util
import signal
import sys
import time
from pathlib import Path
import melee


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ISO = PROJECT_ROOT / "smash_bros_melee.iso"
DEFAULT_P1_BOT = PROJECT_ROOT / "teams" / "team_a" / "bot.py"
DEFAULT_P2_BOT = PROJECT_ROOT / "teams" / "team_b" / "bot.py"


def configure_render_to_main(console: melee.Console) -> None:
    """Use one Dolphin window by rendering the game in its main frame."""
    if console.dolphin_home_path is None:
        raise RuntimeError("Dolphin home directory was not initialized")

    config_path = Path(console.dolphin_home_path) / "Config" / "Dolphin.ini"
    config = configparser.ConfigParser()
    config.read(config_path)
    if not config.has_section("Display"):
        config.add_section("Display")
    config.set("Display", "RenderToMain", "True")

    with config_path.open("w") as config_file:
        config.write(config_file)


def load_bot(bot_path: Path, port: int):
    """Load one team's Bot class without putting team files in one namespace."""
    bot_path = bot_path.expanduser().resolve()
    if not bot_path.is_file():
        raise FileNotFoundError(f"Bot file not found: {bot_path}")

    # The unique module name allows both teams to use a file named bot.py.
    module_name = f"team_bot_port_{port}"
    spec = importlib.util.spec_from_file_location(module_name, bot_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load bot module: {bot_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    bot_class = getattr(module, "Bot", None)
    if bot_class is None:
        raise TypeError(f"{bot_path} must define a class named Bot")

    bot = bot_class(port=port)
    if not callable(getattr(bot, "act", None)):
        raise TypeError(f"Bot in {bot_path} must define act(gamestate, controller)")
    return bot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--p1",
        type=Path,
        default=DEFAULT_P1_BOT,
        help="Bot file for player 1 (default: teams/team_a/bot.py)",
    )
    parser.add_argument(
        "--p2",
        type=Path,
        default=DEFAULT_P2_BOT,
        help="Bot file for player 2 (default: teams/team_b/bot.py)",
    )
    parser.add_argument(
        "--iso",
        type=Path,
        default=DEFAULT_ISO,
        help="Path to the Melee ISO (default: ./smash_bros_melee.iso)",
    )
    parser.add_argument(
        "-e",
        "--dolphin",
        dest="dolphin_path",
        default=None,
        help="Slippi Dolphin installation directory; auto-detected by default",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=0,
        help=(
            "Optional safety timeout in seconds; the runner exits when the "
            "match ends by default (default: 0, disabled)"
        ),
    )
    args = parser.parse_args()

    iso_path = args.iso.expanduser().resolve()
    if not iso_path.is_file():
        print(f"ISO not found: {iso_path}", file=sys.stderr)
        return 2

    # Load the team code before starting Dolphin. Each bot gets its own object
    # and therefore its own persistent state.
    bots = {
        1: load_bot(args.p1, port=1),
        2: load_bot(args.p2, port=2),
    }

    console = melee.Console(
        path=args.dolphin_path,
        fullscreen=True,
        save_replays=False,
    )
    # Ishiiruka otherwise creates a separate game-rendering window alongside
    # its main "Faster Melee Slippi" window. Render into the main frame so
    # the arena has only one visible window.
    configure_render_to_main(console)

    controllers = {
        port: melee.Controller(
            console=console,
            port=port,
            type=melee.ControllerType.STANDARD,
        )
        for port in (1, 2)
    }
    connected_controllers = []
    menu_helper = melee.MenuHelper()
    deadline = (
        None
        if args.timeout <= 0
        else time.monotonic() + args.timeout
    )
    match_started = False

    def stop(*_args) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)

    try:
        print(f"Player 1 bot: {Path(args.p1).expanduser()}")
        print(f"Player 2 bot: {Path(args.p2).expanduser()}")
        print(f"Starting Dolphin with {iso_path}")
        console.run(iso_path=str(iso_path))

        print("Connecting to Slippi...")
        if not console.connect():
            print("Could not connect to Slippi Dolphin.", file=sys.stderr)
            return 1
        print("Connected to Slippi.")

        for controller in controllers.values():
            if not controller.connect():
                print(
                    f"Could not connect controller on port {controller.port}.",
                    file=sys.stderr,
                )
                return 1
            connected_controllers.append(controller)
        print("Virtual controllers connected.")

        while deadline is None or time.monotonic() < deadline:
            gamestate = console.step()
            if gamestate is None:
                continue

            if gamestate.menu_state in (
                melee.Menu.IN_GAME,
                melee.Menu.SUDDEN_DEATH,
            ):
                match_started = True
                # Inputs persist in Dolphin. Neutralize each controller before
                # dispatching so a team cannot accidentally leave stale input
                # active from a previous frame.
                for controller in controllers.values():
                    controller.release_all()

                # Each team can only affect the controller passed to its bot.
                # Both bots observe the same frame before the next step().
                bots[1].act(gamestate, controllers[1])
                bots[2].act(gamestate, controllers[2])
            elif match_started:
                # The live libmelee menu parser does not currently emit
                # POSTGAME_SCORES. After the match, its first menu event is
                # normally MAIN_MENU or STAGE_SELECT. Any transition out of
                # gameplay after we have seen IN_GAME means this round is over.
                # Stop before MenuHelper can press START or select another map.
                print(
                    "Match finished; stopping Dolphin "
                    f"(next state: {gamestate.menu_state.name})."
                )
                break
            else:
                # The runner, not either team, owns menu navigation. Both
                # players are bots, so neither port is configured as a CPU.
                for port, controller in controllers.items():
                    menu_helper.menu_helper_simple(
                        gamestate=gamestate,
                        controller=controller,
                        character_selected=melee.Character.FOX,
                        stage_selected=melee.Stage.YOSHIS_STORY,
                        cpu_level=0,
                        costume=0,
                        autostart=port == 1,
                        swag=False,
                    )

        if deadline is not None and time.monotonic() >= deadline:
            print("Safety timeout reached before the match finished.")
        else:
            print("Arena finished after one match.")
        return 0

    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    finally:
        for controller in connected_controllers:
            controller.disconnect()
        console.stop()


if __name__ == "__main__":
    raise SystemExit(main())
