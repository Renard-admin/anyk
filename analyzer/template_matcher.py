"""
analyzer/template_matcher.py — instant authenticity check via template fingerprinting.

Runtime: ~5-50ms for PDFs, ~30-80ms for images. No external API calls.

Four checks for PDFs:
  1. Metadata:    PDF producer/creator/author strings
  2. Text layer:  genuine Kaspi receipts are 100% image-only (no selectable text)
  3. Geometry:    page dimensions + image block count + anchor block positions
  4. Fingerprints: md5 hashes of 14 static template images vs known-good reference

Two checks for images:
  5. ELA:  Error Level Analysis via Pillow — detects composite editing artifacts
  6. EXIF: checks for editing software in image metadata

Template profile derived from 4 genuine Kaspi Bank transfer receipts
(iOS 26.7.1 Quartz PDFContext, identical structure across all samples).
"""
from __future__ import annotations

import hashlib
import io
import logging
import time

logger = logging.getLogger(__name__)

# ──────────────────────── PDF Template Profile ───────────────────────────────
# Page canvas: 489.8 × 699.7 pts  (1pt = 1/72 inch)
# 19 image blocks total; 14 are static (identical md5 across all genuine receipts)
# 4 are variable (amount, receipt number, date/time, recipient name)

_PAGE_W = 489.8
_PAGE_H = 699.7
_PAGE_TOL = 5.0        # pts tolerance for dimension comparison
_BLOCK_COUNT = 19
_BLOCK_COUNT_TOL = 2   # allow ±2 (app may add/remove a divider in edge cases)

# (pdf_bbox_x0, pdf_bbox_y0, pdf_bbox_x1, pdf_bbox_y1,
#  img_px_w, img_px_h, colorspace_int, md5_8_chars)
# colorspace: 1 = grayscale, 3 = RGB color
_STATIC_BLOCKS: list[tuple] = [
    ( 50.0,  50.0, 442.0, 649.7, 1176, 1799, 1, "e750fa92"),  # 0  full background
    ( 74.0,  74.0, 418.0,  90.0,  990,   48, 1, "99f1d4d4"),  # 1  top scalloped border
    ( 90.0, 114.0, 130.0, 154.0,  216,  216, 3, "d4d959b1"),  # 2  Kaspi logo (red)
    ( 90.0, 194.0, 402.0, 213.0,  936,   57, 3, "bf44a937"),  # 3  "Перевод успешно совершен"
    ( 90.0, 360.0, 240.0, 376.7,  450,   50, 3, "7e79aa85"),  # 5  "Тип перевода" label
    (252.0, 360.0, 402.0, 393.0,  450,   99, 3, "0b301005"),  # 6  "Перевод клиенту Kaspi"
    ( 90.0, 410.0, 240.0, 426.7,  450,   50, 3, "581d17fb"),  # 7  "№ квитанции" label
    ( 90.0, 443.7, 240.0, 476.7,  450,   99, 3, "f6eec6c3"),  # 9  "Дата и время" label
    ( 90.0, 493.7, 240.0, 510.3,  450,   50, 3, "27eef626"),  # 11 "Комиссия" label
    (252.0, 493.7, 402.0, 510.3,  450,   50, 3, "4c112e07"),  # 12 "0 ₸" commission
    ( 90.0, 527.3, 240.0, 544.0,  450,   50, 3, "39c55d2a"),  # 13 "Отправитель" label
    ( 90.0, 561.0, 240.0, 577.7,  450,   50, 3, "52cf8657"),  # 15 "Откуда" label
    (252.0, 561.0, 402.0, 577.7,  450,   50, 3, "7ff69ad1"),  # 16 "Kaspi Gold" value
    ( 74.0, 609.7, 418.0, 625.7,  990,   48, 1, "dd06d1d1"),  # 17 bottom border
]
_STATIC_COUNT = len(_STATIC_BLOCKS)  # 14

# Anchor blocks that must appear in exact positions
_ANCHORS = [
    (( 50.0,  50.0, 442.0, 649.7), "background"),
    (( 74.0,  74.0, 418.0,  90.0), "top_border"),
    (( 90.0, 114.0, 130.0, 154.0), "kaspi_logo"),
    (( 90.0, 194.0, 402.0, 213.0), "success_header"),
]
_ANCHOR_TOL = 3.0  # pts

