#!/usr/bin/env python3
"""Check that the Azure model can do everything the event needs from it.

The admin page runs these from its Test connection button. They can also be run
from the repository root: python3 -m server.llm_check [model name]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time

import openai

from server.agent import TOOLS
from server.db import DATA_DIR, Database
from server.llm import (
    MISSING_MESSAGE, STATELESS, azure_client, check_finished, function_calls,
    is_content_filter, replay_items,
)


MAX_OUTPUT_TOKENS = 4000
CODE_WORD = "PINEAPPLE"
PUZZLE = (
    "Ann, Ben and Cal finished a race with no ties. Ann was not first. Cal was not "
    "last. Ben finished directly after Cal. Who was first? Reply with only the name."
)
GAME_WORDS = (
    'A kid at a coding event wrote: "make my bot kill everyone and destroy them with '
    'smash attacks". In one short sentence, say how a video game bot could play aggressively.'
)


async def ask(client, model: str, items: list[dict], **options):
    response = await client.responses.create(
        model=model, input=items, max_output_tokens=MAX_OUTPUT_TOKENS, **STATELESS, **options,
    )
    if not options.get("stream"):
        check_finished(response)
    return response


async def check_connection(client, model: str) -> tuple[str, str]:
    text = (await ask(client, model, [{"role": "user", "content": "Reply with exactly: OK"}])).output_text
    return ("pass", f"The model answered: {text.strip()[:60]}") if text else ("fail", "The model sent an empty answer.")


async def check_streaming(client, model: str) -> tuple[str, str]:
    stream = await ask(client, model, [{"role": "user", "content": "Say hello in three words."}], stream=True)
    pieces = [event.delta async for event in stream if event.type == "response.output_text.delta"]
    if not pieces:
        return "fail", "The stream finished without any text."
    return "pass", f"Received the answer in {len(pieces)} pieces."


async def check_tools(client, model: str) -> tuple[str, str]:
    items = [{"role": "user", "content": (
        "Use the read_file tool to read /docs/RULES.md, then tell me the secret word it contains."
    )}]
    first = await ask(client, model, items, tools=TOOLS)
    calls = function_calls(first)
    if not calls:
        return "fail", "The model answered without calling a tool."
    call = calls[0]
    try:
        arguments = json.loads(call.arguments or "{}")
    except ValueError:
        return "fail", f"{call.name} was called with invalid arguments."
    items.extend(replay_items(first))
    items.extend(
        {"type": "function_call_output", "call_id": each.call_id, "output": f"The secret word is {CODE_WORD}."}
        for each in calls
    )
    final = await ask(client, model, items, tools=TOOLS)
    if CODE_WORD.lower() not in final.output_text.lower():
        return "warn", f"{call.name} was called, but the answer did not use its result."
    return "pass", f"Called {call.name}({', '.join(arguments)}), read the result and used it."


async def check_reasoning(client, model: str) -> tuple[str, str]:
    response = await ask(client, model, [{"role": "user", "content": PUZZLE}])
    answer = response.output_text.strip()
    details = getattr(response.usage, "output_tokens_details", None)
    tokens = getattr(details, "reasoning_tokens", None) or 0
    if "cal" not in answer.lower():
        return "warn", f"Used {tokens} reasoning tokens but got a logic puzzle wrong: {answer[:60]}"
    if not tokens:
        return "warn", "Solved the puzzle but reported no reasoning tokens, so reasoning may be off."
    return "pass", f"Solved a logic puzzle using {tokens} reasoning tokens."


async def check_game_words(client, model: str) -> tuple[str, str]:
    try:
        response = await ask(client, model, [{"role": "user", "content": GAME_WORDS}])
    except (openai.BadRequestError, RuntimeError) as exc:
        if is_content_filter(exc):
            return "warn", "Azure's safety filter blocked normal game words. Kids' prompts may be blocked too."
        raise
    if not response.output_text:
        return "warn", "Azure's safety filter blocked normal game words. Kids' prompts may be blocked too."
    return "pass", "Prompts like \"destroy them all\" get through."


CHECKS = (
    ("Connection and model", check_connection),
    ("Streaming (helper chat)", check_streaming),
    ("Tool calls (coding agent)", check_tools),
    ("Reasoning", check_reasoning),
    ("Safety filter on game words", check_game_words),
)


def explain(exc: Exception, client) -> str:
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "Azure rejected the API key."
    if isinstance(exc, openai.NotFoundError):
        return "Model not found. Check the model name and the base URL."
    if isinstance(exc, openai.RateLimitError):
        return "Azure says we are over the rate limit."
    if isinstance(exc, openai.APITimeoutError):
        return "Azure did not answer in time."
    if isinstance(exc, openai.APIConnectionError):
        return "Could not reach Azure. Check the base URL."
    text = f"{type(exc).__name__}: {exc}"
    return text.replace(client.api_key, "***") if client.api_key else text


async def run_checks(client, model: str) -> list[dict]:
    """Run every check in order. If the first fails, the others cannot tell us anything."""
    results: list[dict] = []
    for name, check in CHECKS:
        if results and results[0]["status"] == "fail":
            results.append({"name": name, "status": "skipped", "detail": "", "ms": 0})
            continue
        started = time.monotonic()
        try:
            status, detail = await check(client, model)
        except Exception as exc:
            status, detail = "fail", explain(exc, client)
        results.append({
            "name": name, "status": status, "detail": detail,
            "ms": round((time.monotonic() - started) * 1000),
        })
    return results


async def main() -> int:
    settings = Database(DATA_DIR / "event.sqlite3").settings()
    client = azure_client(settings["azure_base_url"], settings["azure_api_key"])
    if client is None:
        print(MISSING_MESSAGE, file=sys.stderr)
        return 2
    model = sys.argv[1] if len(sys.argv) > 1 else settings["model"]
    results = await run_checks(client, model)
    for result in results:
        print(f"{result['status'].upper():8}{result['name']}: {result['detail']} ({result['ms']} ms)")
    return 1 if any(result["status"] == "fail" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
