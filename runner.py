#!/usr/bin/env python3
"""Shared libmelee arena runner.

The runner owns Dolphin, the Melee console, controllers, and menu navigation.
Team bots run inside their own Docker containers (see container_bot.py). A
player given only a character has no bot and stands still, for demos.
"""

from __future__ import annotations

import argparse
import configparser
import copy
import json
import math
import signal
import sys
import time
from pathlib import Path
import melee
from melee.slippstream import EnetDisconnected

from container_bot import FRAME_BUDGET_SECONDS, ContainerBot
from replay import NativeReplayRecorder, RecordingError


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_ISO = PROJECT_ROOT / "smash_bros_melee.iso"
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
# No limit: a stock match plays until one player is left, or Melee's clock runs out.
DEFAULT_TIME_LIMIT_SECONDS = 0
# Once a winner is known, keep the game running this long so the last
# knockout can be seen before Dolphin closes.
END_GRACE_FRAMES = 90


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


def configure_match_stocks(console: melee.Console, lives: int) -> None:
    """Set Melee's actual starting stocks, not just the runner's elimination limit."""
    config_path = Path(console.dolphin_home_path) / "GameSettings" / "GALE01r2.ini"
    config = config_path.read_text()
    config = config.replace(
        "[Gecko_Enabled]\n", "[Gecko_Enabled]\n$Event: Starting Stocks\n", 1,
    )
    # Override Slippi's default four-stock setting; retain its other rule bytes.
    stocks = lives or 4
    config += f"\n$Event: Starting Stocks\n043D4A4C {stocks:02X}000A00\n"
    config_path.write_text(config)


def configure_match_time(console: melee.Console, time_limit: float) -> None:
    """Set the actual stock-match clock, including half-minute admin choices."""
    if time_limit <= 0:
        return
    seconds = math.ceil(time_limit)
    if seconds > 0xFFFFFFFF:
        raise ValueError("The match time must fit in Melee's 32-bit timer")
    config_path = Path(console.dolphin_home_path) / "GameSettings" / "GALE01r2.ini"
    config = config_path.read_text()
    config = config.replace(
        "[Gecko_Enabled]\n", "[Gecko_Enabled]\n$Event: Match Clock\n", 1,
    )
    # NTSC 1.02: GameRules.stock_time_limit at 0x803D4A50 is in whole
    # minutes. Override Slippi's eight-minute default for the rules menu.
    minutes = min(99, math.ceil(seconds / 60))
    # gm_80167BC8 normally loads that byte at 0x80167C94 and multiplies it
    # by 60 at 0x80167C98. Load our exact seconds instead (lis r0; ori r0),
    # so 0.5 and 1.5 minutes also start at 0:30 and 1:30, not rounded up.
    # This changes only initialization, never the running countdown.
    config += (
        f"\n$Event: Match Clock\n003D4A50 {minutes:08X}\n"
        f"04167C94 {0x3C000000 | (seconds >> 16):08X}\n"
        f"04167C98 {0x60000000 | (seconds & 0xFFFF):08X}\n"
    )
    config_path.write_text(config)


def stop_dolphin(console: melee.Console) -> None:
    """Stop libmelee safely even if its connection worker never started."""
    worker = console._slippstream._worker
    if worker is not None and worker.pid is None:
        console._slippstream._worker = None
    console.stop()


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


def fighter(value: str) -> tuple[str | None, melee.Character]:
    """Parse CONTAINER=CHARACTER, or CHARACTER alone for a player without a bot.

    Bot code has no say in its character.
    """
    container, equals, name = value.partition("=")
    if not equals:
        container, name = None, value
    character = melee.Character.__members__.get(name)
    if container == "" or character is None or not is_selectable_character(character):
        raise argparse.ArgumentTypeError(f"expected CONTAINER=CHARACTER or CHARACTER, got {value!r}")
    return container, character


def stage_choice(value: str) -> melee.Stage:
    stage = melee.Stage.__members__.get(value)
    if stage is None or stage is melee.Stage.NO_STAGE:
        raise argparse.ArgumentTypeError(f"unknown stage {value!r}")
    return stage


class IdleBot:
    """A player without a bot. It never presses anything, so it stands still."""

    container = None

    def __init__(self, port: int):
        self.port = port

    def start(self) -> "IdleBot":
        return self

    def send(self, gamestate) -> None:
        pass

    def apply(self, controller, deadline: float) -> None:
        pass

    def report(self) -> dict:
        return {"idle": True}

    def close(self) -> None:
        pass


