"""
Geometric and visual layout verification for Kaspi receipt images.

Analyzes OCR bounding boxes and (optionally) the rendered image to check
whether the spatial structure matches the known Kaspi receipt template:

  ┌──────────────────────────────┐
  │  "Перевод успешно совершен"  │  y ≈ 486
  │        560 〒                 │  y ≈ 541
  │  [recipient name]            │  y ≈ 753
  │  Тип перевода    Перевод     │  y ≈ 903
  │  клиенту Kaspi               │  y ≈ 943
  │  Ng квитанции   18-dig num  │  y ≈ 1027
  │  Дата и время    DD.MM.YYYY  │  y ≈ 1113
  │  по              Астане      │  y ≈ 1151
  │  Комиссия        0 〒         │  y ≈ 1236
  │  Отправитель     [name]      │  y ≈ 1319
  │  Откуда          Kaspi Gold  │  y ≈ 1400
  └──────────────────────────────┘

These Y coordinates are normalised to a 2.5× render of a 390 pt page
(≈ 975 px) and were measured from 4 real Kaspi receipts.

The module returns a list of layout_signals (same schema as structural_checker).
It is deliberately lenient: it only raises a signal when *multiple* anchors
are missing or positions deviate significantly (>25% of document height).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ── Reference layout (y-centre, tolerance in px at 2.5× scale) ────────────

_KASPI_ANCHORS: list[dict] = [
    {"label": "success_header", "keywords": ["перевод успешно", "успешно совершен"],    "y_ref": 486,  "tol": 150},
    {"label": "receipt_number", "keywords": ["квитанции", "квитанция", "ng "],         "y_ref": 1027, "tol": 200},
    {"label": "datetime_field", "keywords": ["дата и время", "дата"],                  "y_ref": 1113, "tol": 200},
    {"label": "commission",     "keywords": ["комиссия"],                               "y_ref": 1236, "tol": 200},
    {"label": "sender_field",   "keywords": ["отправитель"],                            "y_ref": 1319, "tol": 200},
]

# Kaspi receipt number: exactly 18 digits
_RECEIPT_RE = re.compile(r"\b(\d{18})\b")
# Valid amount pattern
_AMOUNT_RE  = re.compile(r"\d[\d\s]*(?:[.,]\d+)?\s*(?:〒|₸|тг\.?|тенге|T)", re.IGNORECASE)


def _signal(signal_id, severity, title_ru, description_ru, evidence_text, confidence):
    return {
        "signal_id":      signal_id,
        "category":       "layout",
        "severity":       severity,
        "title_ru":       title_ru,
        "description_ru": description_ru,
        "evidence_text":  evidence_text,
        "confidence":     confidence,
        "method":         "layout_geometry_check",
    }


def _get_region_y(region: dict) -> float:
    """Return the top-left y coordinate of an OCR region bbox."""
    bbox = region.get("bbox", [])
    if not bbox:
        return 0.0
    # bbox may be [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] or [x1,y1,x2,y2]
    if isinstance(bbox[0], (list, tuple)):
        return min(p[1] for p in bbox)
    return float(bbox[1]) if len(bbox) >= 4 else 0.0


def _doc_height(regions: list[dict]) -> float:
    if not regions:
        return 1000.0
    ys = [_get_region_y(r) for r in regions]
    return max(ys) - min(ys) or 1000.0


# ── Receipt-number checks ──────────────────────────────────────────────────

def _check_receipt_number(raw_text: str) -> list[dict]:
    signals = []
    # Look for 18-digit sequence
    matches = _RECEIPT_RE.findall(raw_text)
    if not matches:
        # Not every scan will surface the receipt field clearly;
        # only flag if there are digit clusters that look almost right
        near = re.findall(r"\b\d{15,20}\b", raw_text)
        if near:
            worst = max(near, key=len)
            signals.append(_signal(
                "RECEIPT_NUMBER_WRONG_LENGTH",
                "low",
                "Нетипичная длина номера квитанции",
                f"Номер квитанции Kaspi всегда 18 цифр, найдено {len(worst)} цифр: '{worst}'.",
                worst,
                "medium",
            ))
    return signals


# ── Anchor Y-position checks ───────────────────────────────────────────────

def _check_anchor_positions(regions: list[dict]) -> list[dict]:
    """Check that known Kaspi fields appear near their expected Y positions."""
    if not regions:
        return []

    # Build a flat list of (y, text) from OCR regions
    region_data = []
    for r in regions:
        text = r.get("text", "").strip().lower()
        if not text:
            continue
        y = _get_region_y(r)
        region_data.append((y, text))

    if not region_data:
        return []

    doc_h = _doc_height(regions)
    signals = []
    missed_anchors = []

    for anchor in _KASPI_ANCHORS:
        # Find first matching region
        match_y = None
        for y, text in region_data:
            if any(kw in text for kw in anchor["keywords"]):
                match_y = y
                break

        if match_y is None:
            missed_anchors.append(anchor["label"])
            continue

        # Check Y deviation
        deviation = abs(match_y - anchor["y_ref"])
        tol_abs = anchor["tol"]
        # Scale tolerance by actual document height vs reference height (975 px)
        scale = doc_h / 975.0 if doc_h > 100 else 1.0
        tol_scaled = tol_abs * scale

        if deviation > tol_scaled:
            signals.append(_signal(
                "LAYOUT_FIELD_MISALIGNED",
                "low",
                "Нетипичное расположение поля",
                (
                    f"Поле «{anchor['label']}» находится на y≈{match_y:.0f}, "
                    f"ожидалось ≈{anchor['y_ref']} (±{tol_abs}). "
                    "Возможно визуальное редактирование структуры."
                ),
                anchor["label"],
                "low",
            ))

    # Multiple missing anchors → flag
    if len(missed_anchors) >= 3:
        signals.append(_signal(
            "LAYOUT_ANCHORS_MISSING",
            "medium",
            "Структура чека не совпадает с шаблоном Kaspi",
            (
                f"Не найдены {len(missed_anchors)} ключевых полей "
                f"({', '.join(missed_anchors)}). "
                "Документ может отличаться от оригинального шаблона Kaspi."
            ),
            ", ".join(missed_anchors),
            "medium",
        ))

    return signals


# ── Color / contrast check (optional, needs image path) ───────────────────

def _check_image_colors(image_path: str) -> list[dict]:
    """
    Light CV check: verifies that the image is not grayscale and has typical
    receipt contrast. Kaspi receipts have a white background with black text
    and a single green accent (#1DB954-ish) in the success banner.
    Returns [] if cv2 is unavailable or the image is fine.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return []

    try:
        img = cv2.imread(image_path)
        if img is None:
            return []

        h, w = img.shape[:2]
        # Convert to HSV
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        # 1. Check if image is nearly grayscale (saturation very low)
        mean_sat = float(np.mean(hsv[:, :, 1]))
        if mean_sat < 10:
            return [_signal(
                "IMAGE_GRAYSCALE",
                "low",
                "Изображение похоже на оттенки серого",
                (
                    f"Средняя насыщенность пикселей ({mean_sat:.1f}/255) "
                    "очень низкая — возможна конвертация или пересъёмка скриншота."
                ),
                f"mean_saturation={mean_sat:.1f}",
                "low",
            )]

        # 2. Check overall brightness — receipt background should be light
        mean_val = float(np.mean(hsv[:, :, 2]))
        if mean_val < 80:
            return [_signal(
                "IMAGE_TOO_DARK",
                "low",
                "Изображение слишком тёмное",
                (
                    f"Средняя яркость ({mean_val:.1f}/255) значительно ниже "
                    "ожидаемой для белого фона чека Kaspi."
                ),
                f"mean_brightness={mean_val:.1f}",
                "low",
            )]

    except Exception as e:
        logger.debug("Layout color check failed: %s", e)

    return []


# ── Public API ─────────────────────────────────────────────────────────────

def check_layout(
    ocr_result: dict,
    image_path: Optional[str] = None,
    is_pdf: bool = False,
) -> list[dict]:
    """
    Run all layout/geometry checks. Returns a list of signals (may be empty).

    Args:
        ocr_result: dict from ocr_engine.extract_text() with 'raw_text' and 'regions'.
        image_path: optional path to the source image for pixel-level checks.
        is_pdf: when True, skip the image saturation/color check — PDF-rendered PNGs
                are naturally near-grayscale (black text on white) and would always
                trigger the IMAGE_GRAYSCALE false positive.
    """
    signals: list[dict] = []

    raw_text = ocr_result.get("raw_text", "")
    regions  = ocr_result.get("regions", [])

    signals += _check_receipt_number(raw_text)
    signals += _check_anchor_positions(regions)

    if image_path and not is_pdf:
        signals += _check_image_colors(image_path)

    logger.info("Layout check: %d signal(s)", len(signals))
    return signals
