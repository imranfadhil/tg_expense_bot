from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from bot.config import settings
from bot.db.models import Category, Expense, Household, Member
from bot.db.repo import ExpenseRepo
from bot.keyboards.inline import expense_actions_kb
from bot.services.analytics import (
    check_budget_alerts,
    get_month_bounds,
    is_fixed_category,
    is_once_off_expense,
)
from bot.services.parser import parse_expense_text

router = Router(name="expenses")


async def _format_expense_card(
    repo: ExpenseRepo,
    household: Household,
    expense: Expense,
    category: Category,
    member: Member | None,
    prompt_category_selection: bool = False,
) -> str:
    cur = html.escape(household.currency)
    now = expense.spent_at.astimezone(settings.tz)
    start_dt, end_dt = get_month_bounds(now.year, now.month, settings.tz)

    cat_month_total = await repo.get_period_total(
        household_id=household.id,
        start_dt=start_dt,
        end_dt=end_dt,
        category_id=category.id,
    )
    hh_month_total = await repo.get_period_total(
        household_id=household.id,
        start_dt=start_dt,
        end_dt=end_dt,
    )

    desc_line = (
        f"📝 <b>Note:</b> {html.escape(expense.description)}\n"
        if expense.description
        else ""
    )
    who_line = (
        f"👤 <b>Logged by:</b> {html.escape(member.display_name)}\n"
        if member
        else ""
    )

    lines = [
        f"✅ <b>Expense Recorded: {cur}{expense.amount:,.2f}</b>",
        f"📂 <b>Category:</b> {html.escape(category.display_name)}",
    ]
    if is_once_off_expense(expense, category):
        lines.append("📌 <b>Pace Mode:</b> Fixed / Once-Off <i>(counted 1x in month projection)</i>")
    if desc_line:
        lines.append(desc_line.rstrip("\n"))
    if who_line:
        lines.append(who_line.rstrip("\n"))

    lines.append("")
    lines.append(
        f"📊 <b>{now.strftime('%b')} {html.escape(category.display_name)}:</b> {cur}{cat_month_total:,.2f}  "
        f"|  <b>Month Total:</b> {cur}{hh_month_total:,.2f}"
    )

    alerts = check_budget_alerts(
        currency=cur,
        category=category,
        cat_month_total=cat_month_total,
        household_budget=household.monthly_budget,
        household_month_total=hh_month_total,
    )
    for alert in alerts:
        lines.append(alert.message)

    if prompt_category_selection:
        lines.append("")
        lines.append(
            "💡 <i>Category defaulted to Misc — tap a category below if you'd like to reclassify:</i>"
        )

    return "\n".join(lines)


async def _log_from_text(
    message: Message,
    raw_text: str,
    repo: ExpenseRepo,
    household: Household,
    member: Member,
    explicit_command: bool = False,
) -> None:
    categories = await repo.get_categories(household.id)
    cat_by_name = {c.name.lower(): c for c in categories}

    parsed = parse_expense_text(raw_text, available_category_keys=cat_by_name.keys())
    if parsed is None:
        if explicit_command:
            await message.answer(
                "Usage: <code>/add 45.50 groceries Giant supermarket</code>\n"
                "Or simply send a message like <code>12.80 lunch Maxwell</code> "
                "(add <code>#once</code> for a one-off expense)!",
                parse_mode="HTML",
            )
        return

    category = cat_by_name.get(parsed.category_key)
    if category is None:
        category = cat_by_name.get("misc") or categories[0]

    once_off_flag = parsed.is_once_off or is_fixed_category(category)
    expense = await repo.add_expense(
        household_id=household.id,
        member_id=member.id,
        category_id=category.id,
        amount=parsed.amount,
        description=parsed.description,
        spent_at=settings.now(),
        is_once_off=once_off_flag,
    )

    show_picker = not parsed.matched_explicitly
    card_text = await _format_expense_card(
        repo=repo,
        household=household,
        expense=expense,
        category=category,
        member=member,
        prompt_category_selection=show_picker,
    )
    await message.answer(
        card_text,
        parse_mode="HTML",
        reply_markup=expense_actions_kb(
            expense_id=expense.id,
            categories=categories,
            show_category_picker=show_picker,
            is_once_off=is_once_off_expense(expense, category),
        ),
    )


