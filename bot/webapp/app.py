from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import secrets
from typing import Any, AsyncGenerator, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from bot.config import settings
from bot.db.base import AsyncSessionLocal
from bot.db.models import Category, Expense, Household, Member
from bot.db.repo import ExpenseRepo
from bot.services.analytics import (
    build_month_comparison,
    build_monthly_report,
    check_budget_alerts,
    get_month_bounds,
    is_fixed_category,
    is_once_off_expense,
    next_month,
    prev_month,
)
from bot.services.parser import parse_expense_text
from bot.webapp.auth import (
    create_dashboard_token,
    verify_dashboard_token,
    verify_telegram_init_data,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass
class DashboardContext:
    repo: ExpenseRepo
    household: Household
    member: Optional[Member]
    telegram_user_id: Optional[int] = None


async def get_repo() -> AsyncGenerator[ExpenseRepo, None]:
    async with AsyncSessionLocal() as session:
        yield ExpenseRepo(session)


async def _resolve_member_for_household(
    repo: ExpenseRepo,
    household_id: int,
    member_id: Optional[int] = None,
    telegram_user_id: Optional[int] = None,
) -> Optional[Member]:
    if member_id is not None:
        m = await repo.get_member_by_id(household_id, member_id)
        if m is not None:
            return m
    if telegram_user_id is not None:
        m = await repo.get_member_by_telegram_id(telegram_user_id)
        if m is not None and m.household_id == household_id:
            return m
    members = await repo.get_household_members(household_id)
    return members[0] if members else None


async def get_dashboard_context(
    repo: ExpenseRepo = Depends(get_repo),
    authorization: Optional[str] = Header(default=None),
    x_telegram_init_data: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> DashboardContext:
    # 1. Check Bearer token or query token
    raw_token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        raw_token = authorization[7:].strip()
    elif token:
        raw_token = token.strip()

    if raw_token:
        payload = verify_dashboard_token(raw_token)
        if payload is not None:
            hh = await repo.get_household_by_id(payload["hh"])
            if hh is not None:
                await repo.sync_recurring_expenses(hh.id)
                mem = await _resolve_member_for_household(
                    repo,
                    household_id=hh.id,
                    member_id=payload.get("mem"),
                    telegram_user_id=payload.get("uid"),
                )
                return DashboardContext(
                    repo=repo,
                    household=hh,
                    member=mem,
                    telegram_user_id=payload.get("uid"),
                )

    # 2. Check Telegram Mini App initData header
    if x_telegram_init_data:
        tg_user = verify_telegram_init_data(x_telegram_init_data)
        if tg_user is not None:
            uid = tg_user["user_id"]
            expected_hash = settings.passphrase_hash
            if expected_hash:
                is_auth = await repo.is_user_authorized(uid, expected_hash)
                if not is_auth:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "PASSPHRASE_REQUIRED",
                            "message": "Bot passphrase is required to unlock the dashboard.",
                        },
                    )
            hh, mem, _ = await repo.ensure_context(
                telegram_user_id=uid,
                display_name=tg_user["display_name"],
                chat_id=uid,
                chat_type="private",
            )
            return DashboardContext(
                repo=repo,
                household=hh,
                member=mem,
                telegram_user_id=uid,
            )

    raise HTTPException(
        status_code=401,
        detail={
            "code": "AUTH_REQUIRED",
            "message": "Authentication required. Open from Telegram or sign in with your Family Invite Code.",
        },
    )


class LoginRequest(BaseModel):
    init_data: Optional[str] = None
    token: Optional[str] = None
    invite_code: Optional[str] = None
    passphrase: Optional[str] = None


class CreateExpenseRequest(BaseModel):
    quick_text: Optional[str] = None
    amount: Optional[Decimal] = None
    category_id: Optional[int] = None
    description: str = ""
    is_once_off: bool = False
    is_recurring: bool = False
    member_id: Optional[int] = None


