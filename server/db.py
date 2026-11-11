"""SQLite storage: everything the event must survive a server restart with."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"

DEFAULT_SETTINGS = {
    "prompt_seconds": 300,
    "agent_timeout_seconds": 240,
    # Five minutes by default; 0 plays until one player is left.
    "time_limit_seconds": 300,
    # Stocks a player may lose before they are out. One keeps matches short.
    "lives": 1,
    "azure_base_url": os.environ.get("AZURE_OPENAI_ENDPOINT", ""),
    "azure_api_key": os.environ.get("AZURE_OPENAI_API_KEY", ""),
    "model": "gpt-6-luna",
    "chat_enabled": True,
    "public_url": "",
    "iso_path": str(ROOT / "smash_bros_melee.iso"),
    "record_replays": True,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    phase TEXT NOT NULL, round INTEGER NOT NULL, deadline REAL, stage TEXT
);
CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    token TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL,
    character TEXT,
    points INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0)
);
CREATE TABLE IF NOT EXISTS prompts (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    round INTEGER NOT NULL, text TEXT NOT NULL, updated_at REAL NOT NULL,
    PRIMARY KEY (team_id, round)
);
CREATE TABLE IF NOT EXISTS agent_runs (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    round INTEGER NOT NULL, status TEXT NOT NULL, step TEXT,
    summary TEXT, error TEXT, detail TEXT, updated_at REAL NOT NULL,
    PRIMARY KEY (team_id, round)
);
CREATE TABLE IF NOT EXISTS agent_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    round INTEGER NOT NULL, tone TEXT NOT NULL, label TEXT, detail TEXT, time REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS bot_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    round INTEGER NOT NULL, code TEXT NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS start_choices (
    team_id INTEGER NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    round INTEGER NOT NULL, mode TEXT NOT NULL,
    PRIMARY KEY (team_id, round)
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session TEXT NOT NULL,
    round INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_by_session ON chat_messages (session, id);
CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round INTEGER NOT NULL, team_ids TEXT NOT NULL, status TEXT NOT NULL,
    result TEXT, log TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL, finished_at REAL, stage TEXT
);
"""


