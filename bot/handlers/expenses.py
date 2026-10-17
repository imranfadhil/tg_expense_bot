from __future__ import annotations

import html
import re
import time
from dataclasses import dataclass, field

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.config import settings
from bot.db.models import Category, Expense, Household, Member
from bot.db.repo import ExpenseRepo
from bot.keyboards.inline import cancel_edit_kb, expense_actions_kb
from bot.services.analytics import (
    check_budget_alerts,
    get_month_bounds,
    is_fixed_category,
    is_once_off_expense,
)
from bot.services.parser import parse_amount_only, parse_expense_text

router = Router(name="expenses")

PENDING_EDIT_TTL_SECONDS = 300.0
_ADD_CMD_PREFIX_RE = re.compile(r"^/(?:add|log|spend)(?:@\S+)?\s+", re.IGNORECASE)


@dataclass
class PendingEdit:
    expense_id: int
    field: str  # "amount" or "note"
    card_chat_id: int
    card_message_id: int
    prompt_message_id: int | None = None
    show_back_to_recent: bool = False
    created_at: float = field(default_factory=time.monotonic)


# Keyed by (chat_id, telegram_user_id)
_PENDING_EDITS: dict[tuple[int, int], PendingEdit] = {}


def _get_pending_edit(chat_id: int, user_id: int) -> PendingEdit | None:
    key = (chat_id, user_id)
    pending = _PENDING_EDITS.get(key)
    if pending is None:
        return None
    if time.monotonic() - pending.created_at > PENDING_EDIT_TTL_SECONDS:
        _PENDING_EDITS.pop(key, None)
        return None
    return pending


def _clear_pending_edit(chat_id: int, user_id: int) -> PendingEdit | None:
    return _PENDING_EDITS.pop((chat_id, user_id), None)


def _has_back_to_recent(message: Message | None) -> bool:
    if not message or not message.reply_markup or not message.reply_markup.inline_keyboard:
        return False
    for row in message.reply_markup.inline_keyboard:
        for btn in row:
            if btn.callback_data == "exp_recent":
                return True
    return False


async def _safe_delete_msg(bot, chat_id: int, message_id: int | None) -> None:
    if not bot or not message_id:
        return
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass


