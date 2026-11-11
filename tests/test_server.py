"""Test round rules, the agent loop and the chat with fake Docker and a fake LLM."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from fastapi.testclient import TestClient
import melee
from openai.types.responses import (
    Response, ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText, ResponseReasoningItem,
)
from openai.types.responses.response import IncompleteDetails

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.app import create_admin_app, create_team_app
from server.chat import ChatRejected
from server.containers import Docker
from server.db import Database
from server.event import Event, EventError, RoundClosed
from server.hub import Hub
from server.llm import azure_base_url, azure_client
from server.llm_check import CODE_WORD, run_checks
from server.match import runner_command
from server.stages import STAGES, SVGS
import runner


class FakeDocker:
    def __init__(self):
        self.bots: dict[int, str] = {}
        self.verdict = {"ok": True}
        self.commands: list[str] = []
        self.restarted: list[int] = []
        self.removed: list[int] = []

    async def available(self):
        return True, "ok"

    async def ensure(self, team_id):
        self.bots.setdefault(team_id, "")

    async def restart(self, team_id):
        self.restarted.append(team_id)

    async def remove(self, team_id):
        self.removed.append(team_id)
        self.bots.pop(team_id, None)

    async def write_bot(self, team_id, code):
        self.bots[team_id] = code

    async def read_bot(self, team_id):
        return self.bots.get(team_id)

    async def read_file(self, team_id, path):
        return f"contents of {path}"

    async def run_command(self, team_id, command):
        self.commands.append(command)
        return 0, "ran"

    async def check_bot(self, team_id):
        return self.verdict


SEALED = "sealed-thoughts"


def tool_call(name, **args):
    return ResponseFunctionToolCall(
        type="function_call", id=f"fc-{name}", call_id=f"call-{name}", name=name,
        arguments=json.dumps(args), **{"async": False},
    )


def reply(content="", calls=None, reason=None, reasoning=0):
    output = [ResponseReasoningItem(id="rs-1", type="reasoning", summary=[], encrypted_content=SEALED)]
    if content:
        output.append(ResponseOutputMessage(
            id="msg-1", type="message", role="assistant", status="completed",
            content=[ResponseOutputText(type="output_text", text=content, annotations=[])],
        ))
    return Response.model_construct(
        output=[*output, *(calls or [])], error=None,
        status="incomplete" if reason else "completed",
        incomplete_details=IncompleteDetails(reason=reason) if reason else None,
        usage=NS(output_tokens_details=NS(reasoning_tokens=reasoning)),
    )


class FakeResponses:
    def __init__(self, script=()):
        self.script = list(script)
        self.requests: list[dict] = []

    async def create(self, **request):
        self.requests.append({**request, "input": list(request["input"])})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if request.get("stream"):
            async def events():
                yield NS(type="response.created")
                for piece in item:
                    yield NS(type="response.output_text.delta", delta=piece) if isinstance(piece, str) else piece
            return events()
        return item


def fake_client(script=()):
    return NS(responses=FakeResponses(script))


class EventCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        self.docker = FakeDocker()
        self.client = fake_client()
        self.event = Event(self.db, Hub(), self.docker, self.client)
        self.team = await self.event.add_team("Thunder", "FOX")
        self.team_id = self.team["id"]

    async def finish_background(self):
        while self.event._tasks:
            await asyncio.gather(*list(self.event._tasks), return_exceptions=True)


class RoundTests(EventCase):
    async def test_prompts_are_only_accepted_while_a_round_is_open(self):
        with self.assertRaises(RoundClosed):
            self.event.save_prompt(self.team_id, "too early")
        await self.event.open_round()
        self.event.save_prompt(self.team_id, "jump a lot")
        self.assertEqual(self.db.prompt(self.team_id, 1), "jump a lot")
        await self.event.close_round()
        with self.assertRaises(RoundClosed):
            self.event.save_prompt(self.team_id, "one more thing")
        self.assertEqual(self.db.prompt(self.team_id, 1), "jump a lot")

    async def test_prompt_is_rejected_at_the_deadline_even_before_the_timer_fires(self):
        await self.event.open_round()
        self.db.set_event("prompting", 1, time.time() - 0.01)
        with self.assertRaises(RoundClosed):
            self.event.save_prompt(self.team_id, "late")

    async def test_timer_closes_the_round(self):
        await self.event.open_round()
        self.db.set_event("prompting", 1, time.time() - 1)
        ticker = asyncio.create_task(self.event.tick_forever())
        await asyncio.sleep(0.05)
        ticker.cancel()
        await self.finish_background()
        self.assertEqual(self.db.event()["phase"], "ready")

    async def test_team_state_shows_when_the_round_is_closed(self):
        await self.event.open_round()
        self.assertTrue(self.event.team_state(self.team)["prompt_open"])
        await self.event.close_round()
        await self.finish_background()
        state = self.event.team_state(self.team)
        self.assertFalse(state["prompt_open"])
        self.assertEqual(state["phase"], "ready")

    async def test_add_time_extends_the_deadline(self):
        await self.event.open_round()
        before = self.db.event()["deadline"]
        await self.event.add_time(60)
        self.assertAlmostEqual(self.db.event()["deadline"] - before, 60, delta=1)

    async def test_restart_during_coding_recovers_to_ready(self):
        await self.event.open_round()
        self.db.set_event("coding", 1, None)
        self.db.set_run(self.team_id, 1, "running", step="x")
        await self.event.startup()
        self.assertEqual(self.db.event()["phase"], "ready")
        self.assertEqual(self.db.run(self.team_id, 1)["status"], "failed")

    async def test_match_needs_two_to_four_different_teams(self):
        for team_ids in ([self.team_id], [self.team_id, self.team_id], [1, 2, 3, 4, 5]):
            with self.assertRaises(EventError):
                await self.event.start_match(team_ids)

    async def test_new_teams_get_a_starter_bot(self):
        self.assertIn("class Bot", self.docker.bots[self.team_id])
        self.assertNotIn("get_character", self.docker.bots[self.team_id])
        self.assertEqual(self.db.latest_version(self.team_id)["round"], 0)

    async def test_team_names_never_reach_docker_names(self):
        team = await self.event.add_team("x; rm -rf /", "DK")
        self.assertIn(team["id"], self.docker.bots)


class CharacterTests(EventCase):
    async def test_a_new_team_needs_a_real_character(self):
        for character in ("", "NANA", "SANDBAG", "NOT_A_CHARACTER"):
            with self.subTest(character=character), self.assertRaises(EventError):
                await self.event.add_team("New", character)
        self.assertEqual(len(self.db.teams()), 1)

    async def test_two_teams_cannot_share_a_character(self):
        with self.assertRaises(EventError) as error:
            await self.event.add_team("Copycat", "FOX")
        self.assertIn("Thunder", str(error.exception))
        second = await self.event.add_team("Second", "DK")
        with self.assertRaises(EventError):
            await self.event.set_character(second["id"], "FOX")
        self.assertEqual(self.db.team(second["id"])["character"], "DK")

    async def test_the_database_itself_refuses_duplicates(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.add_team("Sneaky", "FOX")

    async def test_the_host_can_change_to_a_free_character(self):
        await self.event.set_character(self.team_id, "MARTH")
        self.assertEqual(self.db.team(self.team_id)["character"], "MARTH")
        await self.event.set_character(self.team_id, "MARTH")

    async def test_character_cannot_change_while_agents_are_running(self):
        await self.event.open_round()
        self.db.set_event("coding", 1, None)
        with self.assertRaises(EventError):
            await self.event.set_character(self.team_id, "MARTH")

    async def test_team_page_shows_the_character(self):
        await self.event.set_character(self.team_id, "CPTFALCON")
        self.assertEqual(self.event.team_state(self.db.team(self.team_id))["team"]["character"], "Captain Falcon")

    async def test_agent_is_told_the_character_is_fixed(self):
        self.client.responses.script = [reply("done")]
        await self.event.set_character(self.team_id, "MARTH")
        await self.event.open_round()
        self.event.save_prompt(self.team_id, "play as Fox")
        await self.event.close_round()
        await self.finish_background()
        system = self.client.responses.requests[0]["instructions"]
        self.assertIn("This team fights as Marth", system)
        self.assertIn("no code can change it", system)

    async def test_agent_rules_say_a_bot_cannot_choose(self):
        rules = (Path(__file__).resolve().parents[1] / "server" / "chat_context" / "rules.md").read_text()
        self.assertIn("cannot choose or change its character", rules)
        self.assertNotIn("def get_character", rules)

    async def test_older_teams_must_get_a_character_before_playing(self):
        old = self.db.add_team("Old", None)
        with self.assertRaises(EventError) as error:
            await self.event.open_round()
        self.assertIn("Old", str(error.exception))
        with self.assertRaises(EventError):
            await self.event.start_match([self.team_id, old["id"]])
        await self.event.set_character(old["id"], "DK")
        await self.event.open_round()
        self.assertEqual(self.db.event()["phase"], "prompting")


class AgentTests(EventCase):
    async def run_round(self, prompt="be brave"):
        await self.event.open_round()
        self.event.save_prompt(self.team_id, prompt)
        await self.event.close_round()
        await self.finish_background()
        return self.db.run(self.team_id, self.db.event()["round"])

    def starter(self):
        return self.db.latest_version(self.team_id)["code"]

    async def test_agent_edits_are_saved_as_a_new_version(self):
        self.client.responses.script = [
            reply(calls=[tool_call("write_bot", content="class Bot: pass  # brave")]),
            reply("Your bot is brave now!"),
        ]
        run = await self.run_round()
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["summary"], "Your bot is brave now!")
        self.assertIn("brave", self.starter())
        self.assertEqual(self.db.event()["phase"], "ready")

    async def test_code_is_hidden_while_the_agent_is_working(self):
        await self.event.open_round()
        self.db.set_run(self.team_id, 1, "running", step="x")
        self.assertIsNone(self.event.team_state(self.team)["code"])
        self.db.set_run(self.team_id, 1, "done", summary="done")
        self.assertIsNotNone(self.event.team_state(self.team)["code"])

    async def test_empty_prompt_skips_the_agent(self):
        run = await self.run_round(prompt="   ")
        self.assertEqual(run["status"], "skipped")
        self.assertEqual(self.client.responses.requests, [])

    async def test_broken_bot_is_repaired_or_restored(self):
        original = self.starter()
        self.docker.verdict = {"ok": False, "error": "SyntaxError"}
        self.client.responses.script = [
            reply(calls=[tool_call("write_bot", content="broken(")]),
            reply("done"), reply("still done"), reply("still done"),
        ]
        run = await self.run_round()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(self.docker.bots[self.team_id], original)
        self.assertEqual(self.starter(), original)
        self.assertIn("SyntaxError", json.dumps(self.client.responses.requests[-1]["input"]))

    async def test_llm_failure_keeps_the_previous_bot(self):
        original = self.starter()
        self.client.responses.script = [RuntimeError("azure is down")]
        run = await self.run_round()
        self.assertEqual(run["status"], "failed")
        self.assertEqual(self.starter(), original)
        self.assertIn("azure is down", run["detail"])
        self.assertNotIn("azure", run["error"])

    async def test_content_filter_gets_a_friendly_message(self):
        self.client.responses.script = [RuntimeError("content_filter triggered")]
        run = await self.run_round()
        self.assertIn("safety filter", run["error"])

    async def test_filtered_reply_keeps_the_bot_and_tells_the_kids(self):
        original = self.starter()
        self.client.responses.script = [reply(reason="content_filter")]
        run = await self.run_round()
        self.assertEqual(run["status"], "failed")
        self.assertIn("safety filter", run["error"])
        self.assertEqual(self.starter(), original)

    async def test_earlier_prompts_and_crashes_reach_the_next_round(self):
        self.client.responses.script = [reply("ok"), reply("ok")]
        await self.run_round(prompt="first idea")
        match = self.db.add_match(1, [self.team_id], "BATTLEFIELD")
        self.db.finish_match(match, "finished", {
            "bots": {str(self.team_id): {"exceptions": 3, "missed_frames": 0, "last_error": "KeyError: 2"}},
        }, "")
        await self.run_round(prompt="second idea")
        first_message = self.client.responses.requests[-1]["input"][0]["content"]
        self.assertIn("first idea", first_message)
        self.assertIn("second idea", first_message)
        self.assertIn("KeyError: 2", first_message)

    async def test_unknown_tool_is_reported_to_the_model(self):
        self.client.responses.script = [
            reply(calls=[tool_call("delete_everything")]), reply("ok"),
        ]
        await self.run_round()
        tool_message = self.client.responses.requests[1]["input"][-1]
        self.assertIn("unknown tool", tool_message["output"])

    async def test_reasoning_travels_back_with_each_tool_call(self):
        self.client.responses.script = [
            reply(calls=[tool_call("check_bot")]), reply("ok"),
        ]
        await self.run_round()
        first, second = self.client.responses.requests
        self.assertIs(first["store"], False)
        self.assertEqual(first["include"], ["reasoning.encrypted_content"])
        self.assertEqual(
            [item.get("type") or item["role"] for item in second["input"]],
            ["user", "reasoning", "function_call", "function_call_output"],
        )
        self.assertEqual(second["input"][1]["encrypted_content"], SEALED)
        self.assertEqual(second["input"][3]["call_id"], "call-check_bot")

    async def test_replayed_calls_use_the_api_field_names(self):
        self.client.responses.script = [reply(calls=[tool_call("check_bot")]), reply("ok")]
        await self.run_round()
        call = self.client.responses.requests[1]["input"][2]
        self.assertIn("async", call)
        self.assertNotIn("async_", call)

    async def test_tools_are_strict_and_list_every_field(self):
        self.client.responses.script = [reply("ok")]
        await self.run_round()
        for tool in self.client.responses.requests[0]["tools"]:
            with self.subTest(tool=tool["name"]):
                self.assertNotIn("function", tool)
                self.assertTrue(tool["strict"])
                schema = tool["parameters"]
                self.assertIs(schema["additionalProperties"], False)
                self.assertEqual(sorted(schema["required"]), sorted(schema["properties"]))

    async def test_a_cut_off_reply_keeps_the_bot_and_tells_the_host(self):
        original = self.starter()
        self.client.responses.script = [reply(reason="max_output_tokens")]
        run = await self.run_round()
        self.assertEqual(run["status"], "failed")
        self.assertIn("max_output_tokens", run["detail"])
        self.assertEqual(self.starter(), original)

    async def test_a_failed_response_keeps_the_bot(self):
        original = self.starter()
        failed = reply()
        failed.status, failed.error = "failed", NS(code="server_error", message="overloaded")
        self.client.responses.script = [failed]
        run = await self.run_round()
        self.assertEqual(run["status"], "failed")
        self.assertIn("overloaded", run["detail"])
        self.assertEqual(self.starter(), original)


class FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_json(self, message):
        self.sent.append(message)


class ActivityTests(EventCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.team_socket, self.admin_socket = FakeSocket(), FakeSocket()
        self.event.hub.add_team(self.team_id, self.team_socket)
        self.event.hub.add_admin(self.admin_socket)

    async def run_round(self, *script):
        self.client.responses.script = list(script)
        await self.event.open_round()
        self.event.save_prompt(self.team_id, "be brave")
        await self.event.close_round()
        await self.finish_background()

    def entries(self, socket):
        return [m["entry"] for m in socket.sent if m["type"] == "agent_activity"]

    async def test_steps_stream_in_order_for_host_and_team(self):
        await self.run_round(
            reply("Let me look.", calls=[tool_call("read_file", path="/docs/RULES.md")]),
            reply(calls=[tool_call("check_bot")]),
            reply("done"),
        )
        labels = [e["label"] for e in self.entries(self.team_socket)]
        self.assertEqual(labels, [
            "Reading your idea", "Reading the event rules", "Testing your bot",
            "Test passed!", "All done! Your bot is ready",
        ])
        ids = [e["id"] for e in self.entries(self.admin_socket)]
        self.assertEqual(ids, sorted(ids))

    async def test_only_the_host_sees_commands_and_model_text(self):
        await self.run_round(
            reply("private model note", calls=[tool_call("run_command", command="echo secret-command")]),
            reply("done"),
        )
        for entry in self.entries(self.team_socket):
            self.assertNotIn("detail", entry)
        team_traffic = json.dumps(self.team_socket.sent)
        self.assertNotIn("secret-command", team_traffic)
        self.assertNotIn("private model note", team_traffic)
        host_traffic = json.dumps(self.entries(self.admin_socket))
        self.assertIn("secret-command", host_traffic)
        self.assertIn("private model note", host_traffic)
        self.assertIn("be brave", host_traffic)

    async def test_failure_is_shown_with_a_friendly_message(self):
        await self.run_round(RuntimeError("azure is down"))
        team_last = self.entries(self.team_socket)[-1]
        self.assertEqual(team_last["tone"], "bad")
        self.assertNotIn("azure", team_last["label"])
        self.assertIn("azure is down", self.entries(self.admin_socket)[-1]["detail"])

    async def test_a_new_page_gets_the_history_without_details(self):
        await self.run_round(reply(calls=[tool_call("run_command", command="echo hi")]), reply("done"))
        history = self.event.team_activity(self.team_id)
        self.assertIn("Trying things out", [e["label"] for e in history])
        self.assertTrue(all("detail" not in e and e["label"] for e in history))
        self.assertTrue(any("echo hi" in (e["detail"] or "") for e in self.event.admin_activity()))

    async def test_history_shows_only_the_latest_round_for_each_team(self):
        await self.run_round(reply("one"))
        await self.run_round(reply("two"))
        self.assertEqual({e["round"] for e in self.db.latest_activity(self.team_id)}, {2})

    async def test_long_details_are_trimmed(self):
        await self.run_round(reply(calls=[tool_call("write_bot", content="x" * 5000)]), reply("done"))
        self.assertTrue(all(len(e["detail"] or "") <= 1500 for e in self.entries(self.admin_socket)))


SESSION = "11111111-1111-4111-8111-111111111111"
OTHER_SESSION = "22222222-2222-4222-8222-222222222222"


class ChatTests(EventCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.event.open_round()

    async def ask(self, text="Can we see other players?", session=SESSION):
        self.event.admit_question(session, text)
        await self.finish_background()

    async def test_answers_with_the_docs_and_saves_the_conversation(self):
        self.client.responses.script = [["Yes, ", "all of them!"]]
        await self.ask()
        history = self.db.chat_history(SESSION)
        self.assertEqual([m["role"] for m in history], ["user", "assistant"])
        self.assertEqual(history[1]["content"], "Yes, all of them!")
        system = self.client.responses.requests[0]["instructions"]
        self.assertIn("gamestate.distance", system)
        self.assertIn("PlayerState", system)

    async def test_chat_has_no_tools(self):
        self.client.responses.script = [["ok"]]
        await self.ask()
        self.assertNotIn("tools", self.client.responses.requests[0])

    async def test_each_phone_has_its_own_private_conversation(self):
        self.client.responses.script = [["for one"], ["for two"]]
        await self.ask("first kid", SESSION)
        await self.ask("second kid", OTHER_SESSION)
        second_request = self.client.responses.requests[1]["input"]
        self.assertNotIn("first kid", json.dumps(second_request))
        self.assertEqual(len(self.db.chat_history(OTHER_SESSION)), 2)

    async def test_several_phones_can_ask_at_the_same_time(self):
        self.client.responses.script = [["a"], ["b"]]
        self.event.admit_question(SESSION, "one")
        self.event.admit_question(OTHER_SESSION, "two")
        await self.finish_background()
        self.assertEqual(self.db.chat_answers_total(1), 2)

    async def test_chat_stays_open_after_the_round_closes(self):
        await self.event.close_round()
        await self.finish_background()
        self.client.responses.script = [["still here"]]
        await self.ask()
        self.assertEqual(self.db.chat_history(SESSION)[-1]["content"], "still here")

    async def test_chat_text_never_reaches_the_agent(self):
        self.client.responses.script = [["sure"], reply("done")]
        await self.ask("secret chat words")
        self.event.save_prompt(self.team_id, "real prompt")
        await self.event.close_round()
        await self.finish_background()
        self.assertNotIn("secret chat words", json.dumps(self.client.responses.requests[1]["input"]))

    async def test_question_length_is_limited(self):
        with self.assertRaises(ChatRejected):
            self.event.admit_question(SESSION, "x" * 501)

    async def test_there_is_no_limit_on_how_many_questions_are_asked(self):
        self.client.responses.script = [["ok"] for _ in range(40)]
        for number in range(40):
            await self.ask(f"question {number}")
        self.assertEqual(self.db.chat_answers_total(1), 40)

    async def test_one_question_at_a_time_per_phone(self):
        self.client.responses.script = [["a"]]
        self.event.admit_question(SESSION, "first")
        with self.assertRaises(ChatRejected):
            self.event.admit_question(SESSION, "second")
        await self.finish_background()

    async def test_a_failed_answer_frees_the_phone_for_another_question(self):
        self.client.responses.script = [RuntimeError("down")]
        await self.ask()
        self.assertFalse(self.event.chat.busy)

    async def test_a_filtered_stream_gets_a_friendly_message(self):
        socket = FakeSocket()
        self.event.hub.add_chat(SESSION, socket)
        cut_off = NS(type="response.incomplete", response=reply(reason="content_filter"))
        self.client.responses.script = [["Well, ", cut_off]]
        await self.ask()
        self.assertIn("safety filter", [m for m in socket.sent if m["type"] == "chat_error"][0]["message"])

    async def test_a_failed_stream_is_not_saved_as_an_answer(self):
        self.client.responses.script = [["half an ", NS(type="error", code="server_error", message="boom")]]
        await self.ask()
        self.assertEqual([m["role"] for m in self.db.chat_history(SESSION)], ["user"])
        self.assertFalse(self.event.chat.busy)

    async def test_closed_when_disabled(self):
        self.db.update_settings({"chat_enabled": False})
        with self.assertRaises(ChatRejected):
            self.event.admit_question(SESSION, "hello")


class StartChoiceTests(EventCase):
    async def round_with(self, prompt, mode=None, calls=None):
        self.client.responses.script = [reply(calls=calls), reply("done")] if calls else [reply("done")]
        await self.event.open_round()
        if mode:
            self.event.set_start_mode(self.team_id, mode)
        self.event.save_prompt(self.team_id, prompt)
        await self.event.close_round()
        await self.finish_background()

    async def custom_round_one(self):
        await self.round_with("be sneaky", calls=[tool_call("write_bot", content="class Bot: pass  # sneaky")])

    def latest(self):
        return self.db.latest_version(self.team_id)["code"]

    def starter(self):
        return self.db.starter_version(self.team_id)["code"]

    async def test_not_asked_in_the_first_round(self):
        await self.event.open_round()
        self.assertFalse(self.event.team_state(self.team)["start"]["asked"])
        with self.assertRaises(RoundClosed):
            self.event.set_start_mode(self.team_id, "fresh")

    async def test_asked_when_a_later_round_opens(self):
        await self.custom_round_one()
        await self.event.open_round()
        start = self.event.team_state(self.team)["start"]
        self.assertTrue(start["asked"])
        self.assertIsNone(start["mode"])

    async def test_not_asked_when_the_bot_is_still_the_starter(self):
        await self.round_with("   ")
        await self.event.open_round()
        self.assertFalse(self.event.team_state(self.team)["start"]["asked"])

    async def test_keep_builds_on_the_existing_bot(self):
        await self.custom_round_one()
        await self.round_with("add jumping", "keep")
        message = self.client.responses.requests[-1]["input"][0]["content"]
        self.assertIn("sneaky", message)
        self.assertIn("be sneaky", message)
        self.assertNotIn("clean start", message)

    async def test_no_answer_means_keep(self):
        await self.custom_round_one()
        await self.round_with("add jumping")
        self.assertIn("sneaky", self.docker.bots[self.team_id])

    async def test_clean_start_wipes_the_code_and_the_history(self):
        await self.custom_round_one()
        await self.round_with("be a turtle", "fresh")
        message = self.client.responses.requests[-1]["input"][0]["content"]
        self.assertNotIn("sneaky", message)
        self.assertIn("clean start", message)
        self.assertEqual(self.docker.bots[self.team_id], self.starter())
        self.assertEqual(self.latest(), self.starter())

    async def test_nothing_is_wiped_until_the_round_closes(self):
        await self.custom_round_one()
        self.client.responses.script = [reply("done")]
        await self.event.open_round()
        self.event.set_start_mode(self.team_id, "fresh")
        self.assertIn("sneaky", self.latest())
        self.event.set_start_mode(self.team_id, "keep")
        self.event.save_prompt(self.team_id, "go")
        await self.event.close_round()
        await self.finish_background()
        self.assertIn("sneaky", self.latest())

    async def test_clean_start_without_a_prompt_returns_to_the_starter(self):
        await self.custom_round_one()
        await self.round_with("   ", "fresh")
        self.assertEqual(self.latest(), self.starter())
        self.assertEqual(self.db.run(self.team_id, 2)["status"], "skipped")

    async def test_failed_clean_start_falls_back_to_the_last_bot(self):
        await self.custom_round_one()
        self.client.responses.script = [RuntimeError("down")]
        await self.event.open_round()
        self.event.set_start_mode(self.team_id, "fresh")
        self.event.save_prompt(self.team_id, "new idea")
        await self.event.close_round()
        await self.finish_background()
        self.assertEqual(self.db.run(self.team_id, 2)["status"], "failed")
        self.assertIn("sneaky", self.docker.bots[self.team_id])

    async def test_prompts_before_a_clean_start_are_forgotten_later(self):
        await self.custom_round_one()
        await self.round_with("be a turtle", "fresh", calls=[tool_call("write_bot", content="class Bot: pass  # turtle")])
        await self.round_with("add spinning", "keep")
        message = self.client.responses.requests[-1]["input"][0]["content"]
        self.assertNotIn("be sneaky", message)
        self.assertIn("be a turtle", message)

    async def test_choice_is_locked_after_the_round_closes(self):
        await self.custom_round_one()
        await self.event.open_round()
        await self.event.close_round()
        await self.finish_background()
        with self.assertRaises(RoundClosed):
            self.event.set_start_mode(self.team_id, "fresh")

    async def test_old_code_stays_in_the_history_for_the_host(self):
        await self.custom_round_one()
        await self.round_with("be a turtle", "fresh")
        self.assertTrue(any("sneaky" in v["code"] for v in self.db.versions(self.team_id)))


class FakeMatchRunner:
    def __init__(self):
        self.commands: list[list[str]] = []
        self.gate: asyncio.Event | None = None

    async def run(self, command, on_line):
        self.commands.append(command)
        if self.gate is not None:
            await self.gate.wait()
        await on_line("Arena finished after one match.")
        return 0


class StageTests(EventCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.other = await self.event.add_team("Lightning", "MARTH")
        self.both = [self.team_id, self.other["id"]]
        self.event.match_runner = FakeMatchRunner()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        iso = Path(directory.name) / "game.iso"
        iso.touch()
        self.db.update_settings({"iso_path": str(iso)})
        match_dir = patch("server.event.MATCH_DIR", Path(directory.name) / "matches")
        match_dir.start()
        self.addCleanup(match_dir.stop)

    async def play_round(self, seconds=90, stage="DREAMLAND"):
        await self.event.open_round(seconds, stage)
        await self.event.close_round()
        await self.finish_background()

    def runner_stage(self):
        command = self.event.match_runner.commands[-1]
        return command[command.index("--stage") + 1]

    async def test_the_host_picks_the_time_and_stage_for_each_round(self):
        await self.event.open_round(90, "FINAL_DESTINATION")
        self.assertEqual(self.db.event()["stage"], "FINAL_DESTINATION")
        self.assertAlmostEqual(self.db.event()["deadline"] - time.time(), 90, delta=2)
        await self.event.close_round()
        await self.finish_background()
        await self.event.open_round(600, "YOSHIS_STORY")
        self.assertEqual(self.db.event()["stage"], "YOSHIS_STORY")
        self.assertAlmostEqual(self.db.event()["deadline"] - time.time(), 600, delta=2)

    async def test_left_out_choices_are_the_same_as_last_round(self):
        await self.play_round(90, "POKEMON_STADIUM")
        await self.event.open_round()
        self.assertEqual(self.db.event()["stage"], "POKEMON_STADIUM")
        self.assertAlmostEqual(self.db.event()["deadline"] - time.time(), 90, delta=2)

    async def test_an_unknown_stage_or_time_opens_nothing(self):
        for seconds, stage in ((90, "MUTE_CITY"), (90, "NO_STAGE"), (90, "RANDOM_STAGE"),
                               (5, "BATTLEFIELD"), (99999, "BATTLEFIELD")):
            with self.subTest(seconds=seconds, stage=stage), self.assertRaises(EventError):
                await self.event.open_round(seconds, stage)
        self.assertEqual(self.db.event(), {"phase": "setup", "round": 0, "deadline": None, "stage": None})

    async def test_kids_see_the_stage_once_the_round_opens(self):
        self.assertIsNone(self.event.team_state(self.team)["stage"])
        self.assertIsNone(self.event.chat_state(SESSION)["stage"])
        await self.event.open_round(90, "POKEMON_STADIUM")
        for state in (self.event.team_state(self.team), self.event.chat_state(SESSION)):
            self.assertEqual(state["stage"]["name"], "Pokémon Stadium")
            self.assertEqual(state["stage"]["image"], "/stages/POKEMON_STADIUM.svg")
        self.assertIn("FINAL_DESTINATION", self.event.admin_state()["stages"])

    async def test_the_agent_and_the_helper_are_told_the_stage(self):
        await self.event.open_round(90, "FOUNTAIN_OF_DREAMS")
        self.event.save_prompt(self.team_id, "jump on the platforms")
        self.client.responses.script = [reply("ok"), ["Watch the moving platforms!"]]
        await self.event.close_round()
        await self.finish_background()
        self.assertIn("melee.Stage.FOUNTAIN_OF_DREAMS", self.client.responses.requests[0]["input"][0]["content"])
        self.event.admit_question(SESSION, "Which stage is it?")
        await self.finish_background()
        self.assertIn("Fountain of Dreams", self.client.responses.requests[1]["instructions"])

    async def test_a_match_is_played_on_its_rounds_stage(self):
        await self.play_round(stage="DREAMLAND")
        await self.event.start_match(self.both, "BATTLEFIELD")
        await self.finish_background()
        self.assertEqual(self.runner_stage(), "DREAMLAND")
        self.assertEqual(self.db.matches()[0]["stage"], "DREAMLAND")
        self.assertEqual(self.event.admin_state()["matches"][0]["stage"], "Dream Land")
        self.assertEqual(sorted(self.docker.restarted), sorted(self.both))

    async def test_a_test_match_before_round_one_uses_the_picked_stage(self):
        await self.event.start_match(self.both, "YOSHIS_STORY")
        await self.finish_background()
        self.assertEqual(self.runner_stage(), "YOSHIS_STORY")
        with self.assertRaises(EventError):
            await self.event.start_match(self.both, "MUTE_CITY")

    async def test_a_demo_has_no_bots_and_leaves_no_record(self):
        await self.play_round(stage="DREAMLAND")
        self.event.match_runner.gate = asyncio.Event()
        await self.event.start_match(self.both, "FINAL_DESTINATION", demo=True)
        self.assertTrue(self.event.team_state(self.team)["demo"])
        self.assertTrue(self.event.chat_state(SESSION)["demo"])
        self.event.match_runner.gate.set()
        await self.finish_background()
        command = self.event.match_runner.commands[-1]
        self.assertEqual(command[2:4], ["FOX", "MARTH"])
        self.assertEqual(self.runner_stage(), "FINAL_DESTINATION")
        self.assertIn("--no-record", command)
        self.assertEqual(self.docker.restarted, [])
        self.assertEqual(self.db.matches(), [])
        self.assertFalse(self.event.admin_state()["demo"])
        self.assertEqual(self.db.event()["phase"], "ready")
        self.assertEqual(self.db.event()["stage"], "DREAMLAND")

    def add_match(self, round_number, status, winner, team_ids=None):
        match = self.db.add_match(round_number, team_ids or self.both, "BATTLEFIELD")
        self.db.finish_match(match, status, {"winner_team": winner, "players": {}, "bots": {}}, "")

    async def test_only_matches_that_end_by_themselves_in_a_round_count(self):
        self.add_match(0, "finished", self.team_id)           # test match before round 1
        self.add_match(1, "finished", self.other["id"])
        self.add_match(1, "stopped", self.team_id)            # the host stopped it
        self.add_match(2, "failed", self.team_id)
        self.add_match(2, "finished", self.other["id"])
        self.add_match(2, "finished", None)                   # a tie
        table = self.event.scores()["table"]
        self.assertEqual([(row["name"], row["wins"], row["played"]) for row in table],
                         [("Lightning", 2, 3), ("Thunder", 0, 3)])
        self.assertEqual(table[0]["character"], "Marth")

    async def test_kids_see_who_won_the_last_match(self):
        self.assertEqual(self.event.team_state(self.team)["scores"]["last"], None)
        self.add_match(1, "finished", self.team_id)
        mine = self.event.team_state(self.team)["scores"]["last"]
        self.assertEqual((mine["winner"], mine["won"], mine["stage"]), ("Thunder", True, "Battlefield"))
        theirs = self.event.team_state(self.db.team(self.other["id"]))["scores"]["last"]
        self.assertFalse(theirs["won"])
        self.assertEqual(self.event.chat_state(SESSION)["scores"]["last"]["winner"], "Thunder")
        self.add_match(1, "stopped", self.other["id"])
        self.assertEqual(self.event.team_state(self.team)["scores"]["last"]["winner"], "Thunder")

    async def test_a_stopped_match_shows_no_winner_to_the_host(self):
        self.add_match(1, "stopped", self.team_id)
        self.add_match(1, "finished", self.other["id"])
        winners = [match["winner"] for match in self.event.admin_state()["matches"]]
        self.assertEqual(winners, ["Lightning", None])

    async def test_a_match_with_no_time_limit_is_started_without_one(self):
        await self.play_round()
        await self.event.start_match(self.both)
        await self.finish_background()
        command = self.event.match_runner.commands[-1]
        self.assertEqual(command[command.index("--time-limit") + 1], "0")
        self.assertEqual(self.db.matches()[0]["status"], "finished")

    async def test_reset_forgets_the_stage(self):
        await self.play_round()
        await self.event.reset()
        self.assertIsNone(self.db.event()["stage"])


class StageMapTests(unittest.TestCase):
    def test_every_stage_can_be_played_and_has_a_map(self):
        for stage in STAGES:
            with self.subTest(stage=stage):
                self.assertIs(runner.stage_choice(stage), melee.Stage[stage])
                self.assertTrue(ElementTree.fromstring(SVGS[stage]).tag.endswith("svg"))

    def test_maps_use_the_games_own_numbers(self):
        for label in (">x=-68<", ">x=68<", ">y=27<", ">y=54<"):
            self.assertIn(label, SVGS["BATTLEFIELD"])
        self.assertNotIn(">y=", SVGS["FINAL_DESTINATION"])
        self.assertIn(">moves<", SVGS["FOUNTAIN_OF_DREAMS"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.db = _thread_safe_memory_db()
        self.event = Event(self.db, Hub(), FakeDocker(), fake_client())
        self.team = self.db.add_team("Thunder", "FOX")
        self.db.add_version(self.team["id"], 0, "class Bot: pass", "Starter bot")
        self.teams = TestClient(create_team_app(self.event))
        self.admin = TestClient(create_admin_app(self.event, port=8001), base_url="http://127.0.0.1:8001")

    def test_team_page_needs_a_real_token(self):
        self.assertEqual(self.teams.get(f"/t/{self.team['token']}").status_code, 200)
        self.assertEqual(self.teams.get("/t/00000000-0000-4000-8000-000000000000").status_code, 404)
        self.assertEqual(self.teams.get("/").status_code, 404)

    def test_team_site_has_no_admin_routes(self):
        for path in ("/api/settings", "/api/round/open", "/admin-static/admin.js", "/docs"):
            self.assertEqual(self.teams.get(path).status_code, 404, path)
            self.assertEqual(self.teams.post(path).status_code in (404, 405), True, path)

    def test_admin_rejects_other_hosts_and_missing_header(self):
        self.assertEqual(self.admin.post("/api/round/open").status_code, 403)
        spoofed = TestClient(create_admin_app(self.event, port=8001), base_url="http://evil.example")
        self.assertEqual(spoofed.get("/").status_code, 403)

    def test_settings_are_validated(self):
        headers = {"X-Smash-Admin": "1"}
        for limit in (-1, 9999):
            self.assertEqual(self.admin.put("/api/settings", json={"time_limit_seconds": limit}, headers=headers).status_code, 422)
        self.assertEqual(self.admin.put("/api/settings", json={"public_url": "ftp://x"}, headers=headers).status_code, 409)
        self.assertEqual(self.admin.put("/api/settings", json={"time_limit_seconds": 90}, headers=headers).status_code, 200)
        self.assertEqual(self.db.settings()["time_limit_seconds"], 90)
        self.assertEqual(self.db.settings()["lives"], 1)
        for lives in (0, 5):
            self.assertEqual(self.admin.put("/api/settings", json={"lives": lives}, headers=headers).status_code, 422)
        self.assertEqual(self.admin.put("/api/settings", json={"lives": 2}, headers=headers).status_code, 200)

    def test_opening_a_round_needs_a_time_and_a_stage(self):
        headers = {"X-Smash-Admin": "1"}
        for body in ({}, {"seconds": 5, "stage": "BATTLEFIELD"}, {"seconds": 120}):
            self.assertEqual(self.admin.post("/api/round/open", json=body, headers=headers).status_code, 422, body)
        refused = self.admin.post("/api/round/open", json={"seconds": 120, "stage": "MUTE_CITY"}, headers=headers)
        self.assertEqual(refused.status_code, 409)
        self.assertEqual(self.db.event()["phase"], "setup")
        opened = self.admin.post("/api/round/open", json={"seconds": 120, "stage": "DREAMLAND"}, headers=headers)
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(self.db.event()["stage"], "DREAMLAND")

    def test_both_sites_serve_the_stage_maps(self):
        for client in (self.teams, self.admin):
            response = client.get("/stages/YOSHIS_STORY.svg")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "image/svg+xml")
            self.assertIn("Map of Yoshi's Story", response.text)
            self.assertEqual(client.get("/stages/NO_STAGE.svg").status_code, 404)
            self.assertEqual(client.get("/stages/..%2Fapp.py.svg").status_code, 404)

    def test_socket_shows_only_this_teams_state(self):
        other = self.db.add_team("Other", "DK")
        self.db.add_version(other["id"], 0, "class Bot: pass  # other team secret", "Starter bot")
        with self.teams.websocket_connect(f"/ws/{self.team['token']}") as socket:
            history = socket.receive_json()
            state = socket.receive_json()
        self.assertEqual(history["type"], "agent_activity_history")
        self.assertEqual(state["team"]["name"], "Thunder")
        self.assertNotIn("other team secret", json.dumps(state))

    def test_unknown_token_socket_is_closed(self):
        with self.teams.websocket_connect("/ws/not-a-token") as socket:
            message = socket.receive()
        self.assertEqual(message["code"], 4404)

    def test_closed_round_rejects_a_prompt_from_an_open_socket(self):
        with self.teams.websocket_connect(f"/ws/{self.team['token']}") as socket:
            socket.receive_json(), socket.receive_json()
            socket.send_json({"type": "prompt", "text": "sneaky", "seq": 1})
            self.assertEqual(socket.receive_json()["type"], "prompt_rejected")
        self.assertEqual(self.db.prompt(self.team["id"], 0), "")

    def chat_token(self):
        return self.db.settings()["chat_token"]

    def test_chat_page_needs_the_event_link(self):
        self.assertEqual(self.teams.get(f"/c/{self.chat_token()}").status_code, 200)
        self.assertEqual(self.teams.get("/c/00000000-0000-4000-8000-000000000000").status_code, 404)

    def test_chat_link_gives_no_access_to_a_team(self):
        self.assertEqual(self.teams.get(f"/t/{self.chat_token()}").status_code, 404)
        with self.teams.websocket_connect(f"/ws/{self.chat_token()}") as socket:
            self.assertEqual(socket.receive()["code"], 4404)

    def test_chat_socket_cannot_save_prompts_and_sees_no_team_data(self):
        self.db.set_event("prompting", 1, time.time() + 60)
        with self.teams.websocket_connect(f"/ws-chat/{self.chat_token()}") as socket:
            session = socket.receive_json()
            socket.receive_json()
            state = socket.receive_json()
            socket.send_json({"type": "prompt", "text": "sneaky", "seq": 1})
        self.assertEqual(session["type"], "session")
        self.assertTrue(state["prompt_open"])
        self.assertEqual(self.db.prompt(self.team["id"], 1), "")
        self.assertNotIn("team", state)
        self.assertNotIn("code", state)
        self.assertNotIn("prompt", state)

    def test_returning_phone_keeps_its_conversation(self):
        self.db.add_chat(SESSION, 1, "user", "my old question")
        with self.teams.websocket_connect(f"/ws-chat/{self.chat_token()}?session={SESSION}") as socket:
            self.assertEqual(socket.receive_json()["id"], SESSION)
            history = socket.receive_json()
        self.assertEqual(history["messages"][0]["content"], "my old question")

    def test_malformed_session_gets_a_new_one(self):
        with self.teams.websocket_connect(f"/ws-chat/{self.chat_token()}?session=abc") as socket:
            self.assertNotEqual(socket.receive_json()["id"], "abc")

    def test_saving_the_base_url_connects_without_a_restart(self):
        headers = {"X-Smash-Admin": "1"}
        self.assertEqual(self.admin.put(
            "/api/settings", json={"azure_base_url": "https://x.openai.azure.com", "azure_api_key": "key"},
            headers=headers,
        ).status_code, 200)
        self.assertEqual(str(self.event.client.base_url), "https://x.openai.azure.com/openai/v1/")
        self.assertEqual(self.event.client.api_key, "key")
        self.assertIs(self.event.agents.client, self.event.client)
        self.assertIs(self.event.chat.client, self.event.client)

    def test_clearing_the_base_url_disconnects(self):
        response = self.admin.put("/api/settings", json={"azure_base_url": ""}, headers={"X-Smash-Admin": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.event.client)

    def test_model_name_is_saved_and_a_bad_url_is_refused(self):
        headers = {"X-Smash-Admin": "1"}
        self.assertEqual(self.admin.put("/api/settings", json={"model": "my-deploy"}, headers=headers).status_code, 200)
        self.assertEqual(self.db.settings()["model"], "my-deploy")
        self.assertEqual(self.admin.put("/api/settings", json={"azure_base_url": "nope"}, headers=headers).status_code, 409)

    def test_adding_teams_over_http_enforces_characters(self):
        headers = {"X-Smash-Admin": "1"}
        self.assertEqual(self.admin.post("/api/teams", json={"name": "NoCharacter"}, headers=headers).status_code, 422)
        taken = self.admin.post("/api/teams", json={"name": "Copy", "character": "FOX"}, headers=headers)
        self.assertEqual(taken.status_code, 409)
        self.assertIn("already taken", taken.json()["detail"])

    def test_saved_key_is_never_sent_to_a_page(self):
        headers = {"X-Smash-Admin": "1"}
        self.admin.put("/api/settings", json={"azure_api_key": "super-secret-key"}, headers=headers)
        self.assertEqual(self.db.settings()["azure_api_key"], "super-secret-key")
        state = self.event.admin_state()
        self.assertNotIn("super-secret-key", json.dumps(state))
        self.assertNotIn("azure_api_key", state["settings"])
        self.assertTrue(state["health"]["key_saved"])
        team_state = self.event.team_state(self.team)
        self.assertNotIn("super-secret-key", json.dumps(team_state))

    def test_forgetting_the_key_disconnects(self):
        headers = {"X-Smash-Admin": "1"}
        self.admin.put("/api/settings", json={"azure_base_url": "https://x.openai.azure.com", "azure_api_key": "k"}, headers=headers)
        self.assertIsNotNone(self.event.client)
        self.admin.put("/api/settings", json={"azure_api_key": ""}, headers=headers)
        self.assertIsNone(self.event.client)
        self.assertFalse(self.event.admin_state()["health"]["key_saved"])

    def test_connection_test_needs_a_url_and_key(self):
        self.db.update_settings({"azure_base_url": "", "azure_api_key": ""})
        response = self.admin.post("/api/llm/test", json={}, headers={"X-Smash-Admin": "1"})
        self.assertEqual(response.status_code, 409)

    def test_connection_test_tries_unsaved_values_without_saving_them(self):
        self.db.update_settings({"azure_base_url": "https://saved.example.com", "azure_api_key": "saved"})
        captured = {}

        async def fake_checks(client, model):
            captured.update(url=str(client.base_url), key=client.api_key, model=model)
            return [{"name": "x", "status": "pass", "detail": "", "ms": 1}]

        with patch("server.app.run_checks", fake_checks):
            response = self.admin.post(
                "/api/llm/test",
                json={"azure_base_url": "https://new.example.com", "azure_api_key": "new", "model": "other"},
                headers={"X-Smash-Admin": "1"},
            )
        self.assertEqual(response.json()["checks"][0]["status"], "pass")
        self.assertEqual(captured, {"url": "https://new.example.com/openai/v1/", "key": "new", "model": "other"})
        self.assertEqual(self.db.settings()["azure_api_key"], "saved")

    def test_security_headers(self):
        response = self.teams.get(f"/t/{self.team['token']}")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])


def _thread_safe_memory_db() -> Database:
    # TestClient runs the app on another thread, so this connection must be shareable.
    real_connect = __import__("sqlite3").connect
    with patch("server.db.sqlite3.connect", lambda *a, **k: real_connect(*a, check_same_thread=False, **k)):
        return Database(":memory:")


class MigrationTests(unittest.TestCase):
    def test_database_file_is_private_to_this_user(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.sqlite3"
            Database(path).conn.close()
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_database_from_before_characters_keeps_its_teams(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.sqlite3"
            old = sqlite3.connect(path)
            old.executescript(
                "CREATE TABLE teams (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,"
                " token TEXT NOT NULL UNIQUE, created_at REAL NOT NULL);"
                "INSERT INTO teams (name, token, created_at) VALUES ('Donald', 'a', 1), ('Pedro', 'b', 2);"
            )
            old.close()
            db = Database(path)
            self.assertEqual([t["name"] for t in db.teams()], ["Donald", "Pedro"])
            self.assertEqual([t["character"] for t in db.teams()], [None, None])
            db.set_character(1, "FOX")
            with self.assertRaises(sqlite3.IntegrityError):
                db.set_character(2, "FOX")
            db.conn.close()
            Database(path).conn.close()

    def test_a_database_from_before_stages_keeps_its_rounds_and_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.sqlite3"
            old = sqlite3.connect(path)
            old.executescript(
                "CREATE TABLE event (id INTEGER PRIMARY KEY CHECK (id = 1), phase TEXT NOT NULL,"
                " round INTEGER NOT NULL, deadline REAL);"
                "INSERT INTO event VALUES (1, 'ready', 2, NULL);"
                "CREATE TABLE matches (id INTEGER PRIMARY KEY AUTOINCREMENT, round INTEGER NOT NULL,"
                " team_ids TEXT NOT NULL, status TEXT NOT NULL, result TEXT, log TEXT NOT NULL DEFAULT '',"
                " started_at REAL NOT NULL, finished_at REAL);"
                "INSERT INTO matches (round, team_ids, status, started_at) VALUES (1, '[1, 2]', 'finished', 1);"
            )
            old.close()
            db = Database(path)
            self.assertEqual(db.event(), {"phase": "ready", "round": 2, "deadline": None, "stage": None})
            self.assertIsNone(db.matches()[0]["stage"])
            db.conn.close()

    def test_an_old_fixed_match_length_no_longer_cuts_matches_short(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.sqlite3"
            Database(path).update_settings({"match_seconds": 120})
            db = Database(path)
            self.assertNotIn("match_seconds", db.settings())
            self.assertEqual(db.settings()["time_limit_seconds"], 0)
            db.conn.close()


class DockerTests(unittest.IsolatedAsyncioTestCase):
    async def ensure(self, container):
        """container is None when missing, else (image id, running)."""
        calls = []

        async def fake_docker(*args, stdin=None, timeout=60.0):
            calls.append(args)
            if args[0] == "inspect":
                if container is None:
                    return 1, b"", b"No such object"
                return 0, f"{container[0]} {container[1]}\n".encode(), b""
            if args[:2] == ("image", "inspect"):
                return 0, b"sha256:new\n", b""
            return 0, b"", b""

        docker = Docker()
        docker._docker = fake_docker
        await docker.ensure(4)
        return calls

    async def test_a_container_from_an_older_image_is_replaced_but_keeps_its_bot(self):
        calls = await self.ensure(("sha256:old", "true"))
        self.assertEqual([call[0] for call in calls], ["inspect", "image", "rm", "run"])
        self.assertEqual(calls[2], ("rm", "-f", "smash-team-4"))
        self.assertIn("smash-team-4-workspace:/workspace", calls[3])

    async def test_a_current_container_is_kept(self):
        self.assertEqual([c[0] for c in await self.ensure(("sha256:new", "true"))], ["inspect", "image"])
        self.assertEqual([c[0] for c in await self.ensure(("sha256:new", "false"))], ["inspect", "image", "start"])
        self.assertEqual([c[0] for c in await self.ensure(None)], ["inspect", "run"])


class ConnectionCheckTests(unittest.IsolatedAsyncioTestCase):
    def client(self, *script):
        client = fake_client(script)
        client.api_key = "very-secret"
        return client

    def healthy(self):
        return [
            reply("OK"),
            ["he", "llo"],
            reply(calls=[tool_call("read_file", path="/docs/RULES.md")]),
            reply(f"The word is {CODE_WORD}"),
            reply("Cal", reasoning=120),
            reply("Rush in and attack."),
        ]

    async def test_a_healthy_deployment_passes_everything(self):
        client = self.client(*self.healthy())
        results = await run_checks(client, "gpt-6-luna")
        self.assertEqual([r["status"] for r in results], ["pass"] * 5)
        self.assertIn("120 reasoning tokens", results[3]["detail"])
        self.assertEqual({r["model"] for r in client.responses.requests}, {"gpt-6-luna"})

    async def test_tool_round_trip_sends_the_result_back(self):
        client = self.client(*self.healthy())
        await run_checks(client, "m")
        follow_up = client.responses.requests[3]["input"]
        self.assertEqual(
            [m.get("type") or m["role"] for m in follow_up],
            ["user", "reasoning", "function_call", "function_call_output"],
        )
        self.assertIn(CODE_WORD, follow_up[3]["output"])
        self.assertEqual(follow_up[2]["name"], "read_file")

    async def test_a_model_that_never_calls_tools_fails_that_check(self):
        script = self.healthy()
        script[2:4] = [reply("I would rather not.")]
        results = await run_checks(self.client(*script), "m")
        self.assertEqual(results[2]["status"], "fail")
        self.assertEqual(results[3]["status"], "pass")

    async def test_no_reasoning_tokens_is_a_warning(self):
        script = self.healthy()
        script[4] = reply("Cal", reasoning=0)
        results = await run_checks(self.client(*script), "m")
        self.assertEqual(results[3]["status"], "warn")

    async def test_filtered_game_words_are_a_warning(self):
        script = self.healthy()
        script[5] = reply(reason="content_filter")
        results = await run_checks(self.client(*script), "m")
        self.assertEqual(results[4]["status"], "warn")
        self.assertIn("safety filter", results[4]["detail"])

    async def test_a_failed_connection_skips_the_rest_and_hides_the_key(self):
        results = await run_checks(self.client(RuntimeError("bad key very-secret")), "m")
        self.assertEqual([r["status"] for r in results], ["fail", "skipped", "skipped", "skipped", "skipped"])
        self.assertNotIn("very-secret", results[0]["detail"])


class SmallHelpers(unittest.TestCase):
    def test_runner_command_lists_containers_in_player_order(self):
        settings = {"time_limit_seconds": 0, "lives": 1, "iso_path": "/game.iso", "record_replays": False}
        teams = [{"id": 3, "character": "FOX"}, {"id": 7, "character": "MARTH"}]
        command = runner_command(teams, settings, Path("/result.json"), stage="DREAMLAND")
        self.assertEqual(command[command.index("--time-limit") + 1], "0")
        self.assertEqual(command[command.index("--lives") + 1], "1")
        self.assertEqual(command[2:4], ["smash-team-3=FOX", "smash-team-7=MARTH"])
        self.assertEqual(command[command.index("--stage") + 1], "DREAMLAND")
        self.assertIn("--no-record", command)

    def test_a_demo_starts_no_bots_and_is_not_recorded(self):
        settings = {"time_limit_seconds": 0, "lives": 1, "iso_path": "/game.iso", "record_replays": True}
        teams = [{"id": 3, "character": "FOX"}, {"id": 7, "character": "MARTH"}]
        command = runner_command(teams, settings, None, stage="BATTLEFIELD", demo=True)
        self.assertEqual(command[2:4], ["FOX", "MARTH"])
        self.assertNotIn("smash-team", " ".join(command))
        self.assertNotIn("--result-file", command)
        self.assertIn("--no-record", command)

    def test_client_needs_both_the_url_and_the_key(self):
        self.assertIsNone(azure_client("https://x.openai.azure.com", ""))
        self.assertIsNone(azure_client("  ", "key"))
        self.assertIsNotNone(azure_client("https://x.openai.azure.com", "key"))

    def test_azure_base_url(self):
        for endpoint in ("https://x.openai.azure.com", "https://x.openai.azure.com/", "https://x.openai.azure.com/openai/v1/"):
            self.assertEqual(azure_base_url(endpoint), "https://x.openai.azure.com/openai/v1/")


if __name__ == "__main__":
    unittest.main()
