import asyncio
import logging
import os
import re
import time
import urllib.parse
import uuid
from typing import Any, Awaitable

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)

from bot.config import DOWNLOADS_DIR
from bot.observability import log_context, log_media_failure, request_context
from bot.services.converter import convert_to_gif
from bot.services.downloader import (
    download_detected_photo,
    download_media,
    download_tiktok_photos,
    download_twitter_media,
    extract_info,
    get_gallery_count,
    is_gallery,
)
from bot.services.providers.common import file_media_type
from bot.services.providers.instagram import is_instagram_url

logger = logging.getLogger(__name__)

router = Router()


class BotStates(StatesGroup):
    waiting_for_gif_timestamps = State()        # GIF from a URL link
    waiting_for_video_timestamps = State()      # GIF from an uploaded video file
    waiting_for_gallery_selection = State()      # user picks which photos to download


CacheKey = tuple[int, int]
url_cache: dict[CacheKey, str] = {}
# Stores photo URLs when a gallery is detected (TikTok or Twitter)
gallery_cache: dict[CacheKey, list[str]] = {}
# Stores the source platform for gallery downloads ('tiktok' or 'twitter')
gallery_source_cache: dict[CacheKey, str] = {}
# Stores the resolved CDN URL so a photo click does not call the proxy twice.
photo_url_cache: dict[CacheKey, str] = {}
# Keeps only compact provider results needed by the eventual download action.
media_info_cache: dict[CacheKey, dict] = {}
cache_expiry: dict[CacheKey, float] = {}
CACHE_TTL_SECONDS = 15 * 60
MAX_CACHE_ENTRIES = 1000


async def _run_observed(
    message: Message | CallbackQuery,
    url: str | None,
    stage: str,
    media_type: str,
    operation: Awaitable[Any],
    *,
    empty_is_failure: bool = True,
) -> Any:
    """Run a media operation with correlated context and log empty results."""
    with log_context(
        **request_context(message, url),
        download_stage=stage,
        media_type=media_type,
    ):
        try:
            result = await operation
        except Exception as exc:
            log_media_failure(
                logger, stage=stage, error=exc, url=url, media_type=media_type
            )
            return None
        if empty_is_failure and not result:
            log_media_failure(
                logger,
                stage=stage,
                error="Operation returned no media files",
                url=url,
                media_type=media_type,
            )
        return result


def _report_failure(
    message: Message | CallbackQuery,
    url: str | None,
    stage: str,
    media_type: str,
    error: Exception | str,
) -> None:
    with log_context(**request_context(message, url)):
        log_media_failure(
            logger, stage=stage, error=error, url=url, media_type=media_type
        )


def _drop_cache(cache_key: CacheKey) -> None:
    url_cache.pop(cache_key, None)
    gallery_cache.pop(cache_key, None)
    gallery_source_cache.pop(cache_key, None)
    photo_url_cache.pop(cache_key, None)
    media_info_cache.pop(cache_key, None)
    cache_expiry.pop(cache_key, None)


def _purge_cache() -> None:
    now = time.monotonic()
    for cache_key, expires_at in list(cache_expiry.items()):
        if expires_at <= now:
            _drop_cache(cache_key)


def _compact_media_info(info: dict | None) -> dict:
    if not info:
        return {}
    reusable_keys = (
        "_media",
        "_twitter_media",
        "_instagram_media",
        "_reddit_media",
        "_download_url",
    )
    return {key: info[key] for key in reusable_keys if key in info}


def _store_cache(cache_key: CacheKey, value: str, info: dict | None = None) -> None:
    _purge_cache()
    if cache_key not in url_cache and len(url_cache) >= MAX_CACHE_ENTRIES:
        oldest_key = min(
            url_cache,
            key=lambda key: cache_expiry.get(key, float("-inf")),
        )
        _drop_cache(oldest_key)
    url_cache[cache_key] = value
    compact_info = _compact_media_info(info)
    media_info_cache.pop(cache_key, None)
    if compact_info:
        media_info_cache[cache_key] = compact_info
    cache_expiry[cache_key] = time.monotonic() + CACHE_TTL_SECONDS


