"""Test native replay archiving with synthetic video, without starting Dolphin."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import replay


HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,nb_read_frames,avg_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=15,
    )
    return json.loads(result.stdout)["streams"][0]


def synthetic_dump(path, *, realtime=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-v", "error"]
    if realtime:
        command.append("-re")
    command += [
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=60", "-an",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "1",
    ]
    if not realtime:
        command += ["-t", "1"]
    command += ["-f", "matroska", str(path)]
    return subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


class ReplayConfigTests(unittest.TestCase):
    def test_round_names_never_overwrite_existing_files_or_recovery_data(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            first = replay.reserve_replay_path(directory)
            first.write_bytes(b"keep this recording")
            self.assertEqual(first.name, "round_1.mp4")
            (directory / "round_3.mp4").write_bytes(b"another recording")
            (directory / "round_4.log").write_text("previous failure")
            (directory / ".round_5.dolphin").mkdir()
            next_round = replay.reserve_replay_path(directory)
            self.assertEqual(next_round.name, "round_6.mp4")
            self.assertEqual(first.read_bytes(), b"keep this recording")
            self.assertEqual((directory / "round_3.mp4").read_bytes(), b"another recording")
            self.assertEqual((directory / "round_4.log").read_text(), "previous failure")

    def test_concurrent_reservations_are_unique(self):
        with tempfile.TemporaryDirectory() as directory:
            with ThreadPoolExecutor(max_workers=8) as pool:
                paths = list(pool.map(lambda _: replay.reserve_replay_path(Path(directory)), range(8)))
            self.assertEqual({path.name for path in paths}, {f"round_{i}.mp4" for i in range(1, 9)})

    def test_missing_ffmpeg_is_explained(self):
        with patch.object(replay.shutil, "which", return_value=None):
            with self.assertRaisesRegex(replay.RecordingError, "FFmpeg"):
                replay.NativeReplayRecorder.start(Path("unused"))

    def test_output_path_must_be_a_directory(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            replay.shutil, "which", return_value="ffmpeg",
        ):
            file = Path(directory) / "not-a-directory"
            file.touch()
            with self.assertRaisesRegex(replay.RecordingError, "Could not start"):
                replay.NativeReplayRecorder.start(file)


@unittest.skipUnless(HAS_FFMPEG, "ffmpeg and ffprobe are needed for replay tests")
class ReplayIntegrationTests(unittest.TestCase):
    def test_stop_recovers_killed_frame_dump_into_h264_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = replay.NativeReplayRecorder.start(Path(directory))
            dump = recorder.home / "Dump" / "Frames" / "game_0.matroska"
            writer = synthetic_dump(dump)
            try:
                recorder.attach(writer.pid)
                time.sleep(2.5)
                recorder.check_frames()
                writer.kill()
                writer.wait(timeout=5)
                self.assertTrue(recorder.finish(match_started=True))
                stream = probe(recorder.path)
                self.assertEqual(recorder.path.name, "round_1.mp4")
                self.assertEqual(stream["codec_name"], "h264")
                self.assertGreater(int(stream["nb_read_frames"]), 60)
                self.assertGreater(float(Fraction(stream["avg_frame_rate"])), 50)
                self.assertFalse(recorder.home.parent.exists())
            finally:
                if writer.poll() is None:
                    writer.kill()
                    writer.wait()
                if recorder.process.poll() is None:
                    recorder.finish(match_started=False, dolphin_stopped=False)

    def test_multiple_dump_segments_are_combined(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = replay.NativeReplayRecorder.start(Path(directory))
            for i in range(2):
                dump = recorder.home / "Dump" / "Frames" / f"game_{i}.matroska"
                writer = synthetic_dump(dump, realtime=False)
                self.assertEqual(writer.wait(timeout=15), 0)
            self.assertTrue(recorder.finish(match_started=True))
            self.assertGreaterEqual(int(probe(recorder.path)["nb_read_frames"]), 100)

    def test_final_truncated_segment_is_combined_with_an_earlier_segment(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = replay.NativeReplayRecorder.start(Path(directory))
            first = recorder.home / "Dump" / "Frames" / "game_0.matroska"
            writer = synthetic_dump(first, realtime=False)
            self.assertEqual(writer.wait(timeout=15), 0)
            second = recorder.home / "Dump" / "Frames" / "game_1.matroska"
            writer = synthetic_dump(second)
            try:
                recorder.attach(writer.pid)
                time.sleep(2.5)
                writer.kill()
                writer.wait(timeout=5)
                self.assertTrue(recorder.finish(match_started=True))
                self.assertGreater(int(probe(recorder.path)["nb_read_frames"]), 100)
            finally:
                if writer.poll() is None:
                    writer.kill()
                    writer.wait()
                if recorder.process.poll() is None:
                    recorder.finish(match_started=False, dolphin_stopped=False)

    def test_no_frames_is_not_reported_as_a_saved_match(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = replay.NativeReplayRecorder.start(Path(directory))
            with self.assertRaisesRegex(replay.RecordingError, "did not produce"):
                recorder.check_frames()
            with self.assertRaisesRegex(replay.RecordingError, "did not produce"):
                recorder.finish(match_started=True)
            self.assertFalse(recorder.path.exists())

    def test_no_match_and_no_frames_leaves_no_empty_video(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = replay.NativeReplayRecorder.start(Path(directory))
            self.assertFalse(recorder.finish(match_started=False))
            self.assertFalse(recorder.path.exists())
            self.assertFalse(recorder.home.parent.exists())

    def test_failed_remux_preserves_original_dump_and_log(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder = replay.NativeReplayRecorder.start(Path(directory))
            dump = recorder.home / "Dump" / "Frames" / "game_0.matroska"
            dump.parent.mkdir(parents=True)
            dump.write_bytes(b"not a video")
            with self.assertRaisesRegex(replay.RecordingError, "original dumps"):
                recorder.finish(match_started=True)
            self.assertEqual(dump.read_bytes(), b"not a video")
            self.assertTrue(recorder.path.with_suffix(".log").is_file())

    def test_killed_runner_stops_dolphin_and_saves_video(self):
        # A separate process simulates the runner and a synthetic frame writer.
        script = """
