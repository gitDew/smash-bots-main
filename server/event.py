"""The event's rounds: who may do what, and when.

    setup -> prompting -> coding -> ready -> fighting -> ready -> prompting ...

Every rule is enforced here on the server. WebSockets only tell the pages what
happened, so a page that is out of date cannot get around a closed round.
"""

from __future__ import annotations

import asyncio
from collections import Counter, deque
import logging
from pathlib import Path
import time
import uuid

from fastapi import WebSocket

from server.agent import AgentRunner
from server.characters import CHARACTERS
from server.chat import Chat, ChatRejected
from server.containers import Docker, DockerError
from server.db import Database
from server.hub import Hub
from server.match import MATCH_DIR, MatchRunner, read_result, runner_command
from server.stages import DEFAULT_STAGE, STAGES, stage_info


logger = logging.getLogger("smash.event")

MAX_PROMPT_CHARS = 2000
MIN_PROMPT_SECONDS, MAX_PROMPT_SECONDS = 15, 7200
MAX_TEAMS_PER_MATCH = 4
TICK_SECONDS = 0.25
MAX_LOG_LINES = 300
STARTER_BOT = Path(__file__).with_name("starter_bot.py")
START_MODES = ("keep", "fresh")


class EventError(Exception):
    """An admin action that is not possible right now."""


class RoundClosed(Exception):
    """A prompt arrived when no round was open."""


