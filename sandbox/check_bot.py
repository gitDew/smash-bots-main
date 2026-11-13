#!/usr/bin/env python3
"""Try a team's bot on simulated frames and print one JSON verdict."""

from __future__ import annotations

import json
import os
import random
import statistics
import sys
import time

import melee

from bot_host import BOT_PATH, RecordingController, load_team_bot, short_error


# A bot that is usually slower than this misses frames during a real match.
MAX_TYPICAL_ACT_MS = 8.0
FRAMES = 900
ACTIONS = (
    melee.Action.STANDING, melee.Action.DASHING, melee.Action.CROUCHING,
    melee.Action.FALLING, melee.Action.EDGE_HANGING, melee.Action.EDGE_CATCHING,
    melee.Action.ON_HALO_WAIT, melee.Action.DAMAGE_FLY_HIGH, melee.Action.SHIELD,
)
STAGES = (
    melee.Stage.BATTLEFIELD, melee.Stage.FINAL_DESTINATION, melee.Stage.DREAMLAND,
    melee.Stage.FOUNTAIN_OF_DREAMS, melee.Stage.POKEMON_STADIUM, melee.Stage.YOSHIS_STORY,
)


def random_player(rng: random.Random) -> melee.PlayerState:
    off_stage = rng.random() < 0.25
    on_ground = not off_stage and rng.random() < 0.7
    return melee.PlayerState(
        character=rng.choice((melee.Character.FOX, melee.Character.MARTH, melee.Character.DK)),
        position=melee.Position(
            x=rng.uniform(-120, 120) if off_stage else rng.uniform(-60, 60),
            y=0.0 if on_ground else rng.uniform(-40, 60),
        ),
        percent=rng.randint(0, 200),
        stock=rng.randint(1, 4),
        facing=rng.random() < 0.5,
        action=rng.choice(ACTIONS),
        action_frame=rng.randint(1, 30),
        off_stage=off_stage,
        on_ground=on_ground,
        jumps_left=rng.randint(0, 2),
    )


def random_state(rng: random.Random, port: int, frame: int) -> melee.GameState:
    opponents = rng.sample([p for p in range(1, 5) if p != port], rng.randint(0, 3))
    players = {p: random_player(rng) for p in [port, *opponents]}
    # Eliminated players may stay in the state for a moment with no stocks.
    if opponents and rng.random() < 0.15:
        players[opponents[0]].stock = 0
    return melee.GameState(frame=frame, stage=rng.choice(STAGES), players=players)


def check(path=BOT_PATH) -> dict:
    port = 1
    try:
        bot = load_team_bot(port, path)
    except BaseException:
        return {"ok": False, "error": short_error()}

    rng = random.Random(1)
    timings = []
    for index in range(FRAMES):
        state = random_state(rng, port, index - 123)
        started = time.perf_counter()
        try:
            bot.act(state, RecordingController(port))
        except Exception:
            return {
                "ok": False,
                "error": f"act() crashed on a test frame:\n{short_error()}",
            }
        timings.append((time.perf_counter() - started) * 1000)

    typical_ms = statistics.median(timings)
    verdict = {
        "ok": typical_ms <= MAX_TYPICAL_ACT_MS,
        "frames": FRAMES, "typical_ms": round(typical_ms, 3),
    }
    if not verdict["ok"]:
        verdict["error"] = (
            f"act() usually takes {typical_ms:.1f} ms. It must stay below "
            f"{MAX_TYPICAL_ACT_MS:.0f} ms or the bot will miss frames."
        )
    return verdict


def main() -> int:
    # Bot code may print; keep the real stdout for the verdict only.
    verdict_out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    print(json.dumps(check()), file=verdict_out, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
