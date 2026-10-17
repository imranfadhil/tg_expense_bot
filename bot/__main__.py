from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo
import uvicorn

from bot.config import settings
from bot.db.base import AsyncSessionLocal, engine, init_db
from bot.handlers import setup_routers
from bot.middlewares.db import DbSessionMiddleware
from bot.webapp import create_webapp, resolve_public_webapp_url


async def _set_bot_commands(bot: Bot) -> None:
    commands = [
        BotCommand(command="dashboard", description="📱 Open interactive Live Mini App Dashboard"),
        BotCommand(command="report", description="📊 Monthly summary, pace forecast & breakdown"),
        BotCommand(command="compare", description="📈 Month-over-Month comparison & pace check"),
        BotCommand(command="budget", description="🎯 View or set monthly & category budgets"),
        BotCommand(command="recurring", description="🔁 View, edit & manage monthly recurring expenses"),
        BotCommand(command="recent", description="🧾 View, edit & delete recent expenses"),
        BotCommand(command="categories", description="🗂️ View or add custom categories"),
        BotCommand(command="export", description="📥 Export monthly expenses to CSV"),
        BotCommand(command="family", description="👨‍👩‍👧‍👦 Household members & invite code"),
        BotCommand(command="help", description="📖 How to log & manage family expenses"),
    ]
    await bot.set_my_commands(commands)


async def _sync_webapp_menu_button(bot: Bot, logger: logging.Logger) -> None:
    """
    Wait for either WEBAPP_URL or the Cloudflare Quick Tunnel sidecar to become ready,
    then register the Telegram ChatMenuButtonWebApp so users have a 1-tap Dashboard button.
    """
    for _ in range(15):
        public_url = await resolve_public_webapp_url()
        if public_url and public_url.startswith("https://"):
            try:
                await bot.set_chat_menu_button(
                    menu_button=MenuButtonWebApp(
                        text="📊 Dashboard",
                        web_app=WebAppInfo(url=public_url),
                    )
                )
                logger.info("Telegram Mini App Menu Button registered: %s", public_url)
                return
            except Exception as exc:
                logger.warning("Could not set Telegram MenuButtonWebApp: %s", exc)
                return
        await asyncio.sleep(2.0)


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

    web_server: uvicorn.Server | None = None
    web_task: asyncio.Task | None = None
    menu_task: asyncio.Task | None = None

    if settings.webapp_enabled:
        fastapi_app = create_webapp()
        uv_config = uvicorn.Config(
            app=fastapi_app,
            host=settings.webapp_host,
            port=settings.webapp_port,
            log_level="info",
            access_log=False,
        )
        web_server = uvicorn.Server(uv_config)
        web_task = asyncio.create_task(web_server.serve())
        menu_task = asyncio.create_task(_sync_webapp_menu_button(bot, logger))
        logger.info(
            "Started FastAPI Live Dashboard server on http://%s:%d",
            settings.webapp_host,
            settings.webapp_port,
        )

    logger.info("Starting Family Expense Bot polling...")

    try:
        await dp.start_polling(bot)
    finally:
        if menu_task and not menu_task.done():
            menu_task.cancel()
        if web_server is not None:
            web_server.should_exit = True
        if web_task is not None:
            try:
                await asyncio.wait_for(web_task, timeout=5.0)
            except Exception:
                pass
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