class Event:
    def __init__(self, db: Database, hub: Hub, docker: Docker, client, *, team_port: int = 8000):
        self.db = db
        self.hub = hub
        self.docker = docker
        self.client = client
        self.team_port = team_port
        self.agents = AgentRunner(db, docker, client, self.send_team_state, self.send_activity)
        self.chat = Chat(db, hub, client)
        self.match_runner = MatchRunner()
        self.match_log: deque[str] = deque(maxlen=MAX_LOG_LINES)
        # A demo is a match in which nobody has a bot. It is never saved.
        self.demo_match = False
        self.docker_status = "not checked"
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def set_client(self, client) -> None:
        """Takes effect from the next model call; work already running carries on."""
        self.client = client
        self.agents.client = client
        self.chat.client = client

    # Background work

    def spawn(self, coroutine) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)
        return task

    def _task_finished(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("Background task failed", exc_info=task.exception())

    async def startup(self) -> None:
        """Pick up after a restart: work that was running is gone, so say so."""
        ok, detail = await self.docker.available()
        self.docker_status = "ok" if ok else detail
        event = self.db.event()
        if event["phase"] in ("coding", "fighting"):
            self.db.fail_unfinished_runs("The server restarted during this step.")
            self.db.fail_unfinished_matches()
            self.db.set_event("ready" if event["round"] else "setup", event["round"], None)

    async def tick_forever(self) -> None:
        while True:
            event = self.db.event()
            if event["phase"] == "prompting" and event["deadline"] and time.time() >= event["deadline"]:
                try:
                    await self.close_round()
                except EventError:
                    pass
                except Exception:
                    # The clock must keep running for the rest of the event.
                    logger.exception("Could not close the round")
            await asyncio.sleep(TICK_SECONDS)

    # Prompts

    def prompt_open(self, event: dict | None = None) -> bool:
        event = event or self.db.event()
        return event["phase"] == "prompting" and (
            event["deadline"] is None or time.time() < event["deadline"]
        )

    def save_prompt(self, team_id: int, text: str) -> None:
        event = self.db.event()
        if not self.prompt_open(event):
            raise RoundClosed
        self.db.save_prompt(team_id, event["round"], text[:MAX_PROMPT_CHARS])

    def asks_start_choice(self, team_id: int, event: dict | None = None) -> bool:
        """From round 2 on, whenever there is a changed bot to keep or wipe."""
        event = event or self.db.event()
        if not self.prompt_open(event) or event["round"] < 2:
            return False
        latest, starter = self.db.latest_version(team_id), self.db.starter_version(team_id)
        return latest is not None and starter is not None and latest["code"] != starter["code"]

    def set_start_mode(self, team_id: int, mode: str) -> None:
        """Only recorded here: nothing is wiped until the round closes, so kids can change their mind."""
        event = self.db.event()
        if mode not in START_MODES or not self.asks_start_choice(team_id, event):
            raise RoundClosed
        self.db.set_start_mode(team_id, event["round"], mode)

    # Rounds

    async def open_round(self, seconds: int | None = None, stage: str | None = None) -> None:
        """The host picks the prompt time and the stage for each round.

        Left out, they are the same as last round's.
        """
        async with self._lock:
            event = self.db.event()
            if event["phase"] not in ("setup", "ready"):
                raise EventError("A round can only be opened before the first round or between rounds.")
            seconds = seconds or self.db.settings()["prompt_seconds"]
            if not MIN_PROMPT_SECONDS <= seconds <= MAX_PROMPT_SECONDS:
                raise EventError(
                    f"The prompt time must be {MIN_PROMPT_SECONDS} seconds to {MAX_PROMPT_SECONDS // 60} minutes."
                )
            stage = self._require_stage(stage or event["stage"] or DEFAULT_STAGE)
            teams = self.db.teams()
            if not teams:
                raise EventError("Add at least one team first.")
            self._require_characters(teams)
            await asyncio.gather(*(self._prepare_team(team) for team in teams))
            self.db.update_settings({"prompt_seconds": seconds})
            self.db.set_stage(stage)
            self.db.set_event("prompting", event["round"] + 1, time.time() + seconds)
        await self.broadcast()

    async def add_time(self, seconds: int) -> None:
        event = self.db.event()
        if event["phase"] != "prompting" or event["deadline"] is None:
            raise EventError("No round is open.")
        self.db.set_event("prompting", event["round"], max(event["deadline"], time.time()) + seconds)
        await self.broadcast()

    async def close_round(self) -> None:
        async with self._lock:
            event = self.db.event()
            if event["phase"] != "prompting":
                raise EventError("No round is open.")
            round_number = event["round"]
            self.db.set_event("coding", round_number, None)
            queued = []
            for team in self.db.teams():
                if self.db.prompt(team["id"], round_number).strip():
                    self.db.set_run(team["id"], round_number, "queued", step="Waiting to start")
                    queued.append(team["id"])
                elif self.db.start_mode(team["id"], round_number) == "fresh":
                    starter = self.db.starter_version(team["id"])["code"]
                    self.db.add_version(team["id"], round_number, starter, "Clean start")
                    self.db.set_run(
                        team["id"], round_number, "skipped",
                        summary="Clean start! Your bot is back to the starter bot.",
                    )
                else:
                    self.db.set_run(
                        team["id"], round_number, "skipped",
                        summary="No prompt this round, so your bot stayed the same.",
                    )
            if queued:
                self.spawn(self._run_agents(round_number, queued))
            else:
                self.db.set_event("ready", round_number, None)
        await self.broadcast()

    async def _run_agents(self, round_number: int, team_ids: list[int]) -> None:
        results = await asyncio.gather(
            *(self.agents.run(team_id, round_number) for team_id in team_ids),
            return_exceptions=True,
        )
        for team_id, result in zip(team_ids, results):
            if isinstance(result, BaseException):
                logger.error("Agent for team %s failed", team_id, exc_info=result)
                self.db.set_run(
                    team_id, round_number, "failed",
                    error="The agent had a problem, so your last bot will fight.",
                    detail=repr(result),
                )
        event = self.db.event()
        if event["phase"] == "coding" and event["round"] == round_number:
            self.db.set_event("ready", round_number, None)
        await self.broadcast()

    async def retry_agent(self, team_id: int) -> None:
        async with self._lock:
            event = self.db.event()
            if event["phase"] != "ready":
                raise EventError("Agents can only be retried once all of them have finished.")
            if not self.db.prompt(team_id, event["round"]).strip():
                raise EventError("This team has no prompt in this round.")
            self.db.set_event("coding", event["round"], None)
            self.db.set_run(team_id, event["round"], "queued", step="Waiting to start")
            self.spawn(self._run_agents(event["round"], [team_id]))
        await self.broadcast()

    # Teams

    async def _prepare_team(self, team: dict) -> None:
        """Make sure the container exists and holds the team's latest saved bot."""
        await self.docker.ensure(team["id"])
        version = self.db.latest_version(team["id"])
        if version is None:
            self.db.add_version(team["id"], 0, STARTER_BOT.read_text(), "Starter bot")
            version = self.db.latest_version(team["id"])
        await self.docker.write_bot(team["id"], version["code"])

    def _require_idle(self) -> None:
        if self.db.event()["phase"] in ("coding", "fighting"):
            raise EventError("Wait until the agents or the match have finished.")

    def _require_team(self, team_id: int) -> dict:
        team = self.db.team(team_id)
        if team is None:
            raise EventError("That team does not exist.")
        return team

    def _require_characters(self, teams: list[dict]) -> None:
        missing = [team["name"] for team in teams if not team["character"]]
        if missing:
            raise EventError(f"Choose a character for: {', '.join(missing)}.")

    @staticmethod
    def _require_stage(stage: str) -> str:
        if stage not in STAGES:
            raise EventError("Choose a stage from the list.")
        return stage

    def _require_free_character(self, character: str, team_id: int | None = None) -> None:
        if character not in CHARACTERS:
            raise EventError("Choose a character from the list.")
        for team in self.db.teams():
            if team["character"] == character and team["id"] != team_id:
                raise EventError(f"{CHARACTERS[character]} is already taken by {team['name']}.")

    async def set_character(self, team_id: int, character: str) -> None:
        self._require_idle()
        self._require_team(team_id)
        self._require_free_character(character, team_id)
        self.db.set_character(team_id, character)
        await self.broadcast()

    async def add_team(self, name: str, character: str) -> dict:
        name = name.strip()
        if not name or len(name) > 40:
            raise EventError("Team names must be 1 to 40 characters.")
        if any(team["name"].lower() == name.lower() for team in self.db.teams()):
            raise EventError("There is already a team with that name.")
        self._require_free_character(character)
        team = self.db.add_team(name, character)
        try:
            await self._prepare_team(team)
        except (DockerError, OSError) as exc:
            self.db.delete_team(team["id"])
            raise EventError(f"Could not create the team's container: {exc}") from exc
        await self.broadcast()
        return team

    async def rename_team(self, team_id: int, name: str) -> None:
        self._require_team(team_id)
        name = name.strip()
        if not name or len(name) > 40:
            raise EventError("Team names must be 1 to 40 characters.")
        if any(t["name"].lower() == name.lower() and t["id"] != team_id for t in self.db.teams()):
            raise EventError("There is already a team with that name.")
        self.db.rename_team(team_id, name)
        await self.broadcast()

    async def delete_team(self, team_id: int) -> None:
        self._require_idle()
        self._require_team(team_id)
        self.db.delete_team(team_id)
        await self.hub.close_team(team_id)
        await self.docker.remove(team_id)
        await self.broadcast()

    async def regenerate_link(self, team_id: int) -> None:
        self._require_team(team_id)
        self.db.regenerate_token(team_id)
        await self.hub.close_team(team_id)
        await self.broadcast()

    async def rollback(self, team_id: int, version_id: int) -> None:
        self._require_idle()
        self._require_team(team_id)
        version = self.db.version(version_id)
        if version is None or version["team_id"] != team_id:
            raise EventError("That version does not belong to this team.")
        await self.docker.ensure(team_id)
        await self.docker.write_bot(team_id, version["code"])
        self.db.add_version(
            team_id, self.db.event()["round"], version["code"],
            f"Rolled back to version {version_id}",
        )
        await self.broadcast()

    async def reset(self) -> None:
        self._require_idle()
        self.db.reset_event()
        self.match_log.clear()
        await asyncio.gather(*(self._reset_team(team) for team in self.db.teams()))
        await self.broadcast()

    async def _reset_team(self, team: dict) -> None:
        await self.docker.remove(team["id"])
        await self._prepare_team(team)

    # Matches

    async def start_match(self, team_ids: list[int], stage: str | None = None, *, demo: bool = False) -> None:
        """Start a match with the teams' bots, or a demo with their characters and no bots.

        A match is played on its round's stage. The stage given here is only
        for demos, which show the next round's stage, and for matches before
        any round has a stage.
        """
        async with self._lock:
            event = self.db.event()
            if event["phase"] not in ("setup", "ready"):
                raise EventError("A match can only start when no round or agent is running.")
            if len(set(team_ids)) != len(team_ids) or not 2 <= len(team_ids) <= MAX_TEAMS_PER_MATCH:
                raise EventError(f"Choose 2 to {MAX_TEAMS_PER_MATCH} different teams.")
            self._require_characters([self._require_team(team_id) for team_id in team_ids])
            choices = (stage, event["stage"]) if demo else (event["stage"], stage)
            stage = self._require_stage(next((choice for choice in choices if choice), DEFAULT_STAGE))
            if not Path(self.db.settings()["iso_path"]).expanduser().is_file():
                raise EventError("The Melee ISO was not found. Check its path in the settings.")
            self.demo_match = demo
            self.db.set_event("fighting", event["round"], None)
            self.spawn(self._run_match(event["round"], team_ids, stage, demo))
        await self.broadcast()

    async def _log(self, line: str) -> None:
        self.match_log.append(line)
        await self.hub.to_admins({"type": "match_log", "line": line})

    async def _ready_for_match(self, team_id: int) -> None:
        team = self._require_team(team_id)
        await self._prepare_team(team)
        await self.docker.restart(team_id)
        verdict = await self.docker.check_bot(team_id)
        if not verdict.get("ok"):
            raise EventError(f"{team['name']}'s bot failed its check: {verdict.get('error')}")

    async def _run_match(self, round_number: int, team_ids: list[int], stage: str, demo: bool) -> None:
        match_id = None if demo else self.db.add_match(round_number, team_ids, stage)
        self.match_log.clear()
        status, result = "failed", None
        try:
            if demo:
                await self._log(
                    f"Demo on {STAGES[stage]['name']}: nobody has a bot, so every player stands still. "
                    "Press STOP MATCH when you are done explaining."
                )
                result_file = None
            else:
                await self._log(f"Getting the bots ready for {STAGES[stage]['name']}...")
                await asyncio.gather(*(self._ready_for_match(team_id) for team_id in team_ids))
                MATCH_DIR.mkdir(parents=True, exist_ok=True)
                result_file = MATCH_DIR / f"match_{match_id}.json"
            code = await self.match_runner.run(
                runner_command(
                    [self.db.team(t) for t in team_ids], self.db.settings(), result_file,
                    stage=stage, demo=demo,
                ),
                self._log,
            )
            if result_file is not None:
                result = read_result(result_file, team_ids)
            status = "finished" if code == 0 else "stopped" if code == 130 else "failed"
            await self._log(f"Runner exited with code {code}.")
        except (EventError, DockerError, OSError) as exc:
            await self._log(f"Could not run the match: {exc}")
        finally:
            if match_id is not None:
                self.db.finish_match(match_id, status, result, "\n".join(self.match_log))
            self.demo_match = False
            event = self.db.event()
            if event["phase"] == "fighting":
                self.db.set_event("ready" if round_number else "setup", round_number, None)
            await self.broadcast()

    # Scores

    def scores(self, team_id: int | None = None) -> dict:
        """Wins from matches in a real round that ended by themselves.

        A match the host stops counts for nobody, and demos are never saved.
        """
        teams = {team["id"]: team for team in self.db.teams()}
        matches = self.db.finished_matches()
        wins, played = Counter(), Counter()
        for match in matches:
            played.update(match["team_ids"])
            winner = (match["result"] or {}).get("winner_team")
            if winner is not None:
                wins[winner] += 1
        table = sorted(
            (
                {"name": team["name"], "character": CHARACTERS.get(team["character"]),
                 "wins": wins[number], "played": played[number]}
                for number, team in teams.items()
            ),
            key=lambda row: (-row["wins"], row["name"].lower()),
        )
        last = None
        if matches:
            match = matches[-1]
            winner = (match["result"] or {}).get("winner_team")
            last = {
                "round": match["round"],
                "stage": STAGES.get(match["stage"], {}).get("name"),
                "teams": [teams[number]["name"] for number in match["team_ids"] if number in teams],
                "winner": teams[winner]["name"] if winner in teams else None,
                "won": team_id is not None and winner == team_id,
            }
        return {"table": table, "last": last}

    def stop_match(self) -> None:
        if not self.match_runner.stop():
            raise EventError("No match is running.")

    # Chat

    def admit_question(self, session: str, text: str) -> None:
        """Raises ChatRejected, or starts answering in the background."""
        event = self.db.event()
        question = self.chat.admit(session, event, text)
        self.spawn(self._answer(session, event, question))

    async def _answer(self, session: str, event: dict, question: str) -> None:
        await self.send_chat_state(session)
        await self.chat.ask(session, event, question)
        await self.send_chat_state(session)
        await self.hub.to_admins(self.admin_state())

    def chat_token_valid(self, token: str) -> bool:
        return token == self.db.settings()["chat_token"]

    async def regenerate_chat_link(self) -> None:
        self.db.update_settings({"chat_token": str(uuid.uuid4())})
        await self.hub.close_chats()
        await self.broadcast()

    def chat_state(self, session: str) -> dict:
        event = self.db.event()
        return {
            "type": "state", "phase": event["phase"], "round": event["round"],
            "remaining": self._remaining(event), "prompt_open": self.prompt_open(event),
            "stage": stage_info(event["stage"]), "demo": self._demo_running(event),
            "scores": self.scores(),
            "chat": self.chat.status(session, event),
        }

    async def send_chat_state(self, session: str) -> None:
        await self.hub.to_chat(session, self.chat_state(session))

    async def chat_message(self, session: str, socket: WebSocket, message: dict) -> None:
        if message.get("type") != "chat":
            return
        try:
            self.admit_question(session, str(message.get("text", "")))
        except ChatRejected as exc:
            await socket.send_json({"type": "chat_rejected", "message": str(exc)})

    # What the pages see

    def _demo_running(self, event: dict) -> bool:
        return event["phase"] == "fighting" and self.demo_match

    def _remaining(self, event: dict) -> float | None:
        return max(0.0, event["deadline"] - time.time()) if event["deadline"] else None

    def team_state(self, team: dict) -> dict:
        event = self.db.event()
        run = self.db.latest_run(team["id"])
        working = run is not None and run["status"] in ("queued", "running")
        version = self.db.latest_version(team["id"])
        return {
            "type": "state",
            "team": {"name": team["name"], "character": CHARACTERS.get(team["character"])},
            "phase": event["phase"],
            "round": event["round"],
            "remaining": self._remaining(event),
            "prompt_open": self.prompt_open(event),
            "stage": stage_info(event["stage"]),
            "demo": self._demo_running(event),
            "scores": self.scores(team["id"]),
            "prompt": self.db.prompt(team["id"], event["round"]),
            "max_prompt_chars": MAX_PROMPT_CHARS,
            "agent": None if run is None else {
                "round": run["round"], "status": run["status"], "step": run["step"],
                "summary": run["summary"], "error": run["error"],
            },
            # The code appears once the agent has finished changing it.
            "code": None if version is None or working else {
                "id": version["id"], "round": version["round"], "text": version["code"],
            },
            "start": {
                "asked": self.asks_start_choice(team["id"], event),
                "mode": self.db.start_mode(team["id"], event["round"]),
            },
        }

    def _public_base(self, settings: dict) -> str:
        return settings["public_url"].rstrip("/") or f"http://localhost:{self.team_port}"

    def team_link(self, team: dict, settings: dict) -> str:
        return f"{self._public_base(settings)}/t/{team['token']}"

    def chat_link(self, settings: dict) -> str:
        return f"{self._public_base(settings)}/c/{settings['chat_token']}"

    def admin_state(self) -> dict:
        event = self.db.event()
        settings = self.db.settings()
        teams = []
        names = {}
        for team in self.db.teams():
            names[team["id"]] = team["name"]
            run = self.db.run(team["id"], event["round"])
            version = self.db.latest_version(team["id"])
            teams.append({
                "id": team["id"], "name": team["name"], "character": team["character"],
                "link": self.team_link(team, settings),
                "prompt": self.db.prompt(team["id"], event["round"]),
                "online": len(self.hub.teams[team["id"]]),
                "agent": None if run is None else {
                    "status": run["status"], "step": run["step"], "summary": run["summary"],
                    "error": run["error"], "detail": run["detail"],
                },
                "version_note": version["note"] if version else None,
                "start_mode": self.db.start_mode(team["id"], event["round"]),
            })
        matches = []
        for match in self.db.matches(limit=8):
            winner = (match["result"] or {}).get("winner_team")
            matches.append({
                "id": match["id"], "round": match["round"], "status": match["status"],
                "stage": STAGES.get(match["stage"], {}).get("name"),
                "teams": [names.get(team_id, "?") for team_id in match["team_ids"]],
                # A stopped match still has a leader, but nobody won it.
                "winner": names.get(winner) if match["status"] == "finished" else None,
            })
        return {
            "type": "state", "phase": event["phase"], "round": event["round"],
            "remaining": self._remaining(event), "teams": teams,
            "stage": event["stage"], "stages": {stage: stage_info(stage) for stage in STAGES},
            "demo": self._demo_running(event),
            "scores": self.scores(),
            # The key is write-only: the admin page is only ever told if one is saved.
            "settings": {key: value for key, value in settings.items() if key != "azure_api_key"},
            "characters": CHARACTERS,
            "matches": matches, "match_log": list(self.match_log),
            "chat": {
                "link": self.chat_link(settings),
                "questions": self.db.chat_answers_total(event["round"]),
                "phones": len(self.hub.chats),
            },
            "health": {
                "llm": self.client is not None, "key_saved": bool(settings["azure_api_key"]),
                "docker": self.docker_status,
            },
        }

    def team_detail(self, team_id: int) -> dict:
        team = self._require_team(team_id)
        return {
            "name": team["name"],
            "prompts": self.db.prompts(team_id),
            "versions": [
                {"id": v["id"], "round": v["round"], "note": v["note"], "code": v["code"]}
                for v in self.db.versions(team_id)
            ],
        }

    @staticmethod
    def _public_activity(entry: dict) -> dict:
        """What a team may see of an agent step: never the host's detail."""
        return {key: entry[key] for key in ("id", "round", "tone", "label", "time")}

    def team_activity(self, team_id: int) -> list[dict]:
        return [self._public_activity(e) for e in self.db.latest_activity(team_id) if e["label"]]

    def admin_activity(self) -> list[dict]:
        return [entry for team in self.db.teams() for entry in self.db.latest_activity(team["id"])]

    async def send_activity(self, team_id: int, entry: dict) -> None:
        await self.hub.to_admins({"type": "agent_activity", "entry": entry})
        if entry["label"]:
            await self.hub.to_team(team_id, {"type": "agent_activity", "entry": self._public_activity(entry)})

    async def send_team_state(self, team_id: int) -> None:
        team = self.db.team(team_id)
        if team is not None:
            await self.hub.to_team(team_id, self.team_state(team))
            await self.hub.to_admins(self.admin_state())

    async def broadcast(self) -> None:
        await asyncio.gather(*(
            self.hub.to_team(team["id"], self.team_state(team)) for team in self.db.teams()
        ), *(self.send_chat_state(session) for session in list(self.hub.chats)))
        await self.hub.to_admins(self.admin_state())

    async def team_message(self, team: dict, socket: WebSocket, message: dict) -> None:
        kind = message.get("type")
        if kind == "prompt":
            text = str(message.get("text", ""))
            try:
                self.save_prompt(team["id"], text)
            except RoundClosed:
                await socket.send_json({"type": "prompt_rejected"})
                await socket.send_json(self.team_state(team))
                return
            await socket.send_json({"type": "prompt_saved", "seq": message.get("seq")})
            await self.hub.to_team(team["id"], {"type": "prompt", "text": text}, skip=socket)
        elif kind == "start_mode":
            try:
                self.set_start_mode(team["id"], str(message.get("mode")))
            except RoundClosed:
                await socket.send_json(self.team_state(team))
                return
            await self.send_team_state(team["id"])
