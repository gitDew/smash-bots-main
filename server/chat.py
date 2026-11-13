"""The docs helper chat, open to anyone with the event's chat QR code.

It has no tools: it can only return text. Each phone gets its own private
conversation, and every conversation shares the same fixed reference text and
the round's stage, so this code has no way to show anyone a team's prompts or
bots. Chat text never reaches the coding agent.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from server.db import Database, ROOT
from server.hub import Hub
from server.llm import check_finished, is_content_filter
from server.stages import STAGES


MAX_QUESTION_CHARS = 500
HISTORY_MESSAGES = 10
MAX_OUTPUT_TOKENS = 4000
MAX_CONCURRENT_ANSWERS = 8
DOC_PAGES = ("gamestate", "controller", "enums", "framedata", "stages")

INSTRUCTIONS = """You are the friendly helper at a kids' coding event. The kids prompt an AI to write Super Smash Bros. Melee bots in Python using libmelee, and you answer their questions about what a bot can see and do. Each kid is asking from their own phone, and one kid on each team writes the team's prompt.

- Keep answers short: usually a few sentences, in simple words a ten-year-old can follow.
- The event is in German. Always answer in simple German, even if a question or earlier message is in another language. Keep libmelee field names unchanged.
- Answer from the reference below. If it does not say, tell them you are not sure rather than guessing.
- Explain in words. Name a libmelee field if it helps, but never write a whole bot.
- You cannot change anyone's bot. If they ask you to, tell them to give the idea to the teammate who writes the prompt.
- You know nothing about the teams or their bots.
- Kindly steer off-topic questions back to the game. Keep everything friendly and suitable for children.
- The event rules override the libmelee documentation whenever they disagree."""


def reference_text(root: Path = ROOT) -> str:
    context = root / "server" / "chat_context"
    sections = [
        ("EVENT RULES", context / "rules.md"),
        ("CHEAT SHEET SHOWN TO THE KIDS", context / "cheatsheet.md"),
        *((f"LIBMELEE: {page}", root / "docs" / "libmelee" / f"{page}.md") for page in DOC_PAGES),
    ]
    return "\n\n".join(f"===== {title} =====\n{path.read_text()}" for title, path in sections)


class ChatRejected(Exception):
    """A question that cannot be asked right now, with a message for the kids."""


class Chat:
    def __init__(self, db: Database, hub: Hub, client):
        self.db = db
        self.hub = hub
        self.client = client
        self.system = f"{INSTRUCTIONS}\n\n{reference_text()}"
        self.busy: set[str] = set()
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_ANSWERS)

    def status(self, session: str, event: dict) -> dict:
        return {
            "open": bool(self.db.settings()["chat_enabled"]) and event["phase"] != "setup",
            "busy": session in self.busy,
        }

    def admit(self, session: str, event: dict, text: str) -> str:
        """Check a question and reserve this phone's one in-flight slot for it."""
        status = self.status(session, event)
        text = text.strip()
        if not status["open"]:
            raise ChatRejected("Der Hilfe-Chat macht gerade eine Pause.")
        if not text:
            raise ChatRejected("Schreib zuerst eine Frage.")
        if len(text) > MAX_QUESTION_CHARS:
            raise ChatRejected(f"Bitte stelle Fragen mit höchstens {MAX_QUESTION_CHARS} Zeichen.")
        if status["busy"]:
            raise ChatRejected("Bitte stelle immer nur eine Frage auf einmal.")
        self.busy.add(session)
        return text

    async def ask(self, session: str, event: dict, text: str) -> None:
        """Answer an admitted question, streaming it to this phone only."""
        round_number = event["round"]
        try:
            history = self.db.chat_history(session, HISTORY_MESSAGES - 1)
            question = self.db.add_chat(session, round_number, "user", text)
            await self.hub.to_chat(session, {"type": "chat_message", "message": question})
            async with self._slots:
                answer = await self._stream(session, event, [*history, question])
            saved = self.db.add_chat(session, round_number, "assistant", answer)
            await self.hub.to_chat(session, {"type": "chat_done", "message": saved})
        except Exception as exc:
            message = (
                "Der Sicherheitsfilter hat diese Frage gestoppt. Versuche, sie anders zu formulieren."
                if is_content_filter(exc)
                else "Die Hilfe-KI konnte gerade nicht antworten. Bitte versuche es noch einmal."
            )
            await self.hub.to_chat(session, {"type": "chat_error", "message": message})
        finally:
            self.busy.discard(session)

    def instructions(self, event: dict) -> str:
        """The fixed reference, plus this round's stage, which everyone may know."""
        stage = STAGES.get(event.get("stage"))
        if stage is None:
            return self.system
        return (
            f"{self.system}\n\n===== THIS ROUND =====\n"
            f"The match is on {stage['name']} (melee.Stage.{event['stage']}). {stage['about']}"
        )

    async def _stream(self, session: str, event: dict, history: list[dict]) -> str:
        if self.client is None:
            raise RuntimeError("Azure OpenAI is not configured.")
        stream = await self.client.responses.create(
            model=self.db.settings()["model"], stream=True, store=False,
            max_output_tokens=MAX_OUTPUT_TOKENS, instructions=self.instructions(event),
            input=[{"role": row["role"], "content": row["content"]} for row in history],
        )
        answer = ""
        await self.hub.to_chat(session, {"type": "chat_start"})
        async for event in stream:
            if event.type == "response.output_text.delta":
                answer += event.delta
                await self.hub.to_chat(session, {"type": "chat_delta", "text": event.delta})
            elif event.type in ("response.failed", "response.incomplete"):
                check_finished(event.response)
            elif event.type == "error":
                raise RuntimeError(f"{event.code}: {event.message}")
        if not answer:
            raise RuntimeError("The model returned an empty answer.")
        return answer