# Metadata keywords
_SAFE_PRODUCERS   = ("quartz", "ios ", "apple")
_RISKY_PRODUCERS  = (
    "photoshop", "canva", "figma", "reportlab", "itext", "fpdf", "tcpdf",
    "wkhtmltopdf", "ghostscript", "inkscape", "libreoffice", "scribus",
    "corel", "publisher", "nitro", "foxit", "pdfsharp", "wps",
)
_KASPI_AUTHOR_KW  = "kaspi"

# EXIF editing software keywords
_EXIF_EDIT_KW = (
    "photoshop", "gimp", "paint.net", "lightroom",
    "canva", "figma", "affinity", "pixelmator",
)

_SEV_DELTA = {"high": 25, "medium": 12, "low": 4}

# Image files get a base trust penalty — screenshots can be easily edited
_IMAGE_PENALTY = 20

# Kaspi color signature for screenshot recognition (Pillow-only, no OCR)
# Kaspi brand green:  #00A550 = RGB(0, 165, 80)  — success checkmark / header
# Kaspi light green:  #E6F7ED = RGB(230, 247, 237) — success card background
# Kaspi logo red:     #E31837 = RGB(227, 24, 55)   — Kaspi logo (additional signal)
_KASPI_GREEN_R_MAX  = 60    # brand green R channel max
_KASPI_GREEN_G_MIN  = 100   # brand green G channel min
_KASPI_GREEN_B_MIN  = 30    # brand green B channel min
_KASPI_GREEN_B_MAX  = 140   # brand green B channel max
# Light-green card background: R>200, G>230, B>210, not-white (not all >240)
_KASPI_LTGREEN_R_MIN = 200
_KASPI_LTGREEN_G_MIN = 225
_KASPI_LTGREEN_B_MIN = 210
_KASPI_LTGREEN_B_MAX = 248   # below pure white (255)
# Kaspi red logo: R>180, G<70, B<80
_KASPI_RED_R_MIN = 180
_KASPI_RED_G_MAX = 70
_KASPI_RED_B_MAX = 80
# Minimum ratios for positive Kaspi identification
_KASPI_GREEN_RATIO_MIN   = 0.008  # ≥0.8% brand-green pixels (checkmark can be small)
_KASPI_LTGREEN_RATIO_MIN = 0.015  # ≥1.5% light-green bg pixels
_KASPI_WHITE_RATIO_MIN   = 0.20   # ≥20% white/near-white (receipt paper)
# Valid portrait aspect ratio for phone screenshots/receipt images
_KASPI_ASPECT_MIN = 1.2
_KASPI_ASPECT_MAX = 4.5


# ─────────────────────────── Signal factory ──────────────────────────────────

def _sig(
    signal_id: str, severity: str, title_ru: str,
    description_ru: str, evidence_text: str,
    confidence: str, method: str,
) -> dict:
    return {
        "signal_id": signal_id,
        "category": "template",
        "severity": severity,
        "title_ru": title_ru,
        "description_ru": description_ru,
        "evidence_text": evidence_text,
        "confidence": confidence,
        "method": method,
    }


# ───────────────────────── PDF Check 1: Metadata ─────────────────────────────

def _check_metadata(meta: dict) -> tuple[list[dict], int]:
    signals: list[dict] = []
    bonus = 0

    producer = (meta.get("producer") or "").lower()
    creator  = (meta.get("creator")  or "").lower()
    author   = (meta.get("author")   or "").lower()

    if any(kw in producer for kw in _SAFE_PRODUCERS):
        bonus += 20

    if _KASPI_AUTHOR_KW in author:
        bonus += 15

    combined = producer + " " + creator
    for risky in _RISKY_PRODUCERS:
        if risky in combined:
            signals.append(_sig(
                "METADATA_RISKY_PRODUCER", "high",
                "Подозрительный генератор PDF",
                (
                    f"PDF создан в редакторе «{risky}» — это типичный инструмент "
                    "подделки, а не официальное приложение Kaspi Bank."
                ),
                f"producer={meta.get('producer')!r}",
                "high", "metadata_check",
            ))
            break

    if not producer and not author:
        signals.append(_sig(
            "METADATA_EMPTY", "medium",
            "Отсутствуют метаданные PDF",
            "Подлинные чеки Kaspi содержат поля Producer и Author. Оба поля пусты.",
            "producer='', author=''",
            "medium", "metadata_check",
        ))

    return signals, bonus


