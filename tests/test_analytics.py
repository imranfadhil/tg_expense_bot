from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from bot.db.models import Category, Expense, Household, Member
from bot.services.analytics import (
    build_month_comparison,
    build_monthly_report,
    check_budget_alerts,
    format_comparison_message,
    format_monthly_report_message,
)


def _sample_fixtures():
    tz = ZoneInfo("Asia/Singapore")
    hh = Household(
        id=1,
        name="Tan Family",
        invite_code="FAM-TAN123",
        currency="$",
        monthly_budget=Decimal("2000.00"),
    )
    alice = Member(id=1, telegram_user_id=111, display_name="Alice", household_id=1)
    bob = Member(id=2, telegram_user_id=222, display_name="Bob", household_id=1)

    cat_groceries = Category(
        id=1,
        household_id=1,
        name="groceries",
        display_name="🛒 Groceries",
        monthly_budget=Decimal("500.00"),
    )
    cat_dining = Category(
        id=2,
        household_id=1,
        name="dining",
        display_name="🍽️ Dining",
        monthly_budget=Decimal("400.00"),
    )
    return tz, hh, alice, bob, [cat_groceries, cat_dining]


def test_monthly_report_and_pace_forecast():
    tz, hh, alice, bob, categories = _sample_fixtures()
    cat_groceries, cat_dining = categories

    now = datetime(2026, 10, 15, 12, 0, tzinfo=tz)  # Day 15 of 31
    expenses = [
        Expense(
            id=1,
            household_id=1,
            member_id=1,
            category_id=1,
            amount=Decimal("300.00"),
            description="NTUC",
            spent_at=datetime(2026, 10, 5, 10, 0, tzinfo=tz),
            member=alice,
            category=cat_groceries,
        ),
        Expense(
            id=2,
            household_id=1,
            member_id=2,
            category_id=2,
            amount=Decimal("150.00"),
            description="Family dinner",
            spent_at=datetime(2026, 10, 10, 19, 0, tzinfo=tz),
            member=bob,
            category=cat_dining,
        ),
    ]

    report = build_monthly_report(
        household=hh,
        categories=categories,
        expenses=expenses,
        year=2026,
        month=10,
        now=now,
    )
    assert report.total_spent == Decimal("450.00")
    assert report.days_elapsed == 15
    assert report.days_in_month == 31
    assert report.daily_average == Decimal("30.00")
    assert report.projected_month_total == Decimal("930.00")
    assert report.household_budget_pct == Decimal("22.5")
    assert len(report.categories) == 2
    assert len(report.members) == 2

    msg = format_monthly_report_message(report)
    assert "October 2026" in msg
    assert "$450.00" in msg
    assert "$930.00" in msg


def test_month_comparison_and_pace_check():
    tz, hh, alice, bob, categories = _sample_fixtures()
    cat_groceries, cat_dining = categories

    now = datetime(2026, 10, 15, 12, 0, tzinfo=tz)
    curr_expenses = [
        Expense(
            id=1,
            household_id=1,
            member_id=1,
            category_id=1,
            amount=Decimal("420.00"),
            description="Groceries Oct",
            spent_at=datetime(2026, 10, 12, 10, 0, tzinfo=tz),
            member=alice,
            category=cat_groceries,
        ),
    ]
    prev_expenses = [
        Expense(
            id=2,
            household_id=1,
            member_id=1,
            category_id=1,
            amount=Decimal("620.00"),
            description="Groceries Sep",
            spent_at=datetime(2026, 9, 20, 10, 0, tzinfo=tz),
            member=alice,
            category=cat_groceries,
        ),
    ]

    comp = build_month_comparison(
        household=hh,
        categories=categories,
        curr_expenses=curr_expenses,
        prev_expenses=prev_expenses,
        curr_year=2026,
        curr_month=10,
        now=now,
    )
    assert comp.curr_total == Decimal("420.00")
    assert comp.prev_total == Decimal("620.00")
    assert comp.total_diff == Decimal("-200.00")
    assert comp.is_current_month_partial is True
    # 15 / 31 of 620.00 is exactly 300.00, so 420.00 is +120.00 ahead of pace!
    assert comp.prev_equivalent_pace == Decimal("300.00")
    assert comp.pace_diff == Decimal("120.00")

    msg = format_comparison_message(comp)
    assert "Oct 2026 vs Sep 2026" in msg
    assert "Pace Check" in msg


