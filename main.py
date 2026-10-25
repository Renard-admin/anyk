import os
import uuid
import logging
import tempfile
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="АНЫҚ", description="Анализатор платёжных скриншотов")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("RequestValidationError on %s: %s", request.url.path, exc.errors())
    return JSONResponse(
        status_code=400,
        content={
            "error": "form_validation_error",
            "message": "Ошибка формата данных. Попробуйте сделать обычный скриншот или загрузить PDF.",
        },
    )


app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# In-memory report store (hackathon MVP)
_reports: dict[str, dict] = {}

_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff"}
_ALLOWED_PDF_EXTS   = {".pdf"}
_MAX_FILE_BYTES     = 20 * 1024 * 1024  # 20 MB

_PDF_MAGIC = b"%PDF-"
_IMAGE_MAGIC = {b"\x89PNG", b"\xff\xd8\xff"}


def _sniff_mime(data: bytes) -> str | None:
    if data[:5] == _PDF_MAGIC:
        return "pdf"
    for magic in _IMAGE_MAGIC:
        if data[:len(magic)] == magic:
            return "image"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image"
    if data[:2] == b"BM":
        return "image"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image"
    return None


def _run_analysis(tmp_path: str, detected_type: str, filename: str) -> dict:
    """Core analysis pipeline — no OCR, no external APIs. ~5-50ms."""
    from analyzer.pdf_ingestor import ingest_pdf, validate_pdf
    from analyzer.template_matcher import match_template
    from analyzer.risk_scorer import calculate_risk
    from analyzer.report_builder import build_report

    pdf_info: dict | None = None

    if detected_type == "pdf":
        validate_pdf(tmp_path)
        pdf_info = ingest_pdf(tmp_path, render_pages=False)

    template_result = match_template(tmp_path, detected_type, pdf_info)
    risk = calculate_risk(template_result["signals"])
    return build_report(template_result, risk, image_filename=filename, pdf_info=pdf_info)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "anyk", "version": "0.4.0"}


@app.get("/sw.js")
async def service_worker():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


@app.get("/manifest.json")
async def web_manifest():
    return FileResponse("static/manifest.json", media_type="application/manifest+json")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


async def _handle_share_upload(request: Request, uploaded_file) -> "Response":
    """Shared logic for /share-target and /analyze-share."""
    from fastapi.responses import RedirectResponse as _RR
    tmp_path = None
    try:
        content = await uploaded_file.read()
        if len(content) > _MAX_FILE_BYTES:
            return _RR(url="/?share_error=too_large", status_code=303)
        if len(content) < 4:
            return _RR(url="/?share_error=empty", status_code=303)

        detected_type = _sniff_mime(content[:16])
        raw_name = (uploaded_file.filename or "").strip()
        if not raw_name or raw_name.lower() in ("blob", "image", "file"):
            raw_name = "shared_receipt"
        filename = raw_name
        ext = os.path.splitext(filename)[1].lower() or ""

        if detected_type is None:
            if ext in _ALLOWED_IMAGE_EXTS:
                detected_type = "image"
            elif ext in _ALLOWED_PDF_EXTS:
                detected_type = "pdf"
            elif uploaded_file.content_type and "pdf" in uploaded_file.content_type:
                detected_type = "pdf"
                ext = ".pdf"
            elif uploaded_file.content_type and uploaded_file.content_type.startswith("image/"):
                detected_type = "image"
                ext = ".jpg"
            else:
                return _RR(url="/?share_error=format", status_code=303)

        safe_ext = ext if ext else (".pdf" if detected_type == "pdf" else ".png")
        tmp_path = os.path.join(tempfile.gettempdir(), f"anyk_share_{uuid.uuid4().hex}{safe_ext}")
        with open(tmp_path, "wb") as f:
            f.write(content)

        report = _run_analysis(tmp_path, detected_type, filename)
        _reports[report["report_id"]] = report
        return _RR(url=f"/report/{report['report_id']}", status_code=303)

    except Exception:
        logger.exception("Share upload analysis failed")
        return _RR(url="/?share_error=server_error", status_code=303)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@app.post("/analyze-share")
async def analyze_share(
    request: Request,
    file: UploadFile = File(default=None),
):
    """Web Share Target endpoint — receives files shared from Telegram/WhatsApp/Gallery."""
    uploaded_file = file
    if uploaded_file is None:
        form = await request.form()
        for _, val in form.items():
            if hasattr(val, "filename") and val.filename:
                uploaded_file = val
                break
    if uploaded_file is None:
        return RedirectResponse(url="/?share_error=no_file", status_code=303)
    return await _handle_share_upload(request, uploaded_file)