class UpdateExpenseRequest(BaseModel):
    amount: Optional[Decimal] = None
    category_id: Optional[int] = None
    description: Optional[str] = None
    is_once_off: Optional[bool] = None
    is_recurring: Optional[bool] = None


class CategoryBudgetUpdate(BaseModel):
    category_id: int
    monthly_budget: Optional[Decimal] = None
    is_fixed: Optional[bool] = None
    display_name: Optional[str] = None


class UpdateBudgetsRequest(BaseModel):
    household_budget: Optional[Decimal] = Field(default=None)
    clear_household_budget: bool = False
    currency: Optional[str] = None
    categories: list[CategoryBudgetUpdate] = Field(default_factory=list)


class CreateCategoryRequest(BaseModel):
    name: str
    display_name: Optional[str] = None
    is_fixed: bool = False
    monthly_budget: Optional[Decimal] = None


def _serialize_expense(exp: Expense, currency: str) -> dict[str, Any]:
    local_dt = exp.spent_at.astimezone(settings.tz)
    cat = exp.category
    mem = exp.member
    eff_once = is_once_off_expense(exp, cat)
    return {
        "id": exp.id,
        "amount": float(exp.amount),
        "amount_formatted": f"{currency}{exp.amount:,.2f}",
        "description": exp.description or "",
        "category_id": exp.category_id,
        "category_name": cat.name if cat else "misc",
        "category_display_name": cat.display_name if cat else "📦 Misc",
        "category_is_fixed": is_fixed_category(cat),
        "member_id": exp.member_id,
        "member_name": mem.display_name if mem else "Member",
        "is_once_off": bool(exp.is_once_off),
        "is_recurring": bool(exp.is_recurring),
        "effective_once_off": eff_once,
        "spent_at": local_dt.isoformat(),
        "spent_date": local_dt.strftime("%Y-%m-%d"),
        "spent_label": local_dt.strftime("%d %b · %H:%M"),
        "day_of_month": local_dt.day,
    }


