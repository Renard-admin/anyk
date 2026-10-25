"""
PDF ingestion for АНЫҚ.

Strategy:
- Use PyMuPDF to open PDFs and extract metadata.
- Kaspi.kz PDFs (and most mobile-exported receipts) have NO text layer —
  they are image-only exports from iOS Quartz.
- We render each page to a high-resolution PNG and pass it to the OCR pipeline.
- If a text layer IS present (non-empty after stripping), we also extract it
  directly and skip OCR for that page.

Limits (safe defaults for MVP server):
- Max file size: 20 MB
- Max pages processed: 5 (first pages only)
- Max render resolution: 3× (DPI ≈ 216)
"""

import os
import uuid
import logging
import tempfile
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
_MAX_PAGES = 5
_RENDER_SCALE = 2.08  # 72dpi × 2.08 ≈ 150dpi — sufficient for EasyOCR, 40% fewer pixels


def _parse_pdf_date(raw: str) -> str | None:
    """Convert PDF date string D:YYYYMMDDHHmmSS... to ISO-8601."""
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("D:"):
        raw = raw[2:]
    # YYYYMMDDHHmmSS
    try:
        dt = datetime.strptime(raw[:14], "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    try:
        dt = datetime.strptime(raw[:8], "%Y%m%d")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    return raw


def _extract_chrome_text(page) -> str:
    """
    Extract text from a Chrome/Skia PDF page.

    Chrome PDFs fragment text into many small bbox-level blocks with no
    guaranteed order in the raw stream.  We sort all blocks by their
    top-left Y coordinate (then X) so the reassembled text reads top-to-bottom,
    left-to-right — matching the visual layout.
    """
    try:
        blocks = page.get_text("blocks")  # list of (x0,y0,x1,y1,text,block_no,type)
        # type==0 is text, type==1 is image block
        text_blocks = [(b[1], b[0], b[4].strip()) for b in blocks if b[6] == 0 and b[4].strip()]
        text_blocks.sort(key=lambda t: (round(t[0] / 10) * 10, t[1]))  # sort by row then col
        return "\n".join(t[2] for t in text_blocks)
    except Exception as e:
        logger.debug("Chrome text extraction fallback: %s", e)
        return page.get_text("text").strip()


def validate_pdf(path: str) -> None:
    """Raise ValueError with a user-readable message if the PDF is invalid."""
    size = os.path.getsize(path)
    if size > _MAX_FILE_BYTES:
        raise ValueError(
            f"Файл слишком большой ({size // (1024*1024)} МБ). Максимум 20 МБ."
        )
    if size < 4:
        raise ValueError("Файл слишком мал — возможно, повреждён.")

    # Read magic bytes
    with open(path, "rb") as f:
        header = f.read(5)
    if not header.startswith(b"%PDF-"):
        raise ValueError(
            "Файл не является PDF (неверная сигнатура). "
            "Загрузите PDF или изображение (PNG/JPG/WEBP)."
        )


def ingest_pdf(path: str, render_pages: bool = False) -> dict:
    """
    Open a PDF, extract metadata, render pages to PNG temp files.

    Returns a dict:
    {
        "source_type": "pdf",
        "page_count": int,
        "pages_processed": int,
        "page_image_paths": [str, ...],   # temp PNG paths, caller must delete
        "text_layers": [str, ...],        # direct text per page (may be empty)
        "has_text_layer": bool,           # True if ANY page had meaningful text
        "metadata": {
            "author": str,
            "producer": str,
            "creator": str,
            "creation_date": str,
            "mod_date": str,
            "dates_match": bool,
            "format": str,
        },
        "warnings": [str],               # non-fatal issues
    }
    """
    import pymupdf  # noqa: F401 — imported here to avoid startup cost

    validate_pdf(path)

    warnings: list[str] = []
    page_image_paths: list[str] = []
    text_layers: list[str] = []

    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise ValueError(f"Не удалось открыть PDF: {e}")

    if doc.is_encrypted:
        doc.close()
        raise ValueError("PDF зашифрован. Загрузите незашифрованный документ.")

    total_pages = doc.page_count
    pages_to_process = min(total_pages, _MAX_PAGES)
    if total_pages > _MAX_PAGES:
        warnings.append(
            f"Документ содержит {total_pages} страниц. Анализируются первые {_MAX_PAGES}."
        )

    # Extract metadata
    meta = doc.metadata or {}
    creation_raw = meta.get("creationDate", "") or ""
    mod_raw = meta.get("modDate", "") or ""
    creation_iso = _parse_pdf_date(creation_raw)
    mod_iso = _parse_pdf_date(mod_raw)
    dates_match = (creation_raw == mod_raw) if (creation_raw and mod_raw) else None

    pdf_metadata = {
        "author": meta.get("author", "") or "",
        "producer": meta.get("producer", "") or "",
        "creator": meta.get("creator", "") or "",
        "creation_date": creation_iso or creation_raw or "",
        "mod_date": mod_iso or mod_raw or "",
        "dates_match": dates_match,
        "format": meta.get("format", "") or "",
        "creation_date_raw": creation_raw,
        "mod_date_raw": mod_raw,
    }

    # Detect PDF engine from producer field
    producer = pdf_metadata.get("producer", "").lower()
    is_chrome_pdf = any(kw in producer for kw in ("skia", "chromium", "chrome", "pdfium"))
    pdf_metadata["pdf_engine"] = "chrome" if is_chrome_pdf else (
        "ios_quartz" if "quartz" in producer else "unknown"
    )

    has_text_layer = False
    mat = pymupdf.Matrix(_RENDER_SCALE, _RENDER_SCALE)

    for page_num in range(pages_to_process):
        page = doc[page_num]

        # For Chrome/Skia PDFs: reassemble fragmented text blocks by position
        if is_chrome_pdf:
            page_text = _extract_chrome_text(page)
        else:
            page_text = page.get_text("text").strip()

        text_layers.append(page_text)
        if len(page_text) > 30:
            has_text_layer = True

        if render_pages:
            pix = page.get_pixmap(matrix=mat, colorspace=pymupdf.csRGB, alpha=False)
            tmp_path = os.path.join(
                tempfile.gettempdir(),
                f"anyk_pdf_{uuid.uuid4().hex}_p{page_num + 1}.png",
            )
            pix.save(tmp_path)
            page_image_paths.append(tmp_path)
            logger.info(
                "PDF page %d rendered → %s (%dx%d) engine=%s",
                page_num + 1, tmp_path, pix.width, pix.height,
                pdf_metadata["pdf_engine"],
            )

    doc.close()

    # For Chrome PDFs with a text layer: flag hybrid mode so main.py can
    # cross-check the text-layer extraction against OCR results.
    needs_hybrid = is_chrome_pdf and has_text_layer

    return {
        "source_type": "pdf",
        "page_count": total_pages,
        "pages_processed": pages_to_process,
        "page_image_paths": page_image_paths,
        "text_layers": text_layers,
        "has_text_layer": has_text_layer,
        "needs_hybrid": needs_hybrid,
        "metadata": pdf_metadata,
        "warnings": warnings,
    }
