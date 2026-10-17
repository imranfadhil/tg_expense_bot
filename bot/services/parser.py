from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Iterable, Optional

# Smart aliases mapping common words/merchants to canonical category keys
CATEGORY_ALIASES: dict[str, str] = {
    # Groceries
    "groceries": "groceries",
    "grocery": "groceries",
    "supermarket": "groceries",
    "market": "groceries",
    "mart": "groceries",
    "giant": "groceries",
    "ntuc": "groceries",
    "fairprice": "groceries",
    "coldstorage": "groceries",
    "shengsiong": "groceries",
    "mydin": "groceries",
    "lotus": "groceries",
    "lotuss": "groceries",
    "econsave": "groceries",
    "speedmart": "groceries",
    "99speedmart": "groceries",
    "jayagrocer": "groceries",
    "villagegrocer": "groceries",
    "aeon": "groceries",
    "costco": "groceries",
    "walmart": "groceries",
    "aldi": "groceries",
    "tesco": "groceries",
    "veggies": "groceries",
    "fruits": "groceries",
    "meat": "groceries",
    "milk": "groceries",
    "bread": "groceries",
    "barang": "groceries",
    "dapur": "groceries",
    # Dining / Food & Drinks
    "dining": "dining",
    "food": "dining",
    "eat": "dining",
    "makan": "dining",
    "mamak": "dining",
    "nasi": "dining",
    "roti": "dining",
    "sarapan": "dining",
    "breakfast": "dining",
    "brunch": "dining",
    "lunch": "dining",
    "dinner": "dining",
    "supper": "dining",
    "snack": "dining",
    "snacks": "dining",
    "coffee": "dining",
    "kopi": "dining",
    "tea": "dining",
    "teh": "dining",
    "cafe": "dining",
    "restaurant": "dining",
    "hawker": "dining",
    "warung": "dining",
    "takeaway": "dining",
    "tapau": "dining",
    "delivery": "dining",
    "grabfood": "dining",
    "foodpanda": "dining",
    "deliveroo": "dining",
    "drinks": "dining",
    # Transport
    "transport": "transport",
    "transit": "transport",
    "bus": "transport",
    "mrt": "transport",
    "lrt": "transport",
    "ktm": "transport",
    "monorail": "transport",
    "train": "transport",
    "subway": "transport",
    "taxi": "transport",
    "grab": "transport",
    "uber": "transport",
    "gojek": "transport",
    "tada": "transport",
    "in-drive": "transport",
    "indrive": "transport",
    "petrol": "transport",
    "minyak": "transport",
    "petronas": "transport",
    "shell": "transport",
    "petron": "transport",
    "caltex": "transport",
    "bhp": "transport",
    "gas": "transport",
    "fuel": "transport",
    "parking": "transport",
    "erp": "transport",
    "toll": "transport",
    "tng": "transport",
    "touchngo": "transport",
    "rfid": "transport",
    "car": "transport",
    # Utilities & Bills
    "utilities": "utilities",
    "utility": "utilities",
    "bill": "utilities",
    "bills": "utilities",
    "bil": "utilities",
    "electricity": "utilities",
    "electric": "utilities",
    "tnb": "utilities",
    "power": "utilities",
    "water": "utilities",
    "air": "utilities",
    "syabas": "utilities",
    "airselangor": "utilities",
    "iwk": "utilities",
    "indahwater": "utilities",
    "internet": "utilities",
    "unifi": "utilities",
    "maxis": "utilities",
    "celcom": "utilities",
    "digi": "utilities",
    "umobile": "utilities",
    "yes5g": "utilities",
    "time": "utilities",
    "wifi": "utilities",
    "broadband": "utilities",
    "phone": "utilities",
    "mobile": "utilities",
    "telco": "utilities",
    "trash": "utilities",
    "cukai": "utilities",
    # Housing & Home
    "housing": "housing",
    "rent": "housing",
    "sewa": "housing",
    "mortgage": "housing",
    "loan": "housing",
    "condo": "housing",
    "maintenance": "housing",
    "conservancy": "housing",
    "repair": "housing",
    "plumber": "housing",
    "furniture": "housing",
    "ikea": "housing",
    "mrdiy": "housing",
    "appliance": "housing",
    "cleaning": "housing",
    "household": "housing",
    # Kids & Family
    "kids": "kids",
    "kid": "kids",
    "anak": "kids",
    "child": "kids",
    "children": "kids",
    "baby": "kids",
    "diapers": "kids",
    "pampers": "kids",
    "susu": "kids",
    "school": "kids",
    "sekolah": "kids",
    "tuition": "kids",
    "tuisyen": "kids",
    "daycare": "kids",
    "taska": "kids",
    "tadika": "kids",
    "preschool": "kids",
    "enrichment": "kids",
    "toys": "kids",
    "allowance": "kids",
    "nafkah": "kids",
    # Health & Medical
    "health": "health",
    "medical": "health",
    "doctor": "health",
    "clinic": "health",
    "klinik": "health",
    "hospital": "health",
    "dentist": "health",
    "dental": "health",
    "pharmacy": "health",
    "farmasi": "health",
    "medicine": "health",
    "ubat": "health",
    "meds": "health",
    "vitamins": "health",
    "watsons": "health",
    "guardian": "health",
    "caring": "health",
    "insurance": "health",
    "takaful": "health",
    "prubsn": "health",
    "etiqa": "health",
    "hibah": "health",
    # Shopping & Personal
    "shopping": "shopping",
    "shop": "shopping",
    "clothes": "shopping",
    "baju": "shopping",
    "clothing": "shopping",
    "shoes": "shopping",
    "kasut": "shopping",
    "uniqlo": "shopping",
    "shopee": "shopping",
    "lazada": "shopping",
    "tiktok": "shopping",
    "amazon": "shopping",
    "taobao": "shopping",
    "haircut": "shopping",
    "gunting": "shopping",
    "salon": "shopping",
    "gift": "shopping",
    "gifts": "shopping",
    "hadiah": "shopping",
    # Entertainment & Leisure
    "entertainment": "entertainment",
    "movie": "entertainment",
    "movies": "entertainment",
    "wayang": "entertainment",
    "cinema": "entertainment",
    "netflix": "entertainment",
    "spotify": "entertainment",
    "disney": "entertainment",
    "astro": "entertainment",
    "youtube": "entertainment",
    "subscription": "entertainment",
    "game": "entertainment",
    "games": "entertainment",
    "outing": "entertainment",
    "holiday": "entertainment",
    "cuti": "entertainment",
    "travel": "entertainment",
    # Misc
    "misc": "misc",
    "other": "misc",
    "others": "misc",
    "sedekah": "misc",
    "infaq": "misc",
    "zakat": "misc",
}

