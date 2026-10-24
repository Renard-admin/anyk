"""Tests for layout_checker module."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.layout_checker import check_layout, _check_receipt_number

SAMPLES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "samples",
)


def _real_pdf_paths():
    if not os.path.isdir(SAMPLES_DIR):
        return []
    return sorted([
        os.path.join(SAMPLES_DIR, f)
        for f in os.listdir(SAMPLES_DIR)
        if f.lower().endswith(".pdf")
    ])


# ── receipt number checks ──────────────────────────────────────────────────

def test_valid_18digit_receipt_no_signal():
    """An 18-digit receipt number should not trigger RECEIPT_NUMBER_WRONG_LENGTH."""
    signals = _check_receipt_number("Ng квитанции 898936951310176343")
    ids = [s["signal_id"] for s in signals]
    assert "RECEIPT_NUMBER_WRONG_LENGTH" not in ids, (
        f"Valid 18-digit number flagged: {ids}"
    )


def test_short_receipt_number_signals():
    """A 15-digit cluster should flag RECEIPT_NUMBER_WRONG_LENGTH."""
    signals = _check_receipt_number("Ng квитанции 123456789012345")
    ids = [s["signal_id"] for s in signals]
    assert "RECEIPT_NUMBER_WRONG_LENGTH" in ids, (
        f"Expected RECEIPT_NUMBER_WRONG_LENGTH, got: {ids}"
    )


def test_no_receipt_number_no_signal():
    """Text with no digit clusters should produce no signals."""
    signals = _check_receipt_number("Перевод успешно совершен")
    assert signals == []


# ── anchor position checks ─────────────────────────────────────────────────

def _make_region(text, y):
    """Helper: build a minimal OCR region dict."""
    return {
        "text": text,
        "confidence": 0.9,
        "bbox": [[10, y], [200, y], [200, y + 20], [10, y + 20]],
    }


def test_layout_clean_receipt_no_signals():
    """A near-ideal receipt layout should produce no layout signals."""
    regions = [
        _make_region("Перевод успешно совершен", 486),
        _make_region("560 〒",                    541),
        _make_region("Ng квитанции",              1027),
        _make_region("Дата и время",              1113),
        _make_region("Комиссия",                  1236),
        _make_region("Отправитель",               1319),
    ]
    ocr_result = {
        "raw_text": " ".join(r["text"] for r in regions),
        "regions": regions,
        "mean_confidence": 0.92,
        "word_count": 10,
    }
    signals = check_layout(ocr_result)
    layout_ids = [s["signal_id"] for s in signals]
    # Should not flag anchors or misalignment on a clean layout
    assert "LAYOUT_ANCHORS_MISSING" not in layout_ids, (
        f"Clean receipt wrongly flagged: {layout_ids}"
    )


def test_layout_missing_anchors_signals():
    """A receipt missing most anchor fields should flag LAYOUT_ANCHORS_MISSING."""
    regions = [
        _make_region("Какой-то текст", 100),
        _make_region("Ещё строка",     200),
    ]
    ocr_result = {
        "raw_text": "Какой-то текст Ещё строка",
        "regions": regions,
        "mean_confidence": 0.5,
        "word_count": 4,
    }
    signals = check_layout(ocr_result)
    ids = [s["signal_id"] for s in signals]
    assert "LAYOUT_ANCHORS_MISSING" in ids, (
        f"Expected LAYOUT_ANCHORS_MISSING for text without Kaspi fields, got: {ids}"
    )


# ── integration with real PDFs ────────────────────────────────────────────

@pytest.mark.skipif(not _real_pdf_paths(), reason="No PDF samples found")
def test_layout_real_kaspi_pdfs_no_anchor_missing():
    """Real Kaspi receipts should not trigger LAYOUT_ANCHORS_MISSING."""
    import pymupdf
    import tempfile
    import uuid
    from analyzer.preprocessor import preprocess
    from analyzer.ocr_engine import extract_text

    for pdf_path in _real_pdf_paths()[:2]:  # test first 2 to save time
        doc = pymupdf.open(pdf_path)
        mat = pymupdf.Matrix(2.5, 2.5)
        pix = doc[0].get_pixmap(matrix=mat, colorspace=pymupdf.csRGB, alpha=False)
        tmp_png = os.path.join(tempfile.gettempdir(), f"layout_test_{uuid.uuid4().hex}.png")
        pix.save(tmp_png)
        doc.close()

        try:
            pre = preprocess(tmp_png)
            ocr_result = extract_text(pre)
            signals = check_layout(ocr_result, image_path=pre, is_pdf=True)
            ids = [s["signal_id"] for s in signals]
            assert "LAYOUT_ANCHORS_MISSING" not in ids, (
                f"{os.path.basename(pdf_path)}: LAYOUT_ANCHORS_MISSING fired: {ids}"
            )
        finally:
            for p in (tmp_png, pre if pre != tmp_png else None):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass


@pytest.mark.skipif(not _real_pdf_paths(), reason="No PDF samples found")
def test_layout_pdf_no_grayscale_signal():
    """PDF-rendered images must never trigger IMAGE_GRAYSCALE (is_pdf=True suppresses it)."""
    import pymupdf
    import tempfile
    import uuid
    from analyzer.preprocessor import preprocess
    from analyzer.ocr_engine import extract_text

    for pdf_path in _real_pdf_paths()[:4]:
        doc = pymupdf.open(pdf_path)
        mat = pymupdf.Matrix(2.5, 2.5)
        pix = doc[0].get_pixmap(matrix=mat, colorspace=pymupdf.csRGB, alpha=False)
        tmp_png = os.path.join(tempfile.gettempdir(), f"gs_test_{uuid.uuid4().hex}.png")
        pix.save(tmp_png)
        doc.close()

        try:
            pre = preprocess(tmp_png)
            ocr_result = extract_text(pre)
            signals = check_layout(ocr_result, image_path=pre, is_pdf=True)
            ids = [s["signal_id"] for s in signals]
            assert "IMAGE_GRAYSCALE" not in ids, (
                f"{os.path.basename(pdf_path)}: IMAGE_GRAYSCALE fired despite is_pdf=True"
            )
        finally:
            for p in (tmp_png, pre if pre != tmp_png else None):
                if p and os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass


if __name__ == "__main__":
    import subprocess
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], cwd=base)
