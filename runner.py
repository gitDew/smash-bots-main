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

from replay import NativeReplayRecorder, RecordingError


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ISO = PROJECT_ROOT / "smash_bros_melee.iso"
DEFAULT_P1_BOT = PROJECT_ROOT / "teams" / "team_a" / "bot.py"
DEFAULT_P2_BOT = PROJECT_ROOT / "teams" / "team_b" / "bot.py"
DEFAULT_P3_BOT = PROJECT_ROOT / "teams" / "team_c" / "bot.py"
DEFAULT_P4_BOT = PROJECT_ROOT / "teams" / "team_d" / "bot.py"
DEFAULT_DOLPHIN = (
    PROJECT_ROOT / ".dolphin" / "netplay-beta" / "Slippi_Dolphin.app"
    / "Contents" / "MacOS" / "Slippi_Dolphin"
)
DEFAULT_REPLAY_DIR = PROJECT_ROOT / "replays"

# Melee's game timer runs at 60 frames per second.  libmelee does not expose
# the HUD timer on GameState, but GameState.frame is the in-game frame index.
# Frame 0 is the start of timed gameplay; negative frames are the countdown.
GAME_FPS = 60
DEFAULT_GAME_CLOCK_SECONDS = 8 * 60
DEFAULT_TIME_LIMIT_SECONDS = 2 * 60


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


def winner_at_time_limit(gamestate, ports=(1, 2)) -> int | None:
    """Return the unique stock/percent leader, or None for a tie or missing data.

    This is a snapshot-based result for a free-for-all stock match, not a
    result from Melee's own postgame screen.
    """
    players = {port: gamestate.players.get(port) for port in ports}
    if not players or any(player is None for player in players.values()):
        return None

    best_stock = max(player.stock for player in players.values())
    if best_stock <= 0:
        return None
    contenders = {
        port: player for port, player in players.items() if player.stock == best_stock
    }
    best_percent = min(player.percent for player in contenders.values())
    leaders = [
        port for port, player in contenders.items() if player.percent == best_percent
    ]
    return leaders[0] if len(leaders) == 1 else None


def all_bots_selected(gamestate, characters) -> bool:
    """Check that every configured human player has placed the right coin."""
    for port, character in characters.items():
        player = gamestate.players.get(port)
        # Sheik uses Zelda's slot and is selected after stage selection.
        target = (
            melee.Character.ZELDA if character is melee.Character.SHEIK else character
        )
        if (
            player is None
            or player.controller_status is not melee.ControllerStatus.CONTROLLER_HUMAN
            or not player.coin_down
            or player.character is not target
        ):
            return False
    return True


def navigate_menu(menu_helper, gamestate, controllers, characters, stage) -> None:
    """Select the configured players and let port 1 start only when all are ready."""
    on_character_select = gamestate.menu_state == melee.Menu.CHARACTER_SELECT
    if on_character_select:
        # A CPU can occupy an unconfigured slot even if its Dolphin controller
        # is unplugged. Use P1's cursor to remove it before selecting fighters.
        for port in range(1, 5):
            if port in controllers:
                continue
            player = gamestate.players.get(port)
            if (
                player is not None
                and player.controller_status is not melee.ControllerStatus.CONTROLLER_UNPLUGGED
            ):
                for controller in controllers.values():
                    controller.release_all()
                if 1 in gamestate.players:
                    menu_helper.change_controller_status(
                        controllers[1], gamestate, port,
                        melee.ControllerStatus.CONTROLLER_UNPLUGGED,
                    )
                return

    ready = on_character_select and all_bots_selected(gamestate, characters)
    for port, controller in controllers.items():
        if on_character_select:
            player = gamestate.players.get(port)
            if player is None:
                controller.release_all()
                continue
            if player.controller_status is not melee.ControllerStatus.CONTROLLER_HUMAN:
                menu_helper.change_controller_status(
                    controller, gamestate, port, melee.ControllerStatus.CONTROLLER_HUMAN,
                )
                continue

        menu_helper.menu_helper_simple(
            gamestate=gamestate,
            controller=controller,
            character_selected=characters[port],
            stage_selected=stage,
            cpu_level=0,
            costume=0,
            # At stage select P1 must always be allowed to choose the stage.
            autostart=port == 1 and (not on_character_select or ready),
            swag=False,
        )


def configure_render_to_main(
    console: melee.Console, *, legacy_macos_fallback: bool = False,
) -> None:
    """Use one Dolphin window and retain OpenGL for older macOS builds."""
    if console.dolphin_home_path is None:
        raise RuntimeError("Dolphin home directory was not initialized")

    config_path = Path(console.dolphin_home_path) / "Config" / "Dolphin.ini"
    config = configparser.ConfigParser()
    config.read(config_path)
    if not config.has_section("Display"):
        config.add_section("Display")
    config.set("Display", "RenderToMain", "True")
    if legacy_macos_fallback and not console.is_mainline:
        if not config.has_section("Core"):
            config.add_section("Core")
        config.set("Core", "GFXBackend", "OGL")
        console.gfx_backend = "OGL"

    with config_path.open("w") as config_file:
        config.write(config_file)


