"""Start runner.py as a separate process and read back what happened."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import signal
import sys
from typing import Awaitable, Callable

from server.containers import container_name
from server.db import ROOT


MATCH_DIR = ROOT / "data" / "matches"


def runner_command(teams: list[dict], settings: dict, result_file: Path) -> list[str]:
    command = [
        sys.executable, str(ROOT / "runner.py"),
        *(f"{container_name(team['id'])}={team['character']}" for team in teams),
        "--time-limit", str(settings["match_seconds"]),
        "--iso", settings["iso_path"],
        "--result-file", str(result_file),
    ]
    if not settings["record_replays"]:
        command.append("--no-record")
    return command


def team_result(raw: dict, team_ids: list[int]) -> dict:
    """Translate the runner's port numbers into team ids."""
    by_port = {str(port): team_id for port, team_id in enumerate(team_ids, start=1)}
    winner = raw.get("winner_port")
    return {
        "winner_team": by_port.get(str(winner)),
        "time_limit_reached": raw.get("time_limit_reached", False),
        "players": {by_port[port]: value for port, value in raw.get("players", {}).items() if port in by_port},
        "bots": {by_port[port]: value for port, value in raw.get("bots", {}).items() if port in by_port},
    }


class MatchRunner:
    def __init__(self) -> None:
        self.process: asyncio.subprocess.Process | None = None

    async def run(self, command: list[str], on_line: Callable[[str], Awaitable[None]]) -> int:
        self.process = await asyncio.create_subprocess_exec(
            *command, cwd=ROOT, limit=1 << 20,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            async for raw in self.process.stdout:
                await on_line(raw.decode(errors="replace").rstrip())
            return await self.process.wait()
        finally:
            # If the server is shutting down, do not leave the game running.
            if self.process.returncode is None:
                self.process.send_signal(signal.SIGINT)
            self.process = None

    def stop(self) -> bool:
        """Ask the runner to stop the way Ctrl-C would, so it can save its replay."""
        if self.process is None:
            return False
        self.process.send_signal(signal.SIGINT)
        return True


def read_result(result_file: Path, team_ids: list[int]) -> dict | None:
    try:
        return team_result(json.loads(result_file.read_text()), team_ids)
    except (OSError, ValueError, AttributeError):
        return None
