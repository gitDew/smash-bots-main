"""Runner tests that do not start Dolphin or require a game image."""

from __future__ import annotations

import configparser
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import melee

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import runner


CHARACTERS = (
    melee.Character.FOX, melee.Character.DK, melee.Character.MARTH, melee.Character.FALCO,
)


def player(character=melee.Character.FOX, *, stock=4, percent=0, ready=True):
    return melee.PlayerState(
        character=character,
        stock=stock,
        percent=percent,
        coin_down=ready,
        controller_status=melee.ControllerStatus.CONTROLLER_HUMAN,
    )


def css_state(count):
    players = {
        port: player(CHARACTERS[port - 1]) for port in range(1, count + 1)
    }
    players.update({
        port: melee.PlayerState(controller_status=melee.ControllerStatus.CONTROLLER_UNPLUGGED)
        for port in range(count + 1, 5)
    })
    return melee.GameState(menu_state=melee.Menu.CHARACTER_SELECT, players=players)


class ScoreTests(unittest.TestCase):
    def test_two_to_four_player_leaders(self):
        state = melee.GameState(players={
            1: player(stock=3, percent=10),
            2: player(stock=4, percent=90),
            3: player(stock=4, percent=20),
            4: player(stock=4, percent=5),
        })
        self.assertEqual(runner.winner_at_time_limit(state), 2)
        self.assertEqual(runner.winner_at_time_limit(state, (1, 2, 3)), 3)
        self.assertEqual(runner.winner_at_time_limit(state, (1, 2, 3, 4)), 4)

    def test_ties_missing_states_and_all_eliminated_have_no_unique_winner(self):
        state = melee.GameState(players={
            1: player(stock=2, percent=20),
            2: player(stock=2, percent=20),
            3: player(stock=1, percent=0),
        })
        self.assertIsNone(runner.winner_at_time_limit(state, (1, 2, 3)))
        self.assertIsNone(runner.winner_at_time_limit(state, (1, 2, 3, 4)))
        for current in state.players.values():
            current.stock = 0
        self.assertIsNone(runner.winner_at_time_limit(state, (1, 2, 3)))


class MenuTests(unittest.TestCase):
    def setUp(self):
        self.helper = Mock()
        self.controllers = {port: Mock(port=port) for port in range(1, 5)}
        self.characters = dict(enumerate(CHARACTERS, start=1))
        self.stage = melee.Stage.RANDOM_STAGE

    def navigate(self, state, count=4):
        controllers = {port: self.controllers[port] for port in range(1, count + 1)}
        characters = {port: self.characters[port] for port in controllers}
        runner.navigate_menu(self.helper, state, controllers, characters, self.stage)

    def test_waits_for_all_four_players_before_starting(self):
        state = css_state(4)
        state.players[4].coin_down = False
        self.navigate(state)
        self.assertEqual(self.helper.menu_helper_simple.call_count, 4)
        self.assertFalse(self.helper.menu_helper_simple.call_args_list[0].kwargs["autostart"])

        self.helper.reset_mock()
        state.players[4].coin_down = True
        self.navigate(state)
        self.assertTrue(self.helper.menu_helper_simple.call_args_list[0].kwargs["autostart"])
        self.assertTrue(all(
            not invocation.kwargs["autostart"]
            for invocation in self.helper.menu_helper_simple.call_args_list[1:]
        ))

    def test_sheik_uses_zelda_slot_and_missing_or_cpu_is_not_ready(self):
        state = css_state(2)
        self.characters[2] = melee.Character.SHEIK
        state.players[2].character = melee.Character.ZELDA
        self.assertTrue(runner.all_bots_selected(state, {1: self.characters[1], 2: self.characters[2]}))
        state.players[2].controller_status = melee.ControllerStatus.CONTROLLER_CPU
        self.assertFalse(runner.all_bots_selected(state, {1: self.characters[1], 2: self.characters[2]}))
        del state.players[2]
        self.assertFalse(runner.all_bots_selected(state, {1: self.characters[1], 2: self.characters[2]}))

    def test_cpu_in_unused_slot_is_removed_before_selecting_characters(self):
        state = css_state(3)
        state.players[4].controller_status = melee.ControllerStatus.CONTROLLER_CPU
        self.navigate(state, count=3)
        self.helper.change_controller_status.assert_called_once_with(
            self.controllers[1], state, 4, melee.ControllerStatus.CONTROLLER_UNPLUGGED,
        )
        self.helper.menu_helper_simple.assert_not_called()

    def test_configured_cpu_is_switched_to_human(self):
        state = css_state(4)
        state.players[3].controller_status = melee.ControllerStatus.CONTROLLER_CPU
        self.navigate(state)
        self.helper.change_controller_status.assert_called_once_with(
            self.controllers[3], state, 3, melee.ControllerStatus.CONTROLLER_HUMAN,
        )
        self.assertFalse(self.helper.menu_helper_simple.call_args_list[0].kwargs["autostart"])

    def test_stage_selection_keeps_port_one_in_charge(self):
        state = melee.GameState(menu_state=melee.Menu.STAGE_SELECT)
        self.navigate(state)
        self.assertTrue(self.helper.menu_helper_simple.call_args_list[0].kwargs["autostart"])
        self.assertTrue(all(
            not invocation.kwargs["autostart"]
            for invocation in self.helper.menu_helper_simple.call_args_list[1:]
        ))


