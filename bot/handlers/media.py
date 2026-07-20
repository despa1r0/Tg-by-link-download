import os
import re
import logging
from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    FSInputFile, InputMediaPhoto, InputMediaVideo,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter

from bot.services.downloader import (
    download_media, extract_info, is_gallery, get_gallery_count,
    download_tiktok_photos, download_twitter_media,
)
from bot.services.converter import convert_to_gif
from bot.config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

router = Router()


class BotStates(StatesGroup):
    waiting_for_gif_timestamps = State()        # GIF from a URL link
    waiting_for_video_timestamps = State()      # GIF from an uploaded video file
    waiting_for_gallery_selection = State()      # user picks which photos to download


url_cache: dict[int, str] = {}
# Stores photo URLs when a gallery is detected (TikTok or Twitter)
gallery_cache: dict[int, list[str]] = {}
# Stores the source platform for gallery downloads ('tiktok' or 'twitter')
gallery_source_cache: dict[int, str] = {}


def is_valid_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


def parse_time_to_seconds(t: str) -> int:
    """Convert a timestamp like '1', '01:30', '1:05:30' to total seconds."""
    parts = t.split(':')
    parts = [int(p) for p in parts]
    if len(parts) == 1:
        return parts[0]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


# ──────────────────────────────────────────────────────────
# FSM state handlers  (MUST be registered BEFORE the
# catch-all handle_link so aiogram checks them first)
# ──────────────────────────────────────────────────────────

@router.message(BotStates.waiting_for_gif_timestamps)
async def process_gif_timestamps(message: Message, state: FSMContext):
    """User replied with timestamps for a URL → GIF conversion."""
    text = (message.text or "").strip()

    match = re.match(r"^([\d:]+)\s*-\s*([\d:]+)$", text)
    if not match:
        await message.answer(
            "Invalid format. Please use `START-END` (e.g. `00:15-00:25` or `1-6`).\n"
            "Try again or send /cancel."
        )
        return

    start_time, end_time = match.groups()

    # Validate that end > start
    start_sec = parse_time_to_seconds(start_time)
    end_sec = parse_time_to_seconds(end_time)
    if end_sec <= start_sec:
        await message.answer("End time must be after start time. Try again.")
        return

    # Read data BEFORE clearing state
    data = await state.get_data()
    url = data.get("url")
    await state.clear()

    if not url:
        await message.answer("Session expired. Please send the link again.")
        return

    status_msg = await message.answer("Downloading video for GIF conversion… ⏳")

    files = await download_media(url, "video")
    filepath = files[0] if files else None

    if not filepath:
        await status_msg.edit_text("Failed to download video for GIF conversion.")
        return

    await status_msg.edit_text("Converting to GIF… ⏳")
    gif_path = await convert_to_gif(filepath, start_time, end_time)

    if os.path.exists(filepath):
        os.remove(filepath)

    if not gif_path:
        await status_msg.edit_text("Failed to convert video to GIF.")
        return

    try:
        await message.answer_animation(FSInputFile(gif_path))
        await status_msg.edit_text("Done! ✅")
    except Exception:
        await status_msg.edit_text("Failed to send GIF. It might be too large.")
    finally:
        if os.path.exists(gif_path):
            os.remove(gif_path)


