from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db.models import Category, Expense
from bot.services.analytics import next_month, prev_month


def expense_actions_kb(
    expense_id: int,
    categories: Sequence[Category] = (),
    show_category_picker: bool = False,
    is_once_off: bool = False,
    is_recurring: bool = False,
    show_back_to_recent: bool = False,
    back_target: str | None = None,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    once_label = "📌 Once-Off ✅" if is_once_off else "📌 Once-Off"
    rec_label = "🔁 Recurring ✅" if is_recurring else "🔁 Recurring"
    resolved_back = back_target or ("recent" if show_back_to_recent else None)

    if show_category_picker and categories:
        for cat in categories:
            builder.button(
                text=cat.display_name,
                callback_data=f"exp_setcat:{expense_id}:{cat.id}",
            )
        builder.adjust(2)
        builder.row(
            InlineKeyboardButton(
                text="💰 Amount",
                callback_data=f"exp_editamt:{expense_id}",
            ),
            InlineKeyboardButton(
                text="📝 Note",
                callback_data=f"exp_editnote:{expense_id}",
            ),
        )
        builder.row(
            InlineKeyboardButton(
                text=once_label,
                callback_data=f"exp_once:{expense_id}",
            ),
            InlineKeyboardButton(
                text=rec_label,
                callback_data=f"exp_rec:{expense_id}",
            ),
            InlineKeyboardButton(
                text="🗑️ Undo",
                callback_data=f"exp_del:{expense_id}",
            ),
        )
        if resolved_back == "recent":
            builder.row(
                InlineKeyboardButton(
                    text="⬅️ Back to Recent",
                    callback_data="exp_recent",
                )
            )
        elif resolved_back == "recurring":
            builder.row(
                InlineKeyboardButton(
                    text="⬅️ Back to Recurring",
                    callback_data="exp_recurring_list",
                )
            )
        return builder.as_markup()

    builder.row(
        InlineKeyboardButton(
            text="📂 Category",
            callback_data=f"exp_pickcat:{expense_id}",
        ),
        InlineKeyboardButton(
            text="💰 Amount",
            callback_data=f"exp_editamt:{expense_id}",
        ),
        InlineKeyboardButton(
            text="📝 Note",
            callback_data=f"exp_editnote:{expense_id}",
        ),
    )
    builder.row(
        InlineKeyboardButton(
            text=once_label,
            callback_data=f"exp_once:{expense_id}",
        ),
        InlineKeyboardButton(
            text=rec_label,
            callback_data=f"exp_rec:{expense_id}",
        ),
        InlineKeyboardButton(
            text="🗑️ Undo",
            callback_data=f"exp_del:{expense_id}",
        ),
    )
    if resolved_back == "recent":
        builder.row(
            InlineKeyboardButton(
                text="⬅️ Back to Recent",
                callback_data="exp_recent",
            )
        )
    elif resolved_back == "recurring":
        builder.row(
            InlineKeyboardButton(
                text="⬅️ Back to Recurring",
                callback_data="exp_recurring_list",
            )
        )
    return builder.as_markup()


def cancel_edit_kb(expense_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✖️ Cancel",
            callback_data=f"exp_canceledit:{expense_id}",
        )
    )
    return builder.as_markup()


def report_nav_kb(year: int, month: int) -> InlineKeyboardMarkup:
    py, pm = prev_month(year, month)
    ny, nm = next_month(year, month)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"◀️ {py}-{pm:02d}",
            callback_data=f"rep_month:{py}:{pm}",
        ),
        InlineKeyboardButton(
            text="📈 Vs Prev Month",
            callback_data=f"rep_cmp:{year}:{month}",
        ),
        InlineKeyboardButton(
            text=f"{ny}-{nm:02d} ▶️",
            callback_data=f"rep_month:{ny}:{nm}",
        ),
    )
    return builder.as_markup()


def compare_nav_kb(year: int, month: int) -> InlineKeyboardMarkup:
    py, pm = prev_month(year, month)
    ny, nm = next_month(year, month)
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text=f"◀️ {py}-{pm:02d}",
            callback_data=f"rep_cmp:{py}:{pm}",
        ),
        InlineKeyboardButton(
            text="📊 Month Report",
            callback_data=f"rep_month:{year}:{month}",
        ),
        InlineKeyboardButton(
            text=f"{ny}-{nm:02d} ▶️",
            callback_data=f"rep_cmp:{ny}:{nm}",
        ),
    )
    return builder.as_markup()


def recent_expenses_kb(
    expenses: Sequence[Expense],
    currency: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for exp in expenses[:10]:
        cat_label = exp.category.display_name.split()[0] if exp.category else "📦"
        short_desc = ""
        if exp.description:
            trimmed = exp.description.strip()
            short_desc = f" · {trimmed[:10]}…" if len(trimmed) > 11 else f" · {trimmed}"
        builder.button(
            text=f"✏️ {cat_label} {currency}{exp.amount:.2f}{short_desc}",
            callback_data=f"exp_open:{exp.id}",
        )
    builder.adjust(2)
    return builder.as_markup()


def recurring_expenses_kb(
    expenses: Sequence[Expense],
    currency: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for exp in expenses[:25]:
        cat_label = exp.category.display_name.split()[0] if exp.category else "📦"
        short_desc = ""
        if exp.description:
            trimmed = exp.description.strip()
            short_desc = f" · {trimmed[:10]}…" if len(trimmed) > 11 else f" · {trimmed}"
        builder.button(
            text=f"✏️ {cat_label} {currency}{exp.amount:.2f}{short_desc}",
            callback_data=f"exp_openrec:{exp.id}",
        )
    builder.adjust(2)
    return builder.as_markup()

