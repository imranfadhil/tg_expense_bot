from __future__ import annotations

import calendar
import html
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from bot.db.models import Category, Expense, FIXED_CATEGORY_NAMES, Household


def is_fixed_category(cat: Optional[Category]) -> bool:
    if cat is None:
        return False
    return bool(getattr(cat, "is_fixed", False)) or (cat.name.lower() in FIXED_CATEGORY_NAMES)


def is_once_off_expense(exp: Expense, cat: Optional[Category]) -> bool:
    return (
        bool(getattr(exp, "is_once_off", False))
        or bool(getattr(exp, "is_recurring", False))
        or is_fixed_category(cat)
    )


@dataclass(frozen=True)
class CategorySummary:
    category_id: int
    name: str
    display_name: str
    total: Decimal
    count: int
    share_pct: Decimal
    budget: Optional[Decimal]
    budget_pct: Optional[Decimal]
    is_fixed: bool = False
    fixed_total: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class MemberSummary:
    member_id: Optional[int]
    display_name: str
    total: Decimal
    count: int
    share_pct: Decimal


@dataclass(frozen=True)
class MonthlyReport:
    year: int
    month: int
    month_label: str
    currency: str
    total_spent: Decimal
    tx_count: int
    days_elapsed: int
    days_in_month: int
    daily_average: Decimal
    projected_month_total: Decimal
    household_budget: Optional[Decimal]
    household_budget_pct: Optional[Decimal]
    safe_daily_remaining: Optional[Decimal]
    categories: list[CategorySummary]
    members: list[MemberSummary]
    fixed_spent: Decimal = Decimal("0.00")
    variable_spent: Decimal = Decimal("0.00")
    variable_daily_average: Decimal = Decimal("0.00")
    projected_variable_total: Decimal = Decimal("0.00")


@dataclass(frozen=True)
class CategoryDelta:
    category_name: str
    display_name: str
    current_total: Decimal
    prev_total: Decimal
    diff_amount: Decimal
    diff_pct: Optional[Decimal]


@dataclass(frozen=True)
class MonthComparisonReport:
    curr_year: int
    curr_month: int
    curr_label: str
    prev_year: int
    prev_month: int
    prev_label: str
    currency: str
    curr_total: Decimal
    prev_total: Decimal
    total_diff: Decimal
    total_diff_pct: Optional[Decimal]
    is_current_month_partial: bool
    curr_days_elapsed: int
    prev_equivalent_pace: Decimal
    pace_diff: Decimal
    pace_diff_pct: Optional[Decimal]
    category_deltas: list[CategoryDelta]


@dataclass(frozen=True)
class BudgetAlert:
    level: str  # "ok", "warning", "exceeded"
    message: str


def get_month_bounds(year: int, month: int, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, 0, 0, 0, tzinfo=tz)
    ny, nm = next_month(year, month)
    end = datetime(ny, nm, 1, 0, 0, 0, tzinfo=tz)
    return start, end


def prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