def test_budget_alerts_warning_and_exceeded():
    _, hh, _, _, categories = _sample_fixtures()
    cat_groceries = categories[0]  # budget 500

    # 85% of category budget (425 / 500) -> warning
    alerts_warn = check_budget_alerts(
        currency="$",
        category=cat_groceries,
        cat_month_total=Decimal("425.00"),
        household_budget=hh.monthly_budget,
        household_month_total=Decimal("1000.00"),
    )
    assert any(a.level == "warning" for a in alerts_warn)

    # 110% of category budget (550 / 500) -> exceeded
    alerts_exceeded = check_budget_alerts(
        currency="$",
        category=cat_groceries,
        cat_month_total=Decimal("550.00"),
        household_budget=hh.monthly_budget,
        household_month_total=Decimal("2100.00"),
    )
    assert sum(1 for a in alerts_exceeded if a.level == "exceeded") == 2


def test_monthly_report_separates_fixed_and_once_off_from_variable_pace():
    tz, hh, alice, bob, categories = _sample_fixtures()
    cat_groceries, cat_dining = categories
    cat_car_loan = Category(
        id=3,
        household_id=1,
        name="auto",
        display_name="🚗 Car Loan",
        is_fixed=True,
    )
    cat_school_fees = Category(
        id=4,
        household_id=1,
        name="school",
        display_name="🎓 Kids School Fee",
        is_fixed=True,
    )
    cat_saving = Category(
        id=5,
        household_id=1,
        name="saving",
        display_name="🏦 Saving",
        is_fixed=True,
    )
    cat_shopping = Category(
        id=6,
        household_id=1,
        name="shopping",
        display_name="🛍️ Shopping",
        is_fixed=False,
    )
    all_cats = [cat_groceries, cat_dining, cat_car_loan, cat_school_fees, cat_saving, cat_shopping]

    now = datetime(2026, 10, 15, 12, 0, tzinfo=tz)  # Day 15 of 31
    expenses = [
        # Variable expenses: $300 + $150 = $450 over 15 days -> $30/day -> $930 projected variable
        Expense(
            id=1,
            household_id=1,
            member_id=1,
            category_id=1,
            amount=Decimal("300.00"),
            description="Groceries",
            spent_at=datetime(2026, 10, 5, 10, 0, tzinfo=tz),
            member=alice,
            category=cat_groceries,
        ),
        Expense(
            id=2,
            household_id=1,
            member_id=2,
            category_id=2,
            amount=Decimal("150.00"),
            description="Dining",
            spent_at=datetime(2026, 10, 10, 19, 0, tzinfo=tz),
            member=bob,
            category=cat_dining,
        ),
        # Fixed categories: $800 car loan + $400 school fees + $300 saving = $1500
        Expense(
            id=3,
            household_id=1,
            member_id=1,
            category_id=3,
            amount=Decimal("800.00"),
            description="Car loan",
            spent_at=datetime(2026, 10, 2, 9, 0, tzinfo=tz),
            member=alice,
            category=cat_car_loan,
        ),
        Expense(
            id=4,
            household_id=1,
            member_id=1,
            category_id=4,
            amount=Decimal("400.00"),
            description="School fee",
            spent_at=datetime(2026, 10, 3, 9, 0, tzinfo=tz),
            member=alice,
            category=cat_school_fees,
        ),
        Expense(
            id=5,
            household_id=1,
            member_id=2,
            category_id=5,
            amount=Decimal("300.00"),
            description="Monthly saving",
            spent_at=datetime(2026, 10, 1, 9, 0, tzinfo=tz),
            member=bob,
            category=cat_saving,
        ),
        # Manual once-off expense in a variable category: $200 fridge
        Expense(
            id=6,
            household_id=1,
            member_id=2,
            category_id=6,
            amount=Decimal("200.00"),
            description="Appliance",
            is_once_off=True,
            spent_at=datetime(2026, 10, 8, 14, 0, tzinfo=tz),
            member=bob,
            category=cat_shopping,
        ),
    ]

    report = build_monthly_report(
        household=hh,
        categories=all_cats,
        expenses=expenses,
        year=2026,
        month=10,
        now=now,
    )
    assert report.fixed_spent == Decimal("1700.00")
    assert report.variable_spent == Decimal("450.00")
    assert report.total_spent == Decimal("2150.00")
    assert report.variable_daily_average == Decimal("30.00")
    assert report.projected_variable_total == Decimal("930.00")
    # Projected total = $1700 fixed + $930 variable = $2630.00 (NOT $2150/15*31 = $4443.33!)
    assert report.projected_month_total == Decimal("2630.00")

    msg = format_monthly_report_message(report)
    assert "Fixed / Once-Off:" in msg
    assert "$1,700.00" in msg
    assert "$2,630.00" in msg