import json, subprocess, sys, time
from pathlib import Path
import replay
r = replay.NativeReplayRecorder.start(Path(sys.argv[1]))
dump = r.home / 'Dump' / 'Frames' / 'game_0.matroska'
dump.parent.mkdir(parents=True)
p = subprocess.Popen(%r + [str(dump)], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
r.attach(p.pid)
print(json.dumps({'video': str(r.path), 'pid': p.pid}), flush=True)
time.sleep(60)
""" % [
            "ffmpeg", "-v", "error", "-re", "-f", "lavfi", "-i",
            "testsrc2=size=320x240:rate=60", "-an", "-c:v", "libx264",
            "-preset", "ultrafast", "-g", "1", "-f", "matroska",
        ]
        with tempfile.TemporaryDirectory() as directory:
            runner = subprocess.Popen(
                [sys.executable, "-c", script, directory],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, start_new_session=True,
            )
            try:
                details = json.loads(runner.stdout.readline())
                path = Path(details["video"])
                time.sleep(2.5)
                runner.kill()
                runner.wait(timeout=5)
                deadline = time.monotonic() + 20
                while path.with_suffix(".log").exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertFalse(path.with_suffix(".log").exists(), "supervisor did not finish")
                self.assertGreater(int(probe(path)["nb_read_frames"]), 60)
                self.assertFalse((path.parent / ".round_1.dolphin").exists())
            finally:
                if runner.poll() is None:
                    runner.kill()
                    runner.wait()
                try:
                    os.killpg(runner.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                runner.stdout.close()
                runner.stderr.close()


if __name__ == "__main__":
    unittest.main()
