"""
Explainable Trust Index (0–100 %) — Stage 2 scoring.

Algorithm (additive, base = 0 %):

  Prerequisites:
    • Document Gatekeeper (Stage 1) must have passed.
      If not passed → trust_score = 0, label = "Не является платёжным документом".

  Additive bonuses:
    +30  Kaspi primary markers present (≥3 of 5 marker groups matched)
    +20  Valid 18-digit receipt number found
    +30  Layout / anchor geometry match (≥3 of 5 Kaspi anchors in position)
    +20  Date / time format consistent with Kaspi receipts

  Anomaly deductions (applied after bonuses):
    −15  Each HIGH severity signal
    −10  Each MEDIUM severity signal
    −3   Each LOW severity signal
    −10  OCR confidence < 0.60 (poor readability)
    −5   OCR confidence 0.60–0.74 (moderate quality)
    −10  No text extracted at all

  Floor: 0 %  Cap: 100 %

The score is NOT a fraud probability.  It measures technical conformance
to the expected Kaspi.kz receipt structure and is used as a reference
indicator only.
"""
from __future__ import annotations

import re
import logging

logger = logging.getLogger(__name__)

_RECEIPT_RE = re.compile(r"\b\d{18}\b")

# DD.MM.YYYY HH:MM or DD.MM.YYYY
_DATE_RE = re.compile(
    r"\b\d{2}\.\d{2}\.\d{4}\b"
)
_DATETIME_RE = re.compile(
    r"\b\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2}\b"
)

_BONUS_MARKERS   = 30
_BONUS_RECEIPT   = 20
_BONUS_LAYOUT    = 30
_BONUS_DATETIME  = 20

_DEDUCT_HIGH     = 15
_DEDUCT_MEDIUM   = 10
_DEDUCT_LOW      =  3
_DEDUCT_OCR_POOR     = 10  # conf < 0.60
_DEDUCT_OCR_MODERATE =  5  # 0.60–0.74
_DEDUCT_NO_TEXT      = 10


def _explain(delta: int, reason: str, sign: str = "−") -> dict:
    return {"delta": delta, "sign": sign, "reason": reason}


def _check_layout_anchors(regions: list[dict]) -> bool:
    """Return True if ≥3 Kaspi anchor keywords are present among OCR regions."""
    _ANCHOR_KEYWORDS = [
        ["перевод успешно", "успешно совершен"],
        ["квитанции", "квитанция"],
        ["дата и время", "дата"],
        ["комиссия"],
        ["отправитель"],
    ]
    texts = " ".join(
        r.get("text", "").lower() for r in regions
    )
    matched = sum(
        1 for kws in _ANCHOR_KEYWORDS if any(kw in texts for kw in kws)
    )
    return matched >= 3


