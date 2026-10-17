from __future__ import annotations

import calendar
import secrets
import string
from datetime import datetime
from decimal import Decimal
from typing import Optional, Sequence

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.config import settings
from bot.db.models import (
    DEFAULT_CATEGORIES,
    FIXED_CATEGORY_NAMES,
    AuthorizedUser,
    Category,
    Expense,
    Household,
    Member,
    utcnow,
)


def _generate_invite_code() -> str:
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"FAM-{suffix}"


class ExpenseRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_user_authorized(self, telegram_user_id: int, expected_hash: str) -> bool:
        stmt = select(AuthorizedUser).where(
            AuthorizedUser.telegram_user_id == telegram_user_id
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return False
        return secrets.compare_digest(record.passphrase_hash, expected_hash)

    async def authorize_user(
        self, telegram_user_id: int, passphrase_hash: str
    ) -> AuthorizedUser:
        stmt = select(AuthorizedUser).where(
            AuthorizedUser.telegram_user_id == telegram_user_id
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            record = AuthorizedUser(
                telegram_user_id=telegram_user_id,
                passphrase_hash=passphrase_hash,
                authorized_at=utcnow(),
            )
            self.session.add(record)
        else:
            record.passphrase_hash = passphrase_hash
            record.authorized_at = utcnow()
        await self.session.commit()
        return record

    async def revoke_user_authorization(self, telegram_user_id: int) -> bool:
        stmt = select(AuthorizedUser).where(
            AuthorizedUser.telegram_user_id == telegram_user_id
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return False
        await self.session.delete(record)
        await self.session.commit()
        return True

    async def get_member_by_telegram_id(self, telegram_user_id: int) -> Optional[Member]:
        stmt = (
            select(Member)
            .options(selectinload(Member.household))
            .where(Member.telegram_user_id == telegram_user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_household_by_chat_id(self, telegram_chat_id: int) -> Optional[Household]:
        stmt = select(Household).where(Household.telegram_chat_id == telegram_chat_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_household_by_invite_code(self, invite_code: str) -> Optional[Household]:
        code = invite_code.strip().upper()
        stmt = select(Household).where(Household.invite_code == code)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_household_by_id(self, household_id: int) -> Optional[Household]:
        stmt = select(Household).where(Household.id == household_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_member_by_id(
        self, household_id: int, member_id: int
    ) -> Optional[Member]:
        stmt = select(Member).where(
            Member.household_id == household_id,
            Member.id == member_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def ensure_context(
        self,
        telegram_user_id: int,
        display_name: str,
        chat_id: int,
        chat_type: str,
        chat_title: Optional[str] = None,
    ) -> tuple[Household, Member, bool]:
        """
        Resolve or auto-create the Household and Member for the current user/chat.
        Returns (household, member, is_new_household).
        - If in a group/supergroup chat:
          1. Check if the group chat already has a linked Household.
          2. If not, check if the user already belongs to a Household and link or create one for the group.
          3. Ensure the user is registered as a Member of that Household.
        - If in a private chat:
          1. Check if the user is already a Member of a Household.
          2. If not, create a personal/family Household for them with an invite code they can share.
        """
        is_group = chat_type in ("group", "supergroup")
        member = await self.get_member_by_telegram_id(telegram_user_id)

        if is_group:
            household = await self.get_household_by_chat_id(chat_id)
            is_new = False
            if household is None:
                if member is not None:
                    household = member.household
                    if household.telegram_chat_id is None:
                        household.telegram_chat_id = chat_id
                else:
                    household = await self.create_household(
                        name=chat_title or f"{display_name}'s Family",
                        telegram_chat_id=chat_id,
                    )
                    is_new = True

            if member is None:
                member = Member(
                    telegram_user_id=telegram_user_id,
                    display_name=display_name,
                    household_id=household.id,
                )
                self.session.add(member)
                await self.session.flush()
            elif member.household_id != household.id:
                member.household_id = household.id
                member.display_name = display_name
                await self.session.flush()
            else:
                if member.display_name != display_name:
                    member.display_name = display_name
            await self.session.commit()
            await self.sync_recurring_expenses(household.id)
            return household, member, is_new

        # Private chat
        if member is not None:
            if member.display_name != display_name:
                member.display_name = display_name
                await self.session.commit()
            await self.sync_recurring_expenses(member.household.id)
            return member.household, member, False

        household = await self.create_household(name=f"{display_name}'s Family Ledger")
        member = Member(
            telegram_user_id=telegram_user_id,
            display_name=display_name,
            household_id=household.id,
        )
        self.session.add(member)
        await self.session.commit()
        return household, member, True

    async def create_household(
        self,
        name: str,
        telegram_chat_id: Optional[int] = None,
    ) -> Household:
        for _ in range(10):
            code = _generate_invite_code()
            existing = await self.get_household_by_invite_code(code)
            if existing is None:
                break
        else:
            code = _generate_invite_code()

        household = Household(
            name=name,
            invite_code=code,
            telegram_chat_id=telegram_chat_id,
            currency=settings.default_currency,
        )
        self.session.add(household)
        await self.session.flush()

        for key, label in DEFAULT_CATEGORIES:
            cat = Category(
                household_id=household.id,
                name=key,
                display_name=label,
                is_fixed=(key in FIXED_CATEGORY_NAMES),
            )
            self.session.add(cat)

        await self.session.flush()
        return household

    async def join_household_by_code(
        self,
        telegram_user_id: int,
        display_name: str,
        invite_code: str,
        chat_id: Optional[int] = None,
        is_group: bool = False,
    ) -> Optional[Household]:
        target = await self.get_household_by_invite_code(invite_code)
        if target is None:
            return None

        if is_group and chat_id is not None:
            # Unlink any previous household bound to this group chat
            prev_group_hh = await self.get_household_by_chat_id(chat_id)
            if prev_group_hh and prev_group_hh.id != target.id:
                prev_group_hh.telegram_chat_id = None
            target.telegram_chat_id = chat_id

        member = await self.get_member_by_telegram_id(telegram_user_id)
        if member is None:
            member = Member(
                telegram_user_id=telegram_user_id,
                display_name=display_name,
                household_id=target.id,
            )
            self.session.add(member)
        else:
            member.household_id = target.id
            member.display_name = display_name

        await self.session.commit()
        return target

    async def get_household_members(self, household_id: int) -> Sequence[Member]:
        stmt = (
            select(Member)
            .where(Member.household_id == household_id)
            .order_by(Member.joined_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_categories(self, household_id: int) -> Sequence[Category]:
        stmt = (
            select(Category)
            .where(Category.household_id == household_id)
            .order_by(Category.id.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_category_by_id(
        self, household_id: int, category_id: int
    ) -> Optional[Category]:
        stmt = select(Category).where(
            Category.household_id == household_id,
            Category.id == category_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create_category(
        self,
        household_id: int,
        name: str,
        display_name: Optional[str] = None,
        is_fixed: Optional[bool] = None,
    ) -> Category:
        norm = name.strip().lower()
        stmt = select(Category).where(
            Category.household_id == household_id,
            Category.name == norm,
        )
        result = await self.session.execute(stmt)
        cat = result.scalar_one_or_none()
        if cat is not None:
            if is_fixed is not None and cat.is_fixed != is_fixed:
                cat.is_fixed = is_fixed
                await self.session.commit()
            return cat

        cat = Category(
            household_id=household_id,
            name=norm,
            display_name=display_name or f"🏷️ {norm.capitalize()}",
            is_fixed=is_fixed if is_fixed is not None else (norm in FIXED_CATEGORY_NAMES),
        )
        self.session.add(cat)
        await self.session.commit()
        return cat

    async def set_household_budget(
        self, household_id: int, amount: Optional[Decimal]
    ) -> Optional[Household]:
        stmt = select(Household).where(Household.id == household_id)
        result = await self.session.execute(stmt)
        hh = result.scalar_one_or_none()
        if hh is None:
            return None
        hh.monthly_budget = amount
        await self.session.commit()
        return hh

    async def set_household_currency(
        self, household_id: int, currency: str
    ) -> Optional[Household]:
        stmt = select(Household).where(Household.id == household_id)
        result = await self.session.execute(stmt)
        hh = result.scalar_one_or_none()
        if hh is None:
            return None
        hh.currency = currency.strip()[:10]
        await self.session.commit()
        return hh

    async def set_category_budget(
        self, household_id: int, category_id: int, amount: Optional[Decimal]
    ) -> Optional[Category]:
        cat = await self.get_category_by_id(household_id, category_id)
        if cat is None:
            return None
        cat.monthly_budget = amount
        await self.session.commit()
        return cat

    async def update_category(
        self,
        household_id: int,
        category_id: int,
        *,
        display_name: Optional[str] = None,
        monthly_budget: Optional[Decimal] = None,
        clear_budget: bool = False,
        is_fixed: Optional[bool] = None,
    ) -> Optional[Category]:
        cat = await self.get_category_by_id(household_id, category_id)
        if cat is None:
            return None
        if display_name is not None and display_name.strip():
            cat.display_name = display_name.strip()[:80]
        if clear_budget:
            cat.monthly_budget = None
        elif monthly_budget is not None:
            cat.monthly_budget = monthly_budget
        if is_fixed is not None:
            cat.is_fixed = is_fixed
        await self.session.commit()
        return cat

    async def add_expense(
        self,
        household_id: int,
        member_id: int,
        category_id: int,
        amount: Decimal,
        description: str = "",
        spent_at: Optional[datetime] = None,
        is_once_off: bool = False,
        is_recurring: bool = False,
        source_chat_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
        bot_message_id: Optional[int] = None,
    ) -> Expense:
        expense = Expense(
            household_id=household_id,
            member_id=member_id,
            category_id=category_id,
            amount=amount,
            description=description.strip(),
            is_once_off=bool(is_once_off or is_recurring),
            is_recurring=bool(is_recurring),
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            bot_message_id=bot_message_id,
            spent_at=spent_at or settings.now(),
        )
        self.session.add(expense)
        await self.session.commit()
        await self.session.refresh(expense)
        return expense

    async def set_expense_bot_message_id(
        self, household_id: int, expense_id: int, bot_message_id: int
    ) -> Optional[Expense]:
        expense = await self.get_expense_by_id(household_id, expense_id)
        if expense is None:
            return None
        expense.bot_message_id = bot_message_id
        await self.session.commit()
        return expense

    async def get_expense_by_source_message(
        self, household_id: int, source_chat_id: int, source_message_id: int
    ) -> Optional[Expense]:
        stmt = (
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.member))
            .where(
                Expense.household_id == household_id,
                Expense.source_chat_id == source_chat_id,
                Expense.source_message_id == source_message_id,
            )
            .order_by(Expense.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_expense_by_bot_message(
        self, household_id: int, source_chat_id: int, bot_message_id: int
    ) -> Optional[Expense]:
        stmt = (
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.member))
            .where(
                Expense.household_id == household_id,
                Expense.source_chat_id == source_chat_id,
                Expense.bot_message_id == bot_message_id,
            )
            .order_by(Expense.id.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_expense_by_id(
        self, household_id: int, expense_id: int
    ) -> Optional[Expense]:
        stmt = (
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.member))
            .where(
                Expense.household_id == household_id,
                Expense.id == expense_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_expense(
        self,
        household_id: int,
        expense_id: int,
        *,
        amount: Optional[Decimal] = None,
        description: Optional[str] = None,
        category_id: Optional[int] = None,
        is_once_off: Optional[bool] = None,
        is_recurring: Optional[bool] = None,
    ) -> Optional[Expense]:
        expense = await self.get_expense_by_id(household_id, expense_id)
        if expense is None:
            return None
        if category_id is not None:
            cat = await self.get_category_by_id(household_id, category_id)
            if cat is None:
                return None
            expense.category_id = cat.id
        if amount is not None:
            expense.amount = amount
        if description is not None:
            expense.description = description.strip()[:255]
        if is_once_off is not None:
            expense.is_once_off = is_once_off
        if is_recurring is not None:
            expense.is_recurring = is_recurring
            if is_recurring:
                expense.is_once_off = True
        await self.session.commit()
        return await self.get_expense_by_id(household_id, expense_id)

    async def update_expense_category(
        self, household_id: int, expense_id: int, new_category_id: int
    ) -> Optional[Expense]:
        return await self.update_expense(
            household_id, expense_id, category_id=new_category_id
        )

    async def toggle_expense_once_off(
        self, household_id: int, expense_id: int
    ) -> Optional[Expense]:
        expense = await self.get_expense_by_id(household_id, expense_id)
        if expense is None:
            return None
        expense.is_once_off = not bool(expense.is_once_off)
        await self.session.commit()
        return await self.get_expense_by_id(household_id, expense_id)

    async def toggle_expense_recurring(
        self, household_id: int, expense_id: int
    ) -> Optional[Expense]:
        expense = await self.get_expense_by_id(household_id, expense_id)
        if expense is None:
            return None
        expense.is_recurring = not bool(expense.is_recurring)
        if expense.is_recurring:
            expense.is_once_off = True
        await self.session.commit()
        return await self.get_expense_by_id(household_id, expense_id)

    async def get_recurring_expenses(
        self, household_id: int
    ) -> Sequence[Expense]:
        stmt = (
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.member))
            .where(
                Expense.household_id == household_id,
                Expense.is_recurring == True,  # noqa: E712
            )
            .order_by(desc(Expense.spent_at), desc(Expense.id))
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def sync_recurring_expenses(
        self, household_id: int, now: Optional[datetime] = None
    ) -> list[Expense]:
        """
        Roll any active recurring expenses from past months into the current month
        (creating monthly entries up to the current month and moving the active
        is_recurring=True pointer to the latest month's entry).
        """
        now_local = (now or settings.now()).astimezone(settings.tz)
        target_ym = (now_local.year, now_local.month)

        stmt = select(Expense).where(
            Expense.household_id == household_id,
            Expense.is_recurring == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        active_recurring = result.scalars().all()

        spawned: list[Expense] = []
        for exp in active_recurring:
            exp_local = exp.spent_at.astimezone(settings.tz)
            cy, cm = exp_local.year, exp_local.month
            if (cy, cm) >= target_ym:
                continue

            # Historical entry keeps its data in its original month, no longer active template
            exp.is_recurring = False
            exp.is_once_off = True

            steps = 0
            while (cy, cm) < target_ym and steps < 12:
                steps += 1
                if cm == 12:
                    cy, cm = cy + 1, 1
                else:
                    cm += 1
                max_day = calendar.monthrange(cy, cm)[1]
                day = min(exp_local.day, max_day)
                spawn_dt = datetime(
                    cy,
                    cm,
                    day,
                    exp_local.hour,
                    exp_local.minute,
                    exp_local.second,
                    tzinfo=settings.tz,
                )
                is_current_month = (cy, cm) == target_ym or steps == 12
                new_exp = Expense(
                    household_id=household_id,
                    member_id=exp.member_id,
                    category_id=exp.category_id,
                    amount=exp.amount,
                    description=exp.description,
                    is_once_off=True,
                    is_recurring=is_current_month,
                    spent_at=spawn_dt,
                )
                self.session.add(new_exp)
                spawned.append(new_exp)

        if spawned:
            await self.session.commit()
        return spawned

    async def delete_expense(self, household_id: int, expense_id: int) -> bool:
        expense = await self.get_expense_by_id(household_id, expense_id)
        if expense is None:
            return False
        await self.session.delete(expense)
        await self.session.commit()
        return True

    async def get_expenses_in_range(
        self,
        household_id: int,
        start_dt: datetime,
        end_dt: datetime,
    ) -> Sequence[Expense]:
        stmt = (
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.member))
            .where(
                Expense.household_id == household_id,
                Expense.spent_at >= start_dt,
                Expense.spent_at < end_dt,
            )
            .order_by(Expense.spent_at.desc(), Expense.id.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_recent_expenses(
        self, household_id: int, limit: int = 10
    ) -> Sequence[Expense]:
        stmt = (
            select(Expense)
            .options(selectinload(Expense.category), selectinload(Expense.member))
            .where(Expense.household_id == household_id)
            .order_by(desc(Expense.spent_at), desc(Expense.id))
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_period_total(
        self,
        household_id: int,
        start_dt: datetime,
        end_dt: datetime,
        category_id: Optional[int] = None,
    ) -> Decimal:
        stmt = select(func.coalesce(func.sum(Expense.amount), 0)).where(
            Expense.household_id == household_id,
            Expense.spent_at >= start_dt,
            Expense.spent_at < end_dt,
        )
        if category_id is not None:
            stmt = stmt.where(Expense.category_id == category_id)
        result = await self.session.execute(stmt)
        val = result.scalar_one()
        return Decimal(str(val))