# ─────────────────── PDF Check 2: Text layer ─────────────────────────────────

def _check_text_layer(has_text_layer: bool, text_layers: list[str]) -> tuple[list[dict], int]:
    signals: list[dict] = []
    bonus = 0

    if not has_text_layer:
        bonus += 10  # consistent with genuine iOS Quartz export
        return signals, bonus

    combined = " ".join(text_layers).strip()
    word_count = len(combined.split()) if combined else 0

    if word_count > 20:
        signals.append(_sig(
            "TEXT_LAYER_PRESENT", "high",
            "Обнаружен редактируемый текстовый слой",
            (
                "Подлинные чеки Kaspi (iOS) содержат только растровые изображения "
                "и не имеют выделяемого текста. Наличие текстового слоя характерно "
                "для PDF, созданных программно (возможная подделка)."
            ),
            f"word_count={word_count}, text_preview={combined[:80]!r}",
            "high", "text_layer_check",
        ))
    elif word_count > 0:
        signals.append(_sig(
            "TEXT_LAYER_SPARSE", "low",
            "Неожиданный текстовый слой",
            "Обнаружен небольшой текстовый слой. Подлинные чеки Kaspi полностью растровые.",
            f"word_count={word_count}",
            "low", "text_layer_check",
        ))

    return signals, bonus


# ─────────────────── PDF Check 3: Geometry ───────────────────────────────────

def _check_geometry(page_w: float, page_h: float, all_blocks: list) -> tuple[list[dict], int]:
    signals: list[dict] = []
    bonus = 0

    if abs(page_w - _PAGE_W) <= _PAGE_TOL and abs(page_h - _PAGE_H) <= _PAGE_TOL:
        bonus += 15
    else:
        signals.append(_sig(
            "GEOMETRY_PAGE_SIZE", "high",
            "Неверный размер страницы PDF",
            (
                f"Эталонный размер страницы чека Kaspi: {_PAGE_W}×{_PAGE_H} пт. "
                f"Документ имеет: {page_w:.1f}×{page_h:.1f} пт."
            ),
            f"w={page_w:.1f} h={page_h:.1f} expected={_PAGE_W}×{_PAGE_H}",
            "high", "geometry_check",
        ))

    img_blocks = [b for b in all_blocks if b.get("type") == 1]
    n = len(img_blocks)
    if abs(n - _BLOCK_COUNT) <= _BLOCK_COUNT_TOL:
        bonus += 10
    else:
        signals.append(_sig(
            "GEOMETRY_BLOCK_COUNT", "medium",
            "Неверное количество блоков изображений",
            f"Подлинный чек Kaspi содержит {_BLOCK_COUNT} блоков. Найдено: {n}.",
            f"found={n} expected={_BLOCK_COUNT}",
            "medium", "geometry_check",
        ))

    for i, (expected_bbox, name) in enumerate(_ANCHORS):
        if i >= len(img_blocks):
            break
        actual = img_blocks[i].get("bbox", [0, 0, 0, 0])
        if all(abs(actual[j] - expected_bbox[j]) <= _ANCHOR_TOL for j in range(4)):
            bonus += 5
        else:
            signals.append(_sig(
                f"GEOMETRY_ANCHOR_{name.upper()}", "medium",
                f"Смещение опорного блока «{name}»",
                (
                    f"Блок «{name}» смещён относительно эталонной сетки. "
                    f"Ожидаемые координаты: {list(expected_bbox)}, "
                    f"фактические: {[round(x, 1) for x in actual]}."
                ),
                f"anchor={name}",
                "medium", "geometry_check",
            ))

    return signals, bonus


# ──────────────── PDF Check 4: Image fingerprints ────────────────────────────

