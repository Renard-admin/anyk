import logging

logger = logging.getLogger(__name__)

_SCORE_MAP = {"high": 30, "medium": 15, "low": 5}

_UNCERTAIN_SIGNALS = {"OCR_LOW_QUALITY", "UNKNOWN_TEMPLATE"}

_LEVEL_RU = {
    "clear": "Признаки не обнаружены",
    "medium": "Обнаружены сигналы",
    "high": "Сильные сигналы",
    "uncertain": "Недостаточно данных",
}


def calculate_risk(signals: list[dict]) -> dict:
    signal_ids = {s.get("signal_id", "") for s in signals}

    if signal_ids & _UNCERTAIN_SIGNALS:
        level = "uncertain"
        score = 0
    else:
        score = sum(_SCORE_MAP.get(s.get("severity", "low"), 0) for s in signals)
        if score == 0:
            level = "clear"
        elif score < 40:
            level = "medium"
        else:
            level = "high"

    high_count = sum(1 for s in signals if s.get("severity") == "high")
    medium_count = sum(1 for s in signals if s.get("severity") == "medium")

    result = {
        "level": level,
        "level_ru": _LEVEL_RU[level],
        "score": score,
        "signal_count": len(signals),
        "high_count": high_count,
        "medium_count": medium_count,
    }
    logger.info("Risk: level=%s score=%d signals=%d", level, score, len(signals))
    return result
