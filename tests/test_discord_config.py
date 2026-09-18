import os
import unittest
from unittest.mock import patch

from bot.discord_app.config import DiscordSettings


class DiscordConfigTests(unittest.TestCase):
    def test_dms_and_allowlisted_guild_channels_are_explicit(self):
        environment = {
            "DISCORD_ALLOW_DMS": "false",
            "DISCORD_ALLOWED_CHANNEL_IDS": "10, 20",
            "DISCORD_FALLBACK_UPLOAD_MB": "12",
            "DISCORD_ATTACHMENTS_PER_MESSAGE": "5",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = DiscordSettings.from_env()

        self.assertFalse(settings.channel_allowed(1, is_dm=True))
        self.assertTrue(settings.channel_allowed(10, is_dm=False))
        self.assertFalse(settings.channel_allowed(30, is_dm=False))
        self.assertEqual(settings.fallback_upload_bytes, 12 * 1024 * 1024)
        self.assertEqual(settings.attachments_per_message, 5)

    def test_invalid_channel_allowlist_is_rejected(self):
        with patch.dict(
            os.environ,
            {"DISCORD_ALLOWED_CHANNEL_IDS": "not-an-id"},
            clear=True,
        ):
            with self.assertRaises(ValueError):
                DiscordSettings.from_env()


if __name__ == "__main__":
    unittest.main()
