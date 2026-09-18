import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bot.services.providers import ytdlp


class RuntimeCookieTests(unittest.TestCase):
    def test_cookie_secret_is_copied_to_private_writable_temporary_file(self):
        contents = "# Netscape HTTP Cookie File\n.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tsecret\n"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "cookies.txt"
            source.write_text(contents)
            source.chmod(0o400)

            with patch.object(ytdlp, "YTDLP_COOKIES_FILE", str(source)):
                with ytdlp._runtime_cookie_file() as runtime_path:
                    self.assertNotEqual(runtime_path, str(source))
                    self.assertEqual(Path(runtime_path).read_text(), contents)
                    # Windows does not implement POSIX mode bits; the Docker/Linux
                    # deployment does and is covered by this assertion in CI.
                    if os.name != "nt":
                        self.assertEqual(
                            stat.S_IMODE(os.stat(runtime_path).st_mode),
                            0o600,
                        )
                    with ytdlp.yt_dlp.YoutubeDL(
                        {"cookiefile": runtime_path, "quiet": True}
                    ):
                        pass
                    created_path = runtime_path

            self.assertFalse(os.path.exists(created_path))
            self.assertEqual(source.read_text(), contents)

    def test_cookie_context_is_empty_when_not_configured(self):
        with patch.object(ytdlp, "YTDLP_COOKIES_FILE", None):
            with ytdlp._runtime_cookie_file() as runtime_path:
                self.assertIsNone(runtime_path)


if __name__ == "__main__":
    unittest.main()
