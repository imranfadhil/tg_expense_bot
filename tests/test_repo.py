from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.db.base import Base
from bot.db.repo import ExpenseRepo
from bot.services.analytics import get_month_bounds


@pytest.mark.asyncio
async def test_repo_household_invite_and_expense_lifecycle():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        repo = ExpenseRepo(session)

        # 1. First family member starts bot in private chat
        hh1, m1, is_new = await repo.ensure_context(
            telegram_user_id=1001,
            display_name="Alice",
            chat_id=1001,
            chat_type="private",
        )
        assert is_new is True
        assert hh1.invite_code.startswith("FAM-")

        # 2. Second family member joins via invite code
        hh2, m2, _ = await repo.ensure_context(
            telegram_user_id=1002,
            display_name="Bob",
            chat_id=1002,
            chat_type="private",
        )
        assert hh2.id != hh1.id

        joined = await repo.join_household_by_code(
            telegram_user_id=1002,
            display_name="Bob",
            invite_code=hh1.invite_code,
        )
        assert joined is not None
        assert joined.id == hh1.id

        members = await repo.get_household_members(hh1.id)
        assert len(members) == 2

        # 3. Log expenses from both members into the shared household
        categories = await repo.get_categories(hh1.id)
        groceries = next(c for c in categories if c.name == "groceries")
        dining = next(c for c in categories if c.name == "dining")
        car_loan = next(c for c in categories if c.name == "auto")
        school_fees = next(c for c in categories if c.name == "school")
        salary_ded = next(c for c in categories if c.name == "saving")
        assert car_loan.is_fixed is True
        assert school_fees.is_fixed is True
        assert salary_ded.is_fixed is True
        assert groceries.is_fixed is False

        tz = ZoneInfo("Asia/Singapore")
        dt = datetime(2026, 10, 17, 13, 30, tzinfo=tz)

        exp1 = await repo.add_expense(
            household_id=hh1.id,
            member_id=m1.id,
            category_id=groceries.id,
            amount=Decimal("64.50"),
            description="FairPrice",
            spent_at=dt,
        )
        exp2 = await repo.add_expense(
            household_id=hh1.id,
            member_id=m2.id,
            category_id=dining.id,
            amount=Decimal("25.50"),
            description="Lunch",
            spent_at=dt,
        )
        assert exp2.is_once_off is False

        toggled = await repo.toggle_expense_once_off(hh1.id, exp2.id)
        assert toggled is not None
        assert toggled.is_once_off is True

        start_dt, end_dt = get_month_bounds(2026, 10, tz)
        total = await repo.get_period_total(hh1.id, start_dt, end_dt)
        assert total == Decimal("90.00")

        # 4. Update category and delete expense
        updated = await repo.update_expense_category(hh1.id, exp2.id, groceries.id)
        assert updated is not None
        assert updated.category_id == groceries.id

        deleted = await repo.delete_expense(hh1.id, exp1.id)
        assert deleted is True

        total_after = await repo.get_period_total(hh1.id, start_dt, end_dt)
        assert total_after == Decimal("25.50")

    await engine.dispose()


@pytest.mark.asyncio
async def test_db_session_middleware_unwraps_update():
    from aiogram.types import Chat, Message, Update, User
    from bot.middlewares.db import DbSessionMiddleware

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mw = DbSessionMiddleware(session_maker)

    msg = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=555, type="private"),
        from_user=User(id=555, is_bot=False, first_name="TestUser"),
        text="/help",
    )
    upd = Update(update_id=100, message=msg)

    captured_data = {}

    async def dummy_handler(event, data):
        captured_data.update(data)
        return "ok"

    res = await mw(dummy_handler, upd, {})
    assert res == "ok"
    assert "household" in captured_data
    assert "member" in captured_data
    assert captured_data["member"].display_name == "TestUser"

    await engine.dispose()


