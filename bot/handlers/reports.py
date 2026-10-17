from __future__ import annotations

import csv
import html
import io
import re
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message

from bot.config import settings
from bot.db.models import Household, Member
from bot.db.repo import ExpenseRepo
from bot.keyboards.inline import (
    compare_nav_kb,
    dashboard_launch_kb,
    recent_expenses_kb,
    recurring_expenses_kb,
    report_nav_kb,
)
from bot.services.analytics import (
    build_month_comparison,
    build_monthly_report,
    format_comparison_message,
    format_monthly_report_message,
    get_month_bounds,
    is_once_off_expense,
    prev_month,
)
from bot.webapp.auth import create_dashboard_token, resolve_public_webapp_url

router = Router(name="reports")

_YEAR_MONTH_RE = re.compile(r"^(?P<year>20\d{2})[-/](?P<month>0?[1-9]|1[0-2])$")


def _parse_year_month(arg: str | None) -> tuple[int, int]:
    now = settings.now()
    if not arg or not arg.strip():
        return now.year, now.month
    m = _YEAR_MONTH_RE.match(arg.strip())
    if not m:
        return now.year, now.month
    return int(m.group("year")), int(m.group("month"))


async def _build_signed_dashboard_url(
    household: Household,
    member: Member | None = None,
    year: int | None = None,
    month: int | None = None,
) -> tuple[str | None, str]:
    token = create_dashboard_token(
        household_id=household.id,
        member_id=member.id if member else None,
        telegram_user_id=member.telegram_user_id if member else None,
    )
    qs = f"token={token}"
    if year and month:
        qs += f"&year={year}&month={month}"

    base_url = await resolve_public_webapp_url()
    if base_url:
        return f"{base_url}/?{qs}", token
    return None, token


async def _render_monthly_report(
    repo: ExpenseRepo,
    household: Household,
    year: int,
    month: int,
) -> str:
    now = settings.now()
    start_dt, end_dt = get_month_bounds(year, month, settings.tz)
    categories = await repo.get_categories(household.id)
    expenses = await repo.get_expenses_in_range(household.id, start_dt, end_dt)
    report = build_monthly_report(
        household=household,
        categories=categories,
        expenses=expenses,
        year=year,
        month=month,
        now=now,
    )
    return format_monthly_report_message(report)


async def _render_comparison_report(
    repo: ExpenseRepo,
    household: Household,
    year: int,
    month: int,
) -> str:
    now = settings.now()
    curr_start, curr_end = get_month_bounds(year, month, settings.tz)
    py, pm = prev_month(year, month)
    prev_start, prev_end = get_month_bounds(py, pm, settings.tz)

    categories = await repo.get_categories(household.id)
    curr_expenses = await repo.get_expenses_in_range(household.id, curr_start, curr_end)
    prev_expenses = await repo.get_expenses_in_range(household.id, prev_start, prev_end)

    comp = build_month_comparison(
        household=household,
        categories=categories,
        curr_expenses=curr_expenses,
        prev_expenses=prev_expenses,
        curr_year=year,
        curr_month=month,
        now=now,
    )
    return format_comparison_message(comp)


@router.message(Command("dashboard", "app", "live", "webapp"))
async def cmd_dashboard(
    message: Message,
    command: CommandObject,
    household: Household,
    member: Member | None = None,
) -> None:
    year, month = _parse_year_month(command.args if command else None)
    dash_url, token = await _build_signed_dashboard_url(
        household=household,
        member=member,
        year=year,
        month=month,
    )
    is_private = bool(message.chat and message.chat.type == "private")
    local_url = f"http://localhost:{settings.webapp_port}/?token={token}&year={year}&month={month}"

    if dash_url:
        kb = dashboard_launch_kb(dash_url, is_private_chat=is_private)
        mode_hint = (
            "Tap the button below to launch the interactive <b>Telegram Mini App</b> directly inside Telegram:"
            if (is_private and dash_url.startswith("https://"))
            else "Tap the button below to open your interactive <b>Live Financial Dashboard</b>:"
        )
        await message.answer(
            f"📱 <b>{html.escape(household.name)} — Live Dashboard</b>\n\n"
            f"{mode_hint}\n"
            f"• 📊 Interactive spend trajectory & category donut charts\n"
            f"• 🧾 Search, filter, add, edit & delete expenses\n"
            f"• 🎯 Manage monthly & category budgets in real time\n"
            f"• 🔁 Manage monthly recurring commitments",
            parse_mode="HTML",
            reply_markup=kb,
        )
    else:
        await message.answer(
            f"📱 <b>{html.escape(household.name)} — Live Dashboard</b>\n\n"
            f"🌐 <b>Local Browser Link:</b>\n"
            f"<code>{html.escape(local_url)}</code>\n\n"
            f"💡 <i>To open as an embedded Telegram Mini App button, set <code>WEBAPP_URL=https://...</code> "
            f"in <code>.env</code> or run with the built-in Docker Compose <code>tunnel</code> service.</i>",
            parse_mode="HTML",
        )


