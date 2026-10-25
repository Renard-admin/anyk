import os
import uuid
import logging
import tempfile

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="АНЫҚ", description="Анализатор платёжных скриншотов")

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# In-memory report store (hackathon MVP)
_reports: dict[str, dict] = {}

_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
_ALLOWED_PDF_EXTS   = {".pdf"}
_MAX_FILE_BYTES     = 20 * 1024 * 1024  # 20 MB

# PDF magic bytes
_PDF_MAGIC = b"%PDF-"
# Image magic: PNG, JPEG, WEBP (RIFF....WEBP)
_IMAGE_MAGIC = {
    b"\x89PNG",
    b"\xff\xd8\xff",  # JPEG
}


def _sniff_mime(data: bytes) -> str | None:
    """Return 'pdf', 'image', or None based on magic bytes."""
    if data[:5] == _PDF_MAGIC:
        return "pdf"
    for magic in _IMAGE_MAGIC:
        if data[:len(magic)] == magic:
            return "image"
    # WEBP: RIFF????WEBP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    # BMP
    if data[:2] == b"BM":
        return "image"
    # TIFF
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"
    return None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "anyk", "version": "0.3.0"}


@app.get("/sw.js")
async def service_worker():
    """Serve service worker from root scope (required for PWA)."""
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json")
async def web_manifest():
    """Serve web app manifest from root (for PWA install)."""
    return FileResponse("static/manifest.json", media_type="application/manifest+json")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/share-target")