def stop_dolphin(console: melee.Console) -> None:
    """Stop libmelee safely even if its connection worker never started."""
    worker = console._slippstream._worker
    if worker is not None and worker.pid is None:
        console._slippstream._worker = None
    console.stop()


def load_bot(bot_path: Path, port: int):
    """Load one team's Bot class without putting team files in one namespace."""
    bot_path = bot_path.expanduser().resolve()
    if not bot_path.is_file():
        raise FileNotFoundError(f"Bot file not found: {bot_path}")

    # The unique module name allows all teams to use a file named bot.py.
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
        "--players", type=int, choices=(2, 3, 4), default=4,
        help="Number of players (default: 4)",
    )
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
        "--p3",
        type=Path,
        help="Bot file for player 3 (default: teams/team_c/bot.py)",
    )
    parser.add_argument(
        "--p4",
        type=Path,
        help="Bot file for player 4 (default: teams/team_d/bot.py)",
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
        type=Path,
        default=DEFAULT_DOLPHIN,
        help="Slippi Mainline executable or installation directory (default: project-local beta)",
    )
    parser.add_argument(
        "--gfx-backend",
        help="Dolphin graphics backend (default: Metal on macOS; e.g. OGL for OpenGL)",
    )
    parser.add_argument(
        "--time-limit",
        type=float,
        default=DEFAULT_TIME_LIMIT_SECONDS,
        help=(
            "Stop after this many in-game seconds have elapsed. The default "
            "is 120 seconds, which stops an 8:00 game when its clock reaches "
            "6:00. Set to 0 to disable."
        ),
    )
    parser.add_argument(
        "--no-record", action="store_true",
        help="Run without automatically recording a video",
    )
    parser.add_argument(
        "--replay-dir", type=Path, default=DEFAULT_REPLAY_DIR,
        help="Directory for recorded MP4 files (default: ./replays in the arena directory)",
    )
    args = parser.parse_args()
    if not math.isfinite(args.time_limit):
        parser.error("--time-limit must be a finite number")
    if args.players < 3 and args.p3 is not None:
        parser.error("--p3 requires at least 3 players")
    if args.players < 4 and args.p4 is not None:
        parser.error("--p4 requires 4 players")

    player_paths = (
        args.p1, args.p2,
        args.p3 if args.p3 is not None else DEFAULT_P3_BOT,
        args.p4 if args.p4 is not None else DEFAULT_P4_BOT,
    )
    bot_paths = dict(enumerate(player_paths[:args.players], start=1))
    iso_path = args.iso.expanduser().resolve()
    if not iso_path.is_file():
        print(f"ISO not found: {iso_path}", file=sys.stderr)
        return 2

    # Load the team code and character choices before starting Dolphin. Each
    # bot gets its own object and therefore its own persistent state. Character
    # choices are fixed for the match and are not queried during gameplay.
    bots = {port: load_bot(path, port=port) for port, path in bot_paths.items()}
    characters = {
        port: get_bot_character(bot, port)
        for port, bot in bots.items()
    }
    stage = melee.Stage.RANDOM_STAGE

    recorder = None
    console = None
    try:
        if not args.no_record:
            recorder = NativeReplayRecorder.start(args.replay_dir)
        console = melee.Console(
            path=str(args.dolphin_path.expanduser().resolve()),
            fullscreen=True,
            gfx_backend=args.gfx_backend if args.gfx_backend is not None else (
                "Metal" if sys.platform == "darwin" else ""
            ),
            disable_audio=False,
            save_replays=False,
            dolphin_home_path=str(recorder.home) if recorder else None,
            tmp_home_directory=recorder is None,
            dump_config=melee.DumpConfig(
                dump=True, format="matroska", codec="h264", encoder="h264_videotoolbox",
            ) if recorder else None,
        )
        if recorder is not None and not console.is_mainline:
            raise RecordingError("Video recording requires the project-local Slippi Mainline beta.")
        configure_render_to_main(
            console, legacy_macos_fallback=sys.platform == "darwin" and args.gfx_backend is None,
        )

        controllers = {
            port: melee.Controller(
                console=console,
                port=port,
                type=melee.ControllerType.STANDARD,
            )
            for port in bots
        }
        # Explicitly disconnect unused ports. Character select can still
        # contain CPUs, handled by navigate_menu().
        for port in range(1, 5):
            if port not in controllers:
                console.setup_dolphin_controller(port, melee.ControllerType.UNPLUGGED)
    except BaseException as exc:
        try:
            if console is not None:
                stop_dolphin(console)
        finally:
            if recorder is not None:
                try:
                    recorder.finish(match_started=False)
                except RecordingError as cleanup_exc:
                    print(f"Recording error: {cleanup_exc}", file=sys.stderr)
        if isinstance(exc, (RecordingError, FileNotFoundError)):
            print(f"Could not start the arena: {exc}", file=sys.stderr)
            if isinstance(exc, FileNotFoundError):
                print("Run ./install_dolphin.sh to install the project-local beta.", file=sys.stderr)
            return 1
        raise
    connected_controllers = []
    menu_helper = melee.MenuHelper()
    time_limit_frames = (
        None
        if args.time_limit <= 0
        else math.ceil(args.time_limit * GAME_FPS)
    )
    match_started = False
    participants_checked = False
    time_limit_reached = False
    frame_dump_checked = False

    def stop(*_args) -> None:
        raise KeyboardInterrupt

    previous_sigint = signal.signal(signal.SIGINT, stop)
    recording_failed = False

    try:
        if recorder is not None:
            print(f"Recording to: {recorder.path}", flush=True)
        for port, path in bot_paths.items():
            print(f"Player {port} bot: {path.expanduser()}")
        print(f"Selected stage: {stage.name}")
        print(f"Starting Dolphin with {iso_path}")
        console.run(iso_path=str(iso_path))
        if recorder is not None:
            recorder.attach(console._process.pid)

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
            if recorder is not None:
                recorder.check()
            gamestate = console.step()
            if gamestate is None:
                continue

            if gamestate.menu_state in (
                melee.Menu.IN_GAME,
                melee.Menu.SUDDEN_DEATH,
            ):
                match_started = True
                if not participants_checked:
                    missing = set(bots) - set(gamestate.players)
                    if gamestate.frame < 0 and missing:
                        # The first countdown frames may have incomplete state.
                        continue
                    unexpected = set(gamestate.players) - set(bots)
                    if missing or unexpected:
                        print(
                            "Match participants do not match configured bots: "
                            f"missing ports {sorted(missing)}, "
                            f"unexpected ports {sorted(unexpected)}.",
                            file=sys.stderr,
                        )
                        return 1
                    if gamestate.is_teams:
                        print(
                            "Team mode is enabled; the runner requires a free-for-all match.",
                            file=sys.stderr,
                        )
                        return 1
                    participants_checked = True

                # Stop early rather than silently losing a whole match if the
                # native video encoder did not start.
                if recorder is not None and not frame_dump_checked and gamestate.frame >= 300:
                    recorder.check_frames()
                    frame_dump_checked = True

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
                    winner = winner_at_time_limit(gamestate, bots)
                    remaining = format_clock(time_left_seconds(gamestate.frame))
                    if any(port not in gamestate.players for port in bots):
                        print(
                            "Time limit reached, but the winner could not be "
                            "determined: player state is incomplete."
                        )
                    else:
                        result = (
                            f"Player {winner} wins on time"
                            if winner is not None else "no unique winner"
                        )
                        scores = "; ".join(
                            f"Player {port}: {gamestate.players[port].stock} stock(s), "
                            f"{gamestate.players[port].percent:.1f}%"
                            for port in bots
                        )
                        print(
                            f"Time limit reached ({remaining} left): "
                            f"{result}. {scores}."
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

                # All bots observe the same frame before the next step(). An
                # eliminated player may no longer appear in the game state.
                for port, bot in bots.items():
                    if port in gamestate.players:
                        bot.act(gamestate, controllers[port])
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
                # The runner, not the bots, owns menu navigation.
                navigate_menu(menu_helper, gamestate, controllers, characters, stage)

        if time_limit_reached:
            print("Arena stopped after the configured time limit.")
        else:
            print("Arena finished after one match.")
        return 0

    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except RecordingError as exc:
        recording_failed = True
        print(f"Recording error: {exc}", file=sys.stderr)
        return 1
    finally:
        # A second Ctrl-C must not interrupt Dolphin cleanup or video saving.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        cleanup_error = None
        dolphin_stopped = False
        try:
            try:
                for controller in connected_controllers:
                    controller.disconnect()
            finally:
                stop_dolphin(console)
                dolphin_stopped = True
        finally:
            try:
                if recorder is not None:
                    try:
                        saved = recorder.finish(
                            match_started=match_started, dolphin_stopped=dolphin_stopped,
                        )
                    except RecordingError as exc:
                        cleanup_error = exc
                    else:
                        if saved and not recording_failed:
                            print(f"Replay saved: {recorder.path}")
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
        if cleanup_error is not None and not recording_failed:
            print(f"Recording error: {cleanup_error}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
