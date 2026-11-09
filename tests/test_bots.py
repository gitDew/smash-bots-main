"""Test starter bot decisions without starting Dolphin."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

import melee


STARTER = Path(__file__).resolve().parents[1] / "server" / "starter_bot.py"


def player(x=0, y=0, *, stock=4, facing=True, off_stage=False,
           on_ground=True, jumps_left=0, action=melee.Action.STANDING):
    return melee.PlayerState(
        position=melee.Position(x=x, y=y), stock=stock, facing=facing,
        off_stage=off_stage, on_ground=on_ground, jumps_left=jumps_left,
        action=action,
    )


class StarterBotTests(unittest.TestCase):
    def bots(self, port):
        spec = importlib.util.spec_from_file_location("starter_bot", STARTER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module.Bot(port=port)

    def test_runs_towards_nearest_active_opponent_on_any_port(self):
        state = melee.GameState(frame=1, players={
            1: player(x=-30), 2: player(x=-1, stock=0),
            3: player(), 4: player(x=40),
        })
        for bot in self.bots(port=3):
            controller = Mock()
            bot.act(state, controller)
            controller.tilt_analog.assert_called_once_with(melee.Button.BUTTON_MAIN, 0.0, 0.5)
            controller.press_button.assert_not_called()

    def test_faces_and_attacks_a_nearby_opponent_with_button_pulses(self):
        for bot in self.bots(port=2):
            me = player(facing=False)
            state = melee.GameState(frame=10, players={1: player(x=5), 2: me})
            controller = Mock()
            bot.act(state, controller)
            controller.tilt_analog.assert_called_once_with(melee.Button.BUTTON_MAIN, 1.0, 0.5)
            controller.press_button.assert_called_once_with(melee.Button.BUTTON_A)

            controller.reset_mock()
            state.frame = 11
            me.facing = True
            bot.act(state, controller)
            controller.press_button.assert_not_called()

    def test_jumps_towards_an_opponent_on_a_higher_platform(self):
        state = melee.GameState(frame=0, players={1: player(), 2: player(x=5, y=20)})
        for bot in self.bots(port=1):
            controller = Mock()
            bot.act(state, controller)
            controller.press_button.assert_called_once_with(melee.Button.BUTTON_X)

    def test_recovers_with_jump_then_up_special_or_ledge_getup(self):
        for bot in self.bots(port=1):
            controller = Mock()
            me = player(x=-80, off_stage=True, on_ground=False, jumps_left=1)
            state = melee.GameState(frame=0, players={1: me, 2: player()})
            bot.act(state, controller)
            controller.tilt_analog.assert_called_once_with(melee.Button.BUTTON_MAIN, 1.0, 0.5)
            controller.press_button.assert_called_once_with(melee.Button.BUTTON_X)

            controller.reset_mock()
            me.jumps_left = 0
            bot.act(state, controller)
            controller.tilt_analog.assert_called_once_with(melee.Button.BUTTON_MAIN, 1.0, 1.0)
            controller.press_button.assert_called_once_with(melee.Button.BUTTON_B)

            controller.reset_mock()
            me.action = melee.Action.EDGE_HANGING
            bot.act(state, controller)
            controller.tilt_analog.assert_called_once_with(melee.Button.BUTTON_MAIN, 1.0, 1.0)
            controller.press_button.assert_not_called()

    def test_does_not_follow_an_opponent_off_stage(self):
        state = melee.GameState(frame=0, players={
            1: player(), 2: player(x=80, off_stage=True, on_ground=False),
        })
        for bot in self.bots(port=1):
            controller = Mock()
            bot.act(state, controller)
            controller.tilt_analog.assert_not_called()
            controller.press_button.assert_not_called()


if __name__ == "__main__":
    unittest.main()
