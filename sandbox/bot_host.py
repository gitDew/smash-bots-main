#!/usr/bin/env python3
"""Run one team's bot inside its container, one game frame at a time.

stdin carries length-prefixed pickled GameState objects from the runner.
stdout carries one JSON message per line back. Only plain input commands ever
leave this container, so the runner never executes or unpickles team data.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import pickle
import struct
import sys
import traceback

# Imported up front so that unpickling the first frame is not slow.
import melee  # noqa: F401


BOT_PATH = Path("/workspace/bot.py")
MAX_ERROR_CHARS = 1000


class RecordingController:
    """Stands in for melee.Controller and records what the bot asked for."""

    def __init__(self, port: int):
        self.port = port
        self.ops: list[list] = []

    def press_button(self, button) -> None:
        self.ops.append(["press_button", button.name])

    def release_button(self, button) -> None:
        self.ops.append(["release_button", button.name])

    def press_shoulder(self, button, amount) -> None:
        self.ops.append(["press_shoulder", button.name, float(amount)])

    def tilt_analog(self, button, x, y) -> None:
        self.ops.append(["tilt_analog", button.name, float(x), float(y)])

    def tilt_analog_unit(self, button, x, y) -> None:
        self.ops.append(["tilt_analog_unit", button.name, float(x), float(y)])

    def simple_press(self, x, y, button) -> None:
        name = None if button is None else button.name
        self.ops.append(["simple_press", float(x), float(y), name])

    def release_all(self) -> None:
        self.ops.append(["release_all"])

    def flush(self) -> None:
        """The runner flushes inputs; a stray call must not break a bot."""


def load_team_bot(port: int, path: Path = BOT_PATH):
    spec = importlib.util.spec_from_file_location("team_bot", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Bot(port=port)


def short_error() -> str:
    return traceback.format_exc(limit=-4)[-MAX_ERROR_CHARS:]


def read_exactly(stream, count: int) -> bytes | None:
    data = b""
    while len(data) < count:
        chunk = stream.read(count - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def serve(port: int, path: Path, stdin, send) -> int:
    try:
        bot = load_team_bot(port, path)
    except BaseException:
        send({"type": "error", "message": short_error()})
        return 1
    send({"type": "ready"})

    while True:
        header = read_exactly(stdin, 4)
        if header is None:
            return 0
        payload = read_exactly(stdin, struct.unpack(">I", header)[0])
        if payload is None:
            return 0
        gamestate = pickle.loads(payload)
        controller = RecordingController(port)
        reply = {"type": "frame", "frame": gamestate.frame, "ops": []}
        try:
            bot.act(gamestate, controller)
            reply["ops"] = controller.ops
        except Exception:
            reply["error"] = short_error()
        send(reply)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--bot", type=Path, default=BOT_PATH)
    args = parser.parse_args()

    # Reserve the real stdout for the protocol; a print() in a bot goes to stderr.
    protocol = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    stdin = os.fdopen(os.dup(0), "rb", buffering=0)

    def send(message: dict) -> None:
        protocol.write(json.dumps(message).encode() + b"\n")

    return serve(args.port, args.bot, stdin, send)


if __name__ == "__main__":
    sys.exit(main())