@pytest.mark.asyncio
async def test_passphrase_protection_blocks_strangers_and_unlocks_on_valid_pin():
    from aiogram.types import Chat, Message, Update, User
    from bot.middlewares.db import DbSessionMiddleware

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mw = DbSessionMiddleware(session_maker, passphrase="secret-family-pin")

    handler_calls = []

    async def dummy_handler(event, data):
        handler_calls.append(data)
        return "handled"

    def make_update(uid: int, text: str, chat_type: str = "private") -> Update:
        msg = Message(
            message_id=1,
            date=datetime.now(),
            chat=Chat(id=uid, type=chat_type),
            from_user=User(id=uid, is_bot=False, first_name="Stranger"),
            text=text,
        )
        return Update(update_id=1, message=msg)

    # 1. Stranger sends /start or expense text -> blocked, no DB rows created
    res1 = await mw(dummy_handler, make_update(999, "/start"), {})
    assert res1 is None
    assert len(handler_calls) == 0

    res2 = await mw(dummy_handler, make_update(999, "45.50 groceries"), {})
    assert res2 is None
    assert len(handler_calls) == 0

    async with session_maker() as session:
        repo = ExpenseRepo(session)
        assert await repo.get_member_by_telegram_id(999) is None

    # 2. User sends valid passphrase via /auth -> unlocked and registered
    res3 = await mw(dummy_handler, make_update(777, "/auth secret-family-pin"), {})
    assert res3 is None  # Middleware replies directly with Access Granted card

    async with session_maker() as session:
        repo = ExpenseRepo(session)
        member = await repo.get_member_by_telegram_id(777)
        assert member is not None

    # 3. Subsequent messages from user 777 pass through to handlers
    res4 = await mw(dummy_handler, make_update(777, "/report"), {})
    assert res4 == "handled"
    assert len(handler_calls) == 1
    assert handler_calls[0]["member"].telegram_user_id == 777

    # 4. Changing passphrase invalidates old authorization until re-authenticated
    mw_new_pin = DbSessionMiddleware(session_maker, passphrase="rotated-pin-2026")
    res5 = await mw_new_pin(dummy_handler, make_update(777, "/report"), {})
    assert res5 is None
    assert len(handler_calls) == 1

    # 5. Rate limiting blocks brute-force after 5 failed attempts
    for _ in range(5):
        await mw_new_pin(dummy_handler, make_update(888, "wrong-pin"), {})
    assert mw_new_pin._is_rate_limited(888) is True
    # Even correct pin is blocked while rate-limited
    res6 = await mw_new_pin(dummy_handler, make_update(888, "rotated-pin-2026"), {})
    assert res6 is None
    async with session_maker() as session:
        repo = ExpenseRepo(session)
        assert await repo.get_member_by_telegram_id(888) is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_expense_editing_via_repo_edited_message_and_interactive_flow():
    from unittest.mock import AsyncMock, MagicMock
    from bot.handlers.expenses import (
        _PENDING_EDITS,
        PendingEdit,
        handle_edited_expense_message,
        handle_quick_text_expense,
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_maker() as session:
        repo = ExpenseRepo(session)
        hh, member, _ = await repo.ensure_context(
            telegram_user_id=2001,
            display_name="Alice",
            chat_id=2001,
            chat_type="private",
        )
        categories = await repo.get_categories(hh.id)
        dining = next(c for c in categories if c.name == "dining")
        groceries = next(c for c in categories if c.name == "groceries")

        # 1. Create an expense with source_chat_id and source_message_id
        exp = await repo.add_expense(
            household_id=hh.id,
            member_id=member.id,
            category_id=dining.id,
            amount=Decimal("12.80"),
            description="lunch Maxwell",
            source_chat_id=2001,
            source_message_id=501,
        )
        await repo.set_expense_bot_message_id(hh.id, exp.id, 502)

        found = await repo.get_expense_by_source_message(hh.id, 2001, 501)
        assert found is not None
        assert found.id == exp.id
        assert found.bot_message_id == 502

        # 2. Simulate #1: User edits their original Telegram message ("15.50 groceries NTUC")
        mock_bot = MagicMock()
        mock_bot.edit_message_text = AsyncMock()
        mock_bot.delete_message = AsyncMock()

        edited_msg = MagicMock()
        edited_msg.text = "15.50 groceries NTUC"
        edited_msg.chat.id = 2001
        edited_msg.message_id = 501
        edited_msg.bot = mock_bot
        edited_msg.answer = AsyncMock()

        await handle_edited_expense_message(edited_msg, repo, hh)

        reloaded = await repo.get_expense_by_id(hh.id, exp.id)
        assert reloaded is not None
        assert reloaded.amount == Decimal("15.50")
        assert reloaded.category_id == groceries.id
        assert reloaded.description == "NTUC"
        mock_bot.edit_message_text.assert_awaited_once()

        # 3. Simulate #2: User taps "💰 Amount" then sends "22.90"
        _PENDING_EDITS[(2001, 2001)] = PendingEdit(
            expense_id=exp.id,
            field="amount",
            card_chat_id=2001,
            card_message_id=502,
            prompt_message_id=600,
        )
        reply_amt_msg = MagicMock()
        reply_amt_msg.text = "22.90"
        reply_amt_msg.chat.id = 2001
        reply_amt_msg.from_user.id = 2001
        reply_amt_msg.message_id = 601
        reply_amt_msg.bot = mock_bot
        reply_amt_msg.answer = AsyncMock()

        await handle_quick_text_expense(reply_amt_msg, repo, hh, member)

        reloaded2 = await repo.get_expense_by_id(hh.id, exp.id)
        assert reloaded2 is not None
        assert reloaded2.amount == Decimal("22.90")
        assert (2001, 2001) not in _PENDING_EDITS

        # 4. Simulate #2: User taps "📝 Note" then sends "FairPrice Finest"
        _PENDING_EDITS[(2001, 2001)] = PendingEdit(
            expense_id=exp.id,
            field="note",
            card_chat_id=2001,
            card_message_id=502,
            prompt_message_id=602,
        )
        reply_note_msg = MagicMock()
        reply_note_msg.text = "FairPrice Finest"
        reply_note_msg.chat.id = 2001
        reply_note_msg.from_user.id = 2001
        reply_note_msg.message_id = 603
        reply_note_msg.bot = mock_bot
        reply_note_msg.answer = AsyncMock()

        await handle_quick_text_expense(reply_note_msg, repo, hh, member)

        reloaded3 = await repo.get_expense_by_id(hh.id, exp.id)
        assert reloaded3 is not None
        assert reloaded3.description == "FairPrice Finest"

        # 5. Simulate #recurring monthly carry-over and /recurring management
        tz = ZoneInfo("Asia/Singapore")
        oct_dt = datetime(2026, 10, 5, 9, 0, tzinfo=tz)
        rec_exp = await repo.add_expense(
            household_id=hh.id,
            member_id=member.id,
            category_id=dining.id,
            amount=Decimal("15.90"),
            description="Netflix",
            spent_at=oct_dt,
            is_recurring=True,
        )
        assert rec_exp.is_recurring is True
        assert rec_exp.is_once_off is True

        active_oct = await repo.get_recurring_expenses(hh.id)
        assert len(active_oct) == 1
        assert active_oct[0].id == rec_exp.id

        # Advance to November 2026 -> sync_recurring_expenses creates Nov entry and retires Oct template flag
        nov_now = datetime(2026, 11, 2, 10, 0, tzinfo=tz)
        spawned = await repo.sync_recurring_expenses(hh.id, now=nov_now)
        assert len(spawned) == 1
        nov_exp = spawned[0]
        assert nov_exp.amount == Decimal("15.90")
        assert nov_exp.description == "Netflix"
        assert nov_exp.is_recurring is True
        assert nov_exp.spent_at.astimezone(tz).month == 11

        # Oct entry remains in Oct history but is_recurring is now False
        oct_reloaded = await repo.get_expense_by_id(hh.id, rec_exp.id)
        assert oct_reloaded is not None
        assert oct_reloaded.is_recurring is False
        assert oct_reloaded.is_once_off is True

        # Active /recurring list now returns the Nov entry
        active_nov = await repo.get_recurring_expenses(hh.id)
        assert len(active_nov) == 1
        assert active_nov[0].id == nov_exp.id

    await engine.dispose()