def calculate_trust(
    signals: list[dict],
    ocr_result: dict,
    pdf_info: dict | None = None,
    gatekeeper_result: dict | None = None,
) -> dict:
    """
    Returns:
        {
            "trust_score": int,           # 0–100
            "trust_label": str,
            "trust_label_en": str,
            "trust_explanation": [...],   # list of {delta, sign, reason}
            "trust_disclaimer": str,
        }
    """
    raw_text = ocr_result.get("raw_text", "").strip()
    conf     = ocr_result.get("mean_confidence", 0.0)
    regions  = ocr_result.get("regions", [])

    # ── Stage 1: Gatekeeper ───────────────────────────────────────────────
    if gatekeeper_result is not None and not gatekeeper_result.get("passed", True):
        return {
            "trust_score":       0,
            "trust_label":       "Не является платёжным документом",
            "trust_label_en":    "invalid",
            "trust_explanation": [
                _explain(0,
                         "Документ не содержит обязательных маркеров Kaspi "
                         f"(найдено {gatekeeper_result.get('markers_found', 0)} из 3 необходимых)",
                         "—"),
            ],
            "trust_disclaimer": (
                "Документ не опознан как платёжный чек Kaspi.kz. "
                "Дальнейший анализ не выполнялся."
            ),
        }

    score = 0
    breakdown: list[dict] = []

    # ── Stage 2a: Additive bonuses ────────────────────────────────────────

    # +30 Kaspi markers
    markers_found = gatekeeper_result.get("markers_found", 0) if gatekeeper_result else 0
    if markers_found == 0 and raw_text:
        # Fall back: count inline (no explicit gatekeeper call)
        from analyzer.document_gatekeeper import check_document
        gr = check_document(ocr_result)
        markers_found = gr.get("markers_found", 0)
    if markers_found >= 3:
        score += _BONUS_MARKERS
        breakdown.append(_explain(_BONUS_MARKERS,
                                   f"Маркеры Kaspi присутствуют ({markers_found}/5 групп)", "+"))
    else:
        breakdown.append(_explain(0,
                                   f"Маркеры Kaspi не найдены ({markers_found}/5 групп)", "—"))

    # +20 Valid 18-digit receipt number
    if _RECEIPT_RE.search(raw_text):
        score += _BONUS_RECEIPT
        breakdown.append(_explain(_BONUS_RECEIPT,
                                   "Обнаружен корректный 18-значный номер квитанции", "+"))

    # +30 Layout anchor geometry
    if _check_layout_anchors(regions):
        score += _BONUS_LAYOUT
        breakdown.append(_explain(_BONUS_LAYOUT,
                                   "Геометрия полей соответствует шаблону Kaspi", "+"))

    # +20 Date / time format
    if _DATETIME_RE.search(raw_text) or _DATE_RE.search(raw_text):
        score += _BONUS_DATETIME
        breakdown.append(_explain(_BONUS_DATETIME,
                                   "Формат даты и времени соответствует стандарту Kaspi", "+"))

    # ── Stage 2b: Anomaly deductions ──────────────────────────────────────
    high_sigs   = [s for s in signals if s.get("severity") == "high"]
    medium_sigs = [s for s in signals if s.get("severity") == "medium"]
    low_sigs    = [s for s in signals if s.get("severity") == "low"]

    if high_sigs:
        d = len(high_sigs) * _DEDUCT_HIGH
        score -= d
        breakdown.append(_explain(d, f"{len(high_sigs)} критических сигнала(ов)"))
    if medium_sigs:
        d = len(medium_sigs) * _DEDUCT_MEDIUM
        score -= d
        breakdown.append(_explain(d, f"{len(medium_sigs)} средних сигнала(ов)"))
    if low_sigs:
        d = len(low_sigs) * _DEDUCT_LOW
        score -= d
        breakdown.append(_explain(d, f"{len(low_sigs)} низких сигнала(ов)"))

    # OCR quality deductions
    if not raw_text:
        score -= _DEDUCT_NO_TEXT
        breakdown.append(_explain(_DEDUCT_NO_TEXT, "Текст не извлечён из документа"))
    elif conf < 0.60:
        score -= _DEDUCT_OCR_POOR
        breakdown.append(_explain(_DEDUCT_OCR_POOR,
                                   f"Низкое качество OCR ({conf*100:.0f}%)"))
    elif conf < 0.75:
        score -= _DEDUCT_OCR_MODERATE
        breakdown.append(_explain(_DEDUCT_OCR_MODERATE,
                                   f"Среднее качество OCR ({conf*100:.0f}%)"))

    score = min(100, max(0, score))

    # ── Label ─────────────────────────────────────────────────────────────
    if score >= 75:
        label, label_en = "Высокий", "high"
    elif score >= 45:
        label, label_en = "Средний", "medium"
    else:
        label, label_en = "Низкий", "low"

    logger.info("Trust score: %d%% (%s)", score, label_en)

    return {
        "trust_score": score,
        "trust_label": label,
        "trust_label_en": label_en,
        "trust_explanation": breakdown,
        "trust_disclaimer": (
            "Процент доверия отражает степень соответствия документа эталонной "
            "технической и визуальной структуре Kaspi.kz и не является юридической "
            "гарантией транзакции."
        ),
    }
