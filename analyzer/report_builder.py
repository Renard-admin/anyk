import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DISCLAIMER = (
    "Система выявляет признаки несоответствия в структуре документа. "
    "Она не устанавливает факт транзакции и не является судебным "
    "экспертным инструментом. Окончательное решение принимает аналитик."
)

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def build_report(
    template_result: dict,
    risk: dict,
    image_filename: str = "",
    pdf_info: dict | None = None,
) -> dict:
    """
    Build the final report dict from template_matcher output + risk score.

    Args:
        template_result: output of analyzer.template_matcher.match_template()
        risk:            output of analyzer.risk_scorer.calculate_risk()
        image_filename:  original uploaded filename
        pdf_info:        output of analyzer.pdf_ingestor.ingest_pdf() or None for images
    """
    signals = sorted(
        template_result.get("signals", []),
        key=lambda s: _SEVERITY_ORDER.get(s.get("severity", "low"), 2),
    )

    source_type = "pdf" if pdf_info else "image"
    checks_run = template_result.get("checks_run", [])

    report = {
        "report_id":        str(uuid.uuid4()),
        "timestamp":        datetime.now(timezone.utc).isoformat(),
        "image_filename":   image_filename,
        "source_type":      source_type,
        "ocr_source":       "template_matching",
        "risk_level":       risk["level"],
        "risk_level_ru":    risk["level_ru"],
        "risk_score":       risk["score"],
        "trust_score":      template_result.get("trust_score", 0),
        "trust_label":      template_result.get("trust_label", ""),
        "trust_label_en":   template_result.get("trust_label_en", ""),
        "trust_explanation": template_result.get("trust_explanation", ""),
        "trust_disclaimer": template_result.get("trust_disclaimer", _DISCLAIMER),
        "signals":          signals,
        "ocr_text":         "",
        "ocr_confidence":   1.0,
        "signal_count":     len(signals),
        "context_provided": False,
        "disclaimer_ru":    _DISCLAIMER,
        "analysis_methods": checks_run,
        "elapsed_ms":       template_result.get("elapsed_ms", 0),
    }

    if pdf_info:
        report["pdf_metadata"]        = pdf_info.get("metadata", {})
        report["pdf_page_count"]      = pdf_info.get("page_count", 1)
        report["pdf_pages_processed"] = pdf_info.get("pages_processed", 1)
        report["pdf_has_text_layer"]  = pdf_info.get("has_text_layer", False)
        report["pdf_warnings"]        = pdf_info.get("warnings", [])

    logger.info(
        "Report built: id=%s risk=%s trust=%d%% signals=%d elapsed=%.1fms",
        report["report_id"], report["risk_level"],
        report["trust_score"], report["signal_count"],
        report["elapsed_ms"],
    )
    return report
