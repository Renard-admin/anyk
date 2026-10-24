"""
Tests for document_gatekeeper, trust_scorer, and Chrome-PDF support.

New algorithm (base = 0, additive):
  +30  Kaspi markers (≥3 marker groups)
  +20  Valid 18-digit receipt number
  +30  Layout anchor geometry (≥3 anchors)
  +20  Date / time format match

Anomaly deductions:
  −15 HIGH  −10 MEDIUM  −3 LOW
  −10 OCR conf<0.60  −5 OCR conf 0.60-0.74  −10 no text
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.trust_scorer import calculate_trust
from analyzer.document_gatekeeper import check_document

SAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "samples",
)

# ── Helpers ────────────────────────────────────────────────────────────────

_KASPI_TEXT = (
    "Перевод успешно совершен\n"
    "№ квитанции 898936951310176343\n"
    "Дата и время 15.04.2024 14:30\n"
    "Отправитель Иванов И.И.\n"
    "Kaspi Gold"
)

_KASPI_REGIONS = [
    {"text": "Перевод успешно совершен", "bbox": [[10, 486], [300, 486], [300, 510], [10, 510]]},
    {"text": "№ квитанции", "bbox": [[10, 1027], [200, 1027], [200, 1050], [10, 1050]]},
    {"text": "Дата и время", "bbox": [[10, 1113], [200, 1113], [200, 1136], [10, 1136]]},
    {"text": "Комиссия 0 тг", "bbox": [[10, 1236], [200, 1236], [200, 1260], [10, 1260]]},
    {"text": "Отправитель", "bbox": [[10, 1319], [200, 1319], [200, 1343], [10, 1343]]},
]


def _ocr_kaspi(conf=0.88):
    """Full Kaspi receipt OCR — all markers + date + receipt number."""
    return {
        "raw_text": _KASPI_TEXT,
        "regions": _KASPI_REGIONS,
        "mean_confidence": conf,
        "word_count": 20,
    }


def _ocr_plain(text="Оплата 5000 тг", conf=0.82):
    """Generic non-Kaspi OCR result."""
    return {"raw_text": text, "regions": [], "mean_confidence": conf,
            "word_count": len(text.split())}


def _ocr_partial_kaspi(conf=0.82):
    """Kaspi text but only 2 marker groups — fails gatekeeper."""
    text = "Перевод успешно совершен\nДата и время 15.04.2024 14:30"
    return {"raw_text": text, "regions": [], "mean_confidence": conf,
            "word_count": 8}


def _sig(severity="high"):
    return {"signal_id": "TEST_SIG", "severity": severity,
            "confidence": "medium", "method": "test", "evidence_text": "x"}


def _real_pdf_paths():
    if not os.path.isdir(SAMPLES_DIR):
        return []
    return sorted([
        os.path.join(SAMPLES_DIR, f)
        for f in os.listdir(SAMPLES_DIR)
        if f.lower().endswith(".pdf")
    ])


# ── Document Gatekeeper tests ──────────────────────────────────────────────

def test_gatekeeper_passes_full_kaspi():
    result = check_document(_ocr_kaspi())
    assert result["passed"] is True
    assert result["markers_found"] >= 3


def test_gatekeeper_fails_random_image():
    result = check_document(_ocr_plain("random noise abc 123"))
    assert result["passed"] is False
    assert result["markers_found"] < 3


def test_gatekeeper_fails_partial_markers():
    result = check_document(_ocr_partial_kaspi())
    assert result["passed"] is False


def test_gatekeeper_threshold_exactly_3():
    """Exactly 3 groups → passes."""
    text = "Перевод успешно совершен Дата и время Отправитель"
    result = check_document({"raw_text": text, "regions": []})
    assert result["passed"] is True
    assert result["markers_found"] >= 3


# ── Non-receipt image → 0% ─────────────────────────────────────────────────

def test_non_receipt_image():
    """Random image / noise with no Kaspi markers → Trust Index = 0%."""
    random_ocr = _ocr_plain("абвгд xyz 12345 random noise foo bar")
    gate = check_document(random_ocr)
    result = calculate_trust([], random_ocr, gatekeeper_result=gate)
    assert result["trust_score"] == 0, (
        f"Non-receipt image must score 0%, got {result['trust_score']}%"
    )
    assert result["trust_label_en"] == "invalid"


def test_non_receipt_image_empty_text():
    """Empty OCR text → Trust = 0% (no markers found)."""
    empty_ocr = {"raw_text": "", "regions": [], "mean_confidence": 0.0}
    gate = check_document(empty_ocr)
    result = calculate_trust([], empty_ocr, gatekeeper_result=gate)
    assert result["trust_score"] == 0


# ── Additive bonuses ───────────────────────────────────────────────────────

def test_trust_markers_bonus():
    """Full Kaspi markers → +30 in score."""
    ocr = _ocr_kaspi(conf=0.88)
    # Strip receipt number and date to isolate marker bonus only
    ocr["raw_text"] = (
        "Перевод успешно совершен\n"
        "№ квитанции abc\n"
        "Отправитель Ivan\n"
        "Kaspi Gold\n"
        "Дата х"
    )
    ocr["regions"] = []
    gate = check_document(ocr)
    result = calculate_trust([], ocr, gatekeeper_result=gate)
    bonus_items = [i for i in result["trust_explanation"] if i["sign"] == "+"]
    marker_bonus = next((i for i in bonus_items if "Маркеры" in i["reason"]), None)
    assert marker_bonus is not None, "Expected marker bonus in explanation"
    assert marker_bonus["delta"] == 30


def test_trust_receipt_number_bonus():
    """18-digit receipt number adds +20."""
    text_no  = "Перевод успешно совершен Отправитель Kaspi Gold № квитанции Дата и время"
    text_yes = text_no + " 898936951310176343"
    no  = calculate_trust([], {"raw_text": text_no,  "regions": [], "mean_confidence": 0.88})
    yes = calculate_trust([], {"raw_text": text_yes, "regions": [], "mean_confidence": 0.88})
    assert yes["trust_score"] - no["trust_score"] == 20


def test_trust_layout_bonus():
    """Layout anchor match adds +30."""
    ocr_no_regions  = {**_ocr_kaspi(), "regions": []}
    ocr_with_regions = _ocr_kaspi()
    r_no  = calculate_trust([], ocr_no_regions)
    r_yes = calculate_trust([], ocr_with_regions)
    assert r_yes["trust_score"] - r_no["trust_score"] == 30


def test_trust_datetime_bonus():
    """Date match adds +20."""
    text_no  = "Перевод успешно совершен Отправитель Kaspi Gold № квитанции"
    text_yes = text_no + " 15.04.2024 14:30"
    no  = calculate_trust([], {"raw_text": text_no,  "regions": [], "mean_confidence": 0.88})
    yes = calculate_trust([], {"raw_text": text_yes, "regions": [], "mean_confidence": 0.88})
    assert yes["trust_score"] - no["trust_score"] == 20


# ── Deductions ─────────────────────────────────────────────────────────────

def test_trust_deducts_high_signal():
    """Each HIGH signal deducts 15."""
    ocr = _ocr_kaspi()
    r0 = calculate_trust([],            ocr)
    r1 = calculate_trust([_sig("high")], ocr)
    assert r0["trust_score"] - r1["trust_score"] == 15


def test_trust_deducts_medium_signal():
    """Each MEDIUM signal deducts 10."""
    ocr = _ocr_kaspi()
    r0 = calculate_trust([],              ocr)
    r1 = calculate_trust([_sig("medium")], ocr)
    assert r0["trust_score"] - r1["trust_score"] == 10


def test_trust_deducts_low_signal():
    """Each LOW signal deducts 3."""
    ocr = _ocr_kaspi()
    r0 = calculate_trust([],           ocr)
    r1 = calculate_trust([_sig("low")], ocr)
    assert r0["trust_score"] - r1["trust_score"] == 3


def test_trust_poor_ocr_deduction():
    """OCR conf < 0.60 deducts 10."""
    ocr_good = {**_ocr_kaspi(), "mean_confidence": 0.88}
    ocr_poor = {**_ocr_kaspi(), "mean_confidence": 0.50}
    good = calculate_trust([], ocr_good)
    poor = calculate_trust([], ocr_poor)
    assert good["trust_score"] - poor["trust_score"] == 10


def test_trust_moderate_ocr_deduction():
    """OCR conf 0.60-0.74 deducts 5."""
    ocr_good = {**_ocr_kaspi(), "mean_confidence": 0.88}
    ocr_mod  = {**_ocr_kaspi(), "mean_confidence": 0.65}
    good = calculate_trust([], ocr_good)
    mod  = calculate_trust([], ocr_mod)
    assert good["trust_score"] - mod["trust_score"] == 5


# ── Clamping ───────────────────────────────────────────────────────────────

def test_trust_score_clamped_to_100():
    """Score never exceeds 100."""
    result = calculate_trust([], _ocr_kaspi(conf=0.99))
    assert result["trust_score"] <= 100


def test_trust_score_clamped_to_0():
    """Score never goes below 0."""
    ocr = _ocr_kaspi(conf=0.40)
    signals = [_sig("high") for _ in range(10)]
    result = calculate_trust(signals, ocr)
    assert result["trust_score"] >= 0


# ── Explanation format ─────────────────────────────────────────────────────

def test_trust_explanation_has_bonus_items():
    """Breakdown must contain at least one '+' item for full Kaspi receipt."""
    result = calculate_trust([], _ocr_kaspi())
    signs = {item["sign"] for item in result["trust_explanation"]}
    assert "+" in signs, "Expected bonus items in breakdown"


def test_trust_explanation_deduction_when_signals():
    """With a HIGH signal, breakdown must include a '−' deduction item."""
    result = calculate_trust([_sig("high")], _ocr_kaspi())
    signs = {item["sign"] for item in result["trust_explanation"]}
    assert "−" in signs, "Expected deduction in breakdown"


# ── Real Kaspi PDF integration tests ─────────────────────────────────────

@pytest.mark.skipif(not _real_pdf_paths(), reason="No PDF samples found")
def test_original_kaspi_receipts():
    """All real Kaspi PDF samples must score 90–100%."""
    from analyzer.pdf_ingestor import ingest_pdf
    from analyzer.preprocessor import preprocess
    from analyzer.ocr_engine import extract_text
    from analyzer.document_gatekeeper import check_document

    pdf_paths = _real_pdf_paths()
    assert pdf_paths, "Expected at least one PDF in data/samples/"

    for pdf_path in pdf_paths:
        pdf_info = ingest_pdf(pdf_path)
        img_paths = pdf_info["page_image_paths"]
        pre = None
        try:
            pre = preprocess(img_paths[0])
            ocr_result = extract_text(pre)
            gate = check_document(ocr_result)
            result = calculate_trust([], ocr_result, pdf_info=pdf_info,
                                     gatekeeper_result=gate)
            assert result["trust_score"] >= 90, (
                f"{os.path.basename(pdf_path)}: expected ≥90%, "
                f"got {result['trust_score']}%  "
                f"gate={gate}  breakdown={result['trust_explanation']}"
            )
        finally:
            cleanup = img_paths[:]
            if pre and pre not in cleanup:
                cleanup.append(pre)
            for p in cleanup:
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass


# ── Chrome PDF / Skia detection (unchanged) ───────────────────────────────

def test_ingest_pdf_engine_field():
    real_pdfs = _real_pdf_paths()
    if real_pdfs:
        from analyzer.pdf_ingestor import ingest_pdf
        result = ingest_pdf(real_pdfs[0])
        assert "pdf_engine" in result["metadata"]
        assert result["metadata"]["pdf_engine"] in ("ios_quartz", "chrome", "unknown")
        for p in result["page_image_paths"]:
            if os.path.exists(p):
                os.unlink(p)


def test_chrome_pdf_engine_detection_logic():
    producers = [
        "Skia/PDF m115 Google Docs Renderer",
        "Chromium 120.0",
        "Chrome PDF Viewer",
        "PDFium",
    ]
    for producer in producers:
        is_chrome = any(kw in producer.lower() for kw in ("skia", "chromium", "chrome", "pdfium"))
        assert is_chrome, f"Should detect as Chrome: {producer!r}"


def test_non_chrome_pdf_engine_detection():
    non_chrome = ["iOS Version 26.7.1 Quartz PDFContext", "Adobe Acrobat", ""]
    for producer in non_chrome:
        is_chrome = any(kw in producer.lower() for kw in ("skia", "chromium", "chrome", "pdfium"))
        assert not is_chrome, f"Should NOT be Chrome: {producer!r}"


def test_needs_hybrid_false_for_kaspi_pdfs():
    real_pdfs = _real_pdf_paths()
    if not real_pdfs:
        pytest.skip("No PDF samples")
    from analyzer.pdf_ingestor import ingest_pdf
    result = ingest_pdf(real_pdfs[0])
    assert result.get("needs_hybrid") is False
    for p in result["page_image_paths"]:
        if os.path.exists(p):
            os.unlink(p)


if __name__ == "__main__":
    import subprocess
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], cwd=base)
