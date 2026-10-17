from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from bot.config import settings
from bot.db.base import AsyncSessionLocal, engine, init_db
from bot.handlers import setup_routers
from bot.middlewares.db import DbSessionMiddleware


async def _set_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="report", description="📊 Monthly summary, pace forecast & breakdown"),
        BotCommand(command="compare", description="📈 Month-over-Month comparison & pace check"),
        BotCommand(command="budget", description="🎯 View or set monthly & category budgets"),
        BotCommand(command="recent", description="🧾 View, edit & delete recent expenses"),
        BotCommand(command="categories", description="🗂️ View or add custom categories"),
        BotCommand(command="export", description="📥 Export monthly expenses to CSV"),
        BotCommand(command="family", description="👨‍👩‍👧‍👦 Household members & invite code"),
        BotCommand(command="help", description="📖 How to log & manage family expenses"),
    ]
    await bot.set_my_commands(commands)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    logger = logging.getLogger(__name__)

    if not settings.bot_token or settings.bot_token.startswith("000000000") or "your_telegram_bot_token" in settings.bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not configured. Copy .env.example to .env and set your BOT_TOKEN from @BotFather."
        )

    logger.info("Initializing PostgreSQL schema...")
    await init_db()

    if settings.passphrase_hash:
        logger.info("Passphrase access control is ENABLED.")
    else:
        logger.warning(
            "BOT_PASSPHRASE is not set — bot is open to any Telegram user. "
            "Set BOT_PASSPHRASE in .env to require a passphrase/PIN login."
        )

    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()

    dp.update.middleware(DbSessionMiddleware(AsyncSessionLocal))
    dp.include_router(setup_routers())

    await _set_bot_commands(bot)
    logger.info("Starting Family Expense Bot polling...")

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

