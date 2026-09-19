"""Independent Discord process using the transport-neutral media services."""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from pathlib import Path
from typing import Any

import discord

from bot.config import MAX_DOWNLOAD_BYTES
from bot.discord_app.config import DiscordSettings
from bot.observability import (
    configure_logging,
    log_context,
    log_media_failure,
    request_context,
)
from bot.services.converter import (
    GifUploadLimitExceeded,
    convert_to_discord_gif,
    parse_gif_times,
)
from bot.services.downloader import cleanup_files, download_media, extract_info
from bot.services.media_model import MediaItem, media_result

configure_logging()
logger = logging.getLogger(__name__)
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
VIDEO_SUFFIXES = frozenset({".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"})


def _known_video_duration(result: Any) -> float | None:
    try:
        duration = float(result.get("duration"))
    except (TypeError, ValueError):
        return None
    return duration if math.isfinite(duration) and 0 < duration < 1_000_000_000_000 else None


def _format_video_duration(duration: float) -> str:
    milliseconds = math.floor(duration * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, fraction = divmod(remainder, 1000)
    formatted = (
        f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        if hours else f"{minutes:02d}:{seconds:02d}"
    )
    return f"{formatted}.{fraction:03d}".rstrip("0") if fraction else formatted


def _is_video_attachment(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    return (
        content_type.startswith("video/")
        or Path(attachment.filename).suffix.lower() in VIDEO_SUFFIXES
    )


def _incoming_media(message: discord.Message) -> tuple[str | None, discord.Attachment | None]:
    """Look through the sent message and Discord's immutable forward snapshots."""
    sources = (message, *getattr(message, "message_snapshots", ()))
    for source in sources:
        match = URL_PATTERN.search(getattr(source, "content", "") or "")
        if match:
            return match.group(0).rstrip(".,);]"), None
        for attachment in getattr(source, "attachments", ()):
            if _is_video_attachment(attachment):
                return None, attachment
    for source in sources:
        for embed in getattr(source, "embeds", ()):
            match = URL_PATTERN.search(getattr(embed, "url", "") or "")
            if match:
                return match.group(0).rstrip(".,);]"), None
    return None, None


class MediaActionView(discord.ui.View):
    """Per-message controls that can only be used by the initiating user."""

    def __init__(
        self,
        *,
        owner_id: int,
        url: str,
        info: dict[str, Any],
        settings: DiscordSettings,
        attachment: bool = False,
    ) -> None:
        super().__init__(timeout=15 * 60)
        self.owner_id = owner_id
        self.url = url
        self.info = info
        self.settings = settings
        self.message: discord.Message | None = None
        self.active_task: asyncio.Task | None = None
        result = info["_media"]
        item_count = len(result["items"])

        if attachment:
            self._add_button("Convert to GIF", discord.ButtonStyle.primary, self._convert_gif)
        elif item_count > 1:
            self._add_button("Download all", discord.ButtonStyle.primary, self._download_all)
            # Discord selects allow 25 options and a view has five component rows.
            # Four ordered selects plus the button row cover 100 children without
            # conflating source positions with UI positions.
            for chunk_start in range(0, min(item_count, 100), 25):
                options = [
                    discord.SelectOption(
                        label=f"Item {ui_index + 1}",
                        value=str(ui_index + 1),
                        description=result["items"][ui_index]["type"],
                    )
                    for ui_index in range(chunk_start, min(chunk_start + 25, item_count))
                ]
                select = discord.ui.Select(
                    placeholder=(
                        f"Choose items {chunk_start + 1}–{chunk_start + len(options)}"
                    ),
                    min_values=1,
                    max_values=len(options),
                    options=options,
                    row=1 + chunk_start // 25,
                )

                async def selected(
                    interaction: discord.Interaction,
                    control: discord.ui.Select = select,
                ) -> None:
                    await self._deliver(
                        interaction,
                        "gallery",
                        indices=[int(value) for value in control.values],
                    )

                select.callback = selected
                self.add_item(select)
        elif result["type"] == "photo":
            self._add_button("Download photo", discord.ButtonStyle.primary, self._download_photo)
        elif result["type"] == "gif":
            self._add_button("Download animation", discord.ButtonStyle.primary, self._download_video)
            self._add_button("Extract audio", discord.ButtonStyle.secondary, self._download_audio)
        else:
            self._add_button("Download video", discord.ButtonStyle.primary, self._download_video)
            self._add_button("Extract audio", discord.ButtonStyle.secondary, self._download_audio)
            self._add_button("Convert to GIF", discord.ButtonStyle.secondary, self._convert_gif)
        self.cancel_button = self._add_button(
            "Cancel", discord.ButtonStyle.danger, self._cancel
        )

    def _add_button(self, label, style, callback) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=style, row=0)
        button.callback = callback
        self.add_item(button)
        return button

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the user who submitted this link can use these controls.",
            ephemeral=True,
        )
        return False

    async def on_timeout(self) -> None:
        self._disable()
        if self.message:
            try:
                await self.message.edit(content="Media controls expired.", view=self)
            except discord.HTTPException:
                pass

    def _disable(self, *, keep_cancel: bool = False) -> None:
        for child in self.children:
            child.disabled = not (keep_cancel and child is self.cancel_button)

    async def _download_all(self, interaction: discord.Interaction) -> None:
        await self._deliver(interaction, "gallery")

    async def _download_photo(self, interaction: discord.Interaction) -> None:
        await self._deliver(interaction, "photo")

    async def _download_video(self, interaction: discord.Interaction) -> None:
        await self._deliver(interaction, "video")

    async def _download_audio(self, interaction: discord.Interaction) -> None:
        await self._deliver(interaction, "audio")

    async def _convert_gif(self, interaction: discord.Interaction) -> None:
        if self.is_finished() or self.active_task:
            await interaction.response.send_message(
                "This operation is already running or has expired.", ephemeral=True
            )
            return
        await interaction.response.send_modal(GifRangeModal(self))

    async def _cancel(self, interaction: discord.Interaction) -> None:
        task = self.active_task
        if task and task is not asyncio.current_task() and not task.done():
            task.cancel()
        self._disable()
        self.stop()
        await interaction.response.edit_message(content="Operation cancelled.", view=self)

    async def _deliver(
        self,
        interaction: discord.Interaction,
        action: str,
        indices: list[int] | None = None,
        *,
        start_time: str | None = None,
        end_time: str | None = None,
        acknowledged: bool = False,
    ) -> None:
        if self.is_finished() or self.active_task:
            message = "This operation is already running or has expired."
            if acknowledged:
                await self._notify(interaction, message)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return
        files: list[str] = []
        context = request_context(interaction, self.url, platform="discord")
        context["provider"] = self.info["_media"].get("provider") or context["provider"]
        with log_context(
            **context,
            download_stage="download_media",
            media_type=action,
        ):
            try:
                self._disable(keep_cancel=True)
                self.active_task = asyncio.current_task()
                if acknowledged:
                    await self.message.edit(content="Processing media…", view=self)
                else:
                    await interaction.response.edit_message(content="Processing media…", view=self)
                selection = ",".join(str(index) for index in indices) if indices else None
                download_action = "video" if action == "gif" else action
                files = await download_media(
                    self.url,
                    download_action,
                    playlist_items=selection,
                    media_info=self.info,
                )
                if action == "gif" and files:
                    source_path = files[0]
                    animation = await convert_to_discord_gif(
                        source_path,
                        start_time,
                        end_time,
                        max_output_bytes=self._upload_limit(interaction),
                    )
                    cleanup_files(files)
                    files = [animation] if animation else []
                if not files:
                    await self._notify(
                        interaction,
                        "The GIF could not be created. Try a smaller video."
                        if action == "gif"
                        else "The media could not be downloaded.",
                    )
                    await self._finish(interaction, "Download failed.")
                    return
                sent = await self._send_files(interaction, files)
                await self._finish(
                    interaction, "Done." if sent else "Upload limit exceeded."
                )
            except asyncio.CancelledError:
                # The cancel interaction has already updated the shared message.
                return
            except GifUploadLimitExceeded:
                limit_mb = self._upload_limit(interaction) / (1024 * 1024)
                await self._notify(
                    interaction,
                    f"The GIF exceeds this channel's upload limit ({limit_mb:g} MB). Try a shorter range.",
                )
                await self._finish(interaction, "Upload limit exceeded.")
            except Exception as exc:
                log_media_failure(
                    logger,
                    stage="discord_delivery",
                    error=exc,
                    url=self.url,
                    media_type=action,
                )
                await self._notify(
                    interaction, "The operation failed. Please try the link again."
                )
                await self._finish(interaction, "Operation failed.")
            finally:
                cleanup_files(files)
                if self.active_task is asyncio.current_task():
                    self.active_task = None

    async def _finish(
        self, interaction: discord.Interaction, content: str
    ) -> None:
        if self.is_finished():
            return
        self._disable()
        self.stop()
        try:
            message = self.message or interaction.message
            await message.edit(content=content, view=self)
        except discord.HTTPException:
            pass

    async def _notify(
        self, interaction: discord.Interaction, content: str
    ) -> None:
        try:
            await interaction.followup.send(content, ephemeral=True)
        except discord.HTTPException:
            # Follow-up tokens expire; a DM keeps long-operation errors private.
            try:
                await interaction.user.send(content)
            except discord.HTTPException:
                pass

    async def _send_files(
        self, interaction: discord.Interaction, paths: list[str]
    ) -> bool:
        byte_limit = self._upload_limit(interaction)
        oversized = [path for path in paths if os.path.getsize(path) > byte_limit]
        if oversized:
            limit_mb = byte_limit / (1024 * 1024)
            await self._notify(
                interaction,
                f"A result exceeds this channel's upload limit ({limit_mb:g} MB).",
            )
            return False

        chunk_size = self.settings.attachments_per_message
        for start in range(0, len(paths), chunk_size):
            chunk = paths[start : start + chunk_size]
            try:
                attachments = [
                    discord.File(path, filename=Path(path).name) for path in chunk
                ]
                await interaction.followup.send(files=attachments)
            except discord.NotFound:
                # An acknowledged interaction token can expire during a slow
                # provider download. Channel sends do not depend on that token.
                attachments = [
                    discord.File(path, filename=Path(path).name) for path in chunk
                ]
                await interaction.channel.send(files=attachments)
        return True

    def _upload_limit(self, interaction: discord.Interaction) -> int:
        guild_limit = getattr(getattr(interaction, "guild", None), "filesize_limit", None)
        return guild_limit or self.settings.fallback_upload_bytes


