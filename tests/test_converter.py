import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ffmpeg

from bot.services.converter import (
    GifUploadLimitExceeded,
    _timestamp_to_seconds,
    convert_to_discord_gif,
    parse_gif_range,
)


class TimestampTests(unittest.TestCase):
    def test_telegram_style_gif_range(self):
        self.assertEqual(parse_gif_range(" 00:15 - 00:35 "), ("00:15", "00:35", 15, 35))
        for value in ("", "15", "15:60-16:00", "5-5", "10-2", "a-b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_gif_range(value)

    def test_supported_timestamp_formats(self):
        self.assertEqual(_timestamp_to_seconds("5"), 5)
        self.assertEqual(_timestamp_to_seconds("7.533"), 7.533)
        self.assertEqual(_timestamp_to_seconds("01:05"), 65)
        self.assertEqual(_timestamp_to_seconds("1:01:05"), 3665)

    def test_invalid_timestamp_components(self):
        for value in ("", ":", "1::2", "01:60", "a:10", "-1", "nan", "inf"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _timestamp_to_seconds(value)


class DiscordGifTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg executable is unavailable")
    async def test_real_video_produces_gif(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "clip.mp4")
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc=size=64x64:rate=10", "-t", "1", "-c:v", "mpeg4",
                    "-y", source,
                ],
                check=True,
                capture_output=True,
                timeout=20,
            )
            with patch("bot.services.converter.DOWNLOADS_DIR", directory):
                result = await convert_to_discord_gif(source, "0", "1")

            self.assertIsNotNone(result)
            self.assertTrue(Path(result).read_bytes().startswith((b"GIF87a", b"GIF89a")))

    async def test_conversion_builds_a_palette_gif(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "clip.mp4")
            Path(source).write_bytes(b"video fixture")
            commands = []

            def fake_run_async(stream, *_args, **_kwargs):
                command = ffmpeg.compile(stream)
                commands.append(" ".join(command))
                output_path = next(part for part in command if part.endswith(".gif"))
                Path(output_path).write_bytes(b"GIF89a")
                process = Mock()
                process.communicate.return_value = (b"", b"")
                process.poll.return_value = 0
                return process

            with patch("bot.services.converter.DOWNLOADS_DIR", directory), patch(
                "ffmpeg._run.run_async", side_effect=fake_run_async
            ):
                result = await convert_to_discord_gif(source, "0", "10")

            self.assertIsNotNone(result)
            self.assertEqual(Path(result).suffix, ".gif")
            self.assertEqual(Path(result).read_bytes(), b"GIF89a")
            self.assertIn("palettegen", commands[0])
            self.assertIn("paletteuse", commands[0])

    async def test_conversion_allows_more_than_ten_seconds(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "clip.mp4")
            Path(source).write_bytes(b"video fixture")
            commands = []

            def fake_run_async(stream, *_args, **_kwargs):
                command = ffmpeg.compile(stream)
                commands.append(command)
                output_path = next(part for part in command if part.endswith(".gif"))
                Path(output_path).write_bytes(b"GIF89a")
                process = Mock()
                process.communicate.return_value = (b"", b"")
                process.poll.return_value = 0
                return process

            with patch("bot.services.converter.DOWNLOADS_DIR", directory), patch(
                "ffmpeg._run.run_async", side_effect=fake_run_async
            ):
                result = await convert_to_discord_gif(source, "0", "25", max_output_bytes=100)

            self.assertIsNotNone(result)
            self.assertIn("-fs", commands[0])
            self.assertIn("100", commands[0])

    async def test_oversized_gif_is_reported_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, "clip.mp4")
            Path(source).write_bytes(b"video fixture")

            def fake_run_async(stream, *_args, **_kwargs):
                output_path = next(part for part in ffmpeg.compile(stream) if part.endswith(".gif"))
                Path(output_path).write_bytes(b"GIF89a" + b"x" * 100)
                process = Mock()
                process.communicate.return_value = (b"", b"")
                process.poll.return_value = 0
                return process

            with patch("bot.services.converter.DOWNLOADS_DIR", directory), patch(
                "ffmpeg._run.run_async", side_effect=fake_run_async
            ):
                with self.assertRaises(GifUploadLimitExceeded):
                    await convert_to_discord_gif(source, "0", "25", max_output_bytes=100)

            self.assertEqual(list(Path(directory).glob("*.gif")), [])


if __name__ == "__main__":
    unittest.main()
