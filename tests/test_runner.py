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
    def test_melee_clock_uses_exact_requested_seconds_not_slippis_eight_minutes(self):
        source = Path(melee.__file__).parent / "GALE01r2.ini"
        for seconds in (30, 60, 90, 300, 480):
            with self.subTest(seconds=seconds), tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "GameSettings" / "GALE01r2.ini"
                config_path.parent.mkdir()
                config_path.write_text(source.read_text().format(extra_codes=""))
                console = Mock(dolphin_home_path=directory)
                runner.configure_match_stocks(console, 1)
                runner.configure_match_time(console, seconds)
                config = config_path.read_text()
                self.assertIn("[Gecko_Enabled]\n$Event: Match Clock\n", config)
                self.assertIn(f"003D4A50 {(seconds + 59) // 60:08X}", config)
                self.assertIn("04167C94 3C000000", config)
                self.assertIn(f"04167C98 {0x60000000 | seconds:08X}", config)
                self.assertIn("043D4A4C 01000A00", config)
                self.assertIn("$Optional: Extract Menu Info", config)

    def test_no_time_limit_keeps_the_original_melee_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "GameSettings" / "GALE01r2.ini"
            config_path.parent.mkdir()
            original = "[Gecko_Enabled]\n[Gecko]\n"
            config_path.write_text(original)
            runner.configure_match_time(Mock(dolphin_home_path=directory), 0)
            self.assertEqual(config_path.read_text(), original)

    def test_melee_starts_with_the_requested_stock_count(self):
        source = Path(melee.__file__).parent / "GALE01r2.ini"
        for lives, expected in ((1, "01000A00"), (3, "03000A00"), (0, "04000A00")):
            with self.subTest(lives=lives), tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "GameSettings" / "GALE01r2.ini"
                config_path.parent.mkdir()
                original = source.read_text().format(extra_codes="")
                config_path.write_text(original)
                runner.configure_match_stocks(Mock(dolphin_home_path=directory), lives)
                config = config_path.read_text()
                self.assertIn("[Gecko_Enabled]\n$Event: Starting Stocks\n", config)
                self.assertTrue(config.endswith(f"$Event: Starting Stocks\n043D4A4C {expected}\n"))
                self.assertIn("$Optional: Extract Menu Info", config)

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

    def test_fighter_reads_the_container_and_character(self):
        self.assertEqual(runner.fighter("smash-team-1=MARTH"), ("smash-team-1", melee.Character.MARTH))

    def test_fighter_without_a_container_has_no_bot(self):
        self.assertEqual(runner.fighter("MARTH"), (None, melee.Character.MARTH))

    def test_match_needs_valid_and_different_fighters(self):
        for fighters in (
            ["a=FOX"],
            ["a=FOX", "b=DK", "c=MARTH", "d=FALCO", "e=ROY"],
            ["a=FOX", "b=FOX"],
            ["a=FOX", "b=NANA"],
            ["a", "b"],
            ["a=FOX", "=DK"],
            ["a=FOX", "b=DK", "--stage", "NO_STAGE"],
            ["a=FOX", "b=DK", "--stage", "MUTE_CITY"],
            ["a=FOX", "b=DK", "--lives", "-1"],
        ):
            with self.subTest(fighters=fighters), patch.object(sys, "argv", ["runner.py", *fighters]):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        runner.main()
                self.assertEqual(error.exception.code, 2)

    def run_mocked_match(
        self, count, *, missing_port=None, unexpected_port=None, team_mode=False,
        incomplete_countdown=False, absent_later=None, interrupted=False, dolphin_closed=False,
        recording_error=None, frame_error=None, stop_error=None, no_record=False,
        gfx_backend=None, idle_ports=(), stage=None, game_stage=melee.Stage.FINAL_DESTINATION,
        last_one_standing=None, lives=None, game_frames=None, time_limit=None,
    ):
        with tempfile.TemporaryDirectory() as directory:
            iso = Path(directory) / "game.iso"
            iso.touch()
            args = ["runner.py", "--iso", str(iso)]
            if last_one_standing is None and game_frames is None:
                args.extend(("--time-limit", "1"))
            if time_limit is not None:
                args.extend(("--time-limit", str(time_limit)))
            if lives is not None:
                args.extend(("--lives", str(lives)))
            args.extend(
                CHARACTERS[port - 1].name if port in idle_ports
                else f"smash-team-{port}={CHARACTERS[port - 1].name}"
                for port in range(1, count + 1)
            )
            if stage is not None:
                args.extend(("--stage", stage))
            if no_record:
                args.append("--no-record")
            if gfx_backend is not None:
                args.extend(("--gfx-backend", gfx_backend))

            controllers = {}
            def make_controller(*, console, port, type):
                controller = Mock(port=port)
                controller.connect.return_value = True
                controllers[port] = controller
                return controller

            bots = {}
            def make_bot(container, port):
                self.assertEqual(container, f"smash-team-{port}")
                bot = Mock()
                bot.start.return_value = bot
                bot.report.return_value = {"missed_frames": 0, "exceptions": 0, "bad_replies": 0}
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
                players=participants, is_teams=team_mode, stage=game_stage,
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
            if game_frames is not None:
                frames.extend(game_frames)
            elif last_one_standing is None:
                frames.append(timeout)
            else:
                # Everyone else has lost their last stock, then Melee leaves the game.
                frames.append(melee.GameState(
                    menu_state=melee.Menu.IN_GAME, frame=5000,
                    players={
                        port: player(CHARACTERS[port - 1], stock=1 if port == last_one_standing else 0)
                        for port in participants
                    },
                ))
                frames.append(melee.GameState(menu_state=melee.Menu.MAIN_MENU))
            console = Mock()
            console.connect.return_value = True
            console.step.side_effect = (
                KeyboardInterrupt if interrupted
                else runner.EnetDisconnected if dolphin_closed
                else frames
            )
            menu_helper = self.menu_helper = Mock()
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
                patch.object(runner, "configure_match_stocks") as configure_stocks,
                patch.object(runner, "configure_match_time") as configure_time,
                patch.object(runner.NativeReplayRecorder, "start", return_value=recorder) as start_recording,
                patch.object(runner, "ContainerBot", side_effect=make_bot),
                patch.object(runner.signal, "signal"),
                contextlib.redirect_stdout(output),
                contextlib.redirect_stderr(errors),
            ):
                result = runner.main()

            self.assertEqual(set(controllers), set(range(1, count + 1)))
            configure_stocks.assert_called_once_with(console, lives if lives is not None else 1)
            configure_time.assert_called_once_with(console, time_limit if time_limit is not None else (
                1 if last_one_standing is None and game_frames is None else 0
            ))
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

    def test_native_timeout_does_not_extend_the_fight_into_sudden_death(self):
        participants = {port: player(CHARACTERS[port - 1], stock=port) for port in (1, 2)}
        frames = [
            melee.GameState(menu_state=melee.Menu.IN_GAME, frame=3599, players=participants),
            melee.GameState(menu_state=melee.Menu.SUDDEN_DEATH, frame=-120, players=participants),
        ]
        result, _, _, output, _ = self.run_mocked_match(2, game_frames=frames, time_limit=60)
        self.assertEqual(result, 0)
        self.assertIn("keine Verlängerung durch Sudden Death", output)
        self.assertIn("Arena nach dem eingestellten Zeitlimit gestoppt", output)

    def test_native_timeout_is_recognized_if_melee_leaves_before_the_cutoff_frame(self):
        participants = {port: player(CHARACTERS[port - 1], stock=port) for port in (1, 2)}
        frames = [
            melee.GameState(menu_state=melee.Menu.IN_GAME, frame=3599, players=participants),
            melee.GameState(menu_state=melee.Menu.MAIN_MENU),
        ]
        result, _, _, output, _ = self.run_mocked_match(2, game_frames=frames, time_limit=60)
        self.assertEqual(result, 0)
        self.assertIn("Spieler 2 gewinnt", output)
        self.assertIn("Arena nach dem eingestellten Zeitlimit gestoppt", output)

    def test_two_three_and_four_player_matches(self):
        for count in (2, 3, 4):
            with self.subTest(count=count):
                result, bots, controllers, output, _ = self.run_mocked_match(count)
                self.assertEqual(result, 0)
                self.assertIn(f"Spieler {count} gewinnt nach Zeitablauf", output)
                for port in range(1, count + 1):
                    self.assertIn(f"Spieler {port}:", output)
                    bots[port].send.assert_called_once()
                    bots[port].get_character.assert_not_called()
                    self.assertIs(bots[port].apply.call_args.args[0], controllers[port])

    def test_players_without_a_bot_stand_still(self):
        result, bots, controllers, output, _ = self.run_mocked_match(3, idle_ports=(1, 3))
        self.assertEqual(result, 0)
        self.assertEqual(set(bots), {2})
        self.assertIn("Bot von Spieler 1: keiner, steht still", output)
        for port in (1, 3):
            controllers[port].release_all.assert_called()
            controllers[port].press_button.assert_not_called()
            controllers[port].tilt_analog.assert_not_called()
        bots[2].send.assert_called_once()

    def test_the_chosen_stage_is_picked_and_checked(self):
        result, _, _, output, errors = self.run_mocked_match(
            2, stage="BATTLEFIELD", game_stage=melee.Stage.BATTLEFIELD,
        )
        self.assertEqual(result, 0)
        self.assertIn("Ausgewählte Bühne: BATTLEFIELD", output)
        picked = {c.kwargs["stage_selected"] for c in self.menu_helper.menu_helper_simple.call_args_list}
        self.assertEqual(picked, {melee.Stage.BATTLEFIELD})
        self.assertNotIn("Warnung", errors)
        result, _, _, _, errors = self.run_mocked_match(
            2, stage="BATTLEFIELD", game_stage=melee.Stage.DREAMLAND,
        )
        self.assertEqual(result, 0)
        self.assertIn("Das Spiel läuft auf DREAMLAND, nicht auf BATTLEFIELD", errors)

    def test_bots_that_stop_playing_are_named_in_the_log(self):
        self.assertIsNone(runner.bot_trouble({"missed_frames": 0, "exceptions": 0, "bad_replies": 0}))
        self.assertIsNone(runner.bot_trouble(runner.IdleBot(1).report()))
        line = runner.bot_trouble({
            "missed_frames": 1446, "exceptions": 0, "bad_replies": 0, "last_error": None,
            "output": ["Traceback ...", "TypeError: Object of type int32 is not JSON serializable"],
        })
        self.assertIn("1446 Spielbilder verpasst", line)
        self.assertIn("int32 is not JSON serializable", line)

    def test_rejects_match_that_did_not_include_every_configured_player(self):
        result, bots, _, _, errors = self.run_mocked_match(4, missing_port=4)
        self.assertEqual(result, 1)
        self.assertIn("fehlende Anschlüsse [4]", errors)
        for bot in bots.values():
            bot.send.assert_not_called()

    def test_rejects_unexpected_player_and_team_mode(self):
        result, _, _, _, errors = self.run_mocked_match(3, unexpected_port=4)
        self.assertEqual(result, 1)
        self.assertIn("unerwartete Anschlüsse [4]", errors)
        result, _, _, _, errors = self.run_mocked_match(4, team_mode=True)
        self.assertEqual(result, 1)
        self.assertIn("Der Teammodus ist aktiviert", errors)

    def test_interrupt_finalizes_recording_and_stops_dolphin(self):
        result, _, _, output, _ = self.run_mocked_match(2, interrupted=True)
        self.assertEqual(result, 130)
        self.assertIn("Abgebrochen.", output)
        self.assertIn("Wiederholung gespeichert:", output)

    def test_with_no_time_limit_the_match_lasts_until_one_player_is_left(self):
        for count, survivor in ((2, 1), (4, 3)):
            with self.subTest(count=count):
                result, bots, _, output, _ = self.run_mocked_match(count, last_one_standing=survivor, lives=0)
                self.assertEqual(result, 0)
                self.assertNotIn("Zeitlimit erreicht", output)
                self.assertIn(f"Kampf beendet: Spieler {survivor} gewinnt.", output)
                # Frame 5000 is long past any old fixed length, and the bots still played it.
                self.assertEqual(bots[1].send.call_count, 2)

    @staticmethod
    def game(frame, stocks):
        """An in-game frame. run_mocked_match starts every player with stocks equal to its port."""
        return melee.GameState(menu_state=melee.Menu.IN_GAME, frame=frame, players={
            port: player(CHARACTERS[port - 1], stock=stock) for port, stock in stocks.items()
        })

    def test_with_one_life_the_first_knockout_ends_a_two_player_match(self):
        frames = [self.game(frame, {1: 1, 2: 1}) for frame in (100, 150, 190)]
        result, bots, _, output, _ = self.run_mocked_match(2, game_frames=frames)
        self.assertEqual(result, 0)
        self.assertIn("Spieler 2 ist raus.", output)
        self.assertIn("Letzter Spieler übrig: Spieler 1 gewinnt.", output)
        # The game runs on a moment so the knockout is seen; the loser gets no more frames.
        self.assertEqual(bots[1].send.call_count, 3)
        self.assertEqual(bots[2].send.call_count, 1)

    def test_players_who_are_out_vanish_from_the_bots_view(self):
        frames = [
            self.game(100, {1: 1, 2: 2, 3: 2}),
            self.game(200, {1: 1, 2: 1, 3: 2}),
            self.game(290, {1: 1, 2: 1, 3: 2}),
        ]
        result, bots, _, output, _ = self.run_mocked_match(3, lives=1, game_frames=frames)
        self.assertEqual(result, 0)
        self.assertLess(output.index("Spieler 3 ist raus."), output.index("Spieler 2 ist raus."))
        self.assertIn("Letzter Spieler übrig: Spieler 1 gewinnt.", output)
        seen_by_one = bots[1].send.call_args_list[1].args[0]
        self.assertEqual(set(seen_by_one.players), {1, 2})
        self.assertEqual(bots[3].send.call_count, 1)

    def test_closing_dolphin_stops_the_match_cleanly(self):
        result, _, _, output, errors = self.run_mocked_match(2, dolphin_closed=True)
        self.assertEqual(result, 130)
        self.assertIn("Dolphin wurde geschlossen", output)
        self.assertNotIn("Traceback", errors)

    def test_recording_failures_are_reported(self):
        error = runner.RecordingError("disk full")
        result, _, _, output, errors = self.run_mocked_match(2, recording_error=error)
        self.assertEqual(result, 1)
        self.assertIn("disk full", errors)
        self.assertNotIn("Wiederholung gespeichert:", output)
        result, _, _, output, errors = self.run_mocked_match(2, stop_error=error)
        self.assertEqual(result, 1)
        self.assertIn("disk full", errors)
        self.assertNotIn("Wiederholung gespeichert:", output)

    def test_missing_native_frame_dump_stops_the_match(self):
        error = runner.RecordingError("Dolphin did not produce a frame dump")
        result, _, _, output, errors = self.run_mocked_match(2, frame_error=error)
        self.assertEqual(result, 1)
        self.assertIn("did not produce a frame dump", errors)
        self.assertNotIn("Wiederholung gespeichert:", output)

    def test_graphics_backend_can_be_overridden(self):
        result, _, _, _, _ = self.run_mocked_match(2, gfx_backend="OGL")
        self.assertEqual(result, 0)

    def test_recording_can_be_disabled(self):
        result, _, _, output, _ = self.run_mocked_match(2, no_record=True)
        self.assertEqual(result, 0)
        self.assertNotIn("Aufnahme wird gespeichert unter:", output)

    def test_incomplete_countdown_and_missing_eliminated_player(self):
        result, bots, _, _, _ = self.run_mocked_match(
            4, incomplete_countdown=True, absent_later=4,
        )
        self.assertEqual(result, 0)
        for port in (1, 2, 3):
            self.assertEqual(bots[port].send.call_count, 2)
        self.assertEqual(bots[4].send.call_count, 1)


if __name__ == "__main__":
    unittest.main()