class Database:
    def __init__(self, path: Path | str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None)
        if path != ":memory:":
            # This file holds the Azure API key.
            os.chmod(path, 0o600)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        # Teams created before characters existed have none until the host picks one.
        self._add_missing_column("teams", "character")
        self._add_missing_column("teams", "points", "INTEGER NOT NULL DEFAULT 0 CHECK (points >= 0)")
        # Rounds and matches from before stages were picked have none.
        self._add_missing_column("event", "stage")
        self._add_missing_column("matches", "stage")
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS teams_character ON teams (character)")
        self.conn.execute(
            "INSERT OR IGNORE INTO event (id, phase, round, deadline) VALUES (1, 'setup', 0, NULL)"
        )
        # Matches used to stop after a fixed length. Now they play until one
        # player is left, so an old saved length must not cut them short.
        self.conn.execute("DELETE FROM settings WHERE key = 'match_seconds'")
        self.conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('chat_token', ?)",
            (json.dumps(str(uuid.uuid4())),),
        )

    def _add_missing_column(self, table: str, column: str, definition: str = "TEXT") -> None:
        if column not in {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _one(self, sql: str, *args) -> dict | None:
        row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _all(self, sql: str, *args) -> list[dict]:
        return [dict(row) for row in self.conn.execute(sql, args)]

    # Settings and event state

    def settings(self) -> dict:
        stored = {row["key"]: json.loads(row["value"]) for row in self._all("SELECT * FROM settings")}
        return {**DEFAULT_SETTINGS, **stored}

    def update_settings(self, values: dict) -> None:
        for key, value in values.items():
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def event(self) -> dict:
        return self._one("SELECT phase, round, deadline, stage FROM event WHERE id = 1")

    def set_event(self, phase: str, round_number: int, deadline: float | None) -> None:
        self.conn.execute(
            "UPDATE event SET phase = ?, round = ?, deadline = ? WHERE id = 1",
            (phase, round_number, deadline),
        )

    def set_stage(self, stage: str | None) -> None:
        """The current round's stage. It stays until the next round is opened."""
        self.conn.execute("UPDATE event SET stage = ? WHERE id = 1", (stage,))

    # Teams

    def teams(self) -> list[dict]:
        return self._all("SELECT * FROM teams ORDER BY id")

    def team(self, team_id: int) -> dict | None:
        return self._one("SELECT * FROM teams WHERE id = ?", team_id)

    def team_by_token(self, token: str) -> dict | None:
        return self._one("SELECT * FROM teams WHERE token = ?", token)

    def add_team(self, name: str, character: str | None) -> dict:
        cursor = self.conn.execute(
            "INSERT INTO teams (name, token, created_at, character) VALUES (?, ?, ?, ?)",
            (name, str(uuid.uuid4()), time.time(), character),
        )
        return self.team(cursor.lastrowid)

    def rename_team(self, team_id: int, name: str) -> None:
        self.conn.execute("UPDATE teams SET name = ? WHERE id = ?", (name, team_id))

    def set_character(self, team_id: int, character: str) -> None:
        self.conn.execute("UPDATE teams SET character = ? WHERE id = ?", (character, team_id))

    def adjust_points(self, team_id: int, delta: int) -> None:
        self.conn.execute("UPDATE teams SET points = MAX(0, points + ?) WHERE id = ?", (delta, team_id))

    def regenerate_token(self, team_id: int) -> None:
        self.conn.execute("UPDATE teams SET token = ? WHERE id = ?", (str(uuid.uuid4()), team_id))

    def delete_team(self, team_id: int) -> None:
        self.conn.execute("DELETE FROM teams WHERE id = ?", (team_id,))

    # Prompts

    def prompt(self, team_id: int, round_number: int) -> str:
        row = self._one("SELECT text FROM prompts WHERE team_id = ? AND round = ?", team_id, round_number)
        return row["text"] if row else ""

    def save_prompt(self, team_id: int, round_number: int, text: str) -> None:
        self.conn.execute(
            "INSERT INTO prompts (team_id, round, text, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(team_id, round) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at",
            (team_id, round_number, text, time.time()),
        )

    def prompts(self, team_id: int) -> list[dict]:
        return self._all("SELECT round, text FROM prompts WHERE team_id = ? ORDER BY round", team_id)

    # Keep the bot or start clean

    def start_mode(self, team_id: int, round_number: int) -> str | None:
        row = self._one("SELECT mode FROM start_choices WHERE team_id = ? AND round = ?", team_id, round_number)
        return row["mode"] if row else None

    def set_start_mode(self, team_id: int, round_number: int, mode: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO start_choices (team_id, round, mode) VALUES (?, ?, ?)",
            (team_id, round_number, mode),
        )

    def last_fresh_round(self, team_id: int, before_round: int) -> int:
        row = self._one(
            "SELECT MAX(round) AS round FROM start_choices WHERE team_id = ? AND mode = 'fresh' AND round < ?",
            team_id, before_round,
        )
        return row["round"] or 0

    # Agent runs

    def run(self, team_id: int, round_number: int) -> dict | None:
        return self._one("SELECT * FROM agent_runs WHERE team_id = ? AND round = ?", team_id, round_number)

    def latest_run(self, team_id: int) -> dict | None:
        return self._one("SELECT * FROM agent_runs WHERE team_id = ? ORDER BY round DESC LIMIT 1", team_id)

    def set_run(self, team_id: int, round_number: int, status: str, *, step=None,
                summary=None, error=None, detail=None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO agent_runs "
            "(team_id, round, status, step, summary, error, detail, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (team_id, round_number, status, step, summary, error, detail, time.time()),
        )

    def fail_unfinished_runs(self, message: str) -> None:
        self.conn.execute(
            "UPDATE agent_runs SET status = 'failed', step = NULL, error = ? "
            "WHERE status IN ('queued', 'running')",
            (message,),
        )

    # Live agent activity. label is for the kids; detail is only for the host.

    def add_activity(self, team_id: int, round_number: int, tone: str,
                     label: str | None, detail: str | None) -> dict:
        now = time.time()
        cursor = self.conn.execute(
            "INSERT INTO agent_activity (team_id, round, tone, label, detail, time) VALUES (?, ?, ?, ?, ?, ?)",
            (team_id, round_number, tone, label, detail, now),
        )
        return {"id": cursor.lastrowid, "team_id": team_id, "round": round_number,
                "tone": tone, "label": label, "detail": detail, "time": now}

    def latest_activity(self, team_id: int, limit: int = 400) -> list[dict]:
        """Everything from the most recent round in which this team's agent ran."""
        rows = self._all(
            "SELECT * FROM agent_activity WHERE team_id = ? AND round = "
            "(SELECT MAX(round) FROM agent_activity WHERE team_id = ?) ORDER BY id DESC LIMIT ?",
            team_id, team_id, limit,
        )
        return rows[::-1]

    # Bot versions

    def latest_version(self, team_id: int) -> dict | None:
        return self._one("SELECT * FROM bot_versions WHERE team_id = ? ORDER BY id DESC LIMIT 1", team_id)

    def versions(self, team_id: int) -> list[dict]:
        return self._all("SELECT * FROM bot_versions WHERE team_id = ? ORDER BY id DESC", team_id)

    def starter_version(self, team_id: int) -> dict | None:
        return self._one("SELECT * FROM bot_versions WHERE team_id = ? AND round = 0 ORDER BY id LIMIT 1", team_id)

    def version(self, version_id: int) -> dict | None:
        return self._one("SELECT * FROM bot_versions WHERE id = ?", version_id)

    def add_version(self, team_id: int, round_number: int, code: str, note: str) -> None:
        self.conn.execute(
            "INSERT INTO bot_versions (team_id, round, code, note, created_at) VALUES (?, ?, ?, ?, ?)",
            (team_id, round_number, code, note, time.time()),
        )

    # Chat

    def chat_history(self, session: str, limit: int = 200) -> list[dict]:
        rows = self._all(
            "SELECT id, round, role, content FROM chat_messages WHERE session = ? ORDER BY id DESC LIMIT ?",
            session, limit,
        )
        return rows[::-1]

    def add_chat(self, session: str, round_number: int, role: str, content: str) -> dict:
        cursor = self.conn.execute(
            "INSERT INTO chat_messages (session, round, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (session, round_number, role, content, time.time()),
        )
        return {"id": cursor.lastrowid, "round": round_number, "role": role, "content": content}

    def chat_answers_total(self, round_number: int) -> int:
        row = self._one(
            "SELECT COUNT(*) AS n FROM chat_messages WHERE round = ? AND role = 'assistant'", round_number,
        )
        return row["n"]

    def chat_log(self, limit: int = 300) -> list[dict]:
        rows = self._all(
            "SELECT session, round, role, content FROM chat_messages ORDER BY id DESC LIMIT ?", limit,
        )
        return rows[::-1]

    # Matches

    def add_match(self, round_number: int, team_ids: list[int], stage: str) -> int:
        cursor = self.conn.execute(
            "INSERT INTO matches (round, team_ids, status, started_at, stage) VALUES (?, ?, 'running', ?, ?)",
            (round_number, json.dumps(team_ids), time.time(), stage),
        )
        return cursor.lastrowid

    def finish_match(self, match_id: int, status: str, result: dict | None, log: str) -> None:
        self.conn.execute(
            "UPDATE matches SET status = ?, result = ?, log = ?, finished_at = ? WHERE id = ?",
            (status, json.dumps(result) if result is not None else None, log, time.time(), match_id),
        )

    def fail_unfinished_matches(self) -> None:
        self.conn.execute(
            "UPDATE matches SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
            (time.time(),),
        )

    def matches(self, limit: int = 10) -> list[dict]:
        rows = self._all("SELECT * FROM matches ORDER BY id DESC LIMIT ?", limit)
        for row in rows:
            row["team_ids"] = json.loads(row["team_ids"])
            row["result"] = json.loads(row["result"]) if row["result"] else None
        return rows

    def last_bot_report(self, team_id: int) -> dict | None:
        """How this team's bot behaved in the most recent match it played."""
        for match in self.matches(limit=50):
            if team_id in match["team_ids"] and match["result"]:
                return match["result"].get("bots", {}).get(str(team_id))
        return None

    def reset_event(self) -> None:
        """Forget all play but keep teams, links and settings."""
        for table in ("prompts", "start_choices", "agent_runs", "agent_activity", "bot_versions",
                      "chat_messages", "matches"):
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.execute("UPDATE teams SET points = 0")
        self.set_event("setup", 0, None)
        self.set_stage(None)
