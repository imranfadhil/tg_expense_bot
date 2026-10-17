from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import settings
from bot.db.models import Household
from bot.db.repo import ExpenseRepo
from bot.services.analytics import get_month_bounds, progress_bar
from bot.services.parser import CATEGORY_ALIASES

router = Router(name="budgets")


def _parse_budget_amount(raw: str) -> Decimal | None:
    cleaned = raw.strip().lstrip("$€£¥₹").replace(",", "")
    if cleaned.lower() in ("0", "off", "none", "clear", "reset"):
        return Decimal("0")
    try:
        val = Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if val < Decimal("0") or val >= Decimal("100000000"):
        return None
    return val


@router.message(Command("budget", "budgets"))
async def cmd_budget(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    cur = html.escape(household.currency)
    categories = await repo.get_categories(household.id)

    # If no arguments: show current month's budget overview
    if not command.args or not command.args.strip():
        now = settings.now()
        start_dt, end_dt = get_month_bounds(now.year, now.month, settings.tz)
        expenses = await repo.get_expenses_in_range(household.id, start_dt, end_dt)

        total_spent = sum((e.amount for e in expenses), Decimal("0.00"))
        by_cat: dict[int, Decimal] = {}
        for e in expenses:
            by_cat[e.category_id] = by_cat.get(e.category_id, Decimal("0.00")) + e.amount

        lines: list[str] = [
            f"🎯 <b>{now.strftime('%B %Y')} — Prudent Budget Overview</b>",
            "",
        ]
        if household.monthly_budget and household.monthly_budget > 0:
            pct = (total_spent / household.monthly_budget * Decimal("100")).quantize(
                Decimal("0.1")
            )
            rem = household.monthly_budget - total_spent
            bar = progress_bar(pct)
            status = "🚨 Over" if rem < 0 else f"{cur}{rem:,.2f} left"
            lines.append(
                f"🏦 <b>Total Monthly Budget:</b> {cur}{total_spent:,.2f} / "
                f"{cur}{household.monthly_budget:,.2f} ({pct}% · {status})\n"
                f"<code>{bar}</code>"
            )
        else:
            lines.append(
                f"🏦 <b>Total Monthly Budget:</b> <i>Not set</i> "
                f"(Spent: {cur}{total_spent:,.2f})\n"
                f"   👉 Set with: <code>/budget total 3000</code>"
            )

        lines.append("")
        lines.append("📂 <b>Category Budgets:</b>")
        any_cat_budget = False
        for c in categories:
            c_spent = by_cat.get(c.id, Decimal("0.00"))
            if c.monthly_budget and c.monthly_budget > 0:
                any_cat_budget = True
                cpct = (c_spent / c.monthly_budget * Decimal("100")).quantize(Decimal("0.1"))
                crem = c.monthly_budget - c_spent
                cbar = progress_bar(cpct, width=8)
                icon = "🚨" if cpct >= 100 else ("⚠️" if cpct >= 80 else "✅")
                lines.append(
                    f"<code>{cbar}</code> <b>{html.escape(c.display_name)}</b> "
                    f"(<code>{c.name}</code>): {cur}{c_spent:,.2f} / {cur}{c.monthly_budget:,.2f} "
                    f"({cpct}%) {icon}"
                )
        if not any_cat_budget:
            lines.append("<i>No per-category budgets configured yet.</i>")

        lines.append("")
        lines.append(
            "💡 <b>How to set or update budgets:</b>\n"
            f"• Overall monthly cap: <code>/budget total 3000</code>\n"
            f"• Category cap: <code>/budget groceries 600</code>\n"
            f"• Clear a cap: <code>/budget groceries off</code>"
        )
        await message.answer("\n".join(lines), parse_mode="HTML")
        return

    parts = command.args.strip().split()
    if len(parts) == 1:
        # e.g. /budget 3000 -> sets total household budget
        amt = _parse_budget_amount(parts[0])
        if amt is None:
            await message.answer(
                "Usage:\n"
                "• <code>/budget total 3000</code>\n"
                "• <code>/budget groceries 600</code>",
                parse_mode="HTML",
            )
            return
        new_val = None if amt == Decimal("0") else amt
        await repo.set_household_budget(household.id, new_val)
        if new_val is None:
            await message.answer("✅ Overall monthly budget cleared.", parse_mode="HTML")
        else:
            await message.answer(
                f"✅ Overall monthly budget set to <b>{cur}{new_val:,.2f}</b>",
                parse_mode="HTML",
            )
        return

    target_key = parts[0].strip().lower()
    amt = _parse_budget_amount(parts[1])
    if amt is None:
        await message.answer(
            "❌ Invalid budget amount. Example: <code>/budget groceries 600</code>",
            parse_mode="HTML",
        )
        return

    new_val = None if amt == Decimal("0") else amt

    if target_key in ("total", "overall", "household", "all", "month"):
        await repo.set_household_budget(household.id, new_val)
        if new_val is None:
            await message.answer("✅ Overall monthly budget cleared.", parse_mode="HTML")
        else:
            await message.answer(
                f"✅ Overall monthly household budget set to <b>{cur}{new_val:,.2f}</b>",
                parse_mode="HTML",
            )
        return

    # Match category by name or alias
    resolved_key = CATEGORY_ALIASES.get(target_key, target_key)
    cat_map = {c.name.lower(): c for c in categories}
    cat = cat_map.get(target_key) or cat_map.get(resolved_key)
    if cat is None:
        valid_keys = ", ".join(f"<code>{c.name}</code>" for c in categories)
        await message.answer(
            f"❌ Category <code>{html.escape(target_key)}</code> not found.\n"
            f"Available categories: {valid_keys}",
            parse_mode="HTML",
        )
        return

    await repo.set_category_budget(household.id, cat.id, new_val)
    if new_val is None:
        await message.answer(
            f"✅ Monthly budget for <b>{html.escape(cat.display_name)}</b> cleared.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"✅ Monthly budget for <b>{html.escape(cat.display_name)}</b> set to "
            f"<b>{cur}{new_val:,.2f}</b>",
            parse_mode="HTML",
        )


@router.message(Command("categories"))
async def cmd_categories(
    message: Message,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    cur = html.escape(household.currency)
    categories = await repo.get_categories(household.id)
    lines = ["🗂️ <b>Household Expense Categories:</b>", ""]
    for c in categories:
        b_str = (
            f" — Budget: {cur}{c.monthly_budget:,.2f}/mo"
            if (c.monthly_budget and c.monthly_budget > 0)
            else ""
        )
        lines.append(
            f"• <b>{html.escape(c.display_name)}</b> (keyword: <code>{c.name}</code>){b_str}"
        )
    lines.append("")
    lines.append(
        "➕ <b>Add a custom category:</b>\n"
        "<code>/addcategory pets 🐶 Pets</code>"
    )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("addcategory"))
async def cmd_addcategory(
    message: Message,
    command: CommandObject,
    repo: ExpenseRepo,
    household: Household,
) -> None:
    if not command.args or not command.args.strip():
        await message.answer(
            "Usage: <code>/addcategory &lt;keyword&gt; [Emoji Display Name]</code>\n"
            "Example: <code>/addcategory pets 🐶 Pets</code>",
            parse_mode="HTML",
        )
        return

    parts = command.args.strip().split(maxsplit=1)
    raw_key = re.sub(r"[^a-z0-9_-]", "", parts[0].lower())
    if not raw_key:
        await message.answer(
            "❌ Category keyword must contain letters or numbers (e.g. <code>pets</code>).",
            parse_mode="HTML",
        )
        return

    display_name = parts[1].strip() if len(parts) > 1 else f"🏷️ {raw_key.capitalize()}"
    cat = await repo.get_or_create_category(
        household_id=household.id,
        name=raw_key,
        display_name=display_name,
    )
    await message.answer(
        f"✅ Category ready: <b>{html.escape(cat.display_name)}</b> "
        f"(keyword: <code>{cat.name}</code>)\n"
        f"You can now log expenses like: <code>25.00 {cat.name} description</code>",
        parse_mode="HTML",
    )