def create_webapp() -> FastAPI:
    app = FastAPI(
        title="Telegram Family Expense Bot — Live Dashboard",
        version="1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    @app.get("/dashboard", response_class=HTMLResponse)
    async def serve_dashboard() -> FileResponse:
        index_path = STATIC_DIR / "index.html"
        return FileResponse(
            index_path,
            media_type="text/html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    @app.post("/api/auth/login")
    async def login(
        body: LoginRequest,
        repo: ExpenseRepo = Depends(get_repo),
    ) -> dict[str, Any]:
        active_pin = (
            settings.bot_passphrase.strip()
            if settings.bot_passphrase and settings.bot_passphrase.strip()
            else None
        )
        expected_hash = settings.passphrase_hash

        # Path 1: Telegram WebApp initData
        if body.init_data:
            tg_user = verify_telegram_init_data(body.init_data)
            if tg_user is None:
                raise HTTPException(status_code=401, detail="Invalid Telegram initData signature.")

            uid = tg_user["user_id"]
            if active_pin and expected_hash:
                is_auth = await repo.is_user_authorized(uid, expected_hash)
                if not is_auth:
                    candidate = (body.passphrase or "").strip()
                    if not candidate:
                        raise HTTPException(
                            status_code=403,
                            detail={
                                "code": "PASSPHRASE_REQUIRED",
                                "message": "Enter the bot passphrase to unlock your dashboard.",
                            },
                        )
                    if not secrets.compare_digest(candidate, active_pin):
                        raise HTTPException(status_code=403, detail="Invalid bot passphrase.")
                    await repo.authorize_user(uid, expected_hash)

            hh, mem, _ = await repo.ensure_context(
                telegram_user_id=uid,
                display_name=tg_user["display_name"],
                chat_id=uid,
                chat_type="private",
            )
            # Check if start_param specifies a household or invite code
            start_param = (tg_user.get("start_param") or "").strip()
            if start_param.upper().startswith("FAM-"):
                joined = await repo.join_household_by_code(
                    telegram_user_id=uid,
                    display_name=tg_user["display_name"],
                    invite_code=start_param.upper(),
                )
                if joined:
                    hh = joined

            signed = create_dashboard_token(
                household_id=hh.id,
                member_id=mem.id if mem else None,
                telegram_user_id=uid,
            )
            return {
                "token": signed,
                "household": {
                    "id": hh.id,
                    "name": hh.name,
                    "invite_code": hh.invite_code,
                    "currency": hh.currency,
                },
                "member": {
                    "id": mem.id if mem else None,
                    "display_name": mem.display_name if mem else tg_user["display_name"],
                },
            }

        # Path 2: Signed URL Token
        if body.token:
            payload = verify_dashboard_token(body.token)
            if payload is None:
                raise HTTPException(status_code=401, detail="Dashboard link expired or invalid.")
            hh = await repo.get_household_by_id(payload["hh"])
            if hh is None:
                raise HTTPException(status_code=404, detail="Household not found.")
            mem = await _resolve_member_for_household(
                repo,
                household_id=hh.id,
                member_id=payload.get("mem"),
                telegram_user_id=payload.get("uid"),
            )
            return {
                "token": body.token,
                "household": {
                    "id": hh.id,
                    "name": hh.name,
                    "invite_code": hh.invite_code,
                    "currency": hh.currency,
                },
                "member": {
                    "id": mem.id if mem else None,
                    "display_name": mem.display_name if mem else "Family Member",
                },
            }

        # Path 3: Browser Login via Invite Code (+ Passphrase if configured)
        if active_pin:
            candidate = (body.passphrase or "").strip()
            if not candidate or not secrets.compare_digest(candidate, active_pin):
                raise HTTPException(
                    status_code=403,
                    detail="Invalid passphrase. Please enter your bot passphrase.",
                )

        invite_code = (body.invite_code or "").strip().upper()
        hh: Optional[Household] = None
        if invite_code:
            hh = await repo.get_household_by_invite_code(invite_code)
            if hh is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Household with invite code '{invite_code}' not found.",
                )
        else:
            # If only a single household exists in DB, allow convenient access after passphrase check
            stmt = select(Household).order_by(Household.id.asc()).limit(2)
            res = await repo.session.execute(stmt)
            households = list(res.scalars().all())
            if len(households) == 1:
                hh = households[0]
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Please enter your Family Invite Code (run /family in the bot to view it).",
                )

        mem = await _resolve_member_for_household(repo, household_id=hh.id)
        signed = create_dashboard_token(
            household_id=hh.id,
            member_id=mem.id if mem else None,
            telegram_user_id=mem.telegram_user_id if mem else None,
        )
        return {
            "token": signed,
            "household": {
                "id": hh.id,
                "name": hh.name,
                "invite_code": hh.invite_code,
                "currency": hh.currency,
            },
            "member": {
                "id": mem.id if mem else None,
                "display_name": mem.display_name if mem else "Family Member",
            },
        }

    @app.get("/api/dashboard")
    async def get_dashboard_data(
        year: Optional[int] = Query(default=None, ge=2000, le=2100),
        month: Optional[int] = Query(default=None, ge=1, le=12),
        ctx: DashboardContext = Depends(get_dashboard_context),
    ) -> dict[str, Any]:
        now = settings.now()
        target_year = year or now.year
        target_month = month or now.month

        repo = ctx.repo
        household = ctx.household

        start_dt, end_dt = get_month_bounds(target_year, target_month, settings.tz)
        py, pm = prev_month(target_year, target_month)
        ny, nm = next_month(target_year, target_month)
        prev_start, prev_end = get_month_bounds(py, pm, settings.tz)

        categories = await repo.get_categories(household.id)
        members = await repo.get_household_members(household.id)
        curr_expenses = await repo.get_expenses_in_range(household.id, start_dt, end_dt)
        prev_expenses = await repo.get_expenses_in_range(household.id, prev_start, prev_end)
        recurring_expenses = await repo.get_recurring_expenses(household.id)

        report = build_monthly_report(
            household=household,
            categories=categories,
            expenses=curr_expenses,
            year=target_year,
            month=target_month,
            now=now,
        )
        comp = build_month_comparison(
            household=household,
            categories=categories,
            curr_expenses=curr_expenses,
            prev_expenses=prev_expenses,
            curr_year=target_year,
            curr_month=target_month,
            now=now,
        )

        # Build day-by-day spend series for live charts
        days_in_month = calendar.monthrange(target_year, target_month)[1]
        is_current_month = (target_year, target_month) == (now.year, now.month)
        is_future_month = (target_year, target_month) > (now.year, now.month)
        cutoff_day = now.day if is_current_month else (0 if is_future_month else days_in_month)

        daily_var: dict[int, Decimal] = {d: Decimal("0.00") for d in range(1, days_in_month + 1)}
        daily_fix: dict[int, Decimal] = {d: Decimal("0.00") for d in range(1, days_in_month + 1)}
        cat_map = {c.id: c for c in categories}

        for exp in curr_expenses:
            local_dt = exp.spent_at.astimezone(settings.tz)
            d = local_dt.day
            cat = cat_map.get(exp.category_id) or exp.category
            if is_once_off_expense(exp, cat):
                daily_fix[d] = daily_fix.get(d, Decimal("0.00")) + exp.amount
            else:
                daily_var[d] = daily_var.get(d, Decimal("0.00")) + exp.amount

        daily_series: list[dict[str, Any]] = []
        running_total = Decimal("0.00")
        running_var = Decimal("0.00")
        hh_budget_float = float(household.monthly_budget) if household.monthly_budget else None

        for d in range(1, days_in_month + 1):
            v_amt = daily_var[d]
            f_amt = daily_fix[d]
            t_amt = v_amt + f_amt
            running_total += t_amt
            running_var += v_amt

            # Ideal budget trajectory: fixed spent so far + pro-rated remaining variable budget
            budget_pace_val: Optional[float] = None
            if hh_budget_float and hh_budget_float > 0:
                budget_pace_val = round((hh_budget_float / days_in_month) * d, 2)

            daily_series.append(
                {
                    "day": d,
                    "date": f"{target_year:04d}-{target_month:02d}-{d:02d}",
                    "variable": float(v_amt),
                    "fixed": float(f_amt),
                    "total": float(t_amt),
                    "cumulative_total": float(running_total) if d <= cutoff_day or t_amt > 0 else None,
                    "cumulative_variable": float(running_var) if d <= cutoff_day or v_amt > 0 else None,
                    "budget_pace": budget_pace_val,
                }
            )

        # Map category spend from report
        report_cat_by_id = {c.category_id: c for c in report.categories}
        all_categories_payload: list[dict[str, Any]] = []
        for c in categories:
            cs = report_cat_by_id.get(c.id)
            c_total = float(cs.total) if cs else 0.0
            c_count = cs.count if cs else 0
            c_share = float(cs.share_pct) if cs else 0.0
            c_budget = float(c.monthly_budget) if (c.monthly_budget and c.monthly_budget > 0) else None
            c_budget_pct = float(cs.budget_pct) if (cs and cs.budget_pct is not None) else None
            c_fixed_total = float(cs.fixed_total) if cs else 0.0
            all_categories_payload.append(
                {
                    "id": c.id,
                    "name": c.name,
                    "display_name": c.display_name,
                    "is_fixed": is_fixed_category(c),
                    "total": c_total,
                    "fixed_total": c_fixed_total,
                    "count": c_count,
                    "share_pct": c_share,
                    "budget": c_budget,
                    "budget_pct": c_budget_pct,
                }
            )

        # Sort categories by spend desc, then budget desc, then id asc
        categories_by_spend = sorted(
            all_categories_payload,
            key=lambda item: (item["total"], item["budget"] or 0.0, -item["id"]),
            reverse=True,
        )

        total_recurring_commitment = sum(
            (e.amount for e in recurring_expenses), Decimal("0.00")
        )

        return {
            "server_time": now.isoformat(),
            "household": {
                "id": household.id,
                "name": household.name,
                "invite_code": household.invite_code,
                "currency": household.currency,
                "monthly_budget": float(household.monthly_budget)
                if (household.monthly_budget and household.monthly_budget > 0)
                else None,
            },
            "current_member": {
                "id": ctx.member.id if ctx.member else None,
                "display_name": ctx.member.display_name if ctx.member else "Member",
            },
            "members": [
                {
                    "id": m.id,
                    "display_name": m.display_name,
                }
                for m in members
            ],
            "navigation": {
                "year": target_year,
                "month": target_month,
                "month_label": report.month_label,
                "short_label": f"{calendar.month_abbr[target_month]} {target_year}",
                "is_current_month": is_current_month,
                "prev": {"year": py, "month": pm, "label": f"{calendar.month_abbr[pm]} {py}"},
                "next": {"year": ny, "month": nm, "label": f"{calendar.month_abbr[nm]} {ny}"},
            },
            "report": {
                "year": report.year,
                "month": report.month,
                "month_label": report.month_label,
                "currency": report.currency,
                "total_spent": float(report.total_spent),
                "tx_count": report.tx_count,
                "days_elapsed": report.days_elapsed,
                "days_in_month": report.days_in_month,
                "daily_average": float(report.daily_average),
                "projected_month_total": float(report.projected_month_total),
                "fixed_spent": float(report.fixed_spent),
                "variable_spent": float(report.variable_spent),
                "variable_daily_average": float(report.variable_daily_average),
                "projected_variable_total": float(report.projected_variable_total),
                "household_budget": float(report.household_budget)
                if (report.household_budget and report.household_budget > 0)
                else None,
                "household_budget_pct": float(report.household_budget_pct)
                if report.household_budget_pct is not None
                else None,
                "safe_daily_remaining": float(report.safe_daily_remaining)
                if report.safe_daily_remaining is not None
                else None,
                "member_breakdown": [
                    {
                        "member_id": ms.member_id,
                        "display_name": ms.display_name,
                        "total": float(ms.total),
                        "count": ms.count,
                        "share_pct": float(ms.share_pct),
                    }
                    for ms in report.members
                ],
            },
            "comparison": {
                "curr_label": comp.curr_label,
                "prev_label": comp.prev_label,
                "curr_total": float(comp.curr_total),
                "prev_total": float(comp.prev_total),
                "total_diff": float(comp.total_diff),
                "total_diff_pct": float(comp.total_diff_pct)
                if comp.total_diff_pct is not None
                else None,
                "is_current_month_partial": comp.is_current_month_partial,
                "curr_days_elapsed": comp.curr_days_elapsed,
                "prev_equivalent_pace": float(comp.prev_equivalent_pace),
                "pace_diff": float(comp.pace_diff),
                "pace_diff_pct": float(comp.pace_diff_pct)
                if comp.pace_diff_pct is not None
                else None,
                "category_deltas": [
                    {
                        "category_name": d.category_name,
                        "display_name": d.display_name,
                        "current_total": float(d.current_total),
                        "prev_total": float(d.prev_total),
                        "diff_amount": float(d.diff_amount),
                        "diff_pct": float(d.diff_pct) if d.diff_pct is not None else None,
                    }
                    for d in comp.category_deltas
                ],
            },
            "daily_series": daily_series,
            "categories": categories_by_spend,
            "expenses": [
                _serialize_expense(exp, household.currency) for exp in curr_expenses
            ],
            "recurring": {
                "total_monthly": float(total_recurring_commitment),
                "count": len(recurring_expenses),
                "items": [
                    _serialize_expense(exp, household.currency)
                    for exp in recurring_expenses
                ],
            },
        }

    @app.post("/api/expenses")
    async def api_create_expense(
        body: CreateExpenseRequest,
        ctx: DashboardContext = Depends(get_dashboard_context),
    ) -> dict[str, Any]:
        repo = ctx.repo
        household = ctx.household
        categories = await repo.get_categories(household.id)
        if not categories:
            raise HTTPException(status_code=400, detail="No categories configured.")

        cat_by_name = {c.name.lower(): c for c in categories}
        cat_by_id = {c.id: c for c in categories}

        member_id = body.member_id or (ctx.member.id if ctx.member else None)
        if member_id is None:
            members = await repo.get_household_members(household.id)
            if members:
                member_id = members[0].id
            else:
                raise HTTPException(status_code=400, detail="No household member found.")

        if body.quick_text and body.quick_text.strip():
            parsed = parse_expense_text(
                body.quick_text.strip(),
                available_category_keys=cat_by_name.keys(),
            )
            if parsed is None:
                raise HTTPException(
                    status_code=400,
                    detail="Could not parse expense text. Try e.g. '25.50 groceries NTUC'.",
                )
            category = (
                cat_by_name.get(parsed.category_key)
                or cat_by_name.get("misc")
                or categories[0]
            )
            amount = parsed.amount
            description = parsed.description
            is_recurring = bool(parsed.is_recurring or body.is_recurring)
            is_once_off = bool(
                parsed.is_once_off
                or is_recurring
                or body.is_once_off
                or is_fixed_category(category)
            )
        else:
            if body.amount is None or body.amount <= Decimal("0"):
                raise HTTPException(
                    status_code=400, detail="Expense amount must be greater than zero."
                )
            try:
                amount = Decimal(str(body.amount)).quantize(Decimal("0.01"))
            except InvalidOperation:
                raise HTTPException(status_code=400, detail="Invalid amount.")

            category = (
                cat_by_id.get(body.category_id)
                if body.category_id is not None
                else None
            ) or cat_by_name.get("misc") or categories[0]
            description = (body.description or "").strip()
            is_recurring = bool(body.is_recurring)
            is_once_off = bool(
                body.is_once_off or is_recurring or is_fixed_category(category)
            )

        now = settings.now()
        expense = await repo.add_expense(
            household_id=household.id,
            member_id=member_id,
            category_id=category.id,
            amount=amount,
            description=description,
            spent_at=now,
            is_once_off=is_once_off,
            is_recurring=is_recurring,
        )
        full_exp = await repo.get_expense_by_id(household.id, expense.id)

        start_dt, end_dt = get_month_bounds(now.year, now.month, settings.tz)
        cat_month_total = await repo.get_period_total(
            household.id, start_dt, end_dt, category_id=category.id
        )
        hh_month_total = await repo.get_period_total(household.id, start_dt, end_dt)
        alerts = check_budget_alerts(
            currency=household.currency,
            category=category,
            cat_month_total=cat_month_total,
            household_budget=household.monthly_budget,
            household_month_total=hh_month_total,
        )

        return {
            "expense": _serialize_expense(full_exp or expense, household.currency),
            "alerts": [{"level": a.level, "message": a.message} for a in alerts],
        }

    @app.patch("/api/expenses/{expense_id}")
    async def api_update_expense(
        expense_id: int,
        body: UpdateExpenseRequest,
        ctx: DashboardContext = Depends(get_dashboard_context),
    ) -> dict[str, Any]:
        repo = ctx.repo
        household = ctx.household

        existing = await repo.get_expense_by_id(household.id, expense_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Expense not found.")

        amt: Optional[Decimal] = None
        if body.amount is not None:
            if body.amount <= Decimal("0"):
                raise HTTPException(
                    status_code=400, detail="Amount must be greater than 0."
                )
            amt = Decimal(str(body.amount)).quantize(Decimal("0.01"))

        updated = await repo.update_expense(
            household_id=household.id,
            expense_id=expense_id,
            amount=amt,
            category_id=body.category_id,
            description=body.description,
            is_once_off=body.is_once_off,
            is_recurring=body.is_recurring,
        )
        if updated is None:
            raise HTTPException(
                status_code=400, detail="Could not update expense (check category)."
            )

        return {"expense": _serialize_expense(updated, household.currency)}

    @app.delete("/api/expenses/{expense_id}")
    async def api_delete_expense(
        expense_id: int,
        ctx: DashboardContext = Depends(get_dashboard_context),
    ) -> dict[str, Any]:
        deleted = await ctx.repo.delete_expense(ctx.household.id, expense_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Expense not found.")
        return {"deleted": True, "id": expense_id}

    @app.put("/api/budgets")
    async def api_update_budgets(
        body: UpdateBudgetsRequest,
        ctx: DashboardContext = Depends(get_dashboard_context),
    ) -> dict[str, Any]:
        repo = ctx.repo
        household = ctx.household

        if body.clear_household_budget:
            await repo.set_household_budget(household.id, None)
        elif body.household_budget is not None:
            new_hh_budget = (
                None
                if body.household_budget <= Decimal("0")
                else Decimal(str(body.household_budget)).quantize(Decimal("0.01"))
            )
            await repo.set_household_budget(household.id, new_hh_budget)

        if body.currency and body.currency.strip():
            await repo.set_household_currency(household.id, body.currency.strip())

        for item in body.categories:
            clear_cat_b = (
                item.monthly_budget is not None and item.monthly_budget <= Decimal("0")
            )
            cat_b_val = (
                Decimal(str(item.monthly_budget)).quantize(Decimal("0.01"))
                if (item.monthly_budget is not None and item.monthly_budget > Decimal("0"))
                else None
            )
            await repo.update_category(
                household_id=household.id,
                category_id=item.category_id,
                display_name=item.display_name,
                monthly_budget=cat_b_val,
                clear_budget=clear_cat_b,
                is_fixed=item.is_fixed,
            )

        refreshed_hh = await repo.get_household_by_id(household.id)
        return {
            "status": "updated",
            "household_budget": float(refreshed_hh.monthly_budget)
            if (refreshed_hh and refreshed_hh.monthly_budget)
            else None,
            "currency": refreshed_hh.currency if refreshed_hh else household.currency,
        }

    @app.post("/api/categories")
    async def api_create_category(
        body: CreateCategoryRequest,
        ctx: DashboardContext = Depends(get_dashboard_context),
    ) -> dict[str, Any]:
        raw_key = re.sub(r"[^a-z0-9_-]", "", (body.name or "").strip().lower())
        if not raw_key:
            raise HTTPException(
                status_code=400,
                detail="Category keyword must contain letters or numbers (e.g. 'pets').",
            )
        display_name = (
            body.display_name.strip()
            if (body.display_name and body.display_name.strip())
            else f"🏷️ {raw_key.capitalize()}"
        )
        cat = await ctx.repo.get_or_create_category(
            household_id=ctx.household.id,
            name=raw_key,
            display_name=display_name,
            is_fixed=body.is_fixed,
        )
        if body.monthly_budget is not None:
            b_val = (
                Decimal(str(body.monthly_budget)).quantize(Decimal("0.01"))
                if body.monthly_budget > Decimal("0")
                else None
            )
            cat = await ctx.repo.set_category_budget(ctx.household.id, cat.id, b_val) or cat

        return {
            "category": {
                "id": cat.id,
                "name": cat.name,
                "display_name": cat.display_name,
                "is_fixed": is_fixed_category(cat),
                "budget": float(cat.monthly_budget)
                if (cat.monthly_budget and cat.monthly_budget > 0)
                else None,
            }
        }

    return app

