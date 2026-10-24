import uuid
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_DISCLAIMER = (
    "Система выявляет признаки несоответствия в тексте документа. "
    "Система не устанавливает факт транзакции и не является судебным "
    "экспертным инструментом. Окончательное решение принимает аналитик."
)

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def build_report(
    ocr_result: dict,
    structural_signals: list[dict],
    llm_signals: list[dict],
    risk: dict,
    context: str = "",
    image_filename: str = "",
    pdf_info: dict | None = None,
) -> dict:
    all_signals = structural_signals + llm_signals
    all_signals.sort(key=lambda s: _SEVERITY_ORDER.get(s.get("severity", "low"), 2))

    methods = [ocr_result.get("source", "ocr")]
    if structural_signals:
        methods.append("structural_check")
    if llm_signals:
        methods.append("llm_consistency")

    source_type = "pdf" if pdf_info else "image"
    ocr_source = ocr_result.get("source", "ocr")

    # Trust Index (with Stage-1 gatekeeper)
    from analyzer.document_gatekeeper import check_document
    from analyzer.trust_scorer import calculate_trust
    gatekeeper = check_document(ocr_result)
    trust = calculate_trust(all_signals, ocr_result, pdf_info,
                            gatekeeper_result=gatekeeper)

    report = {
        "report_id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "image_filename": image_filename,
        "source_type": source_type,
        "ocr_source": ocr_source,
        "risk_level": risk["level"],
        "risk_level_ru": risk["level_ru"],
        "risk_score": risk["score"],
        "trust_score": trust["trust_score"],
        "trust_label": trust["trust_label"],
        "trust_label_en": trust["trust_label_en"],
        "trust_explanation": trust["trust_explanation"],
        "trust_disclaimer": trust["trust_disclaimer"],
        "signals": all_signals,
        "ocr_text": ocr_result.get("raw_text", ""),
        "ocr_confidence": ocr_result.get("mean_confidence", 0.0),
        "signal_count": len(all_signals),
        "context_provided": bool(context and context.strip()),
        "disclaimer_ru": _DISCLAIMER,
        "analysis_methods": methods,
    }

    # PDF-specific metadata
    if pdf_info:
        report["pdf_metadata"] = pdf_info.get("metadata", {})
        report["pdf_page_count"] = pdf_info.get("page_count", 1)
        report["pdf_pages_processed"] = pdf_info.get("pages_processed", 1)
        report["pdf_has_text_layer"] = pdf_info.get("has_text_layer", False)
        report["pdf_warnings"] = pdf_info.get("warnings", [])

    logger.info(
        "Report built: id=%s risk=%s signals=%d source=%s",
        report["report_id"], report["risk_level"], report["signal_count"], source_type,
    )
    return report