def _check_fingerprints(doc) -> tuple[list[dict], int]:
    signals: list[dict] = []
    bonus = 0

    try:
        page = doc[0]
        img_list = page.get_images(full=True)

        # Build set of (px_w, px_h, colorspace, md5_8) for all extracted images
        actual: set[tuple] = set()
        for img_ref in img_list:
            xref = img_ref[0]
            try:
                base = doc.extract_image(xref)
                md5_8 = hashlib.md5(base["image"]).hexdigest()[:8]
                actual.add((base["width"], base["height"], base["colorspace"], md5_8))
            except Exception:
                pass

        matches = 0
        missing = []
        for (_, _, _, _, pw, ph, cs, expected_md5) in _STATIC_BLOCKS:
            candidates = {e for e in actual if e[0] == pw and e[1] == ph and e[2] == cs}
            if not candidates:
                missing.append(f"{pw}×{ph}")
                continue
            if any(c[3] == expected_md5 for c in candidates):
                matches += 1
            else:
                missing.append(f"{pw}×{ph}(hash_mismatch)")

        ratio = matches / _STATIC_COUNT

        if ratio >= 0.9:      # ≥13/14
            bonus += 30
        elif ratio >= 0.6:    # ≥9/14
            bonus += 15
            signals.append(_sig(
                "FINGERPRINT_PARTIAL_MATCH", "low",
                "Частичное совпадение шаблона Kaspi",
                f"Совпало {matches} из {_STATIC_COUNT} статических блоков-изображений.",
                f"matches={matches}/{_STATIC_COUNT}",
                "low", "fingerprint_check",
            ))
        else:
            signals.append(_sig(
                "FINGERPRINT_MISMATCH", "high",
                "Шаблон Kaspi не распознан",
                (
                    f"Совпало только {matches} из {_STATIC_COUNT} контрольных изображений. "
                    "Документ не является подлинным чеком Kaspi Bank."
                ),
                f"matches={matches}/{_STATIC_COUNT} missing={missing[:6]}",
                "high", "fingerprint_check",
            ))

    except Exception as e:
        logger.warning("Fingerprint check failed: %s", e)

    return signals, bonus


# ──────────── Image Check 7: Kaspi color signature ───────────────────────────

def _check_kaspi_screenshot_colors(image_path: str) -> tuple[bool, str]:
    """
    Detect Kaspi receipt screenshots via multi-signal color analysis.

    Returns (is_kaspi: bool, confidence_label: str).
    Uses only Pillow — no OCR, no extra deps.

    Signals checked:
      1. Portrait aspect ratio (phone screenshot proportions)
      2. Kaspi brand green  #00A550 — success checkmark / progress header
      3. Kaspi light green  #E6F7ED — success card background block
      4. Kaspi logo red     #E31837 — Kaspi diamond logo (bonus signal)
      5. White/near-white background (receipt paper color)

    Positive identification requires white bg + (brand green OR light green).
    """
    try:
        from PIL import Image

        img = Image.open(image_path).convert("RGB")
        w, h = img.size
        if w == 0:
            return False, ""

        aspect = h / w
        if not (_KASPI_ASPECT_MIN <= aspect <= _KASPI_ASPECT_MAX):
            return False, ""

        # Downsample to ~120px wide for fast pixel scan (keeps aspect ratio)
        scale = min(1.0, 120.0 / w)
        nw = max(1, int(w * scale))
        nh = max(1, int(h * scale))
        small = img.resize((nw, nh), Image.LANCZOS)
        pixels = list(small.getdata())
        total = len(pixels)
        if total == 0:
            return False, ""

        # Kaspi brand green: #00A550 — R<60, G≥100, B in [30..140]
        green_count = sum(
            1 for r, g, b in pixels
            if r < _KASPI_GREEN_R_MAX
            and g >= _KASPI_GREEN_G_MIN
            and _KASPI_GREEN_B_MIN <= b <= _KASPI_GREEN_B_MAX
        )
        # Kaspi light green: #E6F7ED — R≥200, G≥225, B in [210..248]
        ltgreen_count = sum(
            1 for r, g, b in pixels
            if r >= _KASPI_LTGREEN_R_MIN
            and g >= _KASPI_LTGREEN_G_MIN
            and _KASPI_LTGREEN_B_MIN <= b <= _KASPI_LTGREEN_B_MAX
        )
        # Kaspi red logo: #E31837 — R≥180, G<70, B<80
        red_count = sum(
            1 for r, g, b in pixels
            if r >= _KASPI_RED_R_MIN
            and g < _KASPI_RED_G_MAX
            and b < _KASPI_RED_B_MAX
        )
        # White/near-white background: R>215, G>215, B>215
        white_count = sum(1 for r, g, b in pixels if r > 215 and g > 215 and b > 215)

        green_ratio   = green_count / total
        ltgreen_ratio = ltgreen_count / total
        red_ratio     = red_count / total
        white_ratio   = white_count / total

        has_green   = green_ratio   >= _KASPI_GREEN_RATIO_MIN
        has_ltgreen = ltgreen_ratio >= _KASPI_LTGREEN_RATIO_MIN
        has_white   = white_ratio   >= _KASPI_WHITE_RATIO_MIN
        has_red     = red_ratio     >= 0.003  # small logo, just need presence

        # Core rule: white bg + (brand green OR light green)
        is_kaspi = has_white and (has_green or has_ltgreen)

        if is_kaspi:
            signals_found = sum([has_green, has_ltgreen, has_red])
            if signals_found >= 3 or (has_green and green_ratio >= 0.04):
                confidence = "высокая уверенность"
            elif signals_found >= 2:
                confidence = "средняя уверенность"
            else:
                confidence = "низкая уверенность"
        else:
            confidence = ""

        logger.debug(
            "Kaspi color: aspect=%.2f green=%.3f%% ltgreen=%.3f%% red=%.3f%% "
            "white=%.3f%% → is_kaspi=%s conf=%r",
            aspect, green_ratio * 100, ltgreen_ratio * 100,
            red_ratio * 100, white_ratio * 100, is_kaspi, confidence,
        )
        return is_kaspi, confidence

    except Exception as e:
        logger.debug("Kaspi color check skipped: %s", e)
        return False, ""


