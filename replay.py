"""Archive Dolphin's native frame dumps, including after an interrupted run."""

from __future__ import annotations

import os
from pathlib import Path
import select
import shutil
import signal
import subprocess
import sys
import time


class RecordingError(RuntimeError):
    """A replay could not be started or saved."""


def reserve_replay_path(directory: Path) -> Path:
    """Reserve the next round number without overwriting an older recording."""
    numbers = []
    for existing in directory.iterdir():
        if existing.suffix.lower() in (".mp4", ".log"):
            stem = existing.stem
        elif existing.name.startswith(".round_") and existing.name.endswith(".dolphin"):
            stem = existing.name[1:-len(".dolphin")]
        else:
            continue
        prefix, separator, number = stem.partition("_")
        if prefix == "round" and separator and number.isascii() and number.isdecimal():
            numbers.append(int(number))
    number = max(numbers, default=0) + 1
    while True:
        path = directory / f"round_{number}.mp4"
        try:
            path.touch(exist_ok=False)
            return path
        except FileExistsError:
            number += 1


def _status(message: str) -> None:
    # The runner may have been killed, closing this status pipe.
    try:
        os.write(sys.stdout.fileno(), (message + "\n").encode())
    except OSError:
        pass


def _wait_for_dolphin(pid: int) -> None:
    """Stop an orphaned Dolphin and wait briefly for its files to close."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    # The process may be a zombie awaiting reaping; its file handles are closed.


def _frame_dumps(home: Path) -> list[Path]:
    frames = home / "Dump" / "Frames"
    # Dolphin uses the configured format as the filename extension.
    return [*frames.glob("*.matroska"), *frames.glob("*.mkv")]


def _archive(output: Path, home: Path, ffmpeg: str, ffprobe: str, log) -> bool:
    """Remux recoverable native dumps into one MP4; keep the originals on error."""
    segments = sorted(_frame_dumps(home), key=lambda path: (path.stat().st_mtime_ns, path.name))
    segments = [path for path in segments if path.stat().st_size > 0]
    if not segments:
        return False

    # Dolphin can create multiple segments if the rendered resolution changes.
    # Quote paths for FFmpeg's concat format, not for a shell.
    listing = home / "segments.ffconcat"
    with listing.open("w", encoding="utf-8") as file:
        file.write("ffconcat version 1.0\n")
        for segment in segments:
            escaped = str(segment.resolve()).replace("'", "'\\''")
            file.write(f"file '{escaped}'\n")

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0:v:0",
        "-c:v", "copy", "-movflags", "+faststart", str(output),
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=log, timeout=120)
    if result.returncode != 0:
        raise RecordingError("FFmpeg could not save the replay")

    # A successful remux can still contain no frames; do not report it as saved.
    result = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(output)],
        stdout=subprocess.PIPE, stderr=log, text=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip().isdigit() or int(result.stdout.strip()) < 1:
        raise RecordingError("The replay contains no readable video frames")
    return True


def _worker(output: Path, home: Path, ffmpeg: str, ffprobe: str) -> int:
    """Finish the replay even if the runner is forcibly killed."""
    log_path = output.with_suffix(".log")
    try:
        with log_path.open("w", encoding="utf-8") as log:
            _status("READY")
            first = sys.stdin.buffer.readline().decode("ascii", errors="replace").strip()
            pid = None
            if first.startswith("PID "):
                pid = int(first[4:])
                result = sys.stdin.buffer.readline().decode("ascii", errors="replace").strip()
            else:
                result = first
            if result != "STOPPED" and pid is not None:
                _wait_for_dolphin(pid)

            if not _archive(output, home, ffmpeg, ffprobe, log):
                output.unlink(missing_ok=True)
                shutil.rmtree(home.parent)
                _status("EMPTY")
            else:
                shutil.rmtree(home.parent)
                _status("SAVED")
    except (OSError, ValueError, RecordingError, subprocess.SubprocessError) as exc:
        # Keep the original dumps in the session directory for recovery.
        try:
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"{exc}\nOriginal Dolphin dumps are in: {home}\n")
        except OSError:
            pass
        _status("ERROR")
        return 1

    log_path.unlink(missing_ok=True)
    return 0


class NativeReplayRecorder:
    """Supervise one Dolphin dump and save it as a numbered MP4."""

    def __init__(self, process: subprocess.Popen, path: Path, home: Path):
        self.process = process
        self.path = path
        self.home = home

    @classmethod
    def start(cls, directory: Path) -> NativeReplayRecorder:
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            raise RecordingError("FFmpeg and ffprobe are required; install FFmpeg or use --no-record.")

        path = None
        session = None
        session_created = False
        try:
            directory = directory.expanduser().resolve()
            directory.mkdir(parents=True, exist_ok=True)
            path = reserve_replay_path(directory)
            session = directory / f".{path.stem}.dolphin"
            session.mkdir()
            session_created = True
            home = session / "User"
            process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), str(path), str(home), ffmpeg, ffprobe],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, start_new_session=True,
            )
        except OSError as exc:
            if path is not None:
                path.unlink(missing_ok=True)
            if session_created:
                shutil.rmtree(session)
            raise RecordingError(f"Could not start replay recording: {exc}") from exc

        recorder = cls(process, path, home)
        try:
            available, _, _ = select.select([process.stdout], [], [], 10)
            if not available or process.stdout.readline().strip() != "READY":
                raise RecordingError("The replay supervisor could not start")
            return recorder
        except BaseException:
            try:
                recorder.finish(match_started=False)
            except RecordingError:
                pass
            raise

    def attach(self, pid: int) -> None:
        """Tell the supervisor which Dolphin process to stop if the runner dies."""
        self.check()
        try:
            self.process.stdin.write(f"PID {pid}\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RecordingError("Could not connect the replay supervisor") from exc

    def check(self) -> None:
        if self.process.poll() is not None:
            raise RecordingError(f"Replay supervisor stopped unexpectedly; see {self.path.with_suffix('.log')}")

    def check_frames(self) -> None:
        """Fail loudly if Dolphin did not actually start its native frame dump."""
        # FFmpeg may buffer the first bytes before writing them to disk, but
        # Dolphin only creates this file after the encoder successfully starts.
        if not _frame_dumps(self.home):
            raise RecordingError(f"Dolphin did not produce a frame dump in {self.home / 'Dump' / 'Frames'}")

    def finish(self, *, match_started: bool, dolphin_stopped: bool = True) -> bool:
        """After Dolphin stops, wait for the supervisor to save the replay."""
        if self.process.stdin and not self.process.stdin.closed:
            try:
                if dolphin_stopped:
                    self.process.stdin.write("STOPPED\n")
                self.process.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        try:
            result = self.process.wait(timeout=150)
            status = self.process.stdout.readline().strip() if self.process.stdout else ""
        except subprocess.TimeoutExpired as exc:
            self.process.kill()
            self.process.wait()
            raise RecordingError(f"Replay saving timed out; original dumps are in {self.home}") from exc
        finally:
            if self.process.stdout:
                self.process.stdout.close()
        if result != 0 or status not in ("SAVED", "EMPTY"):
            raise RecordingError(
                f"Could not save replay; original dumps are in {self.home}. "
                f"See {self.path.with_suffix('.log')} for details."
            )
        if status == "EMPTY" and match_started:
            raise RecordingError("Dolphin did not produce a video for this match")
        return status == "SAVED"


if __name__ == "__main__":
    raise SystemExit(_worker(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4]))