async def share_target(
    request: Request,
    receipt: UploadFile = File(default=None),
):
    """
    Web Share Target API endpoint.
    Accepts files shared from Telegram/WhatsApp/Gallery via the system share menu,
    runs the full analysis pipeline, and redirects to the result page.
    """
    # Some browsers/OS send the file under the manifest param name 'receipt'
    # others may use a generic field — try to get any uploaded file
    uploaded_file = receipt

    # If the named field is missing, check all form files
    if uploaded_file is None:
        form = await request.form()
        for key, val in form.items():
            if hasattr(val, "filename") and val.filename:
                uploaded_file = val
                break

    if uploaded_file is None:
        # No file received — redirect home with an error flag
        return RedirectResponse(url="/?share_error=no_file", status_code=303)

    tmp_raw = None
    tmp_pre = None
    pdf_image_paths: list[str] = []

    try:
        content = await uploaded_file.read()
        if len(content) > _MAX_FILE_BYTES:
            return RedirectResponse(url="/?share_error=too_large", status_code=303)
        if len(content) < 4:
            return RedirectResponse(url="/?share_error=empty", status_code=303)

        detected_type = _sniff_mime(content[:16])
        filename = uploaded_file.filename or "shared_receipt"
        ext = os.path.splitext(filename)[1].lower() or ""

        if detected_type is None:
            if ext in _ALLOWED_IMAGE_EXTS:
                detected_type = "image"
            elif ext in _ALLOWED_PDF_EXTS:
                detected_type = "pdf"
            else:
                return RedirectResponse(url="/?share_error=format", status_code=303)

        safe_ext = ext if ext else (".pdf" if detected_type == "pdf" else ".png")
        tmp_raw = os.path.join(
            tempfile.gettempdir(), f"anyk_share_{uuid.uuid4().hex}{safe_ext}"
        )
        with open(tmp_raw, "wb") as f:
            f.write(content)
        logger.info("Share-target upload: %s (%d bytes, type=%s)", tmp_raw, len(content), detected_type)

        # Reuse analysis logic
        pdf_info: dict | None = None
        if detected_type == "pdf":
            from analyzer.pdf_ingestor import ingest_pdf, validate_pdf
            validate_pdf(tmp_raw)
            pdf_info = ingest_pdf(tmp_raw)
            pdf_image_paths = pdf_info["page_image_paths"]
            if pdf_info["has_text_layer"]:
                combined_text = "\n".join(pdf_info["text_layers"])
                ocr_result = {
                    "raw_text": combined_text, "regions": [],
                    "mean_confidence": 1.0,
                    "word_count": len(combined_text.split()),
                    "source": "pdf_text_layer",
                }
            else:
                from analyzer.preprocessor import preprocess
                from analyzer.ocr_engine import extract_text
                tmp_pre = preprocess(pdf_image_paths[0])
                ocr_result = extract_text(tmp_pre)
                ocr_result["source"] = "ocr"
        else:
            from analyzer.preprocessor import preprocess
            from analyzer.ocr_engine import extract_text
            tmp_pre = preprocess(tmp_raw)
            ocr_result = extract_text(tmp_pre)
            ocr_result["source"] = "ocr"

        from analyzer.structural_checker import check_structure
        from analyzer.layout_checker import check_layout
        from analyzer.llm_analyzer import analyze_consistency
        from analyzer.risk_scorer import calculate_risk
        from analyzer.report_builder import build_report

        structural_signals = check_structure(ocr_result)
        layout_img = pdf_image_paths[0] if pdf_image_paths else tmp_pre
        layout_signals = check_layout(ocr_result, image_path=layout_img, is_pdf=(detected_type == "pdf"))
        llm_signals = analyze_consistency(ocr_result, "")
        risk = calculate_risk(structural_signals + layout_signals + llm_signals)
        report = build_report(
            ocr_result, structural_signals + layout_signals, llm_signals, risk,
            context="", image_filename=filename, pdf_info=pdf_info,
        )
        _reports[report["report_id"]] = report
        return RedirectResponse(url=f"/report/{report['report_id']}", status_code=303)

    except Exception as e:
        logger.exception("Share-target analysis failed")
        return RedirectResponse(url=f"/?share_error={type(e).__name__}", status_code=303)
    finally:
        for p in ([tmp_raw, tmp_pre] + pdf_image_paths):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    context: str = Form(default=""),
):
    tmp_raw = None
    tmp_pre = None
    pdf_image_paths: list[str] = []

    try:
        # ── a. Read file content & validate size ──────────────────────────────
        content = await file.read()
        if len(content) > _MAX_FILE_BYTES:
            return JSONResponse(
                {"error": "file_too_large",
                 "message": f"Файл слишком большой. Максимум 20 МБ."},
                status_code=413,
            )
        if len(content) < 4:
            return JSONResponse(
                {"error": "file_empty", "message": "Файл пустой или повреждён."},
                status_code=422,
            )

        # ── b. Detect type by magic bytes (not just extension) ────────────────
        detected_type = _sniff_mime(content[:16])
        filename = file.filename or "upload"
        ext = os.path.splitext(filename)[1].lower() or ""

        if detected_type is None:
            # Fall back to extension
            if ext in _ALLOWED_IMAGE_EXTS:
                detected_type = "image"
            elif ext in _ALLOWED_PDF_EXTS:
                detected_type = "pdf"
            else:
                return JSONResponse(
                    {"error": "unsupported_format",
                     "message": f"Формат не поддерживается. Загрузите PNG, JPG, WEBP или PDF."},
                    status_code=422,
                )
        if detected_type not in ("image", "pdf"):
            return JSONResponse(
                {"error": "unsupported_format",
                 "message": "Формат не поддерживается. Загрузите PNG, JPG, WEBP или PDF."},
                status_code=422,
            )

        # ── c. Save raw file to temp ──────────────────────────────────────────
        safe_ext = ext if ext else (".pdf" if detected_type == "pdf" else ".png")
        tmp_raw = os.path.join(
            tempfile.gettempdir(), f"anyk_raw_{uuid.uuid4().hex}{safe_ext}"
        )
        with open(tmp_raw, "wb") as f:
            f.write(content)
        logger.info("Saved upload: %s (%d bytes, type=%s)", tmp_raw, len(content), detected_type)

        # ── d. PDF branch ─────────────────────────────────────────────────────
        pdf_info: dict | None = None
        if detected_type == "pdf":
            from analyzer.pdf_ingestor import ingest_pdf, validate_pdf
            try:
                validate_pdf(tmp_raw)
                pdf_info = ingest_pdf(tmp_raw)
            except ValueError as e:
                return JSONResponse(
                    {"error": "pdf_invalid", "message": str(e)},
                    status_code=422,
                )
            pdf_image_paths = pdf_info["page_image_paths"]

            # Combine text from all pages for structural check
            # If text layer exists use it; otherwise rely entirely on OCR
            if pdf_info["has_text_layer"]:
                combined_text = "\n".join(pdf_info["text_layers"])
                ocr_result = {
                    "raw_text": combined_text,
                    "regions": [],
                    "mean_confidence": 1.0,
                    "word_count": len(combined_text.split()),
                    "source": "pdf_text_layer",
                }
                logger.info("PDF has text layer (%d chars)", len(combined_text))

                # Hybrid mode for Chrome PDFs: only fall back to OCR when the
                # text layer is genuinely sparse (< 20 chars), otherwise skip
                # EasyOCR entirely to avoid 504 on 1 vCPU servers.
                if pdf_info.get("needs_hybrid") and pdf_image_paths:
                    text_words = ocr_result["word_count"]
                    text_chars = len(ocr_result["raw_text"])
                    if text_chars >= 20:
                        logger.info(
                            "Chrome hybrid: text layer sufficient (%d chars, %d words) — skipping OCR",
                            text_chars, text_words,
                        )
                    else:
                        from analyzer.preprocessor import preprocess
                        from analyzer.ocr_engine import extract_text
                        tmp_pre = preprocess(pdf_image_paths[0])
                        ocr_fallback = extract_text(tmp_pre)
                        ocr_words = ocr_fallback.get("word_count", 0)
                        if ocr_words > text_words * 1.3:
                            ocr_result["raw_text"] = ocr_fallback["raw_text"]
                            ocr_result["regions"] = ocr_fallback.get("regions", [])
                            ocr_result["mean_confidence"] = ocr_fallback["mean_confidence"]
                            ocr_result["source"] = "hybrid_chrome_ocr"
                            ocr_result["word_count"] = ocr_words
                            logger.info("Chrome hybrid: sparse text layer — using OCR (%d words)", ocr_words)
                        if tmp_pre and tmp_pre not in pdf_image_paths:
                            try:
                                os.unlink(tmp_pre)
                            except OSError:
                                pass
                            tmp_pre = None
            else:
                # Run OCR on first page image
                from analyzer.preprocessor import preprocess
                from analyzer.ocr_engine import extract_text
                first_image = pdf_image_paths[0]
                tmp_pre = preprocess(first_image)
                ocr_result = extract_text(tmp_pre)
                ocr_result["source"] = "ocr"
                logger.info("PDF image-only, OCR result: words=%d conf=%.3f",
                            ocr_result["word_count"], ocr_result["mean_confidence"])
        else:
            # ── e. Image branch ───────────────────────────────────────────────
            from analyzer.preprocessor import preprocess
            from analyzer.ocr_engine import extract_text
            tmp_pre = preprocess(tmp_raw)
            ocr_result = extract_text(tmp_pre)
            ocr_result["source"] = "ocr"

        # ── f. Low quality fast-path ──────────────────────────────────────────
        if ocr_result.get("low_quality"):
            logger.warning("Low OCR quality — returning uncertain report")
            from analyzer.risk_scorer import calculate_risk
            from analyzer.report_builder import build_report
            lq_signal = {
                "signal_id": "OCR_LOW_QUALITY",
                "category": "structural",
                "severity": "low",
                "title_ru": "Низкое качество изображения",
                "description_ru": (
                    "OCR не смог уверенно извлечь текст. "
                    "Попробуйте загрузить изображение лучшего качества."
                ),
                "evidence_text": f"mean_confidence={ocr_result['mean_confidence']:.2f}",
                "confidence": "high",
                "method": "ocr_quality_check",
            }
            risk = calculate_risk([lq_signal])
            report = build_report(
                ocr_result, [lq_signal], [], risk,
                context=context,
                image_filename=filename,
                pdf_info=pdf_info,
            )
            _reports[report["report_id"]] = report
            return JSONResponse(report)

        # ── g. Structural check ───────────────────────────────────────────────
        from analyzer.structural_checker import check_structure
        structural_signals = check_structure(ocr_result)

        # ── g2. Layout / geometry check ───────────────────────────────────────
        from analyzer.layout_checker import check_layout
        # Pass the first rendered image (PDF page or original image) if available
        _layout_img = pdf_image_paths[0] if pdf_image_paths else tmp_pre
        layout_signals = check_layout(ocr_result, image_path=_layout_img, is_pdf=(detected_type == "pdf"))

        # ── h. LLM analysis ───────────────────────────────────────────────────
        from analyzer.llm_analyzer import analyze_consistency
        llm_signals = analyze_consistency(ocr_result, context)

        # ── i. Risk score ─────────────────────────────────────────────────────
        from analyzer.risk_scorer import calculate_risk
        risk = calculate_risk(structural_signals + layout_signals + llm_signals)

        # ── j. Build report ───────────────────────────────────────────────────
        from analyzer.report_builder import build_report
        report = build_report(
            ocr_result, structural_signals + layout_signals, llm_signals, risk,
            context=context,
            image_filename=filename,
            pdf_info=pdf_info,
        )

        _reports[report["report_id"]] = report
        return JSONResponse(report)

    except Exception as e:
        logger.exception("Analysis failed")
        return JSONResponse(
            {"error": "analysis_failed", "message": str(e)},
            status_code=500,
        )
    finally:
        # Clean up temp files
        for p in ([tmp_raw, tmp_pre] + pdf_image_paths):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


