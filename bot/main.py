import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.config import BOT_TOKEN
from bot.handlers import commands, media
from bot.observability import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

async def main():
    if not BOT_TOKEN:
        logger.error(
            "BOT_TOKEN is not configured",
            extra={
                "event": "configuration_error",
                "platform": "telegram",
                "error_type": "MissingBotToken",
            },
        )
        return
        
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    dp.include_router(commands.router)
    dp.include_router(media.router)

    logger.info(
        "Starting bot polling",
        extra={"event": "bot_started", "platform": "telegram"},
    )
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
