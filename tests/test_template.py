"""Tests for the template matching pipeline."""
import os
import pytest

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "samples")


def _sample(name: str) -> str:
    return os.path.join(SAMPLE_DIR, name)


def _has_samples() -> bool:
    return os.path.isdir(SAMPLE_DIR) and any(
        f.endswith(".pdf") for f in os.listdir(SAMPLE_DIR)
    )


# ─── Unit tests for individual checks ────────────────────────────────────────

def test_metadata_risky_producer():
    from analyzer.template_matcher import _check_metadata
    sigs, bonus = _check_metadata({"producer": "Adobe Photoshop", "author": ""})
    assert any(s["signal_id"] == "METADATA_RISKY_PRODUCER" for s in sigs)
    assert bonus == 0


def test_metadata_authentic():
    from analyzer.template_matcher import _check_metadata
    sigs, bonus = _check_metadata({
        "producer": "iOS Version 16.0 Quartz PDFContext",
        "author": "Kaspi.kz",
        "creator": "",
    })
    assert sigs == []
    assert bonus >= 30


def test_text_layer_present():
    from analyzer.template_matcher import _check_text_layer
    long_text = "Перевод успешно совершен 850 тенге клиенту " * 5
    sigs, bonus = _check_text_layer(True, [long_text])
    assert any(s["signal_id"] == "TEXT_LAYER_PRESENT" for s in sigs)


def test_text_layer_absent():
    from analyzer.template_matcher import _check_text_layer
    sigs, bonus = _check_text_layer(False, [])
    assert sigs == []
    assert bonus > 0


def test_geometry_wrong_size():
    from analyzer.template_matcher import _check_geometry
    sigs, bonus = _check_geometry(210.0, 297.0, [])  # A4, not Kaspi
    assert any(s["signal_id"] == "GEOMETRY_PAGE_SIZE" for s in sigs)


def test_geometry_correct_size():
    from analyzer.template_matcher import _check_geometry
    # 19 blocks, correct page size, no anchor blocks to check (empty list)
    fake_blocks = [{"type": 1, "bbox": [0, 0, 1, 1]}] * 19
    sigs, bonus = _check_geometry(489.8, 699.7, fake_blocks)
    # Should have no page-size signal
    assert not any(s["signal_id"] == "GEOMETRY_PAGE_SIZE" for s in sigs)
    assert bonus >= 15


# ─── Integration tests with real samples ─────────────────────────────────────

@pytest.mark.skipif(not _has_samples(), reason="No sample PDFs found")
@pytest.mark.parametrize("filename", [
    f for f in (os.listdir(SAMPLE_DIR) if os.path.isdir(SAMPLE_DIR) else [])
    if f.endswith(".pdf")
])
def test_genuine_receipt_authentic(filename):
    """All genuine Kaspi receipts must score >= 80% with zero high signals."""
    from analyzer.pdf_ingestor import ingest_pdf, validate_pdf
    from analyzer.template_matcher import match_template

    path = _sample(filename)
    validate_pdf(path)
    pdf_info = ingest_pdf(path, render_pages=False)
    result = match_template(path, "pdf", pdf_info)

    assert result["trust_score"] >= 80, (
        f"{filename}: trust={result['trust_score']}% — expected ≥80%"
    )
    high_signals = [s for s in result["signals"] if s["severity"] == "high"]
    assert high_signals == [], (
        f"{filename}: unexpected high signals: {[s['signal_id'] for s in high_signals]}"
    )
    assert result["trust_label_en"] == "authentic", (
        f"{filename}: expected 'authentic', got {result['trust_label_en']!r}"
    )
