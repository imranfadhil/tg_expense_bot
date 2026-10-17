from decimal import Decimal

from bot.services.parser import parse_expense_text

DEFAULT_KEYS = [
    "groceries",
    "dining",
    "transport",
    "utilities",
    "housing",
    "auto",
    "school",
    "saving",
    "kids",
    "health",
    "shopping",
    "gift",
    "entertainment",
    "misc",
]


def test_parse_explicit_category_and_note():
    res = parse_expense_text("45.50 groceries Giant supermarket", DEFAULT_KEYS)
    assert res is not None
    assert res.amount == Decimal("45.50")
    assert res.category_key == "groceries"
    assert res.description == "Giant supermarket"
    assert res.matched_explicitly is True
    assert res.is_once_off is False


def test_parse_alias_keyword_and_currency_prefix():
    res = parse_expense_text("$12.80 lunch Maxwell hawker", DEFAULT_KEYS)
    assert res is not None
    assert res.amount == Decimal("12.80")
    assert res.category_key == "dining"
    assert res.description == "lunch Maxwell hawker"
    assert res.matched_explicitly is True


def test_parse_trailing_amount():
    res = parse_expense_text("coffee at yakun 6.40", DEFAULT_KEYS)
    assert res is not None
    assert res.amount == Decimal("6.40")
    assert res.category_key == "dining"
    assert res.description == "coffee at yakun"
    assert res.matched_explicitly is True


def test_parse_amount_only_defaults_to_misc():
    res = parse_expense_text("18", DEFAULT_KEYS)
    assert res is not None
    assert res.amount == Decimal("18.00")
    assert res.category_key == "misc"
    assert res.description == ""
    assert res.matched_explicitly is False


def test_parse_unrecognized_category_falls_back_to_misc():
    res = parse_expense_text("35.00 anniversary flowers", DEFAULT_KEYS)
    assert res is not None
    assert res.amount == Decimal("35.00")
    assert res.category_key == "misc"
    assert res.description == "anniversary flowers"
    assert res.matched_explicitly is False


def test_parse_custom_category():
    keys = DEFAULT_KEYS + ["pets"]
    res = parse_expense_text("68.00 pets kibble and treats", keys)
    assert res is not None
    assert res.amount == Decimal("68.00")
    assert res.category_key == "pets"
    assert res.description == "kibble and treats"
    assert res.matched_explicitly is True


def test_parse_rm_currency_and_malaysian_keywords():
    res1 = parse_expense_text("RM630 prubsn takaful", DEFAULT_KEYS)
    assert res1 is not None
    assert res1.amount == Decimal("630.00")
    assert res1.category_key == "health"
    assert res1.description == "prubsn takaful"

    res2 = parse_expense_text("nasi lemak mamak RM 14.50", DEFAULT_KEYS)
    assert res2 is not None
    assert res2.amount == Decimal("14.50")
    assert res2.category_key == "dining"
    assert res2.description == "nasi lemak mamak"


def test_parse_once_off_categories_phrases_and_keywords():
    car_res = parse_expense_text("850 car loan Maybank", DEFAULT_KEYS)
    assert car_res is not None
    assert car_res.amount == Decimal("850.00")
    assert car_res.category_key == "auto"
    assert car_res.matched_explicitly is True

    auto_res = parse_expense_text("850 auto Maybank", DEFAULT_KEYS)
    assert auto_res is not None
    assert auto_res.category_key == "auto"

    school_res = parse_expense_text("450 school April", DEFAULT_KEYS)
    assert school_res is not None
    assert school_res.amount == Decimal("450.00")
    assert school_res.category_key == "school"
    assert school_res.matched_explicitly is True

    ded_res = parse_expense_text("620 saving CPF", DEFAULT_KEYS)
    assert ded_res is not None
    assert ded_res.amount == Decimal("620.00")
    assert ded_res.category_key == "saving"
    assert ded_res.matched_explicitly is True

    # Ensure regular 'kids' expenses still match 'kids'
    kids_res = parse_expense_text("120 kids enrichment class", DEFAULT_KEYS)
    assert kids_res is not None
    assert kids_res.category_key == "kids"


def test_parse_manual_once_off_tags():
    res1 = parse_expense_text("1200 shopping new fridge #once", DEFAULT_KEYS)
    assert res1 is not None
    assert res1.amount == Decimal("1200.00")
    assert res1.category_key == "shopping"
    assert res1.description == "new fridge"
    assert res1.is_once_off is True

    res2 = parse_expense_text("350 once-off health annual checkup", DEFAULT_KEYS)
    assert res2 is not None
    assert res2.amount == Decimal("350.00")
    assert res2.category_key == "health"
    assert res2.description == "annual checkup"
    assert res2.is_once_off is True


def test_ignore_non_expense_messages():
    assert parse_expense_text("Hello family, what's for dinner?", DEFAULT_KEYS) is None
    assert parse_expense_text("/report", DEFAULT_KEYS) is None
    assert parse_expense_text("0 groceries", DEFAULT_KEYS) is None


