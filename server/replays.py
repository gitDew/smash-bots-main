"""Only finished fight recordings are available to the local admin site."""

from pathlib import Path
import re

from server.db import ROOT


REPLAY_DIR = ROOT / "replays"
REPLAY_NAME = re.compile(r"round_[1-9][0-9]*\.mp4")


def replay_path(name: str) -> Path | None:
    if REPLAY_NAME.fullmatch(name) is None:
        return None
    path = REPLAY_DIR / name
    try:
        # The recorder reserves an empty MP4 and keeps a log/dump directory
        # until archiving succeeds. Failed and in-progress videos stay hidden.
        if (path.is_symlink() or not path.is_file() or path.stat().st_size == 0
                or path.with_suffix(".log").exists()
                or (REPLAY_DIR / f".{path.stem}.dolphin").exists()):
            return None
    except OSError:
        return None
    return path


def saved_replays() -> list[dict]:
    paths = [path for path in REPLAY_DIR.glob("round_*.mp4") if replay_path(path.name) is not None]
    paths.sort(key=lambda path: int(path.stem.removeprefix("round_")), reverse=True)
    return [{"name": path.name, "url": f"/replays/{path.name}"} for path in paths]
