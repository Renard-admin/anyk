"""
Document Gatekeeper — Stage 1 classification.

Before any scoring the document must prove it is a Kaspi payment receipt.
If at least KASPI_MARKER_THRESHOLD distinct Kaspi marker strings are found in
the OCR text the document passes; otherwise Trust = 0 and analysis stops.

Required markers (any 3 of 5 groups must match):
  1. "Перевод успешно совершен"
  2. "№ квитанции" | "Ng квитанции"
  3. "Дата и время"
  4. "Kaspi Gold" | "клиенту Kaspi" | "Kaspi.kz"
  5. "Отправитель"
"""
from __future__ import annotations

import re

KASPI_MARKER_THRESHOLD = 3

_MARKER_GROUPS: list[tuple[str, list[str]]] = [
    ("success_header",   ["перевод успешно совершен", "успешно совершен"]),
    ("receipt_number",   ["№ квитанции", "ng квитанции", "квитанции"]),
    ("datetime_field",   ["дата и время"]),
    ("kaspi_brand",      ["kaspi gold", "клиенту kaspi", "kaspi.kz", "kaspi"]),
    ("sender_field",     ["отправитель"]),
]


def check_document(ocr_result: dict) -> dict:
    """
    Returns:
        {
            "passed": bool,
            "markers_found": int,
            "matched_groups": list[str],
            "missing_groups": list[str],
        }

    When passed=False the pipeline must set trust_score=0 and status=
    "Не является платёжным документом".
    """
    raw = ocr_result.get("raw_text", "").lower()

    matched: list[str] = []
    missing: list[str] = []

    for group_name, keywords in _MARKER_GROUPS:
        if any(kw.lower() in raw for kw in keywords):
            matched.append(group_name)
        else:
            missing.append(group_name)

    passed = len(matched) >= KASPI_MARKER_THRESHOLD

    return {
        "passed": passed,
        "markers_found": len(matched),
        "matched_groups": matched,
        "missing_groups": missing,
    }
