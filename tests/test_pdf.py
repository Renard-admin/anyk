"""Tests for PDF ingestion and analysis pipeline."""
import os
import sys
import tempfile
import struct

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.pdf_ingestor import validate_pdf, ingest_pdf

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


# ── validate_pdf ──────────────────────────────────────────────────────────────

def test_validate_pdf_rejects_empty_file():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        path = f.name
    try:
        with pytest.raises(ValueError, match="мал|повреждён"):
            validate_pdf(path)
    finally:
        os.unlink(path)


def test_validate_pdf_rejects_wrong_magic():
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)
        path = f.name
    try:
        with pytest.raises(ValueError, match="сигнатур"):
            validate_pdf(path)
    finally:
        os.unlink(path)


def test_validate_pdf_rejects_too_large(tmp_path):
    big = tmp_path / "big.pdf"
    # Write valid magic but huge size
    big.write_bytes(b"%PDF-1.4 " + b"A" * (21 * 1024 * 1024))
    with pytest.raises(ValueError, match="большой"):
        validate_pdf(str(big))


def test_validate_pdf_accepts_valid_magic(tmp_path):
    """A file with PDF magic and minimal content should not raise on validate."""
    # Minimal valid-magic file (not a real openable PDF but passes validate)
    p = tmp_path / "minimal.pdf"
    p.write_bytes(b"%PDF-1.4\n%%EOF\n")
    # Should not raise:
    validate_pdf(str(p))


# ── ingest_pdf — real samples ─────────────────────────────────────────────────

@pytest.mark.skipif(not _real_pdf_paths(), reason="No PDF samples found")
def test_ingest_real_pdfs_returns_image_paths():
    """Real Kaspi PDFs should be parsed, rendered, and produce at least one image path."""
    for path in _real_pdf_paths():
        result = ingest_pdf(path)
        assert result["source_type"] == "pdf"
        assert result["page_count"] >= 1
        assert len(result["page_image_paths"]) >= 1
        # All rendered images should actually exist
        for img_path in result["page_image_paths"]:
            assert os.path.exists(img_path), f"Rendered image not found: {img_path}"
            assert os.path.getsize(img_path) > 1000, "Rendered image suspiciously small"
            # Clean up
            os.unlink(img_path)


@pytest.mark.skipif(not _real_pdf_paths(), reason="No PDF samples found")
def test_ingest_real_pdfs_metadata():
    """Kaspi PDFs should have Author=Kaspi.kz and iOS Quartz producer."""
    for path in _real_pdf_paths():
        result = ingest_pdf(path)
        meta = result["metadata"]
        # Kaspi exports always set Author
        assert meta["author"] == "Kaspi.kz", (
            f"Expected Author=Kaspi.kz, got: {meta['author']!r}"
        )
        assert "Quartz" in meta["producer"] or "iOS" in meta["producer"], (
            f"Unexpected producer: {meta['producer']!r}"
        )
        # These PDFs have no text layer
        assert result["has_text_layer"] is False
        assert len(result["text_layers"]) >= 1

        # Clean up rendered images
        for img_path in result["page_image_paths"]:
            if os.path.exists(img_path):
                os.unlink(img_path)


@pytest.mark.skipif(not _real_pdf_paths(), reason="No PDF samples found")
def test_ingest_real_pdfs_page_images_are_png():
    """Rendered page images must be valid PNG files."""
    PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
    for path in _real_pdf_paths()[:1]:  # test only first PDF to save time
        result = ingest_pdf(path)
        for img_path in result["page_image_paths"]:
            with open(img_path, "rb") as f:
                header = f.read(8)
            assert header == PNG_MAGIC, f"Not a PNG: {img_path}"
            os.unlink(img_path)


# ── report_builder integration ────────────────────────────────────────────────

def test_report_builder_with_pdf_info():
    from analyzer.report_builder import build_report
    from analyzer.risk_scorer import calculate_risk

    fake_ocr = {
        "raw_text": "Kaspi.kz Перевод выполнен 15 000 тг",
        "regions": [],
        "mean_confidence": 0.9,
        "word_count": 6,
        "source": "ocr",
    }
    fake_pdf_info = {
        "source_type": "pdf",
        "page_count": 1,
        "pages_processed": 1,
        "has_text_layer": False,
        "text_layers": [""],
        "page_image_paths": [],
        "warnings": [],
        "metadata": {
            "author": "Kaspi.kz",
            "producer": "iOS Version 26.7.1 Quartz PDFContext",
            "creator": "",
            "creation_date": "2026-10-24T12:00:00+00:00",
            "mod_date": "2026-10-24T12:00:00+00:00",
            "dates_match": True,
            "format": "PDF 1.4",
            "creation_date_raw": "D:20261024120000Z00'00'",
            "mod_date_raw": "D:20261024120000Z00'00'",
        },
    }
    risk = calculate_risk([])
    report = build_report(fake_ocr, [], [], risk,
                          context="", image_filename="receipt.pdf",
                          pdf_info=fake_pdf_info)

    assert report["source_type"] == "pdf"
    assert report["ocr_source"] == "ocr"
    assert report["pdf_metadata"]["author"] == "Kaspi.kz"
    assert report["pdf_has_text_layer"] is False
    assert report["pdf_page_count"] == 1
    assert "ocr" in report["analysis_methods"]


# ── main.py magic-byte detection ─────────────────────────────────────────────

def test_sniff_mime():
    """Test the magic-byte detection function."""
    import main as app_main

    assert app_main._sniff_mime(b"%PDF-1.4 stuff") == "pdf"
    assert app_main._sniff_mime(b"\x89PNG\r\n\x1a\ndata") == "image"
    assert app_main._sniff_mime(b"\xff\xd8\xff\xe0") == "image"
    assert app_main._sniff_mime(b"RIFF\x00\x00\x00\x00WEBP") == "image"
    assert app_main._sniff_mime(b"plaintext file") is None


if __name__ == "__main__":
    import subprocess
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], cwd=base)
