import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

api_key = os.environ.get("GEMINI_API_KEY") or ""
needs_api = pytest.mark.skipif(not api_key, reason="GEMINI_API_KEY not set")

from analyzer.llm_analyzer import analyze_consistency, _parse_signals, _validate_signals


# ── offline unit tests (no API needed) ─────────────────────────────────────

def test_empty_ocr_returns_empty():
    result = analyze_consistency({"raw_text": "", "regions": [], "mean_confidence": 0.0, "word_count": 0})
    assert result == []


def test_parse_signals_valid_json():
    raw = '[{"signal_id":"TEST","severity":"high","title_ru":"Тест","description_ru":"Desc","evidence_text":"цитата","confidence":"high"}]'
    signals = _parse_signals(raw)
    assert len(signals) == 1
    assert signals[0]["signal_id"] == "TEST"
    assert signals[0]["category"] == "semantic"
    assert signals[0]["method"] == "llm_consistency_check"


def test_parse_signals_with_surrounding_text():
    raw = 'Вот результат:\n[{"signal_id":"X","severity":"low","title_ru":"T","description_ru":"D","evidence_text":"e","confidence":"low"}]\nГотово.'
    signals = _parse_signals(raw)
    assert len(signals) == 1


def test_parse_signals_empty_array():
    assert _parse_signals("[]") == []


def test_validate_drops_signal_without_evidence():
    signals = [{"signal_id": "X", "severity": "high", "title_ru": "T",
                "description_ru": "D", "evidence_text": "", "confidence": "high"}]
    result = _validate_signals(signals)
    assert result == []


def test_validate_normalizes_bad_severity():
    signals = [{"signal_id": "X", "severity": "EXTREME", "title_ru": "T",
                "description_ru": "D", "evidence_text": "цитата", "confidence": "high"}]
    result = _validate_signals(signals)
    assert result[0]["severity"] == "low"


# ── live API tests ──────────────────────────────────────────────────────────

@needs_api
def test_name_mismatch_detected():
    """Spec test: sender name in screenshot doesn't match context."""
    ocr = {
        "raw_text": "Перевод 5000 тг. Иван Петров. 15 января.",
        "regions": [],
        "mean_confidence": 0.9,
        "word_count": 7,
    }
    context = "Алмас написал: скинул деньги"
    signals = analyze_consistency(ocr, context)
    ids = [s["signal_id"] for s in signals]
    # Should find some mismatch signal (name or sender)
    assert len(signals) > 0, f"Expected at least one signal for name mismatch, got none"
    # All signals must have evidence_text
    for s in signals:
        assert s["evidence_text"].strip(), f"Signal {s['signal_id']} has no evidence_text"
    # No forbidden words
    for s in signals:
        for forbidden in ("мошенничество", "подделка", "фейк", "обман"):
            assert forbidden not in s["description_ru"].lower(), \
                f"Forbidden word '{forbidden}' found in signal {s['signal_id']}"


@needs_api
def test_consistent_data_no_signals():
    """Clean matching data should return no signals."""
    ocr = {
        "raw_text": "Kaspi.kz Перевод выполнен Сумма: 5000 тг Отправитель: Алмас Получатель: Динара Дата: 15 января 2024",
        "regions": [],
        "mean_confidence": 0.9,
        "word_count": 14,
    }
    context = "Алмас написал: скинул 5000 тенге Динаре"
    signals = analyze_consistency(ocr, context)
    # May return 0 or very few low-confidence signals
    high_signals = [s for s in signals if s["severity"] == "high"]
    assert len(high_signals) == 0, f"Unexpected high signals on clean data: {high_signals}"


@needs_api
def test_amount_mismatch_detected():
    """Spec example: 25000 in screenshot vs 5000 in context."""
    ocr = {
        "raw_text": "Kaspi.kz Перевод выполнен Сумма: 25 000 ₸ Отправитель: Асель Нурланова Получатель: Иван Петров Дата: 14 марта 2024 г. Время: 09:15",
        "regions": [],
        "mean_confidence": 0.95,
        "word_count": 20,
    }
    context = "Марат: я скинул тебе 5000 тенге за билет\nДинара: окей, жду скриншот\nМарат: вот чек"
    signals = analyze_consistency(ocr, context)
    ids = [s["signal_id"] for s in signals]
    # Should detect amount and/or name mismatch
    assert len(signals) >= 1, f"Expected signals for amount/name mismatch, got none"


if __name__ == "__main__":
    import subprocess
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], cwd=base)
