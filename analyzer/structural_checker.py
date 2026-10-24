import re
import logging
from datetime import date, datetime

logger = logging.getLogger(__name__)

# ── helpers ────────────────────────────────────────────────────────────────

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# days in each month (non-leap year; Feb handled separately)
_DAYS_IN_MONTH = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _max_day(month: int, year: int) -> int:
    if month == 2 and _is_leap(year):
        return 29
    return _DAYS_IN_MONTH[month]


def _signal(signal_id, severity, title_ru, description_ru, evidence_text,
            confidence, method):
    return {
        "signal_id": signal_id,
        "category": "structural",
        "severity": severity,
        "title_ru": title_ru,
        "description_ru": description_ru,
        "evidence_text": evidence_text,
        "confidence": confidence,
        "method": method,
    }


# ── date parsing ────────────────────────────────────────────────────────────

def _parse_dates(text: str) -> list[tuple]:
    """Return list of (day, month, year, matched_string)."""
    found = []

    # Pattern: DD.MM.YYYY or D.M.YYYY
    for m in re.finditer(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        found.append((d, mo, y, m.group(0)))

    # Pattern: D MMMM YYYY г. (Russian month names)
    month_pattern = "|".join(MONTHS_RU.keys())
    for m in re.finditer(
        rf"\b(\d{{1,2}})\s+({month_pattern})\s+(\d{{4}})",
        text, re.IGNORECASE
    ):
        d = int(m.group(1))
        mo = MONTHS_RU[m.group(2).lower()]
        y = int(m.group(3))
        found.append((d, mo, y, m.group(0)))

    return found


# ── amount parsing ──────────────────────────────────────────────────────────

def _parse_amounts(text: str) -> list[tuple]:
    """Return list of (amount_float, matched_string)."""
    found = []
    # Match numbers (with spaces/commas as thousands sep) followed by ₸/тг/тенге/KZT
    # or preceded by them
    pattern = r"(\d[\d\s]*(?:[.,]\d+)?)\s*(?:₸|тг\.?|тенге|KZT)"
    for m in re.finditer(pattern, text, re.IGNORECASE):
        raw_num = re.sub(r"[\s,]", "", m.group(1)).replace(",", ".")
        try:
            amount = float(raw_num)
            found.append((amount, m.group(0).strip()))
        except ValueError:
            pass
    return found


# ── checks ─────────────────────────────────────────────────────────────────

def _check_dates(text: str) -> list[dict]:
    signals = []
    today = date.today()

    for d, mo, y, evidence in _parse_dates(text):
        # Validate month range
        if not (1 <= mo <= 12):
            signals.append(_signal(
                "DATE_IMPOSSIBLE", "high",
                "Невозможная дата",
                f"Месяц {mo} не существует (допустимо 1–12).",
                evidence, "high", "date_format_check",
            ))
            continue

        # Validate day range
        if not (1 <= d <= _max_day(mo, y)):
            signals.append(_signal(
                "DATE_IMPOSSIBLE", "high",
                "Невозможная дата",
                f"В {list(MONTHS_RU.keys())[mo-1]} {y} не бывает {d} дней.",
                evidence, "high", "date_format_check",
            ))
            continue

        # Future date check
        try:
            dt = date(y, mo, d)
            if dt > today + __import__("datetime").timedelta(days=1):
                signals.append(_signal(
                    "DATE_FUTURE", "medium",
                    "Дата в будущем",
                    f"Дата {evidence} позже сегодняшней ({today.isoformat()}).",
                    evidence, "medium", "date_future_check",
                ))
        except ValueError:
            pass

    return signals


def _check_amounts(text: str) -> list[dict]:
    signals = []
    for amount, evidence in _parse_amounts(text):
        if amount > 50_000_000:
            signals.append(_signal(
                "AMOUNT_UNREALISTIC_HIGH", "medium",
                "Нереалистично большая сумма",
                f"Сумма {evidence} превышает 50 000 000 ₸.",
                evidence, "medium", "amount_format_check",
            ))
        elif amount < 10:
            signals.append(_signal(
                "AMOUNT_UNREALISTIC_LOW", "medium",
                "Нереалистично маленькая сумма",
                f"Сумма {evidence} меньше 10 ₸.",
                evidence, "medium", "amount_format_check",
            ))
    return signals


def _check_phone(text: str) -> list[dict]:
    signals = []
    # Use word boundaries ((?<!\d) / (?!\d)) so we don't match substrings
    # of 18-digit receipt numbers like 898936951310176343.
    phone_re = re.compile(
        r"(?<!\d)(?:\+?[78]\d{10}|\+?\d[\d\s\-\(\)]{8,14}\d)(?!\d)"
    )
    for m in phone_re.finditer(text):
        phone = m.group(0)
        digits = re.sub(r"\D", "", phone)

        # Skip numbers that appear right after receipt-number keywords
        preceding = text[max(0, m.start() - 40):m.start()].lower()
        if any(kw in preceding for kw in ("квитанци", "ng ", "ng\t", "№", "receipt")):
            continue

        # KZ numbers: 7 or 8 + 10 digits, second pair in 70-77 / 73-75
        if len(digits) == 11 and digits[0] in ("7", "8"):
            prefix = digits[1:3]
            kz_prefixes = (
                "70", "71", "72", "76", "77",  # mobile
                "73", "74", "75",               # regional
            )
            if any(prefix.startswith(p[1:]) for p in kz_prefixes):
                continue  # valid KZ — not a signal
        signals.append(_signal(
            "PHONE_FORMAT_UNUSUAL", "low",
            "Нестандартный формат телефона",
            f"Номер '{phone}' не соответствует казахстанскому формату (+77XX / 87XX).",
            phone, "low", "phone_format_check",
        ))
    return signals


_CASINO_KEYWORDS = [
    "казино", "casino", "слот", "slot", "ставк", "ставка",
    "выигрыш", "выиграл", "бонус", "фриспин", "депозит",
    "промокод", "1win", "mostbet", "олимп",
]

_PYRAMID_KEYWORDS = [
    "розыгрыш", "лотерея", "билет", "слот занят",
    "пассивный доход", "вложи", "получи", "приведи друга", "рефер",
]


def _check_keywords(text: str, keywords: list[str], signal_id: str,
                    title: str, method: str) -> list[dict]:
    text_lower = text.lower()
    hits = [kw for kw in keywords if kw in text_lower]
    if not hits:
        return []
    evidence = ", ".join(hits[:5])
    return [_signal(
        signal_id, "medium",
        title,
        f"Найдены ключевые слова: {evidence}.",
        evidence, "medium", method,
    )]


# ── public API ──────────────────────────────────────────────────────────────

def check_structure(ocr_result: dict) -> list[dict]:
    text = ocr_result.get("raw_text", "")
    signals = []

    signals += _check_dates(text)
    signals += _check_amounts(text)
    signals += _check_phone(text)
    signals += _check_keywords(
        text, _CASINO_KEYWORDS, "CASINO_KEYWORDS_DETECTED",
        "Ключевые слова казино/ставок", "casino_keyword_check",
    )
    signals += _check_keywords(
        text, _PYRAMID_KEYWORDS, "PYRAMID_KEYWORDS_DETECTED",
        "Ключевые слова лотереи/пирамиды", "lottery_pyramid_check",
    )

    logger.info("Structural check: %d signal(s) found", len(signals))
    return signals