async def _format_expense_card(
    repo: ExpenseRepo,
    household: Household,
    expense: Expense,
    category: Category,
    member: Member | None,
    prompt_category_selection: bool = False,
    edited: bool = False,
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

    header = (
        f"✏️ <b>Expense Updated: {cur}{expense.amount:,.2f}</b>"
        if edited
        else f"✅ <b>Expense Recorded: {cur}{expense.amount:,.2f}</b>"
    )
    lines = [
        header,
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
        source_chat_id=message.chat.id if message.chat else None,
        source_message_id=message.message_id,
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
    sent = await message.answer(
        card_text,
        parse_mode="HTML",
        reply_markup=expense_actions_kb(
            expense_id=expense.id,
            categories=categories,
            show_category_picker=show_picker,
            is_once_off=is_once_off_expense(expense, category),
        ),
    )
    if sent and sent.message_id:
        await repo.set_expense_bot_message_id(household.id, expense.id, sent.message_id)


async def _handle_pending_edit_input(
    message: Message,
    pending: PendingEdit,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    raw = (message.text or "").strip()
    expense = await repo.get_expense_by_id(household.id, pending.expense_id)
    if expense is None or expense.category is None:
        _clear_pending_edit(message.chat.id, message.from_user.id)
        await _safe_delete_msg(message.bot, message.chat.id, pending.prompt_message_id)
        await message.answer("⚠️ That expense no longer exists.", parse_mode="HTML")
        return

    updated: Expense | None = None
    if pending.field == "amount":
        amt = parse_amount_only(raw)
        if amt is not None:
            updated = await repo.update_expense(
                household.id, expense.id, amount=amt
            )
        else:
            categories = await repo.get_categories(household.id)
            cat_by_name = {c.name.lower(): c for c in categories}
            parsed = parse_expense_text(raw, available_category_keys=cat_by_name.keys())
            if parsed is None:
                cur = html.escape(household.currency)
                await message.answer(
                    f"❌ Invalid amount. Please send a number like <code>{cur}18.50</code> "
                    f"or tap <b>✖️ Cancel</b>.",
                    parse_mode="HTML",
                    reply_markup=cancel_edit_kb(expense.id),
                )
                return
            new_cat_id = (
                cat_by_name[parsed.category_key].id
                if parsed.matched_explicitly and parsed.category_key in cat_by_name
                else None
            )
            new_desc = parsed.description if parsed.description else None
            updated = await repo.update_expense(
                household.id,
                expense.id,
                amount=parsed.amount,
                category_id=new_cat_id,
                description=new_desc,
            )
    elif pending.field == "note":
        new_note = "" if raw.lower() in ("-", "clear", "none") else raw
        updated = await repo.update_expense(
            household.id, expense.id, description=new_note
        )

    _clear_pending_edit(message.chat.id, message.from_user.id)
    await _safe_delete_msg(message.bot, message.chat.id, pending.prompt_message_id)
    await _safe_delete_msg(message.bot, message.chat.id, message.message_id)

    if updated is None or updated.category is None:
        return

    card_text = await _format_expense_card(
        repo=repo,
        household=household,
        expense=updated,
        category=updated.category,
        member=updated.member,
        prompt_category_selection=False,
        edited=True,
    )
    kb = expense_actions_kb(
        expense_id=updated.id,
        show_category_picker=False,
        is_once_off=is_once_off_expense(updated, updated.category),
        show_back_to_recent=pending.show_back_to_recent,
    )
    if message.bot and pending.card_message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=pending.card_chat_id,
                message_id=pending.card_message_id,
                text=card_text,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return
        except Exception:
            pass

    await message.answer(card_text, parse_mode="HTML", reply_markup=kb)


@router.message(Command("cancel"))
async def cmd_cancel_edit(message: Message) -> None:
    if not message.from_user:
        return
    pending = _clear_pending_edit(message.chat.id, message.from_user.id)
    if pending:
        await _safe_delete_msg(message.bot, message.chat.id, pending.prompt_message_id)
        await message.answer("✖️ Edit cancelled.", parse_mode="HTML")
    else:
        await message.answer("<i>No active edit to cancel.</i>", parse_mode="HTML")


@router.message(Command("add", "log", "spend"))
async def cmd_add_expense(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
    member: Member,
) -> None:
    if message.from_user:
        _clear_pending_edit(message.chat.id, message.from_user.id)
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
    if message.from_user:
        pending = _get_pending_edit(message.chat.id, message.from_user.id)
        if pending is not None:
            await _handle_pending_edit_input(message, pending, repo, household)
            return

    await _log_from_text(
        message=message,
        raw_text=message.text,
        repo=repo,
        household=household,
        member=member,
        explicit_command=False,
    )


@router.edited_message(F.text)
async def handle_edited_expense_message(
    message: Message,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not message.text or not message.chat:
        return

    raw_text = message.text.strip()
    if raw_text.startswith("/"):
        m = _ADD_CMD_PREFIX_RE.match(raw_text)
        if not m:
            return
        raw_text = raw_text[m.end() :].strip()

    existing = await repo.get_expense_by_source_message(
        household.id, message.chat.id, message.message_id
    )
    if existing is None:
        return

    categories = await repo.get_categories(household.id)
    cat_by_name = {c.name.lower(): c for c in categories}
    parsed = parse_expense_text(raw_text, available_category_keys=cat_by_name.keys())
    if parsed is None:
        return

    if parsed.matched_explicitly:
        category = cat_by_name.get(parsed.category_key) or existing.category or categories[0]
    else:
        # Preserve manually chosen category if the edit didn't specify a new category keyword
        category = existing.category or cat_by_name.get("misc") or categories[0]

    once_off_flag = parsed.is_once_off or is_fixed_category(category)
    updated = await repo.update_expense(
        household_id=household.id,
        expense_id=existing.id,
        amount=parsed.amount,
        category_id=category.id,
        description=parsed.description,
        is_once_off=once_off_flag,
    )
    if updated is None or updated.category is None:
        return

    card_text = await _format_expense_card(
        repo=repo,
        household=household,
        expense=updated,
        category=updated.category,
        member=updated.member,
        prompt_category_selection=False,
        edited=True,
    )
    kb = expense_actions_kb(
        expense_id=updated.id,
        categories=categories,
        show_category_picker=False,
        is_once_off=is_once_off_expense(updated, updated.category),
    )

    if updated.bot_message_id and message.bot:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=updated.bot_message_id,
                text=card_text,
                parse_mode="HTML",
                reply_markup=kb,
            )
            return
        except Exception as exc:
            if "message is not modified" in str(exc).lower():
                return

    sent = await message.answer(card_text, parse_mode="HTML", reply_markup=kb)
    if sent and sent.message_id:
        await repo.set_expense_bot_message_id(household.id, updated.id, sent.message_id)


@router.callback_query(F.data.startswith("exp_open:"))
async def cb_open_expense(
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

    card_text = await _format_expense_card(
        repo=repo,
        household=household,
        expense=expense,
        category=expense.category,
        member=expense.member,
        prompt_category_selection=False,
    )
    await callback.message.edit_text(
        card_text,
        parse_mode="HTML",
        reply_markup=expense_actions_kb(
            expense_id=expense.id,
            show_category_picker=False,
            is_once_off=is_once_off_expense(expense, expense.category),
            show_back_to_recent=True,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exp_editamt:"))
async def cb_edit_amount(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.data or not callback.message or not callback.from_user:
        await callback.answer()
        return

    expense_id = int(callback.data.split(":")[1])
    expense = await repo.get_expense_by_id(household.id, expense_id)
    if expense is None:
        await callback.answer("Expense no longer exists.", show_alert=True)
        return

    prev = _clear_pending_edit(callback.message.chat.id, callback.from_user.id)
    if prev:
        await _safe_delete_msg(callback.bot, callback.message.chat.id, prev.prompt_message_id)

    cur = html.escape(household.currency)
    prompt_msg = await callback.message.answer(
        f"💰 <b>Edit Amount</b> (current: <b>{cur}{expense.amount:,.2f}</b>)\n"
        f"Send the new amount (e.g. <code>24.50</code>):",
        parse_mode="HTML",
        reply_markup=cancel_edit_kb(expense.id),
    )
    _PENDING_EDITS[(callback.message.chat.id, callback.from_user.id)] = PendingEdit(
        expense_id=expense.id,
        field="amount",
        card_chat_id=callback.message.chat.id,
        card_message_id=callback.message.message_id,
        prompt_message_id=prompt_msg.message_id if prompt_msg else None,
        show_back_to_recent=_has_back_to_recent(callback.message),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exp_editnote:"))
async def cb_edit_note(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.data or not callback.message or not callback.from_user:
        await callback.answer()
        return

    expense_id = int(callback.data.split(":")[1])
    expense = await repo.get_expense_by_id(household.id, expense_id)
    if expense is None:
        await callback.answer("Expense no longer exists.", show_alert=True)
        return

    prev = _clear_pending_edit(callback.message.chat.id, callback.from_user.id)
    if prev:
        await _safe_delete_msg(callback.bot, callback.message.chat.id, prev.prompt_message_id)

    curr_note = html.escape(expense.description) if expense.description else "<i>(empty)</i>"
    prompt_msg = await callback.message.answer(
        f"📝 <b>Edit Note</b> (current: {curr_note})\n"
        f"Send the new note/description (or send <code>-</code> to clear):",
        parse_mode="HTML",
        reply_markup=cancel_edit_kb(expense.id),
    )
    _PENDING_EDITS[(callback.message.chat.id, callback.from_user.id)] = PendingEdit(
        expense_id=expense.id,
        field="note",
        card_chat_id=callback.message.chat.id,
        card_message_id=callback.message.message_id,
        prompt_message_id=prompt_msg.message_id if prompt_msg else None,
        show_back_to_recent=_has_back_to_recent(callback.message),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("exp_canceledit:"))
async def cb_cancel_edit(callback: CallbackQuery) -> None:
    if not callback.message or not callback.from_user:
        await callback.answer()
        return
    _clear_pending_edit(callback.message.chat.id, callback.from_user.id)
    await _safe_delete_msg(
        callback.bot, callback.message.chat.id, callback.message.message_id
    )
    await callback.answer("Edit cancelled")


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
            show_back_to_recent=_has_back_to_recent(callback.message),
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
    back_to_recent = _has_back_to_recent(callback.message)
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
        edited=True,
    )
    await callback.message.edit_text(
        card_text,
        parse_mode="HTML",
        reply_markup=expense_actions_kb(
            expense_id=updated.id,
            show_category_picker=False,
            is_once_off=is_once_off_expense(updated, updated.category),
            show_back_to_recent=back_to_recent,
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
    back_to_recent = _has_back_to_recent(callback.message)
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
        edited=True,
    )
    await callback.message.edit_text(
        card_text,
        parse_mode="HTML",
        reply_markup=expense_actions_kb(
            expense_id=updated.id,
            show_category_picker=False,
            is_once_off=is_once_off_expense(updated, updated.category),
            show_back_to_recent=back_to_recent,
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
    back_to_recent = _has_back_to_recent(callback.message)
    exp = await repo.get_expense_by_id(household.id, expense_id)
    if exp is None:
        await callback.answer("Expense already deleted.", show_alert=True)
        return

    cur = html.escape(household.currency)
    amt_str = f"{cur}{exp.amount:,.2f}"
    cat_str = html.escape(exp.category.display_name if exp.category else "Expense")
    desc_str = f" ({html.escape(exp.description)})" if exp.description else ""

    await repo.delete_expense(household.id, expense_id)
    reply_markup = None
    if back_to_recent:
        builder = InlineKeyboardBuilder()
        builder.row(
            InlineKeyboardButton(
                text="⬅️ Back to Recent",
                callback_data="exp_recent",
            )
        )
        reply_markup = builder.as_markup()

    await callback.message.edit_text(
        f"🗑️ <s><b>{amt_str}</b> · {cat_str}{desc_str}</s> — <i>Deleted</i>",
        parse_mode="HTML",
        reply_markup=reply_markup,
    )
    await callback.answer("Expense deleted")

