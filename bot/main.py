import asyncio
import logging
from aiogram import Bot, Dispatcher
from bot.config import BOT_TOKEN
from bot.handlers import commands, media

logging.basicConfig(level=logging.INFO)

async def main():
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN is not set. Please set it in your .env file.")
        return
        
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(commands.router)
    dp.include_router(media.router)

    logging.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
