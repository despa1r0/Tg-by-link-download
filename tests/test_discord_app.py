import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from bot.discord_app.config import DiscordSettings
from bot.discord_app.main import MediaActionView
from bot.services.media_model import MediaItem, media_result


def _settings() -> DiscordSettings:
    return DiscordSettings(
        token=None,
        allowed_channel_ids=frozenset(),
        allow_dms=True,
        fallback_upload_bytes=10 * 1024 * 1024,
        attachments_per_message=10,
    )


class DiscordViewTests(unittest.IsolatedAsyncioTestCase):
    @patch("bot.discord_app.main.download_media", new_callable=AsyncMock)
    async def test_download_button_finishes_instead_of_leaving_processing_status(self, download):
        url = "https://x.com/user/status/1"
        result = media_result([
            MediaItem("video", url, direct_url="https://video.twimg.com/one.mp4")
        ], source_url=url, provider="twitter")
        view = MediaActionView(
            owner_id=10,
            url=url,
            info={"_media": result},
            settings=_settings(),
        )
        view._send_files = AsyncMock(return_value=True)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=10),
            message=SimpleNamespace(edit=AsyncMock()),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )
        download.return_value = ["file.mp4"]

        with patch("bot.discord_app.main.cleanup_files"):
            await view._download_video(interaction)

        download.assert_awaited_once_with(
            url, "video", playlist_items=None, media_info={"_media": result}
        )
        view._send_files.assert_awaited_once_with(interaction, ["file.mp4"])
        self.assertEqual(interaction.message.edit.await_args.kwargs["content"], "Done.")

    @patch("bot.discord_app.main.download_media", new_callable=AsyncMock)
    async def test_download_failure_replaces_processing_status(self, download):
        url = "https://x.com/user/status/1"
        result = media_result([
            MediaItem("photo", url, direct_url="https://pbs.twimg.com/one.jpg")
        ], source_url=url, provider="twitter")
        view = MediaActionView(
            owner_id=10,
            url=url,
            info={"_media": result},
            settings=_settings(),
        )
        view._notify = AsyncMock()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=10),
            message=SimpleNamespace(edit=AsyncMock()),
            response=SimpleNamespace(edit_message=AsyncMock()),
        )
        download.side_effect = RuntimeError("download failed")

        await view._download_photo(interaction)

        view._notify.assert_awaited_once()
        self.assertEqual(
            interaction.message.edit.await_args.kwargs["content"], "Operation failed."
        )

    async def test_controls_are_restricted_to_initiator(self):
        result = media_result([
            MediaItem(
                "video",
                "https://x.com/user/status/1",
                direct_url="https://video.twimg.com/one.mp4",
            )
        ])
        view = MediaActionView(
            owner_id=10,
            url="https://x.com/user/status/1",
            info={"_media": result},
            settings=_settings(),
        )
        owner = SimpleNamespace(user=SimpleNamespace(id=10))
        stranger = SimpleNamespace(
            user=SimpleNamespace(id=20),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

        self.assertTrue(await view.interaction_check(owner))
        self.assertFalse(await view.interaction_check(stranger))
        stranger.response.send_message.assert_awaited_once_with(
            "Only the user who submitted this link can use these controls.",
            ephemeral=True,
        )

    async def test_album_select_values_follow_ui_order(self):
        result = media_result([
            MediaItem("photo", "https://post.test/1", direct_url=f"https://cdn.test/{i}.jpg")
            for i in range(3)
        ])
        view = MediaActionView(
            owner_id=10,
            url="https://post.test/1",
            info={"_media": result},
            settings=_settings(),
        )
        select = next(child for child in view.children if isinstance(child, discord.ui.Select))

        self.assertEqual([option.value for option in select.options], ["1", "2", "3"])

    async def test_cancel_stops_active_operation(self):
        result = media_result([
            MediaItem("video", "https://post.test/1", direct_url="https://cdn.test/1.mp4")
        ])
        view = MediaActionView(
            owner_id=10,
            url="https://post.test/1",
            info={"_media": result},
            settings=_settings(),
        )
        active = asyncio.create_task(asyncio.sleep(60))
        view.active_task = active
        interaction = SimpleNamespace(
            response=SimpleNamespace(edit_message=AsyncMock())
        )

        await view._cancel(interaction)
        await asyncio.sleep(0)

        self.assertTrue(active.cancelled())
        interaction.response.edit_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
