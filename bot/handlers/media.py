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

from bot.services.downloader import download_media, extract_info, is_gallery, get_gallery_count
from bot.services.converter import convert_to_gif
from bot.config import DOWNLOADS_DIR

logger = logging.getLogger(__name__)

router = Router()


class BotStates(StatesGroup):
    waiting_for_gif_timestamps = State()        # GIF from a URL link
    waiting_for_video_timestamps = State()      # GIF from an uploaded video file
    waiting_for_gallery_selection = State()      # user picks which photos to download


url_cache: dict[int, str] = {}


def is_valid_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")


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
            "Invalid format. Please use `START-END` (e.g. `00:15-00:25`).\nTry again or send /cancel."
        )
        return

    start_time, end_time = match.groups()

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
    except Exception as e:
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
            "Invalid format. Please use `START-END` (e.g. `00:15-00:25`).\nTry again or send /cancel."
        )
        return

    start_time, end_time = match.groups()
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
    await state.clear()

    if not url:
        await message.answer("Session expired. Please send the link again.")
        return

    # Parse the user input:  "1,3,5"  or  "1-3"  or  "all"
    if text == "all":
        playlist_items = None  # download everything
    else:
        # Accept comma-separated numbers and/or ranges like "1-3,5"
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
                "Send numbers like `1,3,5` or `1-3` or `all`."
            )
            # re-enter the state so they can try again
            await state.update_data(url=url, gallery_count=total)
            await state.set_state(BotStates.waiting_for_gallery_selection)
            return
        playlist_items = ",".join(str(n) for n in sorted(set(nums)))

    status_msg = await message.answer("Downloading selected photos… ⏳")
    files = await download_media(url, "gallery", playlist_items=playlist_items)

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
            # Telegram allows max 10 items per media group
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

    if info and is_gallery(info):
        count = get_gallery_count(info)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📸 Download All", callback_data="dl_gallery_all")],
            [InlineKeyboardButton(text="🔢 Pick Specific Photos", callback_data="dl_gallery_pick")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")],
        ])
        await msg.edit_text(
            f"📷 Gallery detected with **{count}** photos!\nChoose an action:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        url_cache[msg.message_id] = text
        # store gallery count for later
        await state.update_data(gallery_count=count)
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 Download Video", callback_data="dl_video")],
        [InlineKeyboardButton(text="🎵 Download Audio", callback_data="dl_audio")],
        [InlineKeyboardButton(text="🎞 Convert to GIF", callback_data="dl_gif")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")],
    ])
    await msg.edit_text("Link detected! Choose an action:", reply_markup=keyboard)
    url_cache[msg.message_id] = text


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
            "Format: `START-END` (e.g. `00:15-00:25` or `15-25`).",
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
    raw = callback.data  # e.g. "dl_video", "dl_gallery_all", "dl_cancel"
    parts = raw.split("_")
    action = parts[1]
    message_id = callback.message.message_id
    url = url_cache.get(message_id)

    # ── Cancel ──
    if action == "cancel":
        await callback.message.edit_text("Action cancelled.")
        url_cache.pop(message_id, None)
        await state.clear()
        return

    if not url:
        await callback.answer("Link expired. Please send it again.", show_alert=True)
        return

    # ── Gallery ──
    if action == "gallery":
        sub_action = parts[2] if len(parts) > 2 else "all"

        if sub_action == "pick":
            data = await state.get_data()
            count = data.get("gallery_count", "?")
            await state.update_data(url=url)
            await state.set_state(BotStates.waiting_for_gallery_selection)
            await callback.message.edit_text(
                f"There are **{count}** photos.\n"
                "Reply with the numbers you want to download.\n"
                "Examples: `1,3,5` or `1-3` or `all`.",
                parse_mode="Markdown",
            )
            return

        # sub_action == "all"
        await callback.message.edit_text("Downloading all photos… ⏳")
        files = await download_media(url, "gallery")
        if not files:
            await callback.message.edit_text("Failed to download gallery.")
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
        await state.update_data(url=url)
        await state.set_state(BotStates.waiting_for_gif_timestamps)
        await callback.message.edit_text(
            "Reply with the time range for the GIF.\n"
            "Format: `START-END` (e.g. `00:15-00:25` or `15-25`).\n"
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
        if action == "video":
            await callback.message.answer_video(FSInputFile(filepath))
        else:
            await callback.message.answer_audio(FSInputFile(filepath))
        await callback.message.edit_text("Done! ✅")
    except Exception as e:
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
    status_msg = await message.answer("Downloading your video… ⏳") if message.text else message

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
    except Exception as e:
        await status_msg.edit_text("Failed to send GIF. It might be too large.")
    finally:
        if os.path.exists(gif_path):
            os.remove(gif_path)