@app.get("/report/{report_id}")
async def report_page(request: Request, report_id: str):
    report = _reports.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return templates.TemplateResponse("result.html", {"request": request, "report": report})


@app.get("/report/{report_id}/export")
async def export_txt(report_id: str):
    """Export report as plain text file."""
    report = _reports.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    lines = [
        "АНЫҚ — Отчёт об анализе платёжного документа",
        "=" * 52,
        f"ID отчёта:    {report['report_id']}",
        f"Дата:         {report['timestamp']}",
        f"Файл:         {report['image_filename']}",
        f"Тип источника:{report.get('source_type', 'image')}",
        f"Метод OCR:    {report.get('ocr_source', 'ocr')}",
        f"Уровень риска:{report['risk_level_ru']} ({report['risk_level'].upper()})",
        f"Оценка риска: {report['risk_score']}",
        "",
    ]

    if report.get("pdf_metadata"):
        m = report["pdf_metadata"]
        lines += [
            "── Метаданные PDF ──",
            f"  Автор:        {m.get('author') or '(нет)'}",
            f"  Producer:     {m.get('producer') or '(нет)'}",
            f"  Дата создания:{m.get('creation_date') or '(нет)'}",
            f"  Дата изменения:{m.get('mod_date') or '(нет)'}",
            "",
        ]

    lines += [
        "── Извлечённый текст ──",
        report["ocr_text"],
        f"OCR уверенность: {round(report['ocr_confidence'] * 100, 1)}%",
        "",
        f"── Найдено сигналов: {report['signal_count']} ──",
        "-" * 52,
    ]

    for s in report["signals"]:
        lines.append(f"[{s.get('severity','?').upper()}] {s.get('signal_id','?')}: {s.get('title_ru','')}")
        lines.append(f"  {s.get('description_ru','')}")
        lines.append(f"  Доказательство: \"{s.get('evidence_text','')}\"")
        lines.append(f"  Уверенность: {s.get('confidence','?')} · Метод: {s.get('method','?')}")
        lines.append("")

    lines.append(report["disclaimer_ru"])

    text = "\n".join(lines)
    return PlainTextResponse(
        content=text,
        headers={
            "Content-Disposition": f"attachment; filename=anyk_report_{report_id[:8]}.txt"
        },
    )
