from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

router = Router()

@router.message(CommandStart())
async def cmd_start(message: Message):
    text = (
        "👋 Welcome to the Media Downloader Bot!\n\n"
        "Here is what I can do:\n"
        "🔗 **Download from Links**: Just send me a link from YouTube, TikTok, Instagram, or Twitter (X).\n"
        "   - I can download Videos, Audio, or convert them to GIFs!\n"
        "   - I also support downloading TikTok/Instagram Photo Galleries.\n"
        "🎞 **Convert Video to GIF**: Send me any video file directly and I can convert it to a GIF for you.\n\n"
        "Send a link or drop a video to get started!"
    )
    await message.answer(text, parse_mode="Markdown")

@router.message(Command("help"))
async def cmd_help(message: Message):
    text = (
        "Send me a link to a video or post.\n"
        "Supported sites: YouTube, TikTok, Instagram, Twitter.\n"
        "You can choose to download video, audio, or convert it to a GIF.\n\n"
        "You can also upload a video directly to convert it to a GIF!\n"
        "Send /cancel at any time to abort the current action."
    )
    await message.answer(text)

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    current = await state.get_state()
    if current is None:
        await message.answer("Nothing to cancel.")
        return
    await state.clear()
    await message.answer("Action cancelled. ✅\nSend a link or video to start again.")
