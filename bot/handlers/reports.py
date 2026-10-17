from __future__ import annotations

import csv
import html
import io
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import settings
from bot.db.models import Household
from bot.db.repo import ExpenseRepo
from bot.keyboards.inline import compare_nav_kb, recent_expenses_kb, report_nav_kb
from bot.services.analytics import (
    build_month_comparison,
    build_monthly_report,
    format_comparison_message,
    format_monthly_report_message,
    get_month_bounds,
    is_once_off_expense,
    prev_month,
)

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


@router.message(Command("report", "month", "summary"))
async def cmd_report(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    year, month = _parse_year_month(command.args)
    text = await _render_monthly_report(repo, household, year, month)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=report_nav_kb(year, month),
    )


@router.message(Command("compare", "mom"))
async def cmd_compare(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    year, month = _parse_year_month(command.args)
    text = await _render_comparison_report(repo, household, year, month)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=compare_nav_kb(year, month),
    )


@router.callback_query(F.data.startswith("rep_month:"))
async def cb_report_month(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    parts = callback.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    text = await _render_monthly_report(repo, household, year, month)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=report_nav_kb(year, month),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("rep_cmp:"))
async def cb_report_compare(
    callback: CallbackQuery,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not callback.data or not callback.message:
        await callback.answer()
        return
    parts = callback.data.split(":")
    year, month = int(parts[1]), int(parts[2])
    text = await _render_comparison_report(repo, household, year, month)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=compare_nav_kb(year, month),
    )
    await callback.answer()


@router.message(Command("recent", "history"))
async def cmd_recent(
    message: Message,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    expenses = await repo.get_recent_expenses(household.id, limit=10)
    if not expenses:
        await message.answer(
            "📭 <i>No expenses logged yet. Send e.g. <code>25.50 groceries NTUC</code> to log your first expense!</i>",
            parse_mode="HTML",
        )
        return

    cur = html.escape(household.currency)
    lines = ["🧾 <b>Recent Family Expenses (Last 10):</b>", ""]
    for exp in expenses:
        local_dt = exp.spent_at.astimezone(settings.tz)
        dt_str = local_dt.strftime("%d %b %H:%M")
        cat_label = html.escape(exp.category.display_name if exp.category else "📦 Misc")
        once_badge = " 📌" if is_once_off_expense(exp, exp.category) else ""
        desc_str = f" — {html.escape(exp.description)}" if exp.description else ""
        who_str = (
            f" <i>({html.escape(exp.member.display_name)})</i>"
            if exp.member
            else ""
        )
        lines.append(
            f"• <code>{dt_str}</code> | <b>{cur}{exp.amount:,.2f}</b> · {cat_label}{once_badge}{desc_str}{who_str}"
        )

    lines.append("")
    lines.append("<i>Tap a button below to delete one of the latest 5 entries:</i>")
    await message.answer(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=recent_expenses_kb(expenses, household.currency),
    )


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
    writer.writerow(["ID", "Date", "Time", "Category", "Amount", "Currency", "Once-Off", "Member", "Description"])
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