class GifRangeModal(discord.ui.Modal, title="Convert video to GIF"):
    def __init__(self, view: MediaActionView) -> None:
        super().__init__()
        self.media_view = view
        duration = _known_video_duration(view.info["_media"])
        if duration is not None:
            self.title = f"Convert to GIF (video {_format_video_duration(duration)})"
        else:
            self.title = "Convert to GIF (duration unknown)"
        self.start_time = discord.ui.TextInput(
            label="Start time",
            placeholder="Seconds, MM:SS, or HH:MM:SS",
            default="0",
            max_length=32,
        )
        self.end_time = discord.ui.TextInput(
            label="End time",
            placeholder="Seconds, MM:SS, or HH:MM:SS",
            default=str(min(10, math.ceil(duration))) if duration is not None else "10",
            max_length=32,
        )
        self.add_item(self.start_time)
        self.add_item(self.end_time)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.media_view.owner_id:
            await interaction.response.send_message(
                "Only the user who submitted this link can use these controls.", ephemeral=True
            )
            return
        if self.media_view.is_finished() or self.media_view.active_task:
            await interaction.response.send_message(
                "This operation is already running or has expired.", ephemeral=True
            )
            return
        try:
            start_time = self.start_time.value.strip()
            end_time = self.end_time.value.strip()
            start_seconds, end_seconds = parse_gif_times(
                start_time, end_time
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        duration = _known_video_duration(self.media_view.info["_media"])
        if duration is not None and start_seconds >= duration:
            await interaction.response.send_message(
                f"Start time must be before the video ends ({_format_video_duration(duration)}).",
                ephemeral=True,
            )
            return
        if duration is not None and end_seconds > math.ceil(duration):
            await interaction.response.send_message(
                f"End time exceeds the video duration ({_format_video_duration(duration)}).",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        if end_seconds - start_seconds > 10:
            await self.media_view._notify(
                interaction,
                "⚠️ A longer animation may take more time, lose some quality, or exceed "
                "Discord's file-size limit. I will still convert the full range.",
            )
        await self.media_view._deliver(
            interaction,
            "gif",
            start_time=start_time,
            end_time=end_time,
            acknowledged=True,
        )


def _is_dm(channel: discord.abc.Messageable) -> bool:
    return isinstance(channel, discord.DMChannel)


def create_client(settings: DiscordSettings) -> discord.Client:
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        logger.info(
            "Discord bot connected",
            extra={"event": "bot_started", "platform": "discord"},
        )

    @client.event
    async def on_message(message: discord.Message) -> None:
        if message.author.bot:
            return
        if not settings.channel_allowed(message.channel.id, is_dm=_is_dm(message.channel)):
            return
        url, attachment = _incoming_media(message)
        if attachment is not None:
            if attachment.size > MAX_DOWNLOAD_BYTES:
                limit_mb = MAX_DOWNLOAD_BYTES / (1024 * 1024)
                await message.reply(
                    f"Video is too large (maximum {limit_mb:g} MB).",
                    mention_author=False,
                )
                return
            url = attachment.url
            result = media_result(
                [
                    MediaItem(
                        "video",
                        url,
                        direct_url=url,
                        duration=getattr(attachment, "duration", None),
                    )
                ],
                source_url=url,
                provider="discord_attachment",
            )
            view = MediaActionView(
                owner_id=message.author.id,
                url=url,
                info={"_media": result},
                settings=settings,
                attachment=True,
            )
            duration = _known_video_duration(result)
            duration_note = (
                f" Duration: {_format_video_duration(duration)}."
                if duration is not None else " Duration unavailable."
            )
            view.message = await message.reply(
                f"Video attached.{duration_note} Choose an action:",
                mention_author=False,
                view=view,
            )
            return
        if url is None:
            return
        status = await message.reply("Analyzing link…", mention_author=False)
        with log_context(**request_context(message, url, platform="discord")):
            try:
                info = await extract_info(url)
            except Exception as exc:
                log_media_failure(
                    logger, stage="extract_metadata", error=exc, url=url
                )
                info = None
        if not info or not info.get("_media"):
            await status.edit(content="This link is private, unsupported, or unavailable.")
            return

        result = info["_media"]
        view = MediaActionView(
            owner_id=message.author.id,
            url=url,
            info=info,
            settings=settings,
        )
        count = len(result["items"])
        suffix = (
            f" ({count} items; selection is available for the first 100)"
            if count > 100
            else f" ({count} items)" if count > 1 else ""
        )
        duration = _known_video_duration(result) if result["type"] == "video" else None
        duration_note = ""
        if result["type"] == "video":
            duration_note = (
                f" Duration: {_format_video_duration(duration)}."
                if duration is not None else " Duration unavailable."
            )
        await status.edit(
            content=f"{result['type'].capitalize()} detected{suffix}.{duration_note} Choose an action:",
            view=view,
        )
        view.message = status

    return client


async def main() -> None:
    try:
        settings = DiscordSettings.from_env()
    except ValueError as exc:
        logger.error(
            "Invalid Discord configuration",
            extra={
                "event": "configuration_error",
                "platform": "discord",
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        )
        return
    if not settings.token:
        logger.error(
            "DISCORD_BOT_TOKEN is not configured",
            extra={
                "event": "configuration_error",
                "platform": "discord",
                "error_type": "MissingBotToken",
            },
        )
        return
    client = create_client(settings)
    await client.start(settings.token)


if __name__ == "__main__":
    asyncio.run(main())
