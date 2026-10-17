from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.db.repo import ExpenseRepo


class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_pool: async_sessionmaker) -> None:
        super().__init__()
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with self.session_pool() as session:
            repo = ExpenseRepo(session)
            data["repo"] = repo

            actual_event = event
            if isinstance(event, Update):
                actual_event = event.message or event.callback_query or event.edited_message or event

            user = None
            chat = None
            if isinstance(actual_event, Message):
                user = actual_event.from_user
                chat = actual_event.chat
            elif isinstance(actual_event, CallbackQuery):
                user = actual_event.from_user
                if actual_event.message:
                    chat = actual_event.message.chat

            if user and not user.is_bot and chat:
                display_name = user.full_name or user.username or f"User {user.id}"
                household, member, is_new_household = await repo.ensure_context(
                    telegram_user_id=user.id,
                    display_name=display_name,
                    chat_id=chat.id,
                    chat_type=chat.type,
                    chat_title=chat.title,
                )
                data["household"] = household
                data["member"] = member
                data["is_new_household"] = is_new_household

            return await handler(event, data)