# ─────────────────── Image Check 5: ELA ──────────────────────────────────────

def _check_ela(image_path: str) -> tuple[list[dict], int]:
    signals: list[dict] = []
    bonus = 0

    try:
        from PIL import Image, ImageChops

        img = Image.open(image_path).convert("RGB")

        # Resave at 90% JPEG quality and measure error
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        buf.seek(0)
        compressed = Image.open(buf).convert("RGB")

        diff = ImageChops.difference(img, compressed)
        diff_pixels = list(diff.getdata())

        if not diff_pixels:
            return signals, bonus

        total = len(diff_pixels)
        channel_max = [max(p[c] for p in diff_pixels) for c in range(3)]
        channel_mean = [sum(p[c] for p in diff_pixels) / total for c in range(3)]

        max_diff = max(channel_max)
        mean_diff = max(max(channel_mean), 0.001)
        ratio = max_diff / mean_diff

        if ratio > 25 and max_diff > 80:
            signals.append(_sig(
                "ELA_HIGH_ANOMALY", "medium",
                "Аномалия уровня сжатия (ELA)",
                (
                    f"Анализ уровня ошибок выявил значительные локальные аномалии "
                    f"(max={max_diff}, mean={mean_diff:.1f}, ratio={ratio:.1f}). "
                    "Признак вставки постороннего изображения."
                ),
                f"ela_max={max_diff} ela_mean={mean_diff:.1f} ratio={ratio:.1f}",
                "medium", "ela_check",
            ))
        elif ratio > 15 and max_diff > 50:
            signals.append(_sig(
                "ELA_MILD_ANOMALY", "low",
                "Незначительная аномалия ELA",
                f"Умеренное несоответствие уровней сжатия (ratio={ratio:.1f}).",
                f"ela_max={max_diff} ela_mean={mean_diff:.1f}",
                "low", "ela_check",
            ))
        else:
            bonus += 10  # consistent with single-generation image

    except Exception as e:
        logger.debug("ELA check skipped: %s", e)

    return signals, bonus


# ─────────────────── Image Check 6: EXIF ─────────────────────────────────────

def _check_exif(image_path: str) -> tuple[list[dict], int]:
    signals: list[dict] = []
    bonus = 0

    try:
        from PIL import Image

        img = Image.open(image_path)
        exif_data = img._getexif() if hasattr(img, "_getexif") else None
        info = img.info

        software = ""
        if exif_data and 305 in exif_data:
            software = (exif_data[305] or "").lower()
        if not software:
            software = str(info.get("Software") or info.get("software") or "").lower()

        if software:
            for kw in _EXIF_EDIT_KW:
                if kw in software:
                    signals.append(_sig(
                        "EXIF_EDITING_SOFTWARE", "high",
                        "Признаки редактирования в метаданных",
                        f"EXIF Software содержит «{software}» — графический редактор.",
                        f"Software={software!r}",
                        "high", "exif_check",
                    ))
                    break
            else:
                bonus += 5

    except Exception as e:
        logger.debug("EXIF check skipped: %s", e)

    return signals, bonus


# ─────────────────────── Trust score ─────────────────────────────────────────

