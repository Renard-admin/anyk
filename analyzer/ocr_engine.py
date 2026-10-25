"""
OCR engine with three-tier graceful fallback:

  Tier 1 — EasyOCR (full accuracy, requires torch ~1.5 GB)
            Available in local/Docker environments.

  Tier 2 — PyMuPDF text-layer extraction (zero-weight, vector PDFs only)
            Used automatically when the caller already extracted text
            (the pdf_ingestor sets source="pdf_text_layer"); this tier
            is only reached for image inputs that somehow skip Tier 1.

  Tier 3 — Gemini Vision API (gemini-1.5-flash)
            Triggered when EasyOCR is unavailable (Vercel / serverless)
            AND the input is a rasterised image (scan / screenshot).
            Requires GEMINI_API_KEY in the environment.
"""
from __future__ import annotations

import base64
import concurrent.futures
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# ── PyTorch thread limits (must happen before torch is imported) ──────────
# Keeps RAM usage within Render's 512 MB free-tier limit.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

# ── Availability flags ────────────────────────────────────────────────────

def _easyocr_available() -> bool:
    try:
        import easyocr  # noqa: F401
        import torch    # noqa: F401
        return True
    except ImportError:
        return False


_EASYOCR_OK: bool = _easyocr_available()

if _EASYOCR_OK:
    try:
        import torch
        torch.set_num_threads(1)
    except Exception:
        pass

_reader = None


# ── Tier 1: EasyOCR (lazy — loaded only on first OCR call) ────────────────

def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        logger.info("Initializing EasyOCR reader (first call)…")
        _reader = easyocr.Reader(['ru', 'en'], gpu=False, verbose=False,
                                  quantize=True)
        logger.info("EasyOCR reader ready.")
    return _reader


_OCR_TIMEOUT_S = 45  # seconds before we give up and fall back to Gemini


def _ocr_easyocr(image_path: str) -> dict:
    reader = _get_reader()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(reader.readtext, image_path)
        try:
            results = future.result(timeout=_OCR_TIMEOUT_S)
        except concurrent.futures.TimeoutError:
            logger.warning("EasyOCR timed out after %ds — falling back to Gemini", _OCR_TIMEOUT_S)
            raise TimeoutError("EasyOCR timeout")
    regions = [
        {"text": text, "confidence": float(conf), "bbox": bbox}
        for bbox, text, conf in results
    ]
    raw_text = " ".join(r["text"] for r in regions)
    word_count = len(raw_text.split()) if raw_text.strip() else 0
    mean_conf = sum(r["confidence"] for r in regions) / len(regions) if regions else 0.0
    return {"raw_text": raw_text, "regions": regions,
            "mean_confidence": round(mean_conf, 4), "word_count": word_count,
            "source": "easyocr"}


# ── Tier 3: Gemini Vision API ─────────────────────────────────────────────

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)

_GEMINI_PROMPT = (
    "Ты — система OCR. Извлеки весь видимый текст с изображения платёжного чека "
    "ТОЧНО как он написан, строка за строкой, без исправлений и добавлений. "
    "Верни только сырой текст, без пояснений."
)


def _ocr_gemini(image_path: str) -> dict:
    import httpx

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        logger.warning("GEMINI_API_KEY not set — Gemini OCR skipped")
        return _empty_result("gemini_no_key")

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    ext = os.path.splitext(image_path)[1].lower()
    mime = {"png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".bmp": "image/bmp"}.get(ext, "image/png")

    b64 = base64.b64encode(image_bytes).decode()
    payload = {
        "contents": [{
            "parts": [
                {"text": _GEMINI_PROMPT},
                {"inline_data": {"mime_type": mime, "data": b64}},
            ]
        }]
    }

    try:
        resp = httpx.post(
            f"{_GEMINI_URL}?key={api_key}",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_text = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
            .strip()
        )
    except Exception as e:
        logger.warning("Gemini OCR request failed: %s", e)
        return _empty_result("gemini_error")

    word_count = len(raw_text.split()) if raw_text.strip() else 0
    conf = 0.85 if word_count > 0 else 0.0
    return {
        "raw_text": raw_text,
        "regions": [],
        "mean_confidence": conf,
        "word_count": word_count,
        "source": "gemini_vision",
    }


# ── Helpers ───────────────────────────────────────────────────────────────

def _empty_result(source: str = "none") -> dict:
    return {"raw_text": "", "regions": [], "mean_confidence": 0.0,
            "word_count": 0, "source": source, "low_quality": True}


# ── Public API ────────────────────────────────────────────────────────────

def extract_text(image_path: str) -> dict:
    """
    Extract text from an image using the best available backend.

    Priority: EasyOCR → Gemini Vision API → empty result.
    """
    start = time.time()

    if _EASYOCR_OK:
        try:
            result = _ocr_easyocr(image_path)
        except TimeoutError:
            logger.warning("EasyOCR timed out — falling back to Gemini")
            result = _ocr_gemini(image_path)
        except Exception as e:
            logger.warning("EasyOCR failed (%s) — falling back to Gemini", e)
            result = _ocr_gemini(image_path)
    else:
        logger.info("EasyOCR unavailable — using Gemini Vision API")
        result = _ocr_gemini(image_path)

    elapsed = time.time() - start
    logger.info(
        "OCR done in %.2fs | backend=%s | words=%d | mean_conf=%.3f | path=%s",
        elapsed, result.get("source"), result["word_count"],
        result["mean_confidence"], image_path,
    )

    if result["mean_confidence"] < 0.4 and result["word_count"] == 0:
        result["low_quality"] = True

    return result
