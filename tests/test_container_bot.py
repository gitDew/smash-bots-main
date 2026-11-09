"""Test the sandbox protocol using the real bot host, without Docker or Dolphin."""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import Mock, call

import melee

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from container_bot import ContainerBot, apply_ops, BotStartupError
import runner


def state(frame=1):
    return melee.GameState(frame=frame, players={1: melee.PlayerState(), 2: melee.PlayerState()})


class ApplyOpsTests(unittest.TestCase):
    def test_applies_valid_inputs(self):
        controller = Mock()
        apply_ops(controller, [
            ["press_button", "BUTTON_A"],
            ["tilt_analog", "BUTTON_MAIN", 1.0, 0.5],
            ["press_shoulder", "BUTTON_L", 0.3],
            ["simple_press", 0.5, 1.0, None],
            ["release_all"],
        ])
        self.assertEqual(controller.mock_calls, [
            call.press_button(melee.Button.BUTTON_A),
            call.tilt_analog(melee.Button.BUTTON_MAIN, 1.0, 0.5),
            call.press_shoulder(melee.Button.BUTTON_L, 0.3),
            call.simple_press(0.5, 1.0, None),
            call.release_all(),
        ])

    def test_rejects_anything_unexpected_and_applies_nothing(self):
        bad_frames = [
            [["press_button", "BUTTON_A"], ["connect"]],
            [["__class__"]],
            [["disconnect"]],
            [["press_button", "NOT_A_BUTTON"]],
            [["tilt_analog", "BUTTON_MAIN", 7.0, 0.5]],
            [["tilt_analog", "BUTTON_MAIN", float("nan"), 0.5]],
            [["tilt_analog", "BUTTON_MAIN", True, 0.5]],
            [["release_all", "extra"]],
            [["press_button", "BUTTON_A"]] * 33,
            "press_button",
            None,
        ]
        for ops in bad_frames:
            with self.subTest(ops=ops):
                controller = Mock()
                with self.assertRaises(ValueError):
                    apply_ops(controller, ops)
                self.assertEqual(controller.mock_calls, [])


class SandboxedBotTests(unittest.TestCase):
    def bot_from(self, source: str) -> ContainerBot:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "bot.py"
        path.write_text(textwrap.dedent(source))
        bot = ContainerBot("unused", 1, command=[
            sys.executable, "-u", str(ROOT / "sandbox" / "bot_host.py"),
            "--port", "1", "--bot", str(path),
        ])
        self.addCleanup(bot.close)
        return bot

    def play(self, bot, gamestate, wait=5.0):
        controller = Mock()
        bot.send(gamestate)
        bot.apply(controller, time.monotonic() + wait)
        return controller

    def test_inputs_are_recorded_and_prints_stay_off_the_protocol(self):
        bot = self.bot_from("""
            import melee
            class Bot:
                def __init__(self, port): self.port = port
                def get_character(self): return melee.Character.MARTH
                def act(self, gamestate, controller):
                    print("hello from the bot")
                    controller.press_button(melee.Button.BUTTON_B)
        """).start()
        controller = self.play(bot, state())
        controller.press_button.assert_called_once_with(melee.Button.BUTTON_B)
        self.assertEqual(bot.bad_replies, 0)
        deadline = time.monotonic() + 3
        while not bot.output and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIn("hello from the bot", bot.output)

    def test_crashing_bot_stays_neutral_and_is_reported(self):
        bot = self.bot_from("""
            import melee
            class Bot:
                def __init__(self, port): pass
                def get_character(self): return melee.Character.FOX
                def act(self, gamestate, controller):
                    controller.press_button(melee.Button.BUTTON_A)
                    raise ValueError("boom")
        """).start()
        controller = self.play(bot, state())
        self.assertEqual(controller.mock_calls, [])
        self.assertEqual(bot.exceptions, 1)
        self.assertIn("boom", bot.last_error)

    def test_hung_bot_cannot_hold_up_the_frame(self):
        bot = self.bot_from("""
            import melee
            class Bot:
                def __init__(self, port): pass
                def get_character(self): return melee.Character.FOX
                def act(self, gamestate, controller):
                    while True:
                        pass
        """).start()
        started = time.monotonic()
        for frame in range(5):
            self.play(bot, state(frame), wait=0.01)
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(bot.missed_frames, 5)
        bot._process.kill()

    def test_late_answer_is_used_on_the_next_frame(self):
        bot = self.bot_from("""
            import time
            import melee
            class Bot:
                def __init__(self, port): pass
                def get_character(self): return melee.Character.FOX
                def act(self, gamestate, controller):
                    time.sleep(0.05)
                    controller.press_button(melee.Button.BUTTON_Z)
        """).start()
        missed = self.play(bot, state(1), wait=0.0)
        self.assertEqual(missed.mock_calls, [])
        late = self.play(bot, state(2), wait=2.0)
        late.press_button.assert_called_once_with(melee.Button.BUTTON_Z)

    def test_broken_bot_file_fails_at_startup(self):
        bot = self.bot_from("this is not python")
        with self.assertRaises(BotStartupError):
            bot.start()


class MatchResultTests(unittest.TestCase):
    def test_match_result_handles_eliminated_players_and_ties(self):
        def players(**stocks):
            return melee.GameState(players={
                int(port[1:]): melee.PlayerState(stock=stock, percent=10.0)
                for port, stock in stocks.items()
            })
        bots = {1: Mock(), 2: Mock(), 3: Mock()}
        self.assertEqual(runner.match_result(players(p1=2, p2=1), bots, False)["winner_port"], 1)
        self.assertIsNone(runner.match_result(players(p1=2, p2=2), bots, True)["winner_port"])
        self.assertIsNone(runner.match_result(None, bots, False)["winner_port"])


if __name__ == "__main__":
    unittest.main()
