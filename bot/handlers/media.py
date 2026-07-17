import os
import re
import uuid
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaPhoto, InputMediaVideo
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import StateFilter

from bot.services.downloader import download_media, extract_info
from bot.services.converter import convert_to_gif
from bot.config import DOWNLOADS_DIR

router = Router()

class GIFState(StatesGroup):
    waiting_for_timestamps = State()
    waiting_for_video_timestamps = State()

url_cache = {}

def is_valid_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")

@router.message(F.text, StateFilter(None), ~F.text.startswith('/'))
async def handle_link(message: Message, state: FSMContext):
    text = message.text.strip()
    if not is_valid_url(text):
        await message.answer("Please send a valid HTTP/HTTPS URL.")
        return

    msg = await message.reply("Analyzing link... ⏳")
    
    info = await extract_info(text)
    
    if info and (info.get('_type') == 'playlist' or 'entries' in info):
        entries_count = len(info.get('entries', []))
        if entries_count > 0:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📸 Download All Photos/Videos", callback_data="dl_gallery_all")],
                [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")]
            ])
            await msg.edit_text(f"Gallery detected with {entries_count} items! Choose an action:", reply_markup=keyboard)
            url_cache[msg.message_id] = text
            return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 Download Video", callback_data="dl_video")],
        [InlineKeyboardButton(text="🎵 Download Audio", callback_data="dl_audio")],
        [InlineKeyboardButton(text="🎞 Convert to GIF", callback_data="dl_gif")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")]
    ])
    
    await msg.edit_text("Link detected! Choose an action:", reply_markup=keyboard)
    url_cache[msg.message_id] = text

@router.message(F.video, StateFilter(None))
async def handle_video(message: Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎞 Convert to GIF", callback_data="conv_video_gif")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")]
    ])
    msg = await message.reply("Video received! What would you like to do?", reply_markup=keyboard)
    url_cache[msg.message_id] = message.video.file_id

@router.callback_query(F.data.startswith("conv_video_"))
async def handle_video_callback(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split("_")[2]
    message_id = callback.message.message_id
    file_id = url_cache.get(message_id)
    
    if not file_id:
        await callback.answer("File expired. Please send the video again.", show_alert=True)
        return
        
    if action == "gif":
        video = callback.message.reply_to_message.video
        if video.duration > 10:
            await state.update_data(file_id=file_id)
            await state.set_state(GIFState.waiting_for_video_timestamps)
            await callback.message.edit_text(
                f"The video is {video.duration}s long.\n"
                "Please reply to this message with the start and end time for the GIF.\n"
                "Format: `START-END` (e.g., `00:15-00:25` or `15-25`)."
            )
        else:
            await state.update_data(file_id=file_id)
            await state.set_state(GIFState.waiting_for_video_timestamps)
            await callback.message.edit_text("Processing your video... ⏳")
            await process_uploaded_video_gif(callback.message, state, "0", str(video.duration), is_callback=True)

@router.message(GIFState.waiting_for_video_timestamps)
async def process_video_timestamps_reply(message: Message, state: FSMContext):
    text = message.text.strip()
    match = re.match(r"^([\d:]+)\s*-\s*([\d:]+)$", text)
    if not match:
        await message.answer("Invalid format. Please use `START-END` (e.g., `00:15-00:25`). Try again.")
        return
    start_time, end_time = match.groups()
    await process_uploaded_video_gif(message, state, start_time, end_time, is_callback=False)

async def process_uploaded_video_gif(message: Message, state: FSMContext, start_time: str, end_time: str, is_callback: bool = False):
    data = await state.get_data()
    file_id = data.get("file_id")
    await state.clear()
    
    status_msg = message if is_callback else await message.answer("Downloading video... ⏳")
    
    file = await message.bot.get_file(file_id)
    input_path = os.path.join(DOWNLOADS_DIR, f"{file_id}.mp4")
    await message.bot.download_file(file.file_path, destination=input_path)
    
    await status_msg.edit_text("Converting to GIF... ⏳")
    gif_path = await convert_to_gif(input_path, start_time, end_time)
    
    if os.path.exists(input_path):
        os.remove(input_path)
        
    if not gif_path:
        await status_msg.edit_text("Failed to convert video to GIF.")
        return
        
    try:
        if is_callback:
            await message.answer_animation(FSInputFile(gif_path))
            await status_msg.edit_text("Done! ✅")
        else:
            await message.answer_animation(FSInputFile(gif_path))
            await status_msg.edit_text("Done! ✅")
    except Exception as e:
        await status_msg.edit_text("Failed to send GIF. It might be too large.")
    finally:
        if os.path.exists(gif_path):
            os.remove(gif_path)

@router.callback_query(F.data.startswith("dl_"))
async def handle_callback(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split("_")[1]
    message_id = callback.message.message_id
    url = url_cache.get(message_id)

    if action == "cancel":
        await callback.message.edit_text("Action cancelled.")
        if message_id in url_cache:
            del url_cache[message_id]
        return

    if not url:
        await callback.answer("Link expired or not found. Please send it again.", show_alert=True)
        return

    if action == "gallery":
        gallery_action = callback.data.split("_")[2]
        if gallery_action == "all":
            await callback.message.edit_text("Downloading gallery... Please wait ⏳")
            files = await download_media(url, "gallery")
            if not files:
                await callback.message.edit_text("Failed to download gallery.")
                return
            try:
                media_group = []
                for f in files:
                    if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        media_group.append(InputMediaPhoto(media=FSInputFile(f)))
                    elif f.lower().endswith(('.mp4', '.mkv', '.webm', '.avi')):
                        media_group.append(InputMediaVideo(media=FSInputFile(f)))
                
                if media_group:
                    for i in range(0, len(media_group), 10):
                        await callback.message.answer_media_group(media_group[i:i+10])
                await callback.message.edit_text("Done! ✅")
            except Exception as e:
                await callback.message.edit_text("Failed to send some files.")
            finally:
                for f in files:
                    if os.path.exists(f):
                        os.remove(f)
        return

    if action == "gif":
        await state.update_data(url=url)
        await state.set_state(GIFState.waiting_for_timestamps)
        await callback.message.edit_text(
            "Please reply to this message with the start and end time for the GIF.\n"
            "Format: `START-END` (e.g., `00:15-00:25` or `15-25` for seconds).\n"
            "Keep it under 10 seconds for the best results."
        )
        return

    await callback.message.edit_text("Processing your request... Please wait ⏳")
    
    files = await download_media(url, action)
    filepath = files[0] if files else None
    
    if not filepath:
        await callback.message.edit_text("Failed to download media. It might be unsupported or too large.")
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
            
@router.message(GIFState.waiting_for_timestamps)
async def process_gif_timestamps(message: Message, state: FSMContext):
    data = await state.get_data()
    url = data.get("url")
    text = message.text.strip()
    
    match = re.match(r"^([\d:]+)\s*-\s*([\d:]+)$", text)
    if not match:
        await message.answer("Invalid format. Please use `START-END` (e.g., `00:15-00:25`). Try again.")
        return
        
    start_time, end_time = match.groups()
    
    await state.clear()
    status_msg = await message.answer("Downloading video for GIF conversion... ⏳")
    
    files = await download_media(url, "video")
    filepath = files[0] if files else None

    if not filepath:
        await status_msg.edit_text("Failed to download video for GIF conversion.")
        return
        
    await status_msg.edit_text("Converting to GIF... ⏳")
    
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