@router.message(Command("report", "month", "summary"))
async def cmd_report(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
    member: Member | None = None,
) -> None:
    year, month = _parse_year_month(command.args)
    text = await _render_monthly_report(repo, household, year, month)
    dash_url, _ = await _build_signed_dashboard_url(household, member, year, month)
    is_private = bool(message.chat and message.chat.type == "private")
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=report_nav_kb(
            year, month, dashboard_url=dash_url, is_private_chat=is_private
        ),
    )


@router.message(Command("compare", "mom"))
async def cmd_compare(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
    member: Member | None = None,
) -> None:
    year, month = _parse_year_month(command.args)
    text = await _render_comparison_report(repo, household, year, month)
    dash_url, _ = await _build_signed_dashboard_url(household, member, year, month)
    is_private = bool(message.chat and message.chat.type == "private")
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=compare_nav_kb(
            year, month, dashboard_url=dash_url, is_private_chat=is_private
        ),
    )


@router.callback_query(F.data.startswith("rep_month:"))
async def cb_report_month(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
    member: Member | None = None,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    parts = callback.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    text = await _render_monthly_report(repo, household, year, month)
    dash_url, _ = await _build_signed_dashboard_url(household, member, year, month)
    is_private = bool(callback.message.chat and callback.message.chat.type == "private")
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=report_nav_kb(
            year, month, dashboard_url=dash_url, is_private_chat=is_private
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rep_cmp:"))
async def cb_report_compare(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
    member: Member | None = None,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    parts = callback.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    text = await _render_comparison_report(repo, household, year, month)
    dash_url, _ = await _build_signed_dashboard_url(household, member, year, month)
    is_private = bool(callback.message.chat and callback.message.chat.type == "private")
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=compare_nav_kb(
            year, month, dashboard_url=dash_url, is_private_chat=is_private
        ),
    )
    await callback.answer()


async def render_recent_expenses(
    repo: ExpenseRepo,
    household: Household,
) -> tuple[str, InlineKeyboardMarkup | None]:
    expenses = await repo.get_recent_expenses(household.id, limit=10)
    if not expenses:
        return (
            "📭 <i>No expenses logged yet. Send e.g. <code>25.50 groceries NTUC</code> to log your first expense!</i>",
            None,
        )

    cur = html.escape(household.currency)
    lines = ["🧾 <b>Recent Family Expenses (Last 10):</b>", ""]
    for exp in expenses:
        local_dt = exp.spent_at.astimezone(settings.tz)
        dt_str = local_dt.strftime("%d %b %H:%M")
        cat_label = html.escape(exp.category.display_name if exp.category else "📦 Misc")
        rec_badge = " 🔁" if exp.is_recurring else ""
        once_badge = " 📌" if (not exp.is_recurring and is_once_off_expense(exp, exp.category)) else ""
        desc_str = f" — {html.escape(exp.description)}" if exp.description else ""
        who_str = (
            f" <i>({html.escape(exp.member.display_name)})</i>"
            if exp.member
            else ""
        )
        lines.append(
            f"• <code>{dt_str}</code> | <b>{cur}{exp.amount:,.2f}</b> · {cat_label}{rec_badge}{once_badge}{desc_str}{who_str}"
        )

    lines.append("")
    lines.append("<i>Tap any entry below to edit or delete it (use /recurring for monthly recurring entries):</i>")
    return "\n".join(lines), recent_expenses_kb(expenses, household.currency)


async def render_recurring_expenses(
    repo: ExpenseRepo,
    household: Household,
) -> tuple[str, InlineKeyboardMarkup | None]:
    expenses = await repo.get_recurring_expenses(household.id)
    if not expenses:
        return (
            "🔁 <b>Monthly Recurring Expenses</b>\n\n"
            "📭 <i>No recurring monthly expenses set up yet.</i>\n\n"
            "💡 <b>How to add a recurring expense:</b>\n"
            "• Include <code>#recurring</code> when logging an expense:\n"
            "  <code>850 auto Maybank #recurring</code>\n"
            "  <code>15.90 entertainment Netflix #recurring</code>\n"
            "• Or tap <b>🔁 Recurring</b> on any expense card!",
            None,
        )

    cur = html.escape(household.currency)
    total_recurring = sum((e.amount for e in expenses), Decimal("0.00"))
    lines = [
        "🔁 <b>Active Monthly Recurring Expenses</b>",
        f"💰 <b>Total Monthly Commitment:</b> {cur}{total_recurring:,.2f}/mo ({len(expenses)} items)",
        "",
    ]
    for exp in expenses:
        local_dt = exp.spent_at.astimezone(settings.tz)
        day_str = f"Day {local_dt.day}"
        cat_label = html.escape(exp.category.display_name if exp.category else "📦 Misc")
        desc_str = f" — {html.escape(exp.description)}" if exp.description else ""
        who_str = (
            f" <i>({html.escape(exp.member.display_name)})</i>"
            if exp.member
            else ""
        )
        lines.append(
            f"• <code>{day_str:<6}</code> | <b>{cur}{exp.amount:,.2f}</b> · {cat_label}{desc_str}{who_str}"
        )

    lines.append("")
    lines.append(
        "<i>These entries automatically carry over into each new month. "
        "Tap any item below to edit its amount, note, category, pause recurring, or delete it:</i>"
    )
    return "\n".join(lines), recurring_expenses_kb(expenses, household.currency)


@router.message(Command("recent", "history"))
async def cmd_recent(
    message: Message,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    text, kb = await render_recent_expenses(repo, household)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "exp_recent")
async def cb_recent(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.message:
        await callback.answer()
        return
    text, kb = await render_recent_expenses(repo, household)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(Command("recurring", "subscriptions", "bills"))
async def cmd_recurring(
    message: Message,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    text, kb = await render_recurring_expenses(repo, household)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=kb,
    )


@router.callback_query(F.data == "exp_recurring_list")
async def cb_recurring_list(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.message:
        await callback.answer()
        return
    text, kb = await render_recurring_expenses(repo, household)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=kb,
    )
    await callback.answer()


@router.message(Command("export", "csv"))
async def cmd_export(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    year, month = _parse_year_month(command.args)
    start_dt, end_dt = get_month_bounds(year, month, settings.tz)
    expenses = await repo.get_expenses_in_range(household.id, start_dt, end_dt)

    if not expenses:
        await message.answer(
            f"📭 No expenses found for <b>{year}-{month:02d}</b> to export.",
            parse_mode="HTML",
        )
        return

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Date", "Time", "Category", "Amount", "Currency", "Once-Off", "Recurring", "Member", "Description"])
    for exp in reversed(expenses):
        local_dt = exp.spent_at.astimezone(settings.tz)
        writer.writerow(
            [
                exp.id,
                local_dt.strftime("%Y-%m-%d"),
                local_dt.strftime("%H:%M:%S"),
                exp.category.name if exp.category else "misc",
                f"{exp.amount:.2f}",
                household.currency,
                "Yes" if is_once_off_expense(exp, exp.category) else "No",
                "Yes" if exp.is_recurring else "No",
                exp.member.display_name if exp.member else "",
                exp.description,
            ]
        )

    csv_bytes = buf.getvalue().encode("utf-8-sig")
    filename = f"family_expenses_{year}_{month:02d}.csv"
    doc = BufferedInputFile(csv_bytes, filename=filename)
    await message.answer_document(
        document=doc,
        caption=(
            f"📥 <b>Exported {len(expenses)} expenses for {year}-{month:02d}</b>\n"
            f"🏠 {html.escape(household.name)}"
        ),
        parse_mode="HTML",
    )