@router.message(BotStates.waiting_for_video_timestamps)
async def process_video_timestamps(message: Message, state: FSMContext):
    """User replied with timestamps for an uploaded video → GIF conversion."""
    text = (message.text or "").strip()

    match = re.match(r"^([\d:]+)\s*-\s*([\d:]+)$", text)
    if not match:
        await message.answer(
            "Invalid format. Please use `START-END` (e.g. `00:15-00:25` or `1-6`).\n"
            "Try again or send /cancel."
        )
        return

    start_time, end_time = match.groups()

    start_sec = parse_time_to_seconds(start_time)
    end_sec = parse_time_to_seconds(end_time)
    if end_sec <= start_sec:
        await message.answer("End time must be after start time. Try again.")
        return

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
    cache_id = data.get("gallery_cache_id")
    photo_urls = gallery_cache.get(cache_id, []) if cache_id else []
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
        for part in parts:
            range_match = re.match(r"^(\d+)\s*-\s*(\d+)$", part)
            if range_match:
                a, b = int(range_match.group(1)), int(range_match.group(2))
                nums.extend(range(a, b + 1))
            elif part.isdigit():
                nums.append(int(part))
        if not nums:
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

    status_msg = await message.answer("Downloading selected photos… ⏳")

    # Use the appropriate downloader based on the cached source
    source = gallery_source_cache.get(cache_id, '') if cache_id else ''
    if photo_urls and source == 'twitter':
        files = await download_twitter_media(photo_urls, indices)
    elif photo_urls:
        files = await download_tiktok_photos(photo_urls, indices)
    else:
        playlist_items = ",".join(str(n) for n in indices) if indices else None
        files = await download_media(url, "gallery", playlist_items=playlist_items)

    # Clean up gallery cache
    if cache_id and cache_id in gallery_cache:
        del gallery_cache[cache_id]
    if cache_id and cache_id in gallery_source_cache:
        del gallery_source_cache[cache_id]

    if not files:
        await status_msg.edit_text("Failed to download photos.")
        return

    try:
        media_group = []
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                media_group.append(InputMediaPhoto(media=FSInputFile(f)))
            else:
                media_group.append(InputMediaVideo(media=FSInputFile(f)))

        if media_group:
            for i in range(0, len(media_group), 10):
                await message.answer_media_group(media_group[i:i + 10])

        await status_msg.edit_text("Done! ✅")
    except Exception as e:
        logger.error("Failed to send gallery: %s", e)
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

    msg = await message.reply("Analyzing link… ⏳")

    info = await extract_info(text)

    # ── Twitter/X media (GIF, photo, photos, video) ──
    twitter_media = info.get('_twitter_media') if info else None
    if twitter_media:
        tw_type = twitter_media.get('type')
        tw_urls = twitter_media.get('urls', [])
        tw_title = twitter_media.get('title', '')

        # GIF — auto-download and send as animation
        if tw_type == 'gif' and tw_urls:
            await msg.edit_text("🎞 GIF detected! Downloading… ⏳")
            files = await download_twitter_media(tw_urls)
            filepath = files[0] if files else None
            if not filepath:
                await msg.edit_text("Failed to download GIF.")
                return
            try:
                await message.answer_animation(FSInputFile(filepath))
                await msg.edit_text("Done! ✅")
            except Exception as e:
                logger.error("Failed to send Twitter GIF: %s", e)
                await msg.edit_text("Failed to send GIF. It might be too large.")
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)
            return

        # Single photo — auto-download and send
        if tw_type == 'photo' and tw_urls:
            await msg.edit_text("📷 Photo detected! Downloading… ⏳")
            files = await download_twitter_media(tw_urls)
            filepath = files[0] if files else None
            if not filepath:
                await msg.edit_text("Failed to download photo.")
                return
            try:
                await message.answer_photo(FSInputFile(filepath))
                await msg.edit_text("Done! ✅")
            except Exception as e:
                logger.error("Failed to send Twitter photo: %s", e)
                await msg.edit_text("Failed to send photo.")
            finally:
                if os.path.exists(filepath):
                    os.remove(filepath)
            return

        # Multiple photos — gallery selection flow
        if tw_type == 'photos' and tw_urls:
            count = len(tw_urls)
            cache_id = msg.message_id
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
            url_cache[msg.message_id] = text
            await state.update_data(gallery_count=count, gallery_cache_id=cache_id)
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
            url_cache[msg.message_id] = text
            if duration:
                await state.update_data(video_duration=duration)
            return

    if info and is_gallery(info):
        count = get_gallery_count(info)

        # Cache photo URLs for later download (TikTok)
        cache_id = None
        tiktok_photos = info.get('_tiktok_photos')
        if tiktok_photos:
            cache_id = msg.message_id
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
        url_cache[msg.message_id] = text
        await state.update_data(gallery_count=count, gallery_cache_id=cache_id)
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
    url_cache[msg.message_id] = text
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
    url_cache[msg.message_id] = message.video.file_id


# ──────────────────────────────────────────────────────────
# Callback handlers
# ──────────────────────────────────────────────────────────

@router.callback_query(F.data.startswith("conv_"))
async def handle_video_convert_callback(callback: CallbackQuery, state: FSMContext):
    """Callback for converting an uploaded video."""
    message_id = callback.message.message_id
    file_id = url_cache.get(message_id)

    if not file_id:
        await callback.answer("File expired. Please send the video again.", show_alert=True)
        return

    video = callback.message.reply_to_message.video
    if video and video.duration and video.duration > 10:
        await state.update_data(file_id=file_id)
        await state.set_state(BotStates.waiting_for_video_timestamps)
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


