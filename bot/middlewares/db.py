from __future__ import annotations

import hashlib
import html
import logging
import secrets
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from sqlalchemy.ext.asyncio import async_sessionmaker

from bot.config import settings
from bot.db.repo import ExpenseRepo

logger = logging.getLogger(__name__)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_SECONDS = 900  # 15 minutes


def _extract_passphrase_attempt(
    text: Optional[str], chat_type: str
) -> tuple[Optional[str], bool, bool]:
    """
    Inspect incoming message text from an unauthorized user.
    Returns (candidate_passphrase, is_auth_command, is_start_command).
    """
    if not text or not text.strip():
        return None, False, False

    raw = text.strip()
    if raw.startswith("/"):
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower().split("@")[0]
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/auth", "/login", "/unlock", "/pin", "/passphrase"):
            return (arg if arg else None), True, False

        if cmd == "/start" and arg and not arg.upper().startswith("FAM-"):
            return arg, True, True

        return None, False, cmd == "/start"

    if chat_type == "private":
        return raw, False, False

    return None, False, False


async def _safe_answer(message: Message, text: str) -> None:
    try:
        await message.answer(text, parse_mode="HTML")
    except Exception as exc:
        logger.debug("Could not send reply message: %s", exc)


async def _safe_delete(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


class DbSessionMiddleware(BaseMiddleware):
    def __init__(
        self,
        session_pool: async_sessionmaker,
        passphrase: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.session_pool = session_pool
        self.passphrase = passphrase
        self._failed_attempts: dict[int, list[float]] = {}

    def _get_active_passphrase(self) -> Optional[str]:
        raw = self.passphrase if self.passphrase is not None else settings.bot_passphrase
        if raw and raw.strip():
            return raw.strip()
        return None

    def _is_rate_limited(self, user_id: int) -> bool:
        now = time.monotonic()
        attempts = [
            ts for ts in self._failed_attempts.get(user_id, [])
            if now - ts < LOCKOUT_SECONDS
        ]
        if attempts:
            self._failed_attempts[user_id] = attempts
        else:
            self._failed_attempts.pop(user_id, None)
        return len(attempts) >= MAX_FAILED_ATTEMPTS

    def _record_failed_attempt(self, user_id: int) -> int:
        now = time.monotonic()
        attempts = [
            ts for ts in self._failed_attempts.get(user_id, [])
            if now - ts < LOCKOUT_SECONDS
        ]
        attempts.append(now)
        self._failed_attempts[user_id] = attempts
        return len(attempts)

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
                actual_event = (
                    event.message
                    or event.callback_query
                    or event.edited_message
                    or event
                )

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
                active_pin = self._get_active_passphrase()
                just_unlocked_via_non_start = False

                if active_pin is not None:
                    expected_hash = hashlib.sha256(
                        active_pin.encode("utf-8")
                    ).hexdigest()
                    is_auth = await repo.is_user_authorized(user.id, expected_hash)

                    if not is_auth:
                        if isinstance(actual_event, CallbackQuery):
                            try:
                                await actual_event.answer(
                                    "🔒 Access restricted. Please send /auth <passphrase> to unlock.",
                                    show_alert=True,
                                )
                            except Exception:
                                pass
                            return None

                        if isinstance(actual_event, Message):
                            if self._is_rate_limited(user.id):
                                if chat.type == "private" or (
                                    actual_event.text
                                    and actual_event.text.strip().startswith("/")
                                ):
                                    await _safe_answer(
                                        actual_event,
                                        "⏳ <b>Too many failed attempts.</b>\n"
                                        "Please wait 15 minutes before trying again.",
                                    )
                                return None

                            candidate, is_auth_cmd, is_start_cmd = (
                                _extract_passphrase_attempt(
                                    actual_event.text, chat.type
                                )
                            )

                            if candidate is not None and secrets.compare_digest(
                                candidate, active_pin
                            ):
                                self._failed_attempts.pop(user.id, None)
                                await repo.authorize_user(user.id, expected_hash)
                                await _safe_delete(actual_event)
                                logger.info(
                                    "User %s (%s) unlocked bot access.",
                                    user.id,
                                    user.full_name,
                                )
                                if not is_start_cmd:
                                    just_unlocked_via_non_start = True
                            else:
                                if candidate is not None:
                                    failures = self._record_failed_attempt(user.id)
                                    remaining = max(0, MAX_FAILED_ATTEMPTS - failures)
                                    logger.warning(
                                        "Failed passphrase attempt from user %s (%s). Remaining: %d",
                                        user.id,
                                        user.full_name,
                                        remaining,
                                    )
                                    await _safe_answer(
                                        actual_event,
                                        "❌ <b>Invalid passphrase.</b>\n\n"
                                        "🔒 This bot is private. Please enter the correct passphrase "
                                        "or send <code>/auth &lt;passphrase&gt;</code>.",
                                    )
                                elif (
                                    chat.type == "private"
                                    or is_auth_cmd
                                    or (
                                        actual_event.text
                                        and actual_event.text.strip().startswith("/")
                                    )
                                ):
                                    await _safe_answer(
                                        actual_event,
                                        "🔒 <b>Private Family Expense Bot</b>\n\n"
                                        "Access to this bot is restricted to authorized family members.\n"
                                        "Please enter the secret passphrase directly in this chat "
                                        "(or send <code>/auth &lt;passphrase&gt;</code>) to unlock.",
                                    )
                                return None

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

                if just_unlocked_via_non_start and isinstance(actual_event, Message):
                    await _safe_answer(
                        actual_event,
                        f"🔓 <b>Access Granted!</b>\n\n"
                        f"🏠 <b>Household:</b> {html.escape(household.name)}\n"
                        f"🔑 <b>Family Invite Code:</b> <code>{household.invite_code}</code>\n\n"
                        f"You can now log expenses (e.g. <code>12.80 lunch</code>), "
                        f"join an existing family ledger with <code>/join FAM-XXXXXX</code>, "
                        f"or send /start and /help to see all commands.",
                    )
                    return None

            return await handler(event, data)


