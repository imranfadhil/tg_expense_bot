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