def is_valid_url(text: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(text)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        return False


def parse_time_to_seconds(t: str) -> int:
    """Convert a timestamp like '1', '01:30', '1:05:30' to total seconds."""
    parts = t.split(':')
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError("invalid timestamp")
    parts = [int(p) for p in parts]
    if len(parts) > 1 and any(part >= 60 for part in parts[1:]):
        raise ValueError("timestamp component is out of range")
    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError("invalid timestamp")


def _cache_key(message: Message) -> CacheKey:
    return message.chat.id, message.message_id


# ──────────────────────────────────────────────────────────
# FSM state handlers  (MUST be registered BEFORE the
# catch-all handle_link so aiogram checks them first)
# ──────────────────────────────────────────────────────────

@router.message(BotStates.waiting_for_gif_timestamps)
async def process_gif_timestamps(message: Message, state: FSMContext):
    """User replied with timestamps for a URL → GIF conversion."""
    text = (message.text or "").strip()

    match = re.fullmatch(r"(\d+(?::\d+){0,2})\s*-\s*(\d+(?::\d+){0,2})", text)
    if not match:
        await message.answer(
            "Invalid format. Please use `START-END` (e.g. `00:15-00:25` or `1-6`).\n"
            "Try again or send /cancel."
        )
        return

    start_time, end_time = match.groups()

    # Validate that end > start
    try:
        start_sec = parse_time_to_seconds(start_time)
        end_sec = parse_time_to_seconds(end_time)
    except ValueError:
        await message.answer("Invalid timestamp. Use seconds, MM:SS, or HH:MM:SS.")
        return
    if end_sec <= start_sec:
        await message.answer("End time must be after start time. Try again.")
        return
    if end_sec - start_sec > 10:
        await message.answer(
            "⚠️ A longer animation may take more time, lose some quality, or exceed "
            "Telegram's file-size limit. I will still convert the full range."
        )

    # Read data BEFORE clearing state
    data = await state.get_data()
    url = data.get("url")
    media_info = data.get("media_info")
    await state.clear()

    if not url:
        await message.answer("Session expired. Please send the link again.")
        return

    status_msg = await message.answer("Downloading video for GIF conversion… ⏳")

    files = await _run_observed(
        message,
        url,
        "download_for_gif",
        "video",
        download_media(url, "video", media_info=media_info),
    )
    filepath = files[0] if files else None

    if not filepath:
        await status_msg.edit_text("Failed to download video for GIF conversion.")
        return

    await status_msg.edit_text("Converting to GIF… ⏳")
    gif_path = await _run_observed(
        message,
        url,
        "convert_to_gif",
        "gif",
        convert_to_gif(filepath, start_time, end_time),
    )

    if os.path.exists(filepath):
        os.remove(filepath)

    if not gif_path:
        await status_msg.edit_text("Failed to convert video to GIF.")
        return

    try:
        await message.answer_animation(FSInputFile(gif_path))
        await status_msg.edit_text("Done! ✅")
    except Exception as exc:
        _report_failure(message, url, "send_to_telegram", "gif", exc)
        await status_msg.edit_text("Failed to send GIF. It might be too large.")
    finally:
        if os.path.exists(gif_path):
            os.remove(gif_path)


@router.message(BotStates.waiting_for_video_timestamps)
async def process_video_timestamps(message: Message, state: FSMContext):
    """User replied with timestamps for an uploaded video → GIF conversion."""
    text = (message.text or "").strip()

    match = re.fullmatch(r"(\d+(?::\d+){0,2})\s*-\s*(\d+(?::\d+){0,2})", text)
    if not match:
        await message.answer(
            "Invalid format. Please use `START-END` (e.g. `00:15-00:25` or `1-6`).\n"
            "Try again or send /cancel."
        )
        return

    start_time, end_time = match.groups()

    try:
        start_sec = parse_time_to_seconds(start_time)
        end_sec = parse_time_to_seconds(end_time)
    except ValueError:
        await message.answer("Invalid timestamp. Use seconds, MM:SS, or HH:MM:SS.")
        return
    if end_sec <= start_sec:
        await message.answer("End time must be after start time. Try again.")
        return
    if end_sec - start_sec > 10:
        await message.answer(
            "⚠️ A longer animation may take more time, lose some quality, or exceed "
            "Telegram's file-size limit. I will still convert the full range."
        )

    data = await state.get_data()
    file_id = data.get("file_id")
    await state.clear()

    await _convert_uploaded_video(message, file_id, start_time, end_time)


@router.message(BotStates.waiting_for_gallery_selection)
async def process_gallery_selection(message: Message, state: FSMContext):
    """User replied with which photo numbers they want from a gallery."""
    text = (message.text or "").strip().lower()
    data = await state.get_data()
    url = data.get("url")
    total = data.get("gallery_count", 0)
    cache_id = tuple(data.get("gallery_cache_id", ()))
    _purge_cache()
    if cache_id not in url_cache:
        await state.clear()
        await message.answer("Session expired. Please send the link again.")
        return
    photo_urls = gallery_cache.get(cache_id, [])
    media_info = media_info_cache.get(cache_id)
    await state.clear()

    if not url:
        await message.answer("Session expired. Please send the link again.")
        return

    # Parse the user input:  "1,3" or "1-3" or "all"
    if text == "all":
        indices = None  # download everything
    else:
        parts = [p.strip() for p in text.replace(" ", ",").split(",") if p.strip()]
        nums = []
        invalid = False
        for part in parts:
            range_match = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
            if range_match:
                a, b = int(range_match.group(1)), int(range_match.group(2))
                if not 1 <= a <= b <= total:
                    invalid = True
                    break
                nums.extend(range(a, b + 1))
            elif part.isdigit():
                nums.append(int(part))
            else:
                invalid = True
        if invalid or not nums or any(n < 1 or n > total for n in nums):
            await message.answer(
                "Could not understand your selection.\n"
                "Send numbers like `1,3` or `1-3` or `all`."
            )
            # Re-enter the state so they can try again
            await state.update_data(url=url, gallery_count=total, gallery_cache_id=cache_id)
            await state.set_state(BotStates.waiting_for_gallery_selection)
            if cache_id:
                gallery_cache[cache_id] = photo_urls
            return
        indices = sorted(set(nums))

    status_msg = await message.answer("Downloading selected media… ⏳")

    # Use the appropriate downloader based on the cached source
    source = gallery_source_cache.get(cache_id, '') if cache_id else ''
    if photo_urls and source == 'twitter':
        files = await _run_observed(
            message, url, "download_gallery", "gallery",
            download_twitter_media(photo_urls, indices),
        )
    elif photo_urls:
        files = await _run_observed(
            message, url, "download_gallery", "gallery",
            download_tiktok_photos(photo_urls, indices),
        )
    else:
        playlist_items = ",".join(str(n) for n in indices) if indices else None
        files = await _run_observed(
            message,
            url,
            "download_gallery",
            "gallery",
            download_media(
                url,
                "gallery",
                playlist_items=playlist_items,
                media_info=media_info,
            ),
        )

    # Clean up gallery cache
    if cache_id:
        _drop_cache(cache_id)

    if not files:
        await status_msg.edit_text("Failed to download media.")
        return

    try:
        await _send_album(message, files)

        await status_msg.edit_text("Done! ✅")
    except Exception as e:
        _report_failure(message, url, "send_to_telegram", "gallery", e)
        await status_msg.edit_text("Failed to send some files.")
    finally:
        for f in files:
            if os.path.exists(f):
                os.remove(f)


# ──────────────────────────────────────────────────────────
# Catch-all handlers (links / uploaded videos)
# ──────────────────────────────────────────────────────────

@router.message(F.text, StateFilter(None), ~F.text.startswith('/'))
async def handle_link(message: Message, state: FSMContext):
    """User sent a text message that isn't a command — treat it as a URL."""
    text = message.text.strip()
    if not is_valid_url(text):
        await message.answer("Please send a valid HTTP/HTTPS URL.")
        return

    # A state-less FSM context may still contain data from the previous link.
    await state.clear()
    msg = await message.reply("Analyzing link… ⏳")

    info = await _run_observed(
        message, text, "extract_metadata", "unknown", extract_info(text)
    )

    if not info:
        if is_instagram_url(text):
            await msg.edit_text(
                "Instagram did not provide access to this post. Public posts can be "
                "downloaded directly; restricted or rate-limited posts require a "
                "YTDLP_COOKIES_FILE in the bot configuration."
            )
        else:
            await msg.edit_text("Could not analyze this link. It may be private or unsupported.")
        return

    # Shared photo/album controls reuse the ordered metadata during download.
    detected = info.get("_media")
    if detected and detected["type"] == "photo":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Download Photo", callback_data="dl_photo")],
            [InlineKeyboardButton(text="Cancel", callback_data="dl_cancel")],
        ])
        await msg.edit_text("Photo detected! Choose an action:", reply_markup=keyboard)
        cache_id = _cache_key(msg)
        _store_cache(cache_id, text, info)
        photo_url_cache[cache_id] = detected["items"][0]["url"]
        return
    if detected and len(detected["items"]) > 1:
        count = len(detected["items"])
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Download All Media", callback_data="dl_gallery_all")],
            [InlineKeyboardButton(text="Pick Specific Items", callback_data="dl_gallery_pick")],
            [InlineKeyboardButton(text="Cancel", callback_data="dl_cancel")],
        ])
        await msg.edit_text(f"Media album: {count} items. Choose an action:", reply_markup=keyboard)
        _store_cache(_cache_key(msg), text, info)
        return
    if info.get("entries") is not None and not detected:
        await msg.edit_text("Please send a link to a single post or video, rather than a playlist.")
        return

    twitter_media = info.get('_twitter_media') if info else None
    if twitter_media:
        tw_type = twitter_media.get('type')
        tw_urls = twitter_media.get('urls', [])
        # GIF — auto-download and send as animation
        if tw_type == 'gif' and tw_urls:
            await msg.edit_text("🎞 GIF detected! Downloading… ⏳")
            files = await _run_observed(
                message, text, "download_media", "gif", download_twitter_media(tw_urls)
            )
            filepath = files[0] if files else None
            if not filepath:
                await msg.edit_text("Failed to download GIF.")
                return
            try:
                await message.answer_animation(FSInputFile(filepath))
                await msg.edit_text("Done! ✅")
            except Exception as e:
                _report_failure(message, text, "send_to_telegram", "gif", e)
                await msg.edit_text("Failed to send GIF. It might be too large.")
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)
            return

        # Single photo — auto-download and send
        if tw_type == 'photo' and tw_urls:
            await msg.edit_text("📷 Photo detected! Downloading… ⏳")
            files = await _run_observed(
                message, text, "download_media", "photo", download_twitter_media(tw_urls)
            )
            filepath = files[0] if files else None
            if not filepath:
                await msg.edit_text("Failed to download photo.")
                return
            try:
                await _send_file(message, filepath)
                await msg.edit_text("Done! ✅")
            except Exception as e:
                _report_failure(message, text, "send_to_telegram", "photo", e)
                await msg.edit_text("Failed to send photo.")
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)
            return

        # Multiple photos — gallery selection flow
        if tw_type == 'photos' and tw_urls:
            count = len(tw_urls)
            cache_id = _cache_key(msg)
            gallery_cache[cache_id] = tw_urls
            gallery_source_cache[cache_id] = 'twitter'

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📸 Download All Photos", callback_data="dl_gallery_all")],
                [InlineKeyboardButton(text="🔢 Pick Specific Photos", callback_data="dl_gallery_pick")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")],
            ])
            await msg.edit_text(
                f"📷 Twitter photo gallery — **{count}** photos!\nChoose an action:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            _store_cache(cache_id, text)
            await state.update_data(gallery_count=count, gallery_cache_id=list(cache_id))
            return

        # Twitter video — show normal video menu
        if tw_type == 'video':
            duration = twitter_media.get('duration')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎥 Download Video", callback_data="dl_video")],
                [InlineKeyboardButton(text="🎵 Download Audio", callback_data="dl_audio")],
                [InlineKeyboardButton(text="🎞 Convert to GIF", callback_data="dl_gif")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")],
            ])
            await msg.edit_text("🐦 Twitter video detected! Choose an action:", reply_markup=keyboard)
            _store_cache(_cache_key(msg), text, info)
            if duration:
                await state.update_data(video_duration=duration)
            return

    # ── Instagram/Reddit photo ──
    detected_media = info.get('_instagram_media') or info.get('_reddit_media')
    if detected_media and detected_media.get('type') in {'photo', 'image'}:
        platform = info.get('extractor_key', 'Social media')
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📷 Download Photo", callback_data="dl_photo")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")],
        ])
        await msg.edit_text(
            f"📷 {platform} photo detected! Choose an action:",
            reply_markup=keyboard,
        )
        cache_id = _cache_key(msg)
        _store_cache(cache_id, text)
        photo_url_cache[cache_id] = detected_media['url']
        return

    if info and is_gallery(info):
        count = get_gallery_count(info)

        # Cache photo URLs for later download (TikTok)
        cache_id = _cache_key(msg)
        tiktok_photos = info.get('_tiktok_photos')
        if tiktok_photos:
            gallery_cache[cache_id] = tiktok_photos
            gallery_source_cache[cache_id] = 'tiktok'

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📸 Download All Photos", callback_data="dl_gallery_all")],
            [InlineKeyboardButton(text="🔢 Pick Specific Photos", callback_data="dl_gallery_pick")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")],
        ])
        await msg.edit_text(
            f"📷 Photo gallery detected — **{count}** photos!\nChoose an action:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        _store_cache(cache_id, text)
        await state.update_data(
            gallery_count=count,
            gallery_cache_id=list(cache_id),
        )
        return

    # Store video duration for later (GIF auto-convert for short videos)
    duration = info.get('duration') if info else None

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 Download Video", callback_data="dl_video")],
        [InlineKeyboardButton(text="🎵 Download Audio", callback_data="dl_audio")],
        [InlineKeyboardButton(text="🎞 Convert to GIF", callback_data="dl_gif")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")],
    ])
    await msg.edit_text("Link detected! Choose an action:", reply_markup=keyboard)
    _store_cache(_cache_key(msg), text, info)
    if duration:
        await state.update_data(video_duration=duration)