def players_out(gamestate, starting_stocks: dict[int, int], lives: int) -> set[int]:
    """Players who have lost `lives` stocks. A player missing from a frame tells us nothing."""
    out = set()
    for port, start in starting_stocks.items():
        player = gamestate.players.get(port)
        if player is not None and (player.stock <= 0 or start - player.stock >= lives):
            out.add(port)
    return out


def bots_view(gamestate, out: set[int]):
    """The frame as the bots see it: players who are out are gone, as if eliminated."""
    if not out:
        return gamestate
    view = copy.copy(gamestate)
    view.players = {port: player for port, player in gamestate.players.items() if port not in out}
    return view


def dispatch_frame(bots, controllers, gamestate) -> None:
    """Share this frame with every bot; they all answer within one deadline."""
    active = {port: bot for port, bot in bots.items() if port in gamestate.players}
    for bot in active.values():
        bot.send(gamestate)
    deadline = time.monotonic() + FRAME_BUDGET_SECONDS
    for port, bot in active.items():
        bot.apply(controllers[port], deadline)


def bot_trouble(report: dict) -> str | None:
    """A line for the host's match log if a bot did not play every frame."""
    missed, crashed, bad = (report.get(key, 0) for key in ("missed_frames", "exceptions", "bad_replies"))
    if not (missed or crashed or bad):
        return None
    last = report.get("last_error") or (report.get("output") or ["keiner"])[-1]
    return f"hat {missed} Spielbilder verpasst, ist bei {crashed} abgestürzt und hat {bad} ungültige Antworten gesendet. Letzter Fehler: {last}"