# Matches currency prefix/suffix and number like:
# "$45.50", "RM45.50", "RM 45.50", "45.50", "SGD 45.50", "45,50", "1,250.00"
_LEADING_AMOUNT_RE = re.compile(
    r"^\s*[+]?\s*(?:[$€£¥₹]|RM|MYR|SGD|USD|EUR|GBP|AUD|CAD|S\$)?\s*"
    r"(?P<amount>\d{1,3}(?:,\d{3})+|\d+)(?P<decimals>[.,]\d{1,2})?\s+"
    r"(?P<rest>.+?)\s*$",
    re.IGNORECASE,
)

_ONLY_AMOUNT_RE = re.compile(
    r"^\s*[+]?\s*(?:[$€£¥₹]|RM|MYR|SGD|USD|EUR|GBP|AUD|CAD|S\$)?\s*"
    r"(?P<amount>\d{1,3}(?:,\d{3})+|\d+)(?P<decimals>[.,]\d{1,2})?\s*$",
    re.IGNORECASE,
)

_TRAILING_AMOUNT_RE = re.compile(
    r"^\s*(?P<rest>[a-zA-Z].*?)\s+"
    r"(?:[$€£¥₹]|RM|MYR|SGD|USD|EUR|GBP|AUD|CAD|S\$)?\s*"
    r"(?P<amount>\d{1,3}(?:,\d{3})+|\d+)(?P<decimals>[.,]\d{1,2})?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedExpense:
    amount: Decimal
    category_key: str
    description: str
    matched_explicitly: bool


def _normalize_decimal(amount_int: str, decimals: Optional[str]) -> Optional[Decimal]:
    clean_int = amount_int.replace(",", "")
    if decimals:
        clean_dec = decimals.replace(",", ".")
        raw = f"{clean_int}{clean_dec}"
    else:
        raw = clean_int
    try:
        val = Decimal(raw).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    if val <= Decimal("0") or val >= Decimal("100000000"):
        return None
    return val


def _clean_token(word: str) -> str:
    return re.sub(r"[^a-z0-9_-]", "", word.lower())


def parse_expense_text(
    text: str,
    available_category_keys: Optional[Iterable[str]] = None,
) -> Optional[ParsedExpense]:
    """
    Parse natural expense text such as:
      - "45.50 groceries Giant supermarket"
      - "$12.80 lunch Maxwell"
      - "lunch 12.50"
      - "18.00"
    Returns ParsedExpense or None if text does not look like an expense entry.
    """
    if not text or text.strip().startswith("/"):
        return None

    stripped = text.strip()
    if "\n" in stripped:
        return None

    known_keys = {k.lower() for k in (available_category_keys or [])}

    # 1. Try amount only (e.g. "18.50")
    m_only = _ONLY_AMOUNT_RE.match(stripped)
    if m_only:
        amount = _normalize_decimal(m_only.group("amount"), m_only.group("decimals"))
        if amount is None:
            return None
        return ParsedExpense(
            amount=amount,
            category_key="misc",
            description="",
            matched_explicitly=False,
        )

    # 2. Try leading amount (e.g. "45.50 groceries Giant") or trailing amount (e.g. "lunch 12.50")
    m = _LEADING_AMOUNT_RE.match(stripped)
    if not m:
        m = _TRAILING_AMOUNT_RE.match(stripped)
    if not m:
        return None

    amount = _normalize_decimal(m.group("amount"), m.group("decimals"))
    if amount is None:
        return None

    rest = m.group("rest").strip()
    tokens = rest.split()
    if not tokens:
        return ParsedExpense(
            amount=amount,
            category_key="misc",
            description="",
            matched_explicitly=False,
        )

    first_clean = _clean_token(tokens[0])

    # Check if first word is an exact household category name
    if first_clean in known_keys:
        desc = " ".join(tokens[1:]).strip() or tokens[0]
        return ParsedExpense(
            amount=amount,
            category_key=first_clean,
            description=desc,
            matched_explicitly=True,
        )

    # Check if first word matches a canonical category alias
    if first_clean in CATEGORY_ALIASES:
        mapped_cat = CATEGORY_ALIASES[first_clean]
        if not known_keys or mapped_cat in known_keys:
            # If the user typed the exact category name (e.g. "groceries Giant"), strip the category token from description
            # If the user typed a descriptive alias (e.g. "lunch Maxwell" or "ntuc"), keep the full description if there's only 1 token or if it adds context
            if first_clean == mapped_cat and len(tokens) > 1:
                desc = " ".join(tokens[1:]).strip()
            else:
                desc = rest
            return ParsedExpense(
                amount=amount,
                category_key=mapped_cat,
                description=desc,
                matched_explicitly=True,
            )

    # Otherwise scan remaining tokens for any category or alias keyword
    for token in tokens[1:]:
        cleaned = _clean_token(token)
        if cleaned in known_keys:
            return ParsedExpense(
                amount=amount,
                category_key=cleaned,
                description=rest,
                matched_explicitly=True,
            )
        if cleaned in CATEGORY_ALIASES:
            mapped_cat = CATEGORY_ALIASES[cleaned]
            if not known_keys or mapped_cat in known_keys:
                return ParsedExpense(
                    amount=amount,
                    category_key=mapped_cat,
                    description=rest,
                    matched_explicitly=True,
                )

    # Fallback to 'misc' while keeping the full note
    fallback_cat = "misc" if (not known_keys or "misc" in known_keys) else next(iter(known_keys))
    return ParsedExpense(
        amount=amount,
        category_key=fallback_cat,
        description=rest,
        matched_explicitly=False,
    )