@app.post("/share-target")
async def share_target(
    request: Request,
    receipt: UploadFile = File(default=None),
):
    """Legacy share target endpoint kept for backwards compatibility."""
    uploaded_file = receipt
    if uploaded_file is None:
        form = await request.form()
        for _, val in form.items():
            if hasattr(val, "filename") and val.filename:
                uploaded_file = val
                break
    if uploaded_file is None:
        return RedirectResponse(url="/?share_error=no_file", status_code=303)
    return await _handle_share_upload(request, uploaded_file)


@app.post("/analyze")
async def analyze(
    file: UploadFile = File(...),
    context: Optional[str] = Form(default=""),
    disclaimer_accepted: Optional[bool] = Form(default=True),
    notes: Optional[str] = Form(default=None),
):
    tmp_path = None
    try:
        content = await file.read()
        if len(content) > _MAX_FILE_BYTES:
            return JSONResponse(
                {"error": "file_too_large", "message": "Файл слишком большой. Максимум 20 МБ."},
                status_code=413,
            )
        if len(content) < 4:
            return JSONResponse(
                {"error": "file_empty", "message": "Файл пустой или повреждён."},
                status_code=422,
            )

        detected_type = _sniff_mime(content[:16])
        raw_name = (file.filename or "").strip()
        # Mobile browsers sometimes send empty name or 'blob' with no extension
        if not raw_name or raw_name.lower() in ("blob", "image", "file"):
            raw_name = "upload"
        filename = raw_name
        ext = os.path.splitext(filename)[1].lower() or ""

        if detected_type is None:
            if ext in _ALLOWED_IMAGE_EXTS:
                detected_type = "image"
            elif ext in _ALLOWED_PDF_EXTS:
                detected_type = "pdf"
            elif file.content_type and "pdf" in file.content_type:
                detected_type = "pdf"
                ext = ".pdf"
            elif file.content_type and file.content_type.startswith("image/"):
                detected_type = "image"
                ext = ".jpg"
            else:
                return JSONResponse(
                    {"error": "unsupported_format",
                     "message": "Формат не поддерживается. Загрузите PNG, JPG, WEBP или PDF."},
                    status_code=422,
                )

        safe_ext = ext if ext else (".pdf" if detected_type == "pdf" else ".png")
        tmp_path = os.path.join(tempfile.gettempdir(), f"anyk_raw_{uuid.uuid4().hex}{safe_ext}")
        with open(tmp_path, "wb") as f:
            f.write(content)
        logger.info("Upload: %s (%d bytes, type=%s)", filename, len(content), detected_type)

        try:
            report = _run_analysis(tmp_path, detected_type, filename)
        except ValueError as e:
            return JSONResponse({"error": "invalid_file", "message": str(e)}, status_code=422)

        _reports[report["report_id"]] = report
        return JSONResponse(report)

    except Exception as e:
        logger.exception("Analysis failed")
        return JSONResponse({"error": "analysis_failed", "message": str(e)}, status_code=500)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
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
    report = _reports.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    lines = [
        "АНЫҚ — Отчёт об анализе платёжного документа",
        "=" * 52,
        f"ID отчёта:     {report['report_id']}",
        f"Дата:          {report['timestamp']}",
        f"Файл:          {report['image_filename']}",
        f"Тип источника: {report.get('source_type', 'image')}",
        f"Метод анализа: {', '.join(report.get('analysis_methods', []))}",
        f"Уровень риска: {report['risk_level_ru']} ({report['risk_level'].upper()})",
        f"Оценка доверия: {report.get('trust_score', 0)}%  ({report.get('trust_label', '')})",
        f"Время анализа: {report.get('elapsed_ms', 0):.1f} мс",
        "",
    ]

    if report.get("pdf_metadata"):
        m = report["pdf_metadata"]
        lines += [
            "── Метаданные PDF ──",
            f"  Автор:          {m.get('author') or '(нет)'}",
            f"  Producer:       {m.get('producer') or '(нет)'}",
            f"  Дата создания:  {m.get('creation_date') or '(нет)'}",
            f"  Дата изменения: {m.get('mod_date') or '(нет)'}",
            "",
        ]

    lines += [
        f"── Найдено сигналов: {report['signal_count']} ──",
        "-" * 52,
    ]

    for s in report.get("signals", []):
        lines.append(f"[{s.get('severity','?').upper()}] {s.get('signal_id','?')}: {s.get('title_ru','')}")
        lines.append(f"  {s.get('description_ru','')}")
        lines.append(f"  Доказательство: \"{s.get('evidence_text','')}\"")
        lines.append(f"  Уверенность: {s.get('confidence','?')} · Метод: {s.get('method','?')}")
        lines.append("")

    lines.append(report.get("disclaimer_ru", ""))

    return PlainTextResponse(
        content="\n".join(lines),
        headers={"Content-Disposition": f"attachment; filename=anyk_report_{report_id[:8]}.txt"},
    )
