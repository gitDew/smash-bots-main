#!/usr/bin/env python3
"""Show Melee's offline character-select screen until interrupted."""

from __future__ import annotations

import argparse
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import melee

from runner import configure_render_to_main


PROJECT_ROOT = Path(__file__).resolve().parent


def focus_main_display() -> None:
    """With AeroSpace, start on the main monitor's currently visible workspace."""
    aerospace = shutil.which("aerospace") if sys.platform == "darwin" else None
    if not aerospace:
        return
    try:
        subprocess.run(
            [aerospace, "focus-monitor", "main"], check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=3,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Could not focus the main display: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--iso",
        type=Path,
        default=PROJECT_ROOT / "smash_bros_melee.iso",
        help="Melee ISO (default: ./smash_bros_melee.iso)",
    )
    parser.add_argument(
        "--dolphin",
        help="Slippi Dolphin installation directory or executable; auto-detected by default",
    )
    args = parser.parse_args()

    iso = args.iso.expanduser().resolve()
    if not iso.is_file():
        print(f"ISO not found: {iso}", file=sys.stderr)
        return 2

    console = melee.Console(
        path=str(Path(args.dolphin).expanduser()) if args.dolphin else None,
        fullscreen=True,
        gfx_backend="OGL" if sys.platform == "darwin" else "",
        disable_audio=False,
        save_replays=False,
    )
    configure_render_to_main(console)
    controllers = {
        port: melee.Controller(console=console, port=port, type=melee.ControllerType.STANDARD)
        for port in (1, 2)
    }
    connected = []
    menu_helper = melee.MenuHelper()
    showing_roster = False

    def stop(*_args) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    try:
        print("Starting Melee's welcome screen. Press Ctrl-C to stop.", flush=True)
        focus_main_display()
        console.run(iso_path=str(iso))
        if not console.connect():
            print("Could not connect to Slippi Dolphin.", file=sys.stderr)
            return 1
        for controller in controllers.values():
            if not controller.connect():
                print(f"Could not connect controller on port {controller.port}.", file=sys.stderr)
                return 1
            connected.append(controller)

        while True:
            gamestate = console.step()
            if gamestate is None:
                continue

            if showing_roster:
                # Both controllers were neutralized; leave the screen idle.
                continue
            if gamestate.menu_state == melee.Menu.CHARACTER_SELECT:
                for controller in controllers.values():
                    controller.release_all()
                showing_roster = True
                print("Character-select screen ready. Press Ctrl-C to stop.", flush=True)
            else:
                # Use only player 1 to enter offline VS mode. Leaving player 2
                # untouched prevents accidental selections and the READY banner.
                menu_helper.choose_versus_mode(gamestate, controllers[1])
                controllers[2].release_all()
    except KeyboardInterrupt:
        print("\nStopping the welcome screen.")
        return 0
    finally:
        for controller in connected:
            controller.disconnect()
        # libmelee cannot join its worker if interrupted before it was started.
        worker = console._slippstream._worker
        if worker is not None and worker.pid is None:
            console._slippstream._worker = None
        console.stop()


if __name__ == "__main__":
    raise SystemExit(main())
