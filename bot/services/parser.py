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
    # Car Loan / Auto (Fixed / Once-off)
    "auto": "auto",
    "car_loan": "auto",
    "car-loan": "auto",
    "carloan": "auto",
    "autoloan": "auto",
    "auto_loan": "auto",
    "hirepurchase": "auto",
    "ansuran": "auto",
    # Kids School Fees (Fixed / Once-off)
    "school": "school",
    "sekolah": "school",
    "school_fees": "school",
    "school_fee": "school",
    "school-fees": "school",
    "school-fee": "school",
    "schoolfees": "school",
    "schoolfee": "school",
    "yuran": "school",
    # Saving (Fixed / Once-off)
    "saving": "saving",
    "savings": "saving",
    "save": "saving",
    "simpanan": "saving",
    "tabung": "saving",
    "tabunghaji": "saving",
    "asb": "saving",
    "sspn": "saving",
    "fd": "saving",
    "deposit": "saving",
    "invest": "saving",
    "investment": "saving",
    "pelaburan": "saving",
    "stashaway": "saving",
    "wahed": "saving",
    "epf": "saving",
    "kwsp": "saving",
    "cpf": "saving",
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
    # Gift & Presents
    "gift": "gift",
    "gifts": "gift",
    "hadiah": "gift",
    "present": "gift",
    "presents": "gift",
    "angpao": "gift",
    "angpow": "gift",
    "duitraya": "gift",
    "hamper": "gift",
    "saguhati": "gift",
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

# Multi-word phrases checked before single-token aliases so compound categories
# like "car loan", "kids school fee", and "monthly saving" resolve accurately.
PHRASE_ALIASES: dict[tuple[str, ...], str] = {
    ("car", "loan"): "auto",
    ("auto", "loan"): "auto",
    ("vehicle", "loan"): "auto",
    ("car", "installment"): "auto",
    ("car", "instalment"): "auto",
    ("loan", "kereta"): "auto",
    ("ansuran", "kereta"): "auto",
    ("kids", "school", "fee"): "school",
    ("kids", "school", "fees"): "school",
    ("kid", "school", "fee"): "school",
    ("kid", "school", "fees"): "school",
    ("kids", "school"): "school",
    ("kid", "school"): "school",
    ("school", "fee"): "school",
    ("school", "fees"): "school",
    ("tuition", "fee"): "school",
    ("tuition", "fees"): "school",
    ("preschool", "fee"): "school",
    ("preschool", "fees"): "school",
    ("daycare", "fee"): "school",
    ("daycare", "fees"): "school",
    ("yuran", "sekolah"): "school",
    ("yuran", "tadika"): "school",
    ("yuran", "taska"): "school",
    ("monthly", "saving"): "saving",
    ("monthly", "savings"): "saving",
    ("emergency", "fund"): "saving",
    ("fixed", "deposit"): "saving",
    ("tabung", "haji"): "saving",
}

# Explicit manual once-off tags users can include in an expense message
_ONCE_OFF_TOKEN_SET: frozenset[str] = frozenset(
    {
        "#once",
        "#onceoff",
        "#once-off",
        "#once_off",
        "#oneoff",
        "#one-off",
        "#one_off",
        "#onetime",
        "#one-time",
        "#fixed",
        "!once",
        "!onceoff",
        "!oneoff",
        "!fixed",
        "once-off",
        "once_off",
        "onceoff",
        "one-off",
        "one_off",
        "oneoff",
        "one-time",
        "onetime",
        "[once]",
        "(once)",
        "(once-off)",
        "(one-off)",
    }
)

_ONCE_OFF_PHRASE_RE = re.compile(
    r"(?:^|\s)(?:#once(?:-?off)?|#one(?:-?off|-?time)?|#fixed|!once(?:-?off)?|!fixed|"
    r"\[once(?:-?off)?\]|\(once(?:-?off)?\)|\(one(?:-?off|-?time)?\)|"
    r"\bonce[-_\s]off\b|\bone[-_\s]off\b|\bone[-_\s]time\b)(?=\s|$)",
    re.IGNORECASE,
)

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
    is_once_off: bool = False


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


def _extract_once_off_flag(text: str) -> tuple[str, bool]:
    """
    Detect and strip manual once-off markers (e.g. '#once', 'once-off', 'one-off', '#fixed')
    from raw input text. Returns (cleaned_text, is_once_off).
    """
    is_once_off = False
    tokens = text.split()
    kept_tokens: list[str] = []
    for tok in tokens:
        if tok.lower() in _ONCE_OFF_TOKEN_SET:
            is_once_off = True
        else:
            kept_tokens.append(tok)
    joined = " ".join(kept_tokens)
    if _ONCE_OFF_PHRASE_RE.search(joined):
        is_once_off = True
        joined = _ONCE_OFF_PHRASE_RE.sub(" ", joined)
        joined = " ".join(joined.split())
    return joined.strip(), is_once_off