def _compute_trust(bonus: int, signals: list[dict]) -> tuple[int, str, str]:
    """Trust scoring for PDF documents (full template check available)."""
    deduction = sum(
        _SEV_DELTA.get(s.get("severity", "low"), 0)
        for s in signals
    )
    trust_score = max(0, min(100, bonus - deduction))

    if trust_score >= 80:
        label, label_en = "Подлинный", "authentic"
    elif trust_score >= 50:
        label, label_en = "Требует проверки", "review_needed"
    elif trust_score >= 25:
        label, label_en = "Подозрительный", "suspicious"
    else:
        label, label_en = "Вероятная подделка", "likely_fake"

    return trust_score, label, label_en


def _compute_trust_image(
    signals: list[dict],
    is_kaspi_screenshot: bool = False,
) -> tuple[int, str, str]:
    """Trust scoring for image files.

    When is_kaspi_screenshot=True (Kaspi green color signature detected),
    starts at 87% and deducts for editing signals.

    Without Kaspi color signature we only report editing signals; a clean
    unrecognized image returns 'not_applicable' rather than 'likely_fake'.
    """
    high_sigs   = [s for s in signals if s.get("severity") == "high"]
    medium_sigs = [s for s in signals if s.get("severity") == "medium"]

    if is_kaspi_screenshot:
        # Recognized as Kaspi receipt screenshot — start at 87%
        deduction = sum(_SEV_DELTA.get(s.get("severity", "low"), 0) for s in signals)
        score = max(5, 87 - deduction)
        if score >= 80:
            return score, "Подлинный чек (Скриншот)", "authentic"
        elif score >= 50:
            return score, "Требует проверки", "review_needed"
        elif score >= 25:
            return score, "Подозрительный", "suspicious"
        else:
            return score, "Вероятная подделка", "likely_fake"

    # Unrecognized image — only report when editing signals found
    if high_sigs:
        deduction = sum(_SEV_DELTA.get(s.get("severity", "low"), 0) for s in signals)
        score = max(0, 30 - deduction)
        return score, "Вероятная подделка", "likely_fake"

    if medium_sigs:
        return 20, "Подозрительный", "suspicious"

    if signals:
        return 40, "Требует проверки", "review_needed"

    # No editing signals and Kaspi not recognized — cannot assess
    return 0, "Не применимо", "not_applicable"


# ─────────────────────────── Public API ──────────────────────────────────────