@router.message(Command("add", "log", "spend"))
async def cmd_add_expense(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
    member: Member,
) -> None:
    await _log_from_text(
        message=message,
        raw_text=command.args or "",
        repo=repo,
        household=household,
        member=member,
        explicit_command=True,
    )


@router.message(F.text & ~F.text.startswith("/"))
async def handle_quick_text_expense(
    message: Message,
    repo: ExpenseRepo,
    household: Household,
    member: Member,
) -> None:
    if not message.text:
        return
    await _log_from_text(
        message=message,
        raw_text=message.text,
        repo=repo,
        household=household,
        member=member,
        explicit_command=False,
    )


@router.callback_query(F.data.startswith("exp_pickcat:"))
async def cb_pick_category(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return

    expense_id = int(callback.data.split(":")[1])
    expense = await repo.get_expense_by_id(household.id, expense_id)
    if expense is None:
        await callback.answer("Expense no longer exists.", show_alert=True)
        return

    categories = await repo.get_categories(household.id)
    await callback.message.edit_reply_markup(
        reply_markup=expense_actions_kb(
            expense_id=expense.id,
            categories=categories,
            show_category_picker=True,
            is_once_off=is_once_off_expense(expense, expense.category),
        )
    )
    await callback.answer("Select a category:")


@router.callback_query(F.data.startswith("exp_setcat:"))
async def cb_set_category(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return

    parts = callback.data.split(":")
    expense_id, new_cat_id = int(parts[1]), int(parts[2])
    updated = await repo.update_expense_category(household.id, expense_id, new_cat_id)
    if updated is None or updated.category is None:
        await callback.answer("Expense or category not found.", show_alert=True)
        return

    card_text = await _format_expense_card(
        repo=repo,
        household=household,
        expense=updated,
        category=updated.category,
        member=updated.member,
        prompt_category_selection=False,
    )
    await callback.message.edit_text(
        card_text,
        parse_mode="HTML",
        reply_markup=expense_actions_kb(
            expense_id=updated.id,
            show_category_picker=False,
            is_once_off=is_once_off_expense(updated, updated.category),
        ),
    )
    await callback.answer(f"Category updated to {updated.category.display_name}")


@router.callback_query(F.data.startswith("exp_once:"))
async def cb_toggle_once_off(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return

    expense_id = int(callback.data.split(":")[1])
    expense = await repo.get_expense_by_id(household.id, expense_id)
    if expense is None or expense.category is None:
        await callback.answer("Expense no longer exists.", show_alert=True)
        return

    if is_fixed_category(expense.category):
        await callback.answer(
            f"📌 {expense.category.display_name} is a Fixed/Once-off category and is always counted 1x in month-end projections.",
            show_alert=True,
        )
        return

    updated = await repo.toggle_expense_once_off(household.id, expense_id)
    if updated is None or updated.category is None:
        await callback.answer("Expense no longer exists.", show_alert=True)
        return

    card_text = await _format_expense_card(
        repo=repo,
        household=household,
        expense=updated,
        category=updated.category,
        member=updated.member,
        prompt_category_selection=False,
    )
    await callback.message.edit_text(
        card_text,
        parse_mode="HTML",
        reply_markup=expense_actions_kb(
            expense_id=updated.id,
            show_category_picker=False,
            is_once_off=is_once_off_expense(updated, updated.category),
        ),
    )
    status_str = "Marked as Once-Off (counted 1x in projection)" if updated.is_once_off else "Set to regular daily variable spend"
    await callback.answer(status_str)


@router.callback_query(F.data.startswith("exp_del:"))
async def cb_delete_expense(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return

    expense_id = int(callback.data.split(":")[1])
    exp = await repo.get_expense_by_id(household.id, expense_id)
    if exp is None:
        await callback.answer("Expense already deleted.", show_alert=True)
        return

    cur = html.escape(household.currency)
    amt_str = f"{cur}{exp.amount:,.2f}"
    cat_str = html.escape(exp.category.display_name if exp.category else "Expense")
    desc_str = f" ({html.escape(exp.description)})" if exp.description else ""

    await repo.delete_expense(household.id, expense_id)
    await callback.message.edit_text(
        f"🗑️ <s><b>{amt_str}</b> · {cat_str}{desc_str}</s> — <i>Deleted</i>",
        parse_mode="HTML",
    )
    await callback.answer("Expense deleted")

