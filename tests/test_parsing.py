"""Tests for the parts that need neither a TV nor ffmpeg: parsing, formatting and command building."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from firetv_cast import config, firetv, media, streams  # noqa: E402
from firetv_cast.cli import format_time, parse_time  # noqa: E402

MEDIA_SESSION_DUMP = """\
  Sessions Stack - have 1 sessions:
    VLC media session
      package=org.videolan.vlc
      state=PlaybackState {state=3, position=12000, buffered position=0, speed=1.0, updated=5000, actions=0}
      metadata:size=1, description=My Movie, 1h 30m, null
    Other session
      package=com.example.other
"""


class TimeTests(unittest.TestCase):
    def test_parse_time(self) -> None:
        self.assertEqual(parse_time("90"), 90.0)
        self.assertEqual(parse_time("1:30"), 90.0)
        self.assertEqual(parse_time("1:02:03"), 3723.0)

    def test_format_time(self) -> None:
        self.assertEqual(format_time(65), "1:05")
        self.assertEqual(format_time(3723), "1:02:03")
        self.assertEqual(format_time(None), "?")


class FireTVParsingTests(unittest.TestCase):
    def test_session_block_and_title(self) -> None:
        block = firetv._session_block(MEDIA_SESSION_DUMP, "org.videolan.vlc")
        self.assertIn("state=PlaybackState", block)
        self.assertNotIn("com.example.other", block)
        self.assertEqual(firetv._session_title(block), "My Movie")

    def test_missing_session(self) -> None:
        self.assertEqual(firetv._session_block(MEDIA_SESSION_DUMP, "no.such.package"), "")
        self.assertIsNone(firetv._session_title(""))

    def test_input_event_is_16_bytes(self) -> None:
        self.assertEqual(len(firetv._input_event(firetv.EV_KEY, firetv.PLAY.linux, 1)), 16)


class StreamTests(unittest.TestCase):
    def test_is_link(self) -> None:
        self.assertTrue(streams.is_link("https://example.com/watch?v=1"))
        self.assertFalse(streams.is_link(r"C:\videos\movie.mkv"))
        self.assertFalse(streams.is_link("file:///etc/passwd"))

    def test_token_is_stable(self) -> None:
        self.assertEqual(streams.token_for("https://example.com/a"), streams.token_for("https://example.com/a"))
        self.assertNotEqual(streams.token_for("https://example.com/a"), streams.token_for("https://example.com/b"))

    def test_format_respects_height(self) -> None:
        self.assertIn("height<=1080", streams._format(1080))

    def test_error_reasons(self) -> None:
        self.assertEqual(streams._error_reason("ERROR: Unsupported URL: https://x"), streams.NOT_A_VIDEO)
        self.assertIn("needs a login", streams._error_reason("ERROR: [youtube] abc: Sign in to confirm your age"))

    def test_remux_command_joins_two_inputs(self) -> None:
        inputs = [streams.StreamInput("https://v.example/video", {}), streams.StreamInput("https://a.example/audio", {})]
        stream = streams.Stream("https://example.com/p", "t", 10.0, False, inputs, 0.0)
        command = streams.remux_command(stream, 5.0)
        self.assertEqual(command.count("-i"), 2)
        self.assertEqual(command.count("-ss"), 2)
        self.assertIn("pipe:1", command)


class MediaTests(unittest.TestCase):
    def test_needs_conversion(self) -> None:
        self.assertFalse(media.VideoInfo(10.0, "h264", "aac").needs_conversion)
        self.assertTrue(media.VideoInfo(10.0, "prores", "pcm_s16le").needs_conversion)
        self.assertFalse(media.VideoInfo(None, None, None).needs_conversion)

    def test_conversion_command_without_gpu(self) -> None:
        with mock.patch.object(media, "_hardware_encoder", return_value=None):
            command = media.conversion_command(Path("movie.mkv"), 12.5, 20)
        self.assertIn("libx264", command)
        self.assertEqual(command[command.index("-ss") + 1], "12.500")

    def test_scan_library_skips_empty_files_and_respects_depth(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.mkv").write_bytes(b"x")
            (root / "empty.mp4").write_bytes(b"")
            (root / "notes.txt").write_text("hi")
            deep = root / "one" / "two"
            deep.mkdir(parents=True)
            (deep / "deep.mp4").write_bytes(b"x")
            names = {e.path.name for e in media.scan_library([folder], 1, 100)}
            self.assertEqual(names, {"a.mkv"})
            names = {e.path.name for e in media.scan_library([folder], 2, 100)}
            self.assertEqual(names, {"a.mkv", "deep.mp4"})


class ConfigTests(unittest.TestCase):
    def test_first_load_writes_defaults_without_personal_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(config, "CONFIG_PATH", Path(folder) / "config.json"):
            loaded = config.Config.load()
            saved = json.loads((Path(folder) / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(loaded.tv_host, "")
        self.assertEqual(loaded.headphones_name, "")
        self.assertEqual(saved["port"], 8765)

    def test_unknown_keys_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(config, "CONFIG_PATH", Path(folder) / "config.json"):
            (Path(folder) / "config.json").write_text('{"tv_host": "10.0.0.5", "future_option": 1}', encoding="utf-8")
            self.assertEqual(config.Config.load().tv_host, "10.0.0.5")


if __name__ == "__main__":
    unittest.main()