def match_result(gamestate, bots, time_limit_reached: bool, out: set[int] = frozenset()) -> dict:
    """Summarize a match for the event server; eliminated players may be absent.

    Players who are out under --lives cannot win, whatever stocks Melee still gives them.
    """
    players = {}
    if gamestate is not None:
        for port in bots:
            player = gamestate.players.get(port)
            players[port] = {
                "stock": int(player.stock) if player is not None else 0,
                "percent": float(player.percent) if player is not None else 0.0,
            }
    ranked = sorted(
        (port for port, player in players.items() if player["stock"] > 0 and port not in out),
        key=lambda port: (-players[port]["stock"], players[port]["percent"]),
    )
    winner = ranked[0] if ranked else None
    if len(ranked) > 1 and (
        players[ranked[0]]["stock"], players[ranked[0]]["percent"]
    ) == (players[ranked[1]]["stock"], players[ranked[1]]["percent"]):
        winner = None
    return {
        "winner_port": winner,
        "time_limit_reached": time_limit_reached,
        "players": players,
        "bots": {port: bot.report() for port, bot in bots.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "fighters", nargs="+", metavar="CONTAINER=CHARACTER", type=fighter,
        help=(
            "Two to four team containers and their characters, in player order. "
            "A character without a container stands still."
        ),
    )
    parser.add_argument(
        "--stage", type=stage_choice, default=melee.Stage.RANDOM_STAGE,
        help="Stage to play on, such as BATTLEFIELD (default: RANDOM_STAGE)",
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
            "Set Melee's clock and stop after this many in-game seconds; the leader "
            "wins. The default, 0, plays until only one player is left or "
            "Melee's 8:00 clock runs out."
        ),
    )
    parser.add_argument(
        "--lives", type=int, default=1,
        help=(
            "A player is out after losing this many stocks, and the match ends "
            "when one player is left. The default is 1; 0 plays four stocks."
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
    parser.add_argument(
        "--result-file", type=Path,
        help="Write a JSON summary of the match to this file",
    )
    args = parser.parse_args()
    if not math.isfinite(args.time_limit):
        parser.error("--time-limit must be a finite number")
    if args.time_limit > 0xFFFFFFFF:
        parser.error("--time-limit must fit in Melee's 32-bit timer")
    if args.lives < 0:
        parser.error("--lives cannot be negative")
    if not 2 <= len(args.fighters) <= 4:
        parser.error("give 2 to 4 fighters")
    if len({character for _, character in args.fighters}) != len(args.fighters):
        parser.error("each bot needs a different character")
    iso_path = args.iso.expanduser().resolve()
    if not iso_path.is_file():
        print(f"ISO nicht gefunden: {iso_path}", file=sys.stderr)
        return 2

    # Start the bots before Dolphin. Each runs in its own container and keeps
    # its own state. The host assigned the characters, so they are fixed here.
    fighters = dict(enumerate(args.fighters, start=1))
    bots = {
        port: ContainerBot(container, port).start() if container else IdleBot(port)
        for port, (container, _) in fighters.items()
    }
    characters = {port: character for port, (_, character) in fighters.items()}
    stage = args.stage

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
            raise RecordingError("Videoaufnahmen benötigen die lokale Slippi-Mainline-Beta dieses Projekts.")
        configure_render_to_main(
            console, legacy_macos_fallback=sys.platform == "darwin" and args.gfx_backend is None,
        )
        configure_match_stocks(console, args.lives)
        configure_match_time(console, args.time_limit)

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
                    print(f"Aufnahmefehler: {cleanup_exc}", file=sys.stderr)
        if isinstance(exc, (RecordingError, FileNotFoundError)):
            print(f"Die Arena konnte nicht gestartet werden: {exc}", file=sys.stderr)
            if isinstance(exc, FileNotFoundError):
                print("Führe ./install_dolphin.sh aus, um die lokale Beta dieses Projekts zu installieren.", file=sys.stderr)
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
    last_game_frame = None
    starting_stocks: dict[int, int] = {}
    out: set[int] = set()
    end_frame = None

    def stop(*_args) -> None:
        raise KeyboardInterrupt

    previous_sigint = signal.signal(signal.SIGINT, stop)
    recording_failed = False

    try:
        if recorder is not None:
            print(f"Aufnahme wird gespeichert unter: {recorder.path}", flush=True)
        for port, bot in bots.items():
            print(f"Bot von Spieler {port}: {bot.container or 'keiner, steht still'}")
        print(f"Ausgewählte Bühne: {stage.name}")
        print(f"Dolphin wird mit {iso_path} gestartet")
        console.run(iso_path=str(iso_path))
        if recorder is not None:
            recorder.attach(console._process.pid)

        print("Verbindung zu Slippi wird aufgebaut...")
        if not console.connect():
            print("Verbindung zu Slippi Dolphin konnte nicht hergestellt werden.", file=sys.stderr)
            return 1
        print("Mit Slippi verbunden.")

        for controller in controllers.values():
            if not controller.connect():
                print(
                    f"Controller an Anschluss {controller.port} konnte nicht verbunden werden.",
                    file=sys.stderr,
                )
                return 1
            connected_controllers.append(controller)
        print("Virtuelle Controller verbunden.")

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
                if gamestate.menu_state == melee.Menu.SUDDEN_DEATH and time_limit_frames is not None and match_started:
                    # Melee can enter sudden death at 0:00 before Slippi sends
                    # the cutoff frame. A maximum fight time must not start an
                    # extra fight; keep the final normal-game snapshot.
                    time_limit_reached = True
                    print("Zeitlimit erreicht: keine Verlängerung durch Sudden Death.")
                    break
                match_started = True
                last_game_frame = gamestate
                if not participants_checked:
                    missing = set(bots) - set(gamestate.players)
                    if gamestate.frame < 0 and missing:
                        # The first countdown frames may have incomplete state.
                        continue
                    unexpected = set(gamestate.players) - set(bots)
                    if missing or unexpected:
                        print(
                            "Die Spieler im Kampf stimmen nicht mit den eingerichteten Bots überein: "
                            f"fehlende Anschlüsse {sorted(missing)}, "
                            f"unerwartete Anschlüsse {sorted(unexpected)}.",
                            file=sys.stderr,
                        )
                        return 1
                    if gamestate.is_teams:
                        print(
                            "Der Teammodus ist aktiviert. Der Spielprozess benötigt einen Kampf, in dem jeder gegen jeden spielt.",
                            file=sys.stderr,
                        )
                        return 1
                    if stage is not melee.Stage.RANDOM_STAGE and gamestate.stage is not stage:
                        # Still playable, but not the stage the kids prompted for.
                        print(
                            f"Warnung: Das Spiel läuft auf {gamestate.stage.name}, nicht auf {stage.name}.",
                            file=sys.stderr,
                        )
                    starting_stocks = {port: int(gamestate.players[port].stock) for port in bots}
                    participants_checked = True

                # Stop early rather than silently losing a whole match if the
                # native video encoder did not start.
                if recorder is not None and not frame_dump_checked and gamestate.frame >= 300:
                    recorder.check_frames()
                    frame_dump_checked = True

                # Track eliminated players and finish when only one remains.
                if end_frame is not None:
                    if gamestate.frame >= end_frame:
                        break
                elif args.lives:
                    for port in sorted(players_out(gamestate, starting_stocks, args.lives) - out):
                        print(f"Spieler {port} ist raus.")
                        out.add(port)
                    still_in = [port for port in bots if port not in out]
                    if len(still_in) <= 1:
                        print("Letzter Spieler übrig: " + (
                            f"Spieler {still_in[0]} gewinnt." if still_in else "Alle sind raus, niemand gewinnt."
                        ))
                        end_frame = gamestate.frame + END_GRACE_FRAMES

                # There is no HUD-timer field in libmelee's GameState.  Use
                # the Slippi in-game frame index instead; unlike a wall-clock
                # timeout, this does not count menu time or slow Dolphin time.
                # Sudden death after this clock expires is handled above.
                if (
                    gamestate.menu_state == melee.Menu.IN_GAME
                    and end_frame is None
                    and time_limit_frames is not None
                    and gamestate.frame >= time_limit_frames
                ):
                    time_limit_reached = True
                    winner = winner_at_time_limit(gamestate, [port for port in bots if port not in out])
                    remaining = format_clock(time_left_seconds(gamestate.frame, math.ceil(args.time_limit)))
                    if any(port not in gamestate.players for port in bots):
                        print(
                            "Zeitlimit erreicht, aber der Sieger konnte nicht "
                            "ermittelt werden: Die Spielerdaten sind unvollständig."
                        )
                    else:
                        result = (
                            f"Spieler {winner} gewinnt nach Zeitablauf"
                            if winner is not None else "kein eindeutiger Sieger"
                        )
                        scores = "; ".join(
                            f"Spieler {port}: {gamestate.players[port].stock} Leben, "
                            f"{gamestate.players[port].percent:.1f}%"
                            for port in bots
                        )
                        print(
                            f"Zeitlimit erreicht ({remaining} verbleibend): "
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
                # eliminated player may no longer appear in the game state,
                # and a player who is out gets no inputs, so they stand still.
                playing = {port: bot for port, bot in bots.items() if port not in out}
                dispatch_frame(playing, controllers, bots_view(gamestate, out))
            elif match_started:
                # The live libmelee menu parser does not currently emit
                # POSTGAME_SCORES. After the match, its first menu event is
                # normally MAIN_MENU or STAGE_SELECT. Any transition out of
                # gameplay after we have seen IN_GAME means this round is over.
                # Stop before MenuHelper can press START or select another map.
                if time_limit_frames is not None and last_game_frame.frame >= time_limit_frames - 1:
                    time_limit_reached = True
                winner = match_result(last_game_frame, bots, time_limit_reached, out)["winner_port"]
                print(
                    "Kampf beendet: "
                    + (f"Spieler {winner} gewinnt" if winner is not None else "kein Sieger")
                    + f". Dolphin wird gestoppt (nächster Zustand: {gamestate.menu_state.name})."
                )
                break
            else:
                # The runner, not the bots, owns menu navigation.
                navigate_menu(menu_helper, gamestate, controllers, characters, stage)

        if time_limit_reached:
            print("Arena nach dem eingestellten Zeitlimit gestoppt.")
        else:
            print("Arena nach einem Kampf beendet.")
        return 0

    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    except EnetDisconnected:
        print("Dolphin wurde geschlossen. Der Kampf wurde vorzeitig beendet.")
        return 130
    except RecordingError as exc:
        recording_failed = True
        print(f"Aufnahmefehler: {exc}", file=sys.stderr)
        return 1
    finally:
        # A second Ctrl-C must not interrupt Dolphin cleanup or video saving.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        for port, bot in bots.items():
            bot.close()
            trouble = bot_trouble(bot.report())
            if trouble:
                print(f"Bot von Spieler {port} {trouble}")
        if args.result_file is not None and match_started:
            args.result_file.write_text(
                json.dumps(match_result(last_game_frame, bots, time_limit_reached, out))
            )
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
                            print(f"Wiederholung gespeichert: {recorder.path}")
            finally:
                signal.signal(signal.SIGINT, previous_sigint)
        if cleanup_error is not None and not recording_failed:
            print(f"Aufnahmefehler: {cleanup_error}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
