import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from bot.config import MAX_DOWNLOAD_BYTES
from bot.discord_app.config import DiscordSettings
from bot.discord_app.main import GifRangeModal, MediaActionView, create_client
from bot.services.converter import GifUploadLimitExceeded
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
    def _message(self, *, content="", attachments=(), snapshots=()):
        status = SimpleNamespace(edit=AsyncMock())
        message = SimpleNamespace(
            author=SimpleNamespace(bot=False, id=10),
            channel=SimpleNamespace(id=20),
            content=content,
            attachments=list(attachments),
            embeds=[],
            message_snapshots=list(snapshots),
            reply=AsyncMock(return_value=status),
        )
        return message, status

    @patch("bot.discord_app.main._is_dm", return_value=True)
    @patch("bot.discord_app.main.extract_info", new_callable=AsyncMock)
    async def test_forwarded_message_link_is_analyzed(self, extract, _is_dm):
        url = "https://x.com/user/status/1"
        result = media_result([MediaItem("video", url, direct_url=url)])
        extract.return_value = {"_media": result}
        snapshot = SimpleNamespace(content=url, attachments=[], embeds=[])
        message, status = self._message(snapshots=[snapshot])

        await create_client(_settings()).on_message(message)

        extract.assert_awaited_once_with(url)
        self.assertIsInstance(status.edit.await_args.kwargs["view"], MediaActionView)

    @patch("bot.discord_app.main._is_dm", return_value=True)
    @patch("bot.discord_app.main.extract_info", new_callable=AsyncMock)
    async def test_forwarded_link_takes_priority_over_outer_embed(self, extract, _is_dm):
        url = "https://x.com/user/status/2"
        extract.return_value = {
            "_media": media_result([MediaItem("video", url, direct_url=url)])
        }
        snapshot = SimpleNamespace(content=url, attachments=[], embeds=[])
        message, _status = self._message(snapshots=[snapshot])
        message.embeds = [SimpleNamespace(url="https://discord.com/channels/1/2/3")]

        await create_client(_settings()).on_message(message)

        extract.assert_awaited_once_with(url)

    @patch("bot.discord_app.main._is_dm", return_value=True)
    @patch("bot.discord_app.main.extract_info", new_callable=AsyncMock)
    async def test_forwarded_embed_link_is_analyzed(self, extract, _is_dm):
        url = "https://x.com/user/status/3"
        extract.return_value = {
            "_media": media_result([MediaItem("video", url, direct_url=url)])
        }
        snapshot = SimpleNamespace(
            content="", attachments=[], embeds=[SimpleNamespace(url=url)]
        )
        message, _status = self._message(snapshots=[snapshot])

        await create_client(_settings()).on_message(message)

        extract.assert_awaited_once_with(url)

    @patch("bot.discord_app.main._is_dm", return_value=True)
    @patch("bot.discord_app.main.extract_info", new_callable=AsyncMock)
    async def test_video_attachment_offers_gif_conversion(self, extract, _is_dm):
        attachment = SimpleNamespace(
            url="https://cdn.discordapp.com/attachments/1/2/clip.mp4",
            filename="clip.mp4",
            content_type="video/mp4",
            size=1024,
        )
        message, _status = self._message(attachments=[attachment])

        await create_client(_settings()).on_message(message)

        extract.assert_not_awaited()
        view = message.reply.await_args.kwargs["view"]
        self.assertIsInstance(view, MediaActionView)
        self.assertTrue(any("GIF" in child.label for child in view.children))

    @patch("bot.discord_app.main._is_dm", return_value=True)
    async def test_forwarded_video_attachment_offers_gif_conversion(self, _is_dm):
        attachment = SimpleNamespace(
            url="https://cdn.discordapp.com/attachments/1/2/clip.mov",
            filename="clip.mov",
            content_type=None,
            size=1024,
        )
        snapshot = SimpleNamespace(content="", attachments=[attachment], embeds=[])
        message, _status = self._message(snapshots=[snapshot])

        await create_client(_settings()).on_message(message)

        view = message.reply.await_args.kwargs["view"]
        self.assertTrue(any("GIF" in child.label for child in view.children))

    @patch("bot.discord_app.main._is_dm", return_value=True)
    async def test_oversized_video_attachment_is_rejected_before_download(self, _is_dm):
        attachment = SimpleNamespace(
            url="https://cdn.discordapp.com/attachments/1/2/clip.mp4",
            filename="clip.mp4",
            content_type="video/mp4",
            size=MAX_DOWNLOAD_BYTES + 1,
        )
        message, _status = self._message(attachments=[attachment])

        await create_client(_settings()).on_message(message)

        self.assertIn("too large", message.reply.await_args.args[0].lower())
        self.assertNotIn("view", message.reply.await_args.kwargs)

    @patch("bot.discord_app.main.convert_to_discord_gif", new_callable=AsyncMock)
    @patch("bot.discord_app.main.download_media", new_callable=AsyncMock)
    async def test_attachment_conversion_accepts_long_range_and_warns(self, download, convert):
        url = "https://cdn.discordapp.com/attachments/1/2/clip.mp4"
        result = media_result([MediaItem("video", url, direct_url=url)])
        view = MediaActionView(
            owner_id=10, url=url, info={"_media": result},
            settings=_settings(), attachment=True,
        )
        view._send_files = AsyncMock(return_value=True)
        view.message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=10),
            guild=None,
            response=SimpleNamespace(send_modal=AsyncMock(), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        download.return_value = ["clip.mp4"]
        convert.return_value = "clip.gif"

        with patch("bot.discord_app.main.cleanup_files"):
            await view._convert_gif(interaction)
            modal = interaction.response.send_modal.await_args.args[0]
            self.assertIsInstance(modal, GifRangeModal)
            modal.time_range._value = "00:15-00:40"
            await modal.on_submit(interaction)

        interaction.response.defer.assert_awaited_once_with()
        self.assertIn("longer animation", interaction.followup.send.await_args.args[0])
        convert.assert_awaited_once_with(
            "clip.mp4", "00:15", "00:40", max_output_bytes=10 * 1024 * 1024
        )
        view._send_files.assert_awaited_once_with(interaction, ["clip.gif"])
        self.assertEqual(view.message.edit.await_args.kwargs["content"], "Done.")

    @patch("bot.discord_app.main.convert_to_discord_gif", new_callable=AsyncMock)
    @patch("bot.discord_app.main.download_media", new_callable=AsyncMock)
    async def test_short_gif_range_uses_guild_limit_without_warning(self, download, convert):
        url = "https://cdn.discordapp.com/attachments/1/2/clip.mp4"
        result = media_result([MediaItem("video", url, direct_url=url)])
        view = MediaActionView(
            owner_id=10, url=url, info={"_media": result},
            settings=_settings(), attachment=True,
        )
        view.message = SimpleNamespace(edit=AsyncMock())
        view._send_files = AsyncMock(return_value=True)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=10),
            guild=SimpleNamespace(filesize_limit=25 * 1024 * 1024),
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        modal = GifRangeModal(view)
        modal.time_range._value = "1-9"
        download.return_value = ["clip.mp4"]
        convert.return_value = "clip.gif"

        with patch("bot.discord_app.main.cleanup_files"):
            await modal.on_submit(interaction)

        interaction.followup.send.assert_not_awaited()
        convert.assert_awaited_once_with(
            "clip.mp4", "1", "9", max_output_bytes=25 * 1024 * 1024
        )

    @patch("bot.discord_app.main.download_media", new_callable=AsyncMock)
    async def test_invalid_gif_range_does_not_download(self, download):
        url = "https://cdn.discordapp.com/attachments/1/2/clip.mp4"
        result = media_result([MediaItem("video", url, direct_url=url)])
        view = MediaActionView(
            owner_id=10, url=url, info={"_media": result},
            settings=_settings(), attachment=True,
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=10),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        modal = GifRangeModal(view)
        modal.time_range._value = "20-10"

        await modal.on_submit(interaction)

        self.assertIn("End time", interaction.response.send_message.await_args.args[0])
        download.assert_not_awaited()
        self.assertFalse(view.is_finished())

    @patch("bot.discord_app.main.convert_to_discord_gif", new_callable=AsyncMock)
    @patch("bot.discord_app.main.download_media", new_callable=AsyncMock)
    async def test_gif_over_upload_limit_reports_error(self, download, convert):
        url = "https://cdn.discordapp.com/attachments/1/2/clip.mp4"
        result = media_result([MediaItem("video", url, direct_url=url)])
        view = MediaActionView(
            owner_id=10, url=url, info={"_media": result},
            settings=_settings(), attachment=True,
        )
        view.message = SimpleNamespace(edit=AsyncMock())
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=10), guild=None,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        modal = GifRangeModal(view)
        modal.time_range._value = "0-20"
        download.return_value = ["clip.mp4"]
        convert.side_effect = GifUploadLimitExceeded

        with patch("bot.discord_app.main.cleanup_files"):
            await modal.on_submit(interaction)

        self.assertIn("10 MB", interaction.followup.send.await_args.args[0])
        self.assertEqual(view.message.edit.await_args.kwargs["content"], "Upload limit exceeded.")

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
