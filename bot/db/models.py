from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_CATEGORIES: list[tuple[str, str]] = [
    ("groceries", "🛒 Groceries"),
    ("dining", "🍽️ Dining"),
    ("transport", "🚌 Transport"),
    ("utilities", "💡 Utilities"),
    ("housing", "🏠 Housing"),
    ("auto", "🚗 Car Loan"),
    ("school", "🎓 Kids School Fee"),
    ("saving", "🏦 Saving"),
    ("kids", "👶 Kids & Family"),
    ("health", "🏥 Health"),
    ("shopping", "🛍️ Shopping"),
    ("gift", "🎁 Gift"),
    ("entertainment", "🎬 Entertainment"),
    ("misc", "📦 Misc"),
]

# Categories whose expenses are fixed/monthly one-offs rather than daily recurring spend
FIXED_CATEGORY_NAMES: frozenset[str] = frozenset(
    {
        "housing",
        "utilities",
        "auto",
        "school",
        "saving",
        "gift",
    }
)


class Household(Base):
    __tablename__ = "households"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    invite_code: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, unique=True, index=True, nullable=True
    )
    currency: Mapped[str] = mapped_column(String(10), default="$", nullable=False)
    monthly_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    members: Mapped[List["Member"]] = relationship(
        "Member", back_populates="household", cascade="all, delete-orphan"
    )
    categories: Mapped[List["Category"]] = relationship(
        "Category", back_populates="household", cascade="all, delete-orphan"
    )
    expenses: Mapped[List["Expense"]] = relationship(
        "Expense", back_populates="household", cascade="all, delete-orphan"
    )


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    household: Mapped["Household"] = relationship("Household", back_populates="members")
    expenses: Mapped[List["Expense"]] = relationship("Expense", back_populates="member")


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        UniqueConstraint("household_id", "name", name="uq_household_category_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str] = mapped_column(String(80), nullable=False)
    monthly_budget: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    is_fixed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )

    household: Mapped["Household"] = relationship("Household", back_populates="categories")
    expenses: Mapped[List["Expense"]] = relationship("Expense", back_populates="category")


class Expense(Base):
    __tablename__ = "expenses"
    __table_args__ = (
        Index("ix_expenses_household_spent_at", "household_id", "spent_at"),
        Index(
            "ix_expenses_source_msg",
            "household_id",
            "source_chat_id",
            "source_message_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    household_id: Mapped[int] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), nullable=False
    )
    member_id: Mapped[int] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    is_once_off: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0", nullable=False
    )
    source_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    bot_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    spent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    household: Mapped["Household"] = relationship("Household", back_populates="expenses")
    member: Mapped[Optional["Member"]] = relationship("Member", back_populates="expenses")
    category: Mapped["Category"] = relationship("Category", back_populates="expenses")


class AuthorizedUser(Base):
    __tablename__ = "authorized_users"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    passphrase_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    authorized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