@router.callback_query(F.data.startswith("dl_"))
async def handle_dl_callback(callback: CallbackQuery, state: FSMContext):
    """Callback for link-based actions (download video/audio/gif, gallery, cancel)."""
    raw = callback.data
    parts = raw.split("_")
    action = parts[1]
    message_id = callback.message.message_id
    url = url_cache.get(message_id)

    # ── Cancel ──
    if action == "cancel":
        await callback.message.edit_text("Action cancelled.")
        url_cache.pop(message_id, None)
        gallery_cache.pop(message_id, None)
        gallery_source_cache.pop(message_id, None)
        await state.clear()
        return

    if not url:
        await callback.answer("Link expired. Please send it again.", show_alert=True)
        return

    # ── Gallery ──
    if action == "gallery":
        sub_action = parts[2] if len(parts) > 2 else "all"
        data = await state.get_data()
        cache_id = data.get("gallery_cache_id")
        photo_urls = gallery_cache.get(cache_id, []) if cache_id else []

        if sub_action == "pick":
            count = data.get("gallery_count", "?")
            await state.update_data(url=url)
            await state.set_state(BotStates.waiting_for_gallery_selection)
            await callback.message.edit_text(
                f"There are **{count}** photos.\n"
                "Reply with the numbers you want to download.\n"
                "Examples: `1,3` or `1-3` or `all`.",
                parse_mode="Markdown",
            )
            return

        # sub_action == "all" — download all photos
        await callback.message.edit_text("Downloading all photos… ⏳")

        source = gallery_source_cache.get(cache_id, '') if cache_id else ''
        if photo_urls and source == 'twitter':
            files = await download_twitter_media(photo_urls)
        elif photo_urls:
            files = await download_tiktok_photos(photo_urls)
        else:
            files = await download_media(url, "gallery")

        # Clean up
        if cache_id and cache_id in gallery_cache:
            del gallery_cache[cache_id]
        if cache_id and cache_id in gallery_source_cache:
            del gallery_source_cache[cache_id]
        await state.clear()

        if not files:
            await callback.message.edit_text("Failed to download photos.")
            return
        try:
            media_group = []
            for f in files:
                if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    media_group.append(InputMediaPhoto(media=FSInputFile(f)))
                else:
                    media_group.append(InputMediaVideo(media=FSInputFile(f)))
            if media_group:
                for i in range(0, len(media_group), 10):
                    await callback.message.answer_media_group(media_group[i:i + 10])
            await callback.message.edit_text("Done! ✅")
        except Exception as e:
            logger.error("Gallery send error: %s", e)
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
            files = await download_media(url, "video")
            filepath = files[0] if files else None
            if not filepath:
                await callback.message.edit_text("Failed to download video.")
                return
            gif_path = await convert_to_gif(filepath, "0", str(duration))
            if os.path.exists(filepath):
                os.remove(filepath)
            if not gif_path:
                await callback.message.edit_text("Failed to convert video to GIF.")
                return
            try:
                await callback.message.answer_animation(FSInputFile(gif_path))
                await callback.message.edit_text("Done! ✅")
            except Exception:
                await callback.message.edit_text("Failed to send GIF. It might be too large.")
            finally:
                if os.path.exists(gif_path):
                    os.remove(gif_path)
            return

        # Longer video — ask for timestamps
        await state.update_data(url=url)
        await state.set_state(BotStates.waiting_for_gif_timestamps)
        duration_text = f"The video is **{duration}s** long.\n" if duration else ""
        await callback.message.edit_text(
            f"{duration_text}"
            "Reply with the time range for the GIF.\n"
            "Format: `START-END` (e.g. `00:15-00:25` or `1-6`).\n"
            "Keep it under 10 seconds for best results.",
            parse_mode="Markdown",
        )
        return

    # ── Download video / audio ──
    await callback.message.edit_text("Processing your request… ⏳")

    files = await download_media(url, action)
    filepath = files[0] if files else None

    if not filepath:
        await callback.message.edit_text("Failed to download. It might be unsupported or too large.")
        return

    try:
        # Detect actual file type by extension to select the proper send method
        ext = filepath.lower().split('.')[-1]
        if ext in ('jpg', 'jpeg', 'png', 'webp'):
            await callback.message.answer_photo(FSInputFile(filepath))
        elif ext in ('mp3', 'm4a', 'wav', 'ogg') or action == "audio":
            await callback.message.answer_audio(FSInputFile(filepath))
        else:
            await callback.message.answer_video(FSInputFile(filepath))
        await callback.message.edit_text("Done! ✅")
    except Exception as e:
        logger.error(f"Error sending downloaded file: {e}")
        await callback.message.edit_text("Failed to send file. It might be over Telegram's 50MB limit.")
    finally:
        if os.path.exists(filepath):
            os.remove(filepath)


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
    except Exception:
        await status_msg.edit_text("Could not retrieve the video file. Please send it again.")
        return

    input_path = os.path.join(DOWNLOADS_DIR, f"{file_id}.mp4")
    await message.bot.download_file(file.file_path, destination=input_path)

    await status_msg.edit_text("Converting to GIF… ⏳")
    gif_path = await convert_to_gif(input_path, start_time, end_time)

    if os.path.exists(input_path):
        os.remove(input_path)

    if not gif_path:
        await status_msg.edit_text("Failed to convert video to GIF.")
        return

    try:
        await message.answer_animation(FSInputFile(gif_path))
        await status_msg.edit_text("Done! ✅")
    except Exception:
        await status_msg.edit_text("Failed to send GIF. It might be too large.")
    finally:
        if os.path.exists(gif_path):
            os.remove(gif_path)