@router.message(F.video, StateFilter(None))
async def handle_video_upload(message: Message, state: FSMContext):
    """User uploaded a video file directly."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎞 Convert to GIF", callback_data="conv_gif")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")],
    ])
    msg = await message.reply("Video received! What would you like to do?", reply_markup=keyboard)
    _store_cache(_cache_key(msg), message.video.file_id)


# ──────────────────────────────────────────────────────────
# Callback handlers
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("conv_"))
async def handle_video_convert_callback(callback: CallbackQuery, state: FSMContext):
    """Callback for converting an uploaded video."""
    _purge_cache()
    cache_key = _cache_key(callback.message)
    file_id = url_cache.get(cache_key)

    if not file_id:
        await callback.answer("File expired. Please send the video again.", show_alert=True)
        return
    await callback.answer()

    video = callback.message.reply_to_message.video
    if video and video.duration and video.duration > 10:
        await state.update_data(file_id=file_id)
        await state.set_state(BotStates.waiting_for_video_timestamps)
        _drop_cache(cache_key)
        await callback.message.edit_text(
            f"The video is **{video.duration}s** long.\n"
            "Please reply with the time range for the GIF.\n"
            "Format: `START-END` (e.g. `00:15-00:25` or `1-6`).",
            parse_mode="Markdown",
        )
    else:
        # Short video — convert the whole thing
        await callback.message.edit_text("Converting entire video to GIF… ⏳")
        duration = str(video.duration) if video and video.duration else "10"
        await _convert_uploaded_video(callback.message, file_id, "0", duration)
        _drop_cache(cache_key)


@router.callback_query(F.data.startswith("dl_"))
async def handle_dl_callback(callback: CallbackQuery, state: FSMContext):
    """Callback for link-based actions (download video/audio/gif, gallery, cancel)."""
    _purge_cache()
    raw = callback.data
    parts = raw.split("_")
    action = parts[1]
    cache_key = _cache_key(callback.message)
    url = url_cache.get(cache_key)
    media_info = media_info_cache.get(cache_key)

    # ── Cancel ──
    if action == "cancel":
        await callback.answer()
        await callback.message.edit_text("Action cancelled.")
        _drop_cache(cache_key)
        await state.clear()
        return

    if not url:
        await callback.answer("Link expired. Please send it again.", show_alert=True)
        return
    await callback.answer()

    # ── Gallery ──
    if action == "gallery":
        sub_action = parts[2] if len(parts) > 2 else "all"
        cache_id = cache_key
        photo_urls = gallery_cache.get(cache_id, [])
        count = len((media_info or {}).get("_media", {}).get("items", [])) or len(photo_urls)

        if sub_action == "pick":
            await state.update_data(url=url, gallery_count=count, gallery_cache_id=list(cache_id))
            await state.set_state(BotStates.waiting_for_gallery_selection)
            await callback.message.edit_text(
                f"There are **{count}** media items.\n"
                "Reply with the numbers you want to download.\n"
                "Examples: `1,3` or `1-3` or `all`.",
                parse_mode="Markdown",
            )
            return

        # sub_action == "all" — download all photos
        await callback.message.edit_text("Downloading all media… ⏳")

        source = gallery_source_cache.get(cache_id, '') if cache_id else ''
        if photo_urls and source == 'twitter':
            files = await _run_observed(
                callback, url, "download_gallery", "gallery",
                download_twitter_media(photo_urls),
            )
        elif photo_urls:
            files = await _run_observed(
                callback, url, "download_gallery", "gallery",
                download_tiktok_photos(photo_urls),
            )
        else:
            files = await _run_observed(
                callback,
                url,
                "download_gallery",
                "gallery",
                download_media(url, "gallery", media_info=media_info),
            )

        # Clean up
        await state.clear()
        if cache_id:
            _drop_cache(cache_id)
        _drop_cache(cache_key)

        if not files:
            await callback.message.edit_text("Failed to download media.")
            return
        try:
            await _send_album(callback.message, files)
            await callback.message.edit_text("Done! ✅")
        except Exception as e:
            _report_failure(callback, url, "send_to_telegram", "gallery", e)
            await callback.message.edit_text("Failed to send some files.")
        finally:
            for f in files:
                if os.path.exists(f):
                    os.remove(f)
        return

    # ── GIF from link ──
    if action == "gif":
        data = await state.get_data()
        duration = data.get("video_duration")

        # Short video (≤10s) — convert the whole thing, no need to ask
        if duration and duration <= 10:
            await callback.message.edit_text(
                f"Video is only {duration}s — converting the full video to GIF… ⏳"
            )
            files = await _run_observed(
                callback,
                url,
                "download_for_gif",
                "video",
                download_media(url, "video", media_info=media_info),
            )
            filepath = files[0] if files else None
            if not filepath:
                await callback.message.edit_text("Failed to download video.")
                _drop_cache(cache_key)
                await state.clear()
                return
            gif_path = await _run_observed(
                callback,
                url,
                "convert_to_gif",
                "gif",
                convert_to_gif(filepath, "0", str(duration)),
            )
            if os.path.exists(filepath):
                os.remove(filepath)
            if not gif_path:
                await callback.message.edit_text("Failed to convert video to GIF.")
                _drop_cache(cache_key)
                await state.clear()
                return
            try:
                await callback.message.answer_animation(FSInputFile(gif_path))
                await callback.message.edit_text("Done! ✅")
            except Exception as exc:
                _report_failure(
                    callback, url, "send_to_telegram", "gif", exc
                )
                await callback.message.edit_text("Failed to send GIF. It might be too large.")
            finally:
                if os.path.exists(gif_path):
                    os.remove(gif_path)
                _drop_cache(cache_key)
                await state.clear()
            return

        # Longer video — ask for timestamps
        await state.update_data(url=url, media_info=media_info)
        await state.set_state(BotStates.waiting_for_gif_timestamps)
        _drop_cache(cache_key)
        duration_text = f"The video is **{duration}s** long.\n" if duration else ""
        await callback.message.edit_text(
            f"{duration_text}"
            "Reply with the time range for the GIF.\n"
            "Format: `START-END` (e.g. `00:15-00:25` or `1-6`).\n"
            "Longer ranges are allowed, but may produce larger files and lower quality.",
            parse_mode="Markdown",
        )
        return

    # ── Download photo ──
    if action == "photo":
        await callback.message.edit_text("Downloading photo… ⏳")
        files = await _run_observed(
            callback,
            url,
            "download_media",
            "photo",
            download_detected_photo(photo_url_cache.get(cache_key, "")),
        )
        filepath = files[0] if files else None

        if not filepath:
            await callback.message.edit_text("Failed to download photo.")
            _drop_cache(cache_key)
            await state.clear()
            return

        try:
            await _send_file(callback.message, filepath)
            await callback.message.edit_text("Done! ✅")
        except Exception as exc:
            _report_failure(callback, url, "send_to_telegram", "photo", exc)
            await callback.message.edit_text("Failed to send photo.")
        finally:
            if os.path.exists(filepath):
                os.remove(filepath)
            _drop_cache(cache_key)
            await state.clear()
        return

    # ── Download video / audio ──
    await callback.message.edit_text("Processing your request… ⏳")

    files = await _run_observed(
        callback,
        url,
        "download_media",
        action,
        download_media(url, action, media_info=media_info),
    )
    filepath = files[0] if files else None

    if not filepath:
        await callback.message.edit_text("Failed to download. It might be unsupported or too large.")
        _drop_cache(cache_key)
        await state.clear()
        return

    try:
        await _send_file(callback.message, filepath)
        await callback.message.edit_text("Done! ✅")
    except Exception as e:
        _report_failure(callback, url, "send_to_telegram", action, e)
        await callback.message.edit_text("Failed to send file. It might be over Telegram's 50MB limit.")
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)
        _drop_cache(cache_key)
        await state.clear()


# ──────────────────────────────────────────────────────────
# Helper: convert an uploaded video file to GIF
# ──────────────────────────────────────────────────────────

async def _convert_uploaded_video(
    message: Message, file_id: str, start_time: str, end_time: str
):
    """Downloads an uploaded video by file_id, converts a segment to GIF, sends it back."""
    status_msg = await message.answer("Downloading your video… ⏳")

    try:
        file = await message.bot.get_file(file_id)
    except Exception as exc:
        _report_failure(message, None, "retrieve_telegram_file", "video", exc)
        await status_msg.edit_text("Could not retrieve the video file. Please send it again.")
        return

    # Telegram file IDs are credentials for retrieving a file; never put them in
    # filenames where downstream tools may echo them into logs.
    input_path = os.path.join(DOWNLOADS_DIR, f"{uuid.uuid4()}.mp4")
    downloaded = await _run_observed(
        message,
        None,
        "download_telegram_file",
        "video",
        message.bot.download_file(file.file_path, destination=input_path),
        empty_is_failure=False,
    )
    if not downloaded and not os.path.exists(input_path):
        _report_failure(
            message,
            None,
            "download_telegram_file",
            "video",
            "Telegram download completed without creating a file",
        )
        await status_msg.edit_text("Failed to download the uploaded video.")
        return

    await status_msg.edit_text("Converting to GIF… ⏳")
    gif_path = await _run_observed(
        message,
        None,
        "convert_to_gif",
        "gif",
        convert_to_gif(input_path, start_time, end_time),
    )

    if os.path.exists(input_path):
        os.remove(input_path)

    if not gif_path:
        await status_msg.edit_text("Failed to convert video to GIF.")
        return

    try:
        await message.answer_animation(FSInputFile(gif_path))
        await status_msg.edit_text("Done! ✅")
    except Exception as exc:
        _report_failure(message, None, "send_to_telegram", "gif", exc)
        await status_msg.edit_text("Failed to send GIF. It might be too large.")
    finally:
        if os.path.exists(gif_path):
            os.remove(gif_path)


async def _send_file(message: Message, path: str):
    kind = await asyncio.to_thread(file_media_type, path)
    method = {"photo": message.answer_photo, "video": message.answer_video,
              "audio": message.answer_audio, "animation": message.answer_animation}.get(kind)
    if method is None:
        raise ValueError("Unrecognized media content")
    await method(FSInputFile(path))


async def _send_album(message: Message, files: list[str]):
    # Telegram albums require 2-10 photos/videos; send a remaining item alone.
    kinds = [await asyncio.to_thread(file_media_type, path) for path in files]
    if any(kind not in {"photo", "video"} for kind in kinds):
        raise ValueError("Album contains unrecognized media")
    for offset in range(0, len(files), 10):
        chunk = files[offset:offset + 10]
        if len(chunk) == 1:
            await _send_file(message, chunk[0])
        else:
            group = [
                (InputMediaPhoto if kind == "photo" else InputMediaVideo)(media=FSInputFile(path))
                for path, kind in zip(chunk, kinds[offset:offset + 10])
            ]
            await message.answer_media_group(group)