def next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def _q2(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q1(val: Decimal) -> Decimal:
    return val.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def progress_bar(pct: Decimal, width: int = 10) -> str:
    clamped = max(Decimal("0"), min(Decimal("100"), pct))
    filled = int((clamped / Decimal("100") * Decimal(width)).to_integral_value(rounding=ROUND_HALF_UP))
    return "█" * filled + "░" * (width - filled)


def build_monthly_report(
    household: Household,
    categories: Sequence[Category],
    expenses: Sequence[Expense],
    year: int,
    month: int,
    now: datetime,
) -> MonthlyReport:
    days_in_month = calendar.monthrange(year, month)[1]
    if now.year == year and now.month == month:
        days_elapsed = max(1, now.day)
    elif (year, month) < (now.year, now.month):
        days_elapsed = days_in_month
    else:
        days_elapsed = 1

    cat_map = {c.id: c for c in categories}
    cat_totals: dict[int, Decimal] = {}
    cat_fixed_totals: dict[int, Decimal] = {}
    cat_counts: dict[int, int] = {}

    member_totals: dict[Optional[int], Decimal] = {}
    member_counts: dict[Optional[int], int] = {}
    member_names: dict[Optional[int], str] = {}

    fixed_spent = Decimal("0.00")
    variable_spent = Decimal("0.00")

    for exp in expenses:
        cid = exp.category_id
        cat = cat_map.get(cid) or getattr(exp, "category", None)
        if cat is not None and cid not in cat_map:
            cat_map[cid] = cat

        cat_totals[cid] = cat_totals.get(cid, Decimal("0.00")) + exp.amount
        cat_counts[cid] = cat_counts.get(cid, 0) + 1

        if is_once_off_expense(exp, cat):
            fixed_spent += exp.amount
            cat_fixed_totals[cid] = cat_fixed_totals.get(cid, Decimal("0.00")) + exp.amount
        else:
            variable_spent += exp.amount

        mid = exp.member_id
        mname = exp.member.display_name if exp.member else "Member"
        member_names[mid] = mname
        member_totals[mid] = member_totals.get(mid, Decimal("0.00")) + exp.amount
        member_counts[mid] = member_counts.get(mid, 0) + 1

    total_spent = fixed_spent + variable_spent
    tx_count = len(expenses)

    daily_avg = _q2(total_spent / Decimal(days_elapsed)) if total_spent > 0 else Decimal("0.00")
    variable_daily_avg = (
        _q2(variable_spent / Decimal(days_elapsed)) if variable_spent > 0 else Decimal("0.00")
    )
    if (year, month) == (now.year, now.month):
        projected_variable = _q2(variable_daily_avg * Decimal(days_in_month))
        projected_total = _q2(fixed_spent + projected_variable)
    else:
        projected_variable = variable_spent
        projected_total = total_spent

    hh_budget = household.monthly_budget
    hh_budget_pct: Optional[Decimal] = None
    safe_daily_remaining: Optional[Decimal] = None
    if hh_budget and hh_budget > 0:
        hh_budget_pct = _q1((total_spent / hh_budget) * Decimal("100"))
        remaining_days = max(1, days_in_month - now.day + 1) if (year, month) == (now.year, now.month) else 0
        if remaining_days > 0:
            rem_budget = max(Decimal("0.00"), hh_budget - total_spent)
            safe_daily_remaining = _q2(rem_budget / Decimal(remaining_days))

    # Include categories with spending OR categories that have an active budget
    cat_summaries: list[CategorySummary] = []
    seen_cids = set(cat_totals.keys()) | {
        c.id for c in categories if c.monthly_budget and c.monthly_budget > 0
    }
    for cid in seen_cids:
        cat = cat_map.get(cid)
        if not cat:
            continue
        ctotal = cat_totals.get(cid, Decimal("0.00"))
        ccount = cat_counts.get(cid, 0)
        share_pct = (
            _q1((ctotal / total_spent) * Decimal("100"))
            if total_spent > 0
            else Decimal("0.0")
        )
        cbudget = cat.monthly_budget
        cbudget_pct = (
            _q1((ctotal / cbudget) * Decimal("100"))
            if (cbudget and cbudget > 0)
            else None
        )
        cat_summaries.append(
            CategorySummary(
                category_id=cid,
                name=cat.name,
                display_name=cat.display_name,
                total=ctotal,
                count=ccount,
                share_pct=share_pct,
                budget=cbudget,
                budget_pct=cbudget_pct,
                is_fixed=is_fixed_category(cat),
                fixed_total=cat_fixed_totals.get(cid, Decimal("0.00")),
            )
        )
    cat_summaries.sort(key=lambda c: (c.total, c.budget or Decimal("0")), reverse=True)

    mem_summaries: list[MemberSummary] = []
    for mid, mtotal in member_totals.items():
        mshare = (
            _q1((mtotal / total_spent) * Decimal("100"))
            if total_spent > 0
            else Decimal("0.0")
        )
        mem_summaries.append(
            MemberSummary(
                member_id=mid,
                display_name=member_names[mid],
                total=mtotal,
                count=member_counts[mid],
                share_pct=mshare,
            )
        )
    mem_summaries.sort(key=lambda m: m.total, reverse=True)

    month_label = f"{calendar.month_name[month]} {year}"
    return MonthlyReport(
        year=year,
        month=month,
        month_label=month_label,
        currency=household.currency,
        total_spent=total_spent,
        tx_count=tx_count,
        days_elapsed=days_elapsed,
        days_in_month=days_in_month,
        daily_average=daily_avg,
        projected_month_total=projected_total,
        household_budget=hh_budget,
        household_budget_pct=hh_budget_pct,
        safe_daily_remaining=safe_daily_remaining,
        categories=cat_summaries,
        members=mem_summaries,
        fixed_spent=fixed_spent,
        variable_spent=variable_spent,
        variable_daily_average=variable_daily_avg,
        projected_variable_total=projected_variable,
    )


def build_month_comparison(
    household: Household,
    categories: Sequence[Category],
    curr_expenses: Sequence[Expense],
    prev_expenses: Sequence[Expense],
    curr_year: int,
    curr_month: int,
    now: datetime,
) -> MonthComparisonReport:
    py, pm = prev_month(curr_year, curr_month)
    curr_label = f"{calendar.month_abbr[curr_month]} {curr_year}"
    prev_label = f"{calendar.month_abbr[pm]} {py}"

    curr_total = sum((e.amount for e in curr_expenses), Decimal("0.00"))
    prev_total = sum((e.amount for e in prev_expenses), Decimal("0.00"))
    total_diff = curr_total - prev_total
    total_diff_pct = (
        _q1((total_diff / prev_total) * Decimal("100"))
        if prev_total > 0
        else None
    )

    is_partial = (curr_year, curr_month) == (now.year, now.month)
    curr_days_in_month = calendar.monthrange(curr_year, curr_month)[1]
    curr_days_elapsed = max(1, now.day) if is_partial else curr_days_in_month

    # Equivalent pace in previous month up to the same proportion of the month
    pace_ratio = Decimal(curr_days_elapsed) / Decimal(curr_days_in_month)
    prev_equivalent_pace = _q2(prev_total * pace_ratio)
    pace_diff = curr_total - prev_equivalent_pace
    pace_diff_pct = (
        _q1((pace_diff / prev_equivalent_pace) * Decimal("100"))
        if prev_equivalent_pace > 0
        else None
    )

    cat_map = {c.id: c for c in categories}
    curr_by_cat: dict[int, Decimal] = {}
    prev_by_cat: dict[int, Decimal] = {}

    for e in curr_expenses:
        curr_by_cat[e.category_id] = curr_by_cat.get(e.category_id, Decimal("0.00")) + e.amount
    for e in prev_expenses:
        prev_by_cat[e.category_id] = prev_by_cat.get(e.category_id, Decimal("0.00")) + e.amount

    all_cat_ids = set(curr_by_cat.keys()) | set(prev_by_cat.keys())
    deltas: list[CategoryDelta] = []
    for cid in all_cat_ids:
        cat = cat_map.get(cid)
        cname = cat.name if cat else f"cat_{cid}"
        dname = cat.display_name if cat else "📦 Unknown"
        c_amt = curr_by_cat.get(cid, Decimal("0.00"))
        p_amt = prev_by_cat.get(cid, Decimal("0.00"))
        diff = c_amt - p_amt
        diff_pct = _q1((diff / p_amt) * Decimal("100")) if p_amt > 0 else None
        deltas.append(
            CategoryDelta(
                category_name=cname,
                display_name=dname,
                current_total=c_amt,
                prev_total=p_amt,
                diff_amount=diff,
                diff_pct=diff_pct,
            )
        )

    deltas.sort(key=lambda d: max(d.current_total, d.prev_total), reverse=True)

    return MonthComparisonReport(
        curr_year=curr_year,
        curr_month=curr_month,
        curr_label=curr_label,
        prev_year=py,
        prev_month=pm,
        prev_label=prev_label,
        currency=household.currency,
        curr_total=curr_total,
        prev_total=prev_total,
        total_diff=total_diff,
        total_diff_pct=total_diff_pct,
        is_current_month_partial=is_partial,
        curr_days_elapsed=curr_days_elapsed,
        prev_equivalent_pace=prev_equivalent_pace,
        pace_diff=pace_diff,
        pace_diff_pct=pace_diff_pct,
        category_deltas=deltas,
    )


def check_budget_alerts(
    currency: str,
    category: Category,
    cat_month_total: Decimal,
    household_budget: Optional[Decimal],
    household_month_total: Decimal,
) -> list[BudgetAlert]:
    alerts: list[BudgetAlert] = []

    if category.monthly_budget and category.monthly_budget > 0:
        pct = _q1((cat_month_total / category.monthly_budget) * Decimal("100"))
        rem = category.monthly_budget - cat_month_total
        if pct >= Decimal("100"):
            alerts.append(
                BudgetAlert(
                    level="exceeded",
                    message=(
                        f"🚨 <b>{html.escape(category.display_name)} Budget Exceeded!</b>\n"
                        f"   {currency}{cat_month_total:.2f} / {currency}{category.monthly_budget:.2f} "
                        f"(<b>{pct}%</b> — over by {currency}{abs(rem):.2f})"
                    ),
                )
            )
        elif pct >= Decimal("80"):
            alerts.append(
                BudgetAlert(
                    level="warning",
                    message=(
                        f"⚠️ <b>{html.escape(category.display_name)} Budget Alert ({pct}%)</b>\n"
                        f"   {currency}{cat_month_total:.2f} / {currency}{category.monthly_budget:.2f} "
                        f"({currency}{rem:.2f} left)"
                    ),
                )
            )
        else:
            alerts.append(
                BudgetAlert(
                    level="ok",
                    message=(
                        f"🎯 {html.escape(category.display_name)}: "
                        f"{currency}{cat_month_total:.2f} / {currency}{category.monthly_budget:.2f} ({pct}%)"
                    ),
                )
            )

    if household_budget and household_budget > 0:
        hpct = _q1((household_month_total / household_budget) * Decimal("100"))
        hrem = household_budget - household_month_total
        if hpct >= Decimal("100"):
            alerts.append(
                BudgetAlert(
                    level="exceeded",
                    message=(
                        f"🚨 <b>Total Monthly Budget Exceeded!</b>\n"
                        f"   {currency}{household_month_total:.2f} / {currency}{household_budget:.2f} "
                        f"(<b>{hpct}%</b> — over by {currency}{abs(hrem):.2f})"
                    ),
                )
            )
        elif hpct >= Decimal("80"):
            alerts.append(
                BudgetAlert(
                    level="warning",
                    message=(
                        f"⚠️ <b>Total Monthly Budget Alert ({hpct}%)</b>\n"
                        f"   {currency}{household_month_total:.2f} / {currency}{household_budget:.2f} "
                        f"({currency}{hrem:.2f} left)"
                    ),
                )
            )
        else:
            alerts.append(
                BudgetAlert(
                    level="ok",
                    message=(
                        f"🏦 Monthly Total: {currency}{household_month_total:.2f} / "
                        f"{currency}{household_budget:.2f} ({hpct}%)"
                    ),
                )
            )

    return alerts


def format_monthly_report_message(report: MonthlyReport) -> str:
    cur = html.escape(report.currency)
    lines: list[str] = [
        f"📊 <b>{html.escape(report.month_label)} — Family Financial Report</b>",
        "",
        f"💰 <b>Total Spent:</b> {cur}{report.total_spent:,.2f}  <i>({report.tx_count} expenses)</i>",
    ]

    if report.fixed_spent > 0:
        lines.append(
            f"📌 <b>Fixed / Once-Off:</b> {cur}{report.fixed_spent:,.2f}  <i>(counted 1x)</i>"
        )
        lines.append(
            f"🔄 <b>Variable Spend ({report.days_elapsed}/{report.days_in_month}d):</b> "
            f"{cur}{report.variable_spent:,.2f}  <i>({cur}{report.variable_daily_average:,.2f}/day)</i>"
        )
    else:
        lines.append(
            f"📅 <b>Daily Avg ({report.days_elapsed}/{report.days_in_month}d):</b> {cur}{report.daily_average:,.2f}/day"
        )

    if report.days_elapsed < report.days_in_month and report.total_spent > 0:
        if report.fixed_spent > 0:
            lines.append(
                f"🔮 <b>Projected Month-End:</b> {cur}{report.projected_month_total:,.2f}\n"
                f"   <i>↳ {cur}{report.fixed_spent:,.2f} fixed + {cur}{report.projected_variable_total:,.2f} "
                f"variable ({cur}{report.variable_daily_average:,.2f}/d × {report.days_in_month}d)</i>"
            )
        else:
            lines.append(
                f"🔮 <b>Projected Month-End:</b> {cur}{report.projected_month_total:,.2f}"
            )

    if report.household_budget and report.household_budget > 0 and report.household_budget_pct is not None:
        bar = progress_bar(report.household_budget_pct)
        status_icon = (
            "🚨"
            if report.household_budget_pct >= Decimal("100")
            else ("⚠️" if report.household_budget_pct >= Decimal("80") else "✅")
        )
        lines.append("")
        lines.append(
            f"🎯 <b>Monthly Budget:</b> {cur}{report.total_spent:,.2f} / {cur}{report.household_budget:,.2f} "
            f"({report.household_budget_pct}%) {status_icon}"
        )
        lines.append(f"<code>{bar}</code>")
        if report.days_elapsed < report.days_in_month and report.total_spent > 0:
            proj_diff = report.projected_month_total - report.household_budget
            if proj_diff > 0:
                lines.append(
                    f"⚠️ <b>Forecast vs Budget:</b> Projected +{cur}{proj_diff:,.2f} over budget"
                )
            else:
                lines.append(
                    f"✅ <b>Forecast vs Budget:</b> On track ({cur}{abs(proj_diff):,.2f} under budget)"
                )
        if report.safe_daily_remaining is not None:
            lines.append(
                f"🛡️ <b>Prudent Safe Daily Limit:</b> {cur}{report.safe_daily_remaining:,.2f}/day for rest of month"
            )

    lines.append("")
    lines.append("📂 <b>By Category:</b>")
    if not report.categories:
        lines.append("<i>No expenses logged in this month yet.</i>")
    else:
        for c in report.categories:
            bar = progress_bar(c.share_pct, width=8)
            fixed_tag = ""
            if c.is_fixed:
                fixed_tag = " <i>[📌 Fixed]</i>"
            elif c.fixed_total > 0:
                fixed_tag = f" <i>[📌 {cur}{c.fixed_total:,.2f} once-off]</i>"
            budget_tag = ""
            if c.budget and c.budget > 0 and c.budget_pct is not None:
                b_icon = (
                    "🚨"
                    if c.budget_pct >= Decimal("100")
                    else ("⚠️" if c.budget_pct >= Decimal("80") else "")
                )
                budget_tag = f" <i>[Cap: {cur}{c.budget:,.0f} · {c.budget_pct}%{b_icon}]</i>"
            lines.append(
                f"<code>{bar}</code> <b>{html.escape(c.display_name)}</b>: "
                f"{cur}{c.total:,.2f} ({c.share_pct}%){fixed_tag}{budget_tag}"
            )

    if len(report.members) > 1:
        lines.append("")
        lines.append("👨‍👩‍👧‍👦 <b>By Family Member:</b>")
        for m in report.members:
            lines.append(
                f"• <b>{html.escape(m.display_name)}</b>: {cur}{m.total:,.2f} "
                f"({m.share_pct}%, {m.count} tx)"
            )

    return "\n".join(lines)


def format_comparison_message(comp: MonthComparisonReport) -> str:
    cur = html.escape(comp.currency)
    lines: list[str] = [
        f"📈 <b>Month-over-Month: {html.escape(comp.curr_label)} vs {html.escape(comp.prev_label)}</b>",
        "",
        f"• <b>{html.escape(comp.curr_label)}:</b> {cur}{comp.curr_total:,.2f}",
        f"• <b>{html.escape(comp.prev_label)} (Full Month):</b> {cur}{comp.prev_total:,.2f}",
    ]

    sign = "+" if comp.total_diff > 0 else ""
    pct_str = f" ({sign}{comp.total_diff_pct}%)" if comp.total_diff_pct is not None else ""
    trend_icon = "🔺" if comp.total_diff > 0 else ("🟢" if comp.total_diff < 0 else "➖")
    lines.append(
        f"• <b>Net Change:</b> {trend_icon} {sign}{cur}{comp.total_diff:,.2f}{pct_str}"
    )

    if comp.is_current_month_partial and comp.prev_total > 0:
        p_sign = "+" if comp.pace_diff > 0 else ""
        p_pct = f" ({p_sign}{comp.pace_diff_pct}%)" if comp.pace_diff_pct is not None else ""
        p_icon = "⚠️ Faster pace" if comp.pace_diff > 0 else "✅ More prudent pace"
        lines.append("")
        lines.append(
            f"⏱️ <b>Day-{comp.curr_days_elapsed} Pace Check:</b>\n"
            f"   {html.escape(comp.prev_label)} pro-rated ({comp.curr_days_elapsed}d): {cur}{comp.prev_equivalent_pace:,.2f}\n"
            f"   Pace vs Last Month: <b>{p_sign}{cur}{comp.pace_diff:,.2f}{p_pct}</b> ({p_icon})"
        )

    lines.append("")
    lines.append("📂 <b>Category Breakdown ({curr} vs {prev}):</b>".format(
        curr=html.escape(comp.curr_label), prev=html.escape(comp.prev_label)
    ))

    if not comp.category_deltas:
        lines.append("<i>No expenses recorded in either month.</i>")
    else:
        for d in comp.category_deltas:
            d_sign = "+" if d.diff_amount > 0 else ""
            if d.diff_amount > 0:
                arrow = "🔺"
            elif d.diff_amount < 0:
                arrow = "🟢"
            else:
                arrow = "➖"
            dpct = f" ({d_sign}{d.diff_pct}%)" if d.diff_pct is not None else " (new)"
            lines.append(
                f"{arrow} <b>{html.escape(d.display_name)}</b>: "
                f"{cur}{d.current_total:,.2f} vs {cur}{d.prev_total:,.2f} "
                f"<b>[{d_sign}{cur}{d.diff_amount:,.2f}{dpct}]</b>"
            )

    return "\n".join(lines)

