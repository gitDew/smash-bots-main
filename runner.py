#!/usr/bin/env python3
"""Shared libmelee arena runner.

The runner owns Dolphin, the Melee console, controllers, and menu navigation.
Team bots are separate modules and provide Bot.get_character() and Bot.act().
"""

from __future__ import annotations

import argparse
import configparser
import importlib.util
import math
import signal
import sys
from pathlib import Path
import melee


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ISO = PROJECT_ROOT / "smash_bros_melee.iso"
DEFAULT_P1_BOT = PROJECT_ROOT / "teams" / "team_a" / "bot.py"
DEFAULT_P2_BOT = PROJECT_ROOT / "teams" / "team_b" / "bot.py"

# Melee's game timer runs at 60 frames per second.  libmelee does not expose
# the HUD timer on GameState, but GameState.frame is the in-game frame index.
# Frame 0 is the start of timed gameplay; negative frames are the countdown.
GAME_FPS = 60
DEFAULT_GAME_CLOCK_SECONDS = 8 * 60
DEFAULT_TIME_LIMIT_SECONDS = 3 * 60


def time_left_seconds(
    game_frame: int,
    game_clock_seconds: int = DEFAULT_GAME_CLOCK_SECONDS,
) -> float:
    """Estimate the HUD clock from libmelee's in-game frame number."""
    elapsed_frames = max(0, game_frame)
    return max(0.0, game_clock_seconds - elapsed_frames / GAME_FPS)


def format_clock(seconds: float) -> str:
    """Format seconds as the minutes:seconds shown by Melee's HUD."""
    total_seconds = max(0, math.ceil(seconds))
    minutes, remainder = divmod(total_seconds, 60)
    return f"{minutes}:{remainder:02d}"


def winner_at_time_limit(gamestate) -> int | None:
    """Return the winning port, or None when the time-limit score is tied.

    Melee resolves a timeout by comparing remaining stocks first and damage
    percentage second.  This uses the same ordering for the snapshot at which
    the runner stops the game.
    """
    player_one = gamestate.players.get(1)
    player_two = gamestate.players.get(2)
    if player_one is None or player_two is None:
        return None

    if player_one.stock != player_two.stock:
        return 1 if player_one.stock > player_two.stock else 2

    if player_one.percent != player_two.percent:
        return 1 if player_one.percent < player_two.percent else 2

    return None


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
    if not callable(getattr(bot, "get_character", None)):
        raise TypeError(
            f"Bot in {bot_path} must define "
            "get_character() -> melee.Character"
        )
    if not callable(getattr(bot, "act", None)):
        raise TypeError(f"Bot in {bot_path} must define act(gamestate, controller)")
    return bot


def is_selectable_character(character: object) -> bool:
    """Return whether libmelee can navigate to this character on the CSS."""
    if not isinstance(character, melee.Character):
        return False

    # Sheik shares Zelda's character-select slot. MenuHelper handles the
    # transformation separately during stage selection.
    if character is melee.Character.SHEIK:
        return True

    css_id = melee.from_internal(character)
    if css_id == 0xFF:
        return False

    # Confirm that the character's internal ID maps back to the same enum.
    return melee.to_internal(css_id) is character


def get_bot_character(bot, port: int) -> melee.Character:
    """Read and validate a bot's fixed character selection."""
    character = bot.get_character()
    if not is_selectable_character(character):
        raise ValueError(
            f"Bot on port {port} selected {character!r}, "
            "which is not a selectable character"
        )
    return character


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
        "--time-limit",
        type=float,
        default=DEFAULT_TIME_LIMIT_SECONDS,
        help=(
            "Stop after this many in-game seconds have elapsed. The default "
            "is 180 seconds, which stops an 8:00 game when its clock reaches "
            "5:00. Set to 0 to disable."
        ),
    )
    args = parser.parse_args()

    if not math.isfinite(args.time_limit):
        parser.error("--time-limit must be a finite number")

    iso_path = args.iso.expanduser().resolve()
    if not iso_path.is_file():
        print(f"ISO not found: {iso_path}", file=sys.stderr)
        return 2

    # Load the team code and character choices before starting Dolphin. Each
    # bot gets its own object and therefore its own persistent state. Character
    # choices are fixed for the match and are not queried during gameplay.
    bots = {
        1: load_bot(args.p1, port=1),
        2: load_bot(args.p2, port=2),
    }
    characters = {
        port: get_bot_character(bot, port)
        for port, bot in bots.items()
    }
    stage = melee.Stage.RANDOM_STAGE

    console = melee.Console(
        path=args.dolphin_path,
        fullscreen=True,
        # Slippi's Vulkan backend can crash on macOS; use OpenGL instead.
        gfx_backend="OGL" if sys.platform == "darwin" else "",
        disable_audio=False,
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
    time_limit_frames = (
        None
        if args.time_limit <= 0
        else math.ceil(args.time_limit * GAME_FPS)
    )
    match_started = False
    time_limit_reached = False

    def stop(*_args) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)

    try:
        print(f"Player 1 bot: {Path(args.p1).expanduser()}")
        print(f"Player 2 bot: {Path(args.p2).expanduser()}")
        print(f"Selected stage: {stage.name}")
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

        while True:
            gamestate = console.step()
            if gamestate is None:
                continue

            if gamestate.menu_state in (
                melee.Menu.IN_GAME,
                melee.Menu.SUDDEN_DEATH,
            ):
                match_started = True

                # There is no HUD-timer field in libmelee's GameState.  Use
                # the Slippi in-game frame index instead; unlike a wall-clock
                # timeout, this does not count menu time or slow Dolphin time.
                # Do not apply the limit during sudden death: that means the
                # normal game has already reached its own time limit.
                if (
                    gamestate.menu_state == melee.Menu.IN_GAME
                    and time_limit_frames is not None
                    and gamestate.frame >= time_limit_frames
                ):
                    time_limit_reached = True
                    player_one = gamestate.players.get(1)
                    player_two = gamestate.players.get(2)
                    winner = winner_at_time_limit(gamestate)
                    remaining = format_clock(time_left_seconds(gamestate.frame))
                    if player_one is None or player_two is None:
                        print(
                            "Time limit reached, but the winner could not be "
                            "determined: player state is incomplete."
                        )
                    else:
                        if winner is None:
                            result = "the score is tied"
                        else:
                            result = f"Player {winner} wins on time"
                        print(
                            f"Time limit reached ({remaining} left): "
                            f"{result}. "
                            f"Player 1: {player_one.stock} stock(s), "
                            f"{player_one.percent:.1f}%; "
                            f"Player 2: {player_two.stock} stock(s), "
                            f"{player_two.percent:.1f}%."
                        )
                    # Stopping the runner's main loop causes the finally block
                    # to disconnect controllers and stop Dolphin, ending this
                    # round without navigating back through the menus.
                    break

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
                        # Pass the requested character, including SHEIK.
                        # MenuHelper handles Sheik's Zelda CSS slot and
                        # transformation itself.
                        character_selected=characters[port],
                        stage_selected=stage,
                        cpu_level=0,
                        costume=0,
                        autostart=port == 1,
                        swag=False,
                    )

        if time_limit_reached:
            print("Arena stopped after the configured time limit.")
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
