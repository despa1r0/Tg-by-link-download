import os
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.services.downloader import download_media
from bot.services.converter import convert_to_gif

router = Router()

class GIFState(StatesGroup):
    waiting_for_timestamps = State()

url_cache = {}

def is_valid_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://")

@router.message(F.text, ~F.text.startswith('/'))
async def handle_link(message: Message):
    text = message.text.strip()
    if not is_valid_url(text):
        await message.answer("Please send a valid HTTP/HTTPS URL.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎥 Download Video", callback_data="dl_video")],
        [InlineKeyboardButton(text="🎵 Download Audio", callback_data="dl_audio")],
        [InlineKeyboardButton(text="🎞 Convert to GIF", callback_data="dl_gif")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="dl_cancel")]
    ])
    
    msg = await message.reply("Link detected! Choose an action:", reply_markup=keyboard)
    url_cache[msg.message_id] = text

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
    
    filepath = await download_media(url, action)
    
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
    
    filepath = await download_media(url, "video")
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