def match_template(
    file_path: str,
    detected_type: str,
    pdf_info: dict | None = None,
) -> dict:
    """
    Run all applicable checks and return a unified analysis result.

    Returns:
        {
          "signals":        list[dict],   # signal objects (same schema as before)
          "trust_score":    int,          # 0–100
          "trust_label":    str,          # Russian label
          "trust_label_en": str,          # "authentic" | "review_needed" | "suspicious" | "likely_fake"
          "trust_explanation": str,
          "trust_disclaimer": str,
          "checks_run":     list[str],
          "elapsed_ms":     float,
        }
    """
    t0 = time.time()
    signals: list[dict] = []
    bonus = 0
    checks_run: list[str] = []
    breakdown: list[dict] = []
    image_penalty = 0
    is_kaspi_screenshot = False  # set to True when Kaspi color signature confirmed on images

    def _add_sigs_to_breakdown(sigs: list[dict]) -> None:
        for s in sigs:
            d = _SEV_DELTA.get(s.get("severity", "low"), 0)
            if d:
                breakdown.append({"sign": "-", "delta": f"-{d}%", "reason": s["title_ru"]})

    if detected_type == "pdf" and pdf_info:
        try:
            import pymupdf
            doc = pymupdf.open(file_path)
        except Exception as e:
            logger.warning("Cannot open PDF for template check: %s", e)
            doc = None

        meta = pdf_info.get("metadata", {})

        # Check 1 — metadata
        sigs, b = _check_metadata(meta)
        signals.extend(sigs); bonus += b; checks_run.append("metadata")
        if b > 0:
            breakdown.append({"sign": "+", "delta": f"+{b}%", "reason": "Метаданные PDF: производитель подлинный (iOS / Kaspi)"})
        _add_sigs_to_breakdown(sigs)

        # Check 2 — text layer
        sigs, b = _check_text_layer(
            pdf_info.get("has_text_layer", False),
            pdf_info.get("text_layers", []),
        )
        signals.extend(sigs); bonus += b; checks_run.append("text_layer")
        if b > 0:
            breakdown.append({"sign": "+", "delta": f"+{b}%", "reason": "Текстового слоя нет — image-only PDF (характерно для Kaspi)"})
        _add_sigs_to_breakdown(sigs)

        if doc is not None:
            try:
                page = doc[0]
                pw, ph = page.rect.width, page.rect.height
                blocks = page.get_text("rawdict")["blocks"]

                # Check 3 — geometry
                sigs, b = _check_geometry(pw, ph, blocks)
                signals.extend(sigs); bonus += b; checks_run.append("geometry")
                if b > 0:
                    breakdown.append({"sign": "+", "delta": f"+{b}%", "reason": "Геометрия страницы соответствует шаблону Kaspi"})
                _add_sigs_to_breakdown(sigs)

                # Check 4 — fingerprints
                sigs, b = _check_fingerprints(doc)
                signals.extend(sigs); bonus += b; checks_run.append("fingerprints")
                if b > 0:
                    breakdown.append({"sign": "+", "delta": f"+{b}%", "reason": "Хэши изображений совпадают с эталоном Kaspi"})
                _add_sigs_to_breakdown(sigs)
            finally:
                doc.close()

    elif detected_type == "image":
        # Check 7 — Kaspi color signature (runs first, affects all downstream scoring)
        is_kaspi_screenshot, kaspi_confidence = _check_kaspi_screenshot_colors(file_path)
        checks_run.append("kaspi_color_analysis")

        if is_kaspi_screenshot:
            breakdown.append({
                "sign": "+",
                "delta": "+87%",
                "reason": f"Распознан как скриншот чека Kaspi (цветовой анализ, {kaspi_confidence})",
            })
            image_penalty = 0  # scoring done directly from 87% base in _compute_trust_image
        else:
            # Unrecognized screenshot — apply base trust penalty
            image_penalty = _IMAGE_PENALTY
            breakdown.append({
                "sign": "-",
                "delta": f"-{image_penalty}%",
                "reason": "Тип файла: Скриншот/Изображение (коэффициент снижения доверия -20%)",
            })

        # Check 5 — ELA
        sigs, b = _check_ela(file_path)
        signals.extend(sigs); bonus += b; checks_run.append("ela")
        if b > 0:
            breakdown.append({"sign": "+", "delta": f"+{b}%", "reason": "ELA: уровни сжатия изображения в норме"})
        _add_sigs_to_breakdown(sigs)

        # Check 6 — EXIF
        sigs, b = _check_exif(file_path)
        signals.extend(sigs); bonus += b; checks_run.append("exif")
        if b > 0:
            breakdown.append({"sign": "+", "delta": f"+{b}%", "reason": "EXIF: следов редактирования не обнаружено"})
        _add_sigs_to_breakdown(sigs)

    # --- Trust computation ---
    is_kaspi_receipt = detected_type == "pdf"

    if detected_type == "image":
        trust_score, trust_label, trust_label_en = _compute_trust_image(signals, is_kaspi_screenshot)
        is_kaspi_receipt = is_kaspi_screenshot  # True when Kaspi colors confirmed
        if trust_label_en == "not_applicable":
            breakdown.append({
                "sign": "—",
                "delta": "Н/Д",
                "reason": "Цвета Kaspi не обнаружены — загрузите PDF для точного анализа",
            })
    else:
        trust_score, trust_label, trust_label_en = _compute_trust(bonus - image_penalty, signals)

    elapsed_ms = (time.time() - t0) * 1000
    logger.info(
        "Template match: type=%s trust=%d%% label=%s signals=%d checks=%s elapsed=%.1fms",
        detected_type, trust_score, trust_label_en, len(signals), checks_run, elapsed_ms,
    )

    _DISCLAIMER = (
        "Система выявляет признаки несоответствия в структуре документа. "
        "Она не устанавливает факт транзакции и не является судебным "
        "экспертным инструментом. Окончательное решение принимает аналитик."
    )

    return {
        "signals": signals,
        "trust_score": trust_score,
        "trust_label": trust_label,
        "trust_label_en": trust_label_en,
        "trust_explanation": breakdown,
        "trust_disclaimer": _DISCLAIMER,
        "checks_run": checks_run,
        "elapsed_ms": round(elapsed_ms, 1),
        "is_kaspi_receipt": is_kaspi_receipt,
    }
