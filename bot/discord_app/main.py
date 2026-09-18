"""Independent Discord process using the transport-neutral media services."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

import discord

from bot.discord_app.config import DiscordSettings
from bot.observability import (
    configure_logging,
    log_context,
    log_media_failure,
    request_context,
)
from bot.services.converter import convert_to_gif
from bot.services.downloader import cleanup_files, download_media, extract_info

configure_logging()
logger = logging.getLogger(__name__)
URL_PATTERN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


class MediaActionView(discord.ui.View):
    """Per-message controls that can only be used by the initiating user."""

    def __init__(
        self,
        *,
        owner_id: int,
        url: str,
        info: dict[str, Any],
        settings: DiscordSettings,
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

        if item_count > 1:
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
            self._add_button("Animation (first 10s)", discord.ButtonStyle.secondary, self._convert_gif)
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
        await self._deliver(interaction, "gif")

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
    ) -> None:
        self._disable(keep_cancel=True)
        self.active_task = asyncio.current_task()
        await interaction.response.edit_message(content="Processing media…", view=self)
        files: list[str] = []
        with log_context(
            **request_context(interaction, self.url, platform="discord"),
            provider=self.info["_media"].get("provider"),
            download_stage="download_media",
            media_type=action,
        ):
            try:
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
                    duration = self.info["_media"].get("duration") or 10
                    animation = await convert_to_gif(
                        source_path, "0", str(min(float(duration), 10))
                    )
                    cleanup_files(files)
                    files = [animation] if animation else []
                if not files:
                    await self._notify(
                        interaction, "The media could not be downloaded."
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
            await interaction.message.edit(content=content, view=self)
        except discord.HTTPException:
            pass

    async def _notify(
        self, interaction: discord.Interaction, content: str
    ) -> None:
        try:
            await interaction.followup.send(content, ephemeral=True)
        except discord.NotFound:
            # Follow-up tokens expire; a DM keeps long-operation errors private.
            try:
                await interaction.user.send(content)
            except discord.HTTPException:
                pass

    async def _send_files(
        self, interaction: discord.Interaction, paths: list[str]
    ) -> bool:
        guild_limit = getattr(interaction.guild, "filesize_limit", None)
        byte_limit = guild_limit or self.settings.fallback_upload_bytes
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
        match = URL_PATTERN.search(message.content or "")
        if not match:
            return
        url = match.group(0).rstrip(".,);]")
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
        await status.edit(
            content=f"{result['type'].capitalize()} detected{suffix}. Choose an action:",
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
