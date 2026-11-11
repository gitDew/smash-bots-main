"""Talk to a bot running inside its team's Docker container.

The runner sends game state in and gets back only a list of input commands.
Those commands are validated here before they touch the real controller.
"""

from __future__ import annotations

from collections import deque
import json
import math
import pickle
import struct
import subprocess
import threading
import time

import melee


# How long a frame waits for bots. The rest of the 16.6 ms is left for Dolphin.
FRAME_BUDGET_SECONDS = 0.010
STARTUP_TIMEOUT_SECONDS = 30.0
MAX_LINE_BYTES = 64 * 1024
MAX_OPS_PER_FRAME = 32


class BotStartupError(RuntimeError):
    """A sandboxed bot could not be started."""


def _button(name: object) -> melee.Button:
    if not isinstance(name, str) or name not in melee.Button.__members__:
        raise ValueError(f"unknown button {name!r}")
    return melee.Button[name]


def _number(value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"not a number: {value!r}")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"out of range: {value!r}")
    return float(value)


def apply_ops(controller, ops: object) -> None:
    """Validate a whole frame's commands, then apply them; reject bad frames."""
    if not isinstance(ops, list) or len(ops) > MAX_OPS_PER_FRAME:
        raise ValueError("ops must be a short list")
    calls = []
    for op in ops:
        if not isinstance(op, list) or not op:
            raise ValueError(f"malformed op {op!r}")
        name, args = op[0], op[1:]
        if name in ("press_button", "release_button") and len(args) == 1:
            calls.append((name, (_button(args[0]),)))
        elif name == "press_shoulder" and len(args) == 2:
            calls.append((name, (_button(args[0]), _number(args[1], 0, 1))))
        elif name == "tilt_analog" and len(args) == 3:
            calls.append((name, (_button(args[0]), _number(args[1], 0, 1), _number(args[2], 0, 1))))
        elif name == "tilt_analog_unit" and len(args) == 3:
            calls.append((name, (_button(args[0]), _number(args[1], -1, 1), _number(args[2], -1, 1))))
        elif name == "simple_press" and len(args) == 3:
            button = None if args[2] is None else _button(args[2])
            calls.append((name, (_number(args[0], 0, 1), _number(args[1], 0, 1), button)))
        elif name == "release_all" and not args:
            calls.append((name, ()))
        else:
            raise ValueError(f"unknown op {name!r}")
    for name, call_args in calls:
        getattr(controller, name)(*call_args)


class ContainerBot:
    """A team's bot, as seen from the runner. Its code runs in the container."""

    def __init__(self, container: str, port: int, *, command: list[str] | None = None):
        self.container = container
        self.port = port
        self._command = command or [
            "docker", "exec", "-i", container,
            "python", "-u", "/sandbox/bot_host.py", "--port", str(port),
        ]
        self._lock = threading.Condition()
        self._ready = False
        self._startup_error: str | None = None
        self._process: subprocess.Popen | None = None
        self._in_flight: int | None = None
        self._reply: dict | None = None
        self._dead = False
        self.missed_frames = 0
        self.exceptions = 0
        self.bad_replies = 0
        self.last_error: str | None = None
        self.output: deque[str] = deque(maxlen=50)

    def start(self) -> "ContainerBot":
        self._process = subprocess.Popen(
            self._command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        for target, stream in ((self._read_replies, self._process.stdout),
                               (self._read_output, self._process.stderr)):
            threading.Thread(target=target, args=(stream,), daemon=True).start()
        with self._lock:
            self._lock.wait_for(
                lambda: self._ready or self._startup_error or self._dead,
                timeout=STARTUP_TIMEOUT_SECONDS,
            )
            if not self._ready:
                detail = self._startup_error or "\n".join(self.output) or "no answer"
                self.close()
                raise BotStartupError(f"Bot in {self.container} did not start: {detail}")
        return self

    def _read_replies(self, stream) -> None:
        for raw in iter(lambda: stream.readline(MAX_LINE_BYTES), b""):
            try:
                message = json.loads(raw)
                kind = message["type"]
            except (ValueError, TypeError, KeyError):
                self.bad_replies += 1
                continue
            with self._lock:
                if kind == "ready" and not self._ready:
                    self._ready = True
                elif kind == "error" and not self._ready:
                    self._startup_error = str(message.get("message"))
                elif kind == "frame" and message.get("frame") == self._in_flight:
                    self._in_flight = None
                    self._reply = message
                else:
                    self.bad_replies += 1
                self._lock.notify_all()
        stream.close()
        with self._lock:
            self._dead = True
            self._in_flight = None
            self._lock.notify_all()

    def _read_output(self, stream) -> None:
        for raw in iter(lambda: stream.readline(MAX_LINE_BYTES), b""):
            self.output.append(raw.decode(errors="replace").rstrip()[:500])
        stream.close()

    def send(self, gamestate) -> None:
        """Share a frame unless the bot is still busy with an earlier one."""
        with self._lock:
            if self._dead or self._in_flight is not None:
                return
            self._in_flight = int(gamestate.frame)
        payload = pickle.dumps(gamestate, protocol=5)
        try:
            self._process.stdin.write(struct.pack(">I", len(payload)) + payload)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            with self._lock:
                self._dead = True
                self._in_flight = None

    def apply(self, controller, deadline: float) -> None:
        """Press whatever the bot answered in time; otherwise leave it neutral.

        An answer that arrives late is used on the next frame, so a slow bot
        plays a little behind instead of standing still.
        """
        with self._lock:
            self._lock.wait_for(
                lambda: self._reply is not None or self._dead,
                timeout=max(0.0, deadline - time.monotonic()),
            )
            reply, self._reply = self._reply, None
        if reply is None:
            self.missed_frames += 1
            return
        if "error" in reply:
            self.exceptions += 1
            self.last_error = str(reply["error"])[:1000]
            return
        try:
            apply_ops(controller, reply.get("ops"))
        except ValueError as exc:
            self.bad_replies += 1
            self.last_error = f"Rejected inputs: {exc}"[:1000]

    def report(self) -> dict:
        return {
            "missed_frames": self.missed_frames, "exceptions": self.exceptions,
            "bad_replies": self.bad_replies, "last_error": self.last_error,
            "output": list(self.output)[-10:],
        }

    def close(self) -> None:
        if self._process is None:
            return
        try:
            self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
