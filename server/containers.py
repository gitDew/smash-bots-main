"""One long-lived, network-less Docker container per team."""

from __future__ import annotations

import asyncio
import json


IMAGE = "smash-bots-sandbox"
BOT_FILE = "/workspace/bot.py"
MAX_BOT_BYTES = 200 * 1024
MAX_OUTPUT_BYTES = 30 * 1024
COMMAND_SECONDS = 30
CHECK_SECONDS = 25

SANDBOX_FLAGS = (
    "--init", "--network", "none", "--read-only", "--tmpfs", "/tmp:size=64m",
    "--memory", "512m", "--cpus", "1", "--pids-limit", "128",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
)


class DockerError(RuntimeError):
    """A Docker command failed or timed out."""


def container_name(team_id: int) -> str:
    # Derived from the numeric id only; team names never reach a Docker command.
    return f"smash-team-{team_id}"


def volume_name(team_id: int) -> str:
    return f"smash-team-{team_id}-workspace"


class Docker:
    async def _docker(self, *args: str, stdin: bytes | None = None,
                      timeout: float = 60.0) -> tuple[int, bytes, bytes]:
        process = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(stdin), timeout)
        except asyncio.TimeoutError as exc:
            raise DockerError(f"Zeitlimit für docker {args[0]} überschritten") from exc
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        return process.returncode, out, err

    async def _checked(self, *args: str, **kwargs) -> bytes:
        code, out, err = await self._docker(*args, **kwargs)
        if code != 0:
            raise DockerError(err.decode(errors="replace").strip() or f"docker {args[0]} fehlgeschlagen")
        return out

    async def available(self) -> tuple[bool, str]:
        try:
            await self._checked("image", "inspect", IMAGE, timeout=10)
        except (DockerError, OSError) as exc:
            return False, str(exc)
        return True, "ok"

    async def ensure(self, team_id: int) -> None:
        """Start the team's container, first replacing it if an older image made it.

        Without this, a rebuilt image would only reach new teams. The bot is
        kept in a volume, so it survives the replacement.
        """
        name = container_name(team_id)
        code, out, _ = await self._docker("inspect", "-f", "{{.Image}} {{.State.Running}}", name)
        image, _, running = out.decode().strip().partition(" ")
        if code == 0 and image != await self._image_id():
            await self._checked("rm", "-f", name)
            code = 1
        if code != 0:
            await self._checked(
                "run", "-d", "--name", name, *SANDBOX_FLAGS,
                "-v", f"{volume_name(team_id)}:/workspace", IMAGE,
            )
        elif running != "true":
            await self._checked("start", name)

    async def _image_id(self) -> str:
        return (await self._checked("image", "inspect", "-f", "{{.Id}}", IMAGE, timeout=10)).decode().strip()

    async def restart(self, team_id: int) -> None:
        """Clears anything still running from an earlier agent or match."""
        await self._checked("restart", "-t", "0", container_name(team_id))

    async def remove(self, team_id: int) -> None:
        await self._docker("rm", "-f", container_name(team_id))
        await self._docker("volume", "rm", "-f", volume_name(team_id))

    async def write_bot(self, team_id: int, code: str) -> None:
        await self._checked(
            "exec", "-i", container_name(team_id), "sh", "-c",
            f"rm -rf {BOT_FILE} && cat > {BOT_FILE}", stdin=code.encode(),
        )

    async def read_file(self, team_id: int, path: str, limit: int = MAX_OUTPUT_BYTES) -> str:
        code, out, err = await self._docker(
            "exec", container_name(team_id), "head", "-c", str(limit), "--", path,
        )
        if code != 0:
            raise DockerError(err.decode(errors="replace").strip() or f"{path} konnte nicht gelesen werden")
        return out.decode(errors="replace")

    async def read_bot(self, team_id: int) -> str | None:
        """Read through the container, so the host never opens files the agent controlled."""
        try:
            code = await self.read_file(team_id, BOT_FILE, MAX_BOT_BYTES + 1)
        except DockerError:
            return None
        return None if len(code.encode()) > MAX_BOT_BYTES else code

    async def run_command(self, team_id: int, command: str) -> tuple[int, str]:
        script = (
            'set -o pipefail; timeout -k 2 "$1" bash -c "$2" 2>&1 </dev/null | head -c "$3"'
        )
        code, out, _ = await self._docker(
            "exec", container_name(team_id), "bash", "-c", script, "_",
            str(COMMAND_SECONDS), command, str(MAX_OUTPUT_BYTES),
            timeout=COMMAND_SECONDS + 10,
        )
        return code, out.decode(errors="replace")

    async def check_bot(self, team_id: int) -> dict:
        # Bot prints are dropped here so that they cannot flood the host.
        script = f"timeout -k 2 {CHECK_SECONDS} python /sandbox/check_bot.py 2>/dev/null | head -c 20000"
        try:
            code, out, _ = await self._docker(
                "exec", container_name(team_id), "bash", "-c", script, timeout=CHECK_SECONDS + 10,
            )
        except DockerError as exc:
            return {"ok": False, "error": str(exc)}
        lines = out.decode(errors="replace").strip().splitlines()
        try:
            verdict = json.loads(lines[-1])
            if not isinstance(verdict, dict):
                raise ValueError
        except (IndexError, ValueError):
            return {"ok": False, "error": "Der Bot hat seinen Test nicht beendet. Er hängt möglicherweise in einer Schleife fest."}
        return verdict