class RunnerTests(unittest.TestCase):
    def test_backend_fallback_only_applies_to_older_slippi_by_default(self):
        for mainline, fallback, expected in (
            (True, True, "Metal"),
            (False, True, "OGL"),
            (False, False, "Metal"),
        ):
            with self.subTest(mainline=mainline, fallback=fallback), tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "Config" / "Dolphin.ini"
                config_path.parent.mkdir()
                config_path.write_text("[Core]\nGFXBackend = Metal\n")
                console = Mock(dolphin_home_path=directory, is_mainline=mainline, gfx_backend="Metal")
                runner.configure_render_to_main(console, legacy_macos_fallback=fallback)
                config = configparser.ConfigParser()
                config.read(config_path)
                self.assertEqual(config.get("Core", "GFXBackend"), expected)
                self.assertTrue(config.getboolean("Display", "RenderToMain"))

    def test_same_named_bot_files_are_loaded_as_separate_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / team / "bot.py" for team in ("a", "b")]
            for path in paths:
                path.parent.mkdir()
                path.write_text(
                    "class Bot:\n"
                    "    def __init__(self, port): self.port = port\n"
                    "    def get_character(self): pass\n"
                    "    def act(self, gamestate, controller): pass\n"
                )
            first, second = (runner.load_bot(path, port) for port, path in enumerate(paths, 1))
            self.assertEqual((first.port, second.port), (1, 2))
            self.assertIsNot(type(first), type(second))

    def test_bot_arguments_must_fit_the_player_count(self):
        for args in (
            ["--players", "2", "--p3", "team_c/bot.py"],
            ["--players", "3", "--p4", "team_d/bot.py"],
        ):
            with self.subTest(args=args), patch.object(sys, "argv", ["runner.py", *args]):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        runner.main()
                self.assertEqual(error.exception.code, 2)

    def run_mocked_match(
        self, count, *, missing_port=None, unexpected_port=None, team_mode=False,
        incomplete_countdown=False, absent_later=None, interrupted=False,
        recording_error=None, frame_error=None, stop_error=None, no_record=False,
        use_default_players=False, use_default_bots=False, gfx_backend=None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            iso = Path(directory) / "game.iso"
            iso.touch()
            args = ["runner.py", "--iso", str(iso), "--time-limit", "1"]
            if not use_default_players:
                args.extend(("--players", str(count)))
            if no_record:
                args.append("--no-record")
            if gfx_backend is not None:
                args.extend(("--gfx-backend", gfx_backend))
            if not use_default_bots:
                for port in range(3, count + 1):
                    args.extend((f"--p{port}", f"player{port}.py"))

            controllers = {}
            def make_controller(*, console, port, type):
                controller = Mock(port=port)
                controller.connect.return_value = True
                controllers[port] = controller
                return controller

            bots = {}
            def make_bot(path, port):
                bot = Mock()
                bot.get_character.return_value = CHARACTERS[port - 1]
                bots[port] = bot
                return bot

            participants = {
                port: player(CHARACTERS[port - 1], stock=port, percent=port * 10)
                for port in range(1, count + 1) if port != missing_port
            }
            if unexpected_port is not None:
                participants[unexpected_port] = player()
            state = melee.GameState(
                menu_state=melee.Menu.IN_GAME, frame=0,
                players=participants, is_teams=team_mode,
            )
            timeout = melee.GameState(
                menu_state=melee.Menu.IN_GAME,
                frame=300 if frame_error else 60,
                players=participants,
            )
            frames = [css_state(count)]
            if incomplete_countdown:
                frames.append(melee.GameState(
                    menu_state=melee.Menu.IN_GAME, frame=-1,
                    players={1: participants[1]},
                ))
            frames.append(state)
            if absent_later is not None:
                frames.append(melee.GameState(
                    menu_state=melee.Menu.IN_GAME, frame=1,
                    players={port: value for port, value in participants.items() if port != absent_later},
                ))
            frames.append(timeout)
            console = Mock()
            console.connect.return_value = True
            console.step.side_effect = KeyboardInterrupt if interrupted else frames
            menu_helper = Mock()
            recorder = Mock(path=Path(directory) / "replay.mp4", home=Path(directory) / "User")
            recorder.check.side_effect = recording_error
            recorder.check_frames.side_effect = frame_error
            recorder.finish.return_value = True
            recorder.finish.side_effect = stop_error
            output = io.StringIO()
            errors = io.StringIO()
            with (
                patch.object(sys, "argv", args),
                patch.object(runner.melee, "Console", return_value=console) as make_console,
                patch.object(runner.melee, "Controller", side_effect=make_controller),
                patch.object(runner.melee, "MenuHelper", return_value=menu_helper),
                patch.object(runner, "configure_render_to_main"),
                patch.object(runner.NativeReplayRecorder, "start", return_value=recorder) as start_recording,
                patch.object(runner, "load_bot", side_effect=make_bot),
                patch.object(runner.signal, "signal"),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                result = runner.main()

            self.assertEqual(set(controllers), set(range(1, count + 1)))
            self.assertEqual(
                console.setup_dolphin_controller.call_args_list,
                [call(port, melee.ControllerType.UNPLUGGED) for port in range(count + 1, 5)],
            )
            console.stop.assert_called_once()
            self.assertEqual(make_console.call_args.kwargs["path"], str(runner.DEFAULT_DOLPHIN))
            expected_backend = gfx_backend if gfx_backend is not None else (
                "Metal" if sys.platform == "darwin" else ""
            )
            self.assertEqual(make_console.call_args.kwargs["gfx_backend"], expected_backend)
            if no_record:
                start_recording.assert_not_called()
                recorder.finish.assert_not_called()
                self.assertIsNone(make_console.call_args.kwargs["dump_config"])
            else:
                start_recording.assert_called_once_with(runner.DEFAULT_REPLAY_DIR)
                recorder.attach.assert_called_once_with(console._process.pid)
                recorder.finish.assert_called_once()
                dump = make_console.call_args.kwargs["dump_config"]
                self.assertTrue(dump.dump)
                self.assertEqual((dump.format, dump.codec, dump.encoder),
                                 ("matroska", "h264", "h264_videotoolbox"))
                self.assertEqual(make_console.call_args.kwargs["dolphin_home_path"], str(recorder.home))
            for controller in controllers.values():
                controller.disconnect.assert_called_once()
            return result, bots, controllers, output.getvalue(), errors.getvalue()

    def test_two_three_and_four_player_matches(self):
        for count in (2, 3, 4):
            with self.subTest(count=count):
                result, bots, controllers, output, _ = self.run_mocked_match(count)
                self.assertEqual(result, 0)
                self.assertIn(f"Player {count} wins on time", output)
                for port in range(1, count + 1):
                    self.assertIn(f"Player {port}:", output)
                    bots[port].act.assert_called_once()
                    self.assertIs(bots[port].act.call_args.args[1], controllers[port])

    def test_default_match_uses_all_four_default_bots(self):
        result, bots, _, output, _ = self.run_mocked_match(
            4, use_default_players=True, use_default_bots=True,
        )
        self.assertEqual(result, 0)
        self.assertEqual(set(bots), {1, 2, 3, 4})
        self.assertIn(str(runner.DEFAULT_P3_BOT), output)
        self.assertIn(str(runner.DEFAULT_P4_BOT), output)

    def test_three_players_can_use_the_default_third_bot(self):
        result, bots, _, output, _ = self.run_mocked_match(3, use_default_bots=True)
        self.assertEqual(result, 0)
        self.assertEqual(set(bots), {1, 2, 3})
        self.assertIn(str(runner.DEFAULT_P3_BOT), output)
        self.assertNotIn(str(runner.DEFAULT_P4_BOT), output)

    def test_rejects_match_that_did_not_include_every_configured_player(self):
        result, bots, _, _, errors = self.run_mocked_match(4, missing_port=4)
        self.assertEqual(result, 1)
        self.assertIn("missing ports [4]", errors)
        for bot in bots.values():
            bot.act.assert_not_called()

    def test_rejects_unexpected_player_and_team_mode(self):
        result, _, _, _, errors = self.run_mocked_match(3, unexpected_port=4)
        self.assertEqual(result, 1)
        self.assertIn("unexpected ports [4]", errors)
        result, _, _, _, errors = self.run_mocked_match(4, team_mode=True)
        self.assertEqual(result, 1)
        self.assertIn("Team mode is enabled", errors)

    def test_interrupt_finalizes_recording_and_stops_dolphin(self):
        result, _, _, output, _ = self.run_mocked_match(2, interrupted=True)
        self.assertEqual(result, 130)
        self.assertIn("Interrupted.", output)
        self.assertIn("Replay saved:", output)

    def test_recording_failures_are_reported(self):
        error = runner.RecordingError("disk full")
        result, _, _, output, errors = self.run_mocked_match(2, recording_error=error)
        self.assertEqual(result, 1)
        self.assertIn("disk full", errors)
        self.assertNotIn("Replay saved:", output)
        result, _, _, output, errors = self.run_mocked_match(2, stop_error=error)
        self.assertEqual(result, 1)
        self.assertIn("disk full", errors)
        self.assertNotIn("Replay saved:", output)

    def test_missing_native_frame_dump_stops_the_match(self):
        error = runner.RecordingError("Dolphin did not produce a frame dump")
        result, _, _, output, errors = self.run_mocked_match(2, frame_error=error)
        self.assertEqual(result, 1)
        self.assertIn("did not produce a frame dump", errors)
        self.assertNotIn("Replay saved:", output)

    def test_graphics_backend_can_be_overridden(self):
        result, _, _, _, _ = self.run_mocked_match(2, gfx_backend="OGL")
        self.assertEqual(result, 0)

    def test_recording_can_be_disabled(self):
        result, _, _, output, _ = self.run_mocked_match(2, no_record=True)
        self.assertEqual(result, 0)
        self.assertNotIn("Recording to:", output)

    def test_incomplete_countdown_and_missing_eliminated_player(self):
        result, bots, _, _, _ = self.run_mocked_match(
            4, incomplete_countdown=True, absent_later=4,
        )
        self.assertEqual(result, 0)
        for port in (1, 2, 3):
            self.assertEqual(bots[port].act.call_count, 2)
        self.assertEqual(bots[4].act.call_count, 1)


if __name__ == "__main__":
    unittest.main()