def _match_phrase_alias(
    cleaned_tokens: list[str],
    known_keys: set[str],
) -> Optional[tuple[int, int, str]]:
    """
    Check if any 3-word or 2-word slice in cleaned_tokens matches PHRASE_ALIASES.
    Returns (start_idx, length, mapped_category_key) or None.
    """
    n = len(cleaned_tokens)
    for start in range(n):
        for length in (3, 2):
            if start + length <= n:
                phrase = tuple(cleaned_tokens[start : start + length])
                mapped = PHRASE_ALIASES.get(phrase)
                if mapped and (not known_keys or mapped in known_keys):
                    return start, length, mapped
    return None


def parse_expense_text(
    text: str,
    available_category_keys: Optional[Iterable[str]] = None,
) -> Optional[ParsedExpense]:
    """
    Parse natural expense text such as:
      - "45.50 groceries Giant supermarket"
      - "$12.80 lunch Maxwell"
      - "850 car loan Maybank"
      - "1200 shopping new fridge #once"
      - "lunch 12.50"
      - "18.00"
    Returns ParsedExpense or None if text does not look like an expense entry.
    """
    if not text or text.strip().startswith("/"):
        return None

    stripped = text.strip()
    if "\n" in stripped:
        return None

    stripped, is_once_off = _extract_once_off_flag(stripped)
    if not stripped:
        return None

    known_keys = {k.lower() for k in (available_category_keys or [])}

    # 1. Try amount only (e.g. "18.50" or "18.50 #once")
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
            is_once_off=is_once_off,
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
            is_once_off=is_once_off,
        )

    cleaned_tokens = [_clean_token(t) for t in tokens]
    first_clean = cleaned_tokens[0]

    # Check multi-word phrase aliases starting at token 0 first (e.g. "car loan", "kids school fee")
    phrase_match = _match_phrase_alias(cleaned_tokens, known_keys)
    if phrase_match is not None and phrase_match[0] == 0:
        _, _, mapped_cat = phrase_match
        return ParsedExpense(
            amount=amount,
            category_key=mapped_cat,
            description=rest,
            matched_explicitly=True,
            is_once_off=is_once_off,
        )

    # Check if first word is an exact household category name
    if first_clean in known_keys:
        desc = " ".join(tokens[1:]).strip() or tokens[0]
        return ParsedExpense(
            amount=amount,
            category_key=first_clean,
            description=desc,
            matched_explicitly=True,
            is_once_off=is_once_off,
        )

    # Check if first word matches a canonical category alias
    if first_clean in CATEGORY_ALIASES:
        mapped_cat = CATEGORY_ALIASES[first_clean]
        if not known_keys or mapped_cat in known_keys:
            if first_clean == mapped_cat and len(tokens) > 1:
                desc = " ".join(tokens[1:]).strip()
            else:
                desc = rest
            return ParsedExpense(
                amount=amount,
                category_key=mapped_cat,
                description=desc,
                matched_explicitly=True,
                is_once_off=is_once_off,
            )

    # Check if a multi-word phrase alias appears later in the description
    if phrase_match is not None:
        _, _, mapped_cat = phrase_match
        return ParsedExpense(
            amount=amount,
            category_key=mapped_cat,
            description=rest,
            matched_explicitly=True,
            is_once_off=is_once_off,
        )

    # Otherwise scan remaining tokens for any category or alias keyword
    for cleaned in cleaned_tokens[1:]:
        if cleaned in known_keys:
            return ParsedExpense(
                amount=amount,
                category_key=cleaned,
                description=rest,
                matched_explicitly=True,
                is_once_off=is_once_off,
            )
        if cleaned in CATEGORY_ALIASES:
            mapped_cat = CATEGORY_ALIASES[cleaned]
            if not known_keys or mapped_cat in known_keys:
                return ParsedExpense(
                    amount=amount,
                    category_key=mapped_cat,
                    description=rest,
                    matched_explicitly=True,
                    is_once_off=is_once_off,
                )

    # Fallback to 'misc' while keeping the full note
    fallback_cat = "misc" if (not known_keys or "misc" in known_keys) else next(iter(known_keys))
    return ParsedExpense(
        amount=amount,
        category_key=fallback_cat,
        description=rest,
        matched_explicitly=False,
        is_once_off=is_once_off,
    )


