from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "👋 Welcome to the Media Downloader Bot!\n\n"
        "Just send me a link from YouTube, TikTok, Instagram, or Twitter (X).\n"
        "I will reply with options to download the video, extract the audio, or convert a segment to a GIF."
    )
    await message.answer(text)

@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "Send me a link to a video or post.\n"
        "Supported sites: YouTube, TikTok, Instagram, Twitter.\n"
        "You can choose to download video, audio, or convert it to a GIF."
    )
    await message.answer(text)
