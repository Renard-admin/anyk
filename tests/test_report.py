import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.risk_scorer import calculate_risk
from analyzer.report_builder import build_report


# ── risk scorer ─────────────────────────────────────────────────────────────

def test_risk_no_signals():
    r = calculate_risk([])
    assert r["level"] == "clear"
    assert r["score"] == 0
    assert r["signal_count"] == 0


def test_risk_high_signal():
    signals = [{"severity": "high", "signal_id": "DATE_IMPOSSIBLE"}]
    r = calculate_risk(signals)
    assert r["score"] == 30
    assert r["level"] == "medium"  # 30 < 40


def test_risk_two_high_signals():
    signals = [
        {"severity": "high", "signal_id": "DATE_IMPOSSIBLE"},
        {"severity": "high", "signal_id": "AMOUNT_UNREALISTIC_HIGH"},
    ]
    r = calculate_risk(signals)
    assert r["score"] == 60
    assert r["level"] == "high"


def test_risk_medium_signals():
    signals = [{"severity": "medium", "signal_id": "CASINO_KEYWORDS_DETECTED"}] * 3
    r = calculate_risk(signals)
    assert r["score"] == 45
    assert r["level"] == "high"


def test_risk_uncertain_overrides():
    signals = [
        {"severity": "high", "signal_id": "DATE_IMPOSSIBLE"},
        {"severity": "low", "signal_id": "OCR_LOW_QUALITY"},
    ]
    r = calculate_risk(signals)
    assert r["level"] == "uncertain"


def test_risk_level_ru():
    r = calculate_risk([])
    assert r["level_ru"] == "Признаки не обнаружены"
    r2 = calculate_risk([{"severity": "high", "signal_id": "X"}, {"severity": "high", "signal_id": "Y"}])
    assert r2["level_ru"] == "Сильные сигналы"


# ── report builder ──────────────────────────────────────────────────────────

def _make_ocr(text="Kaspi.kz Перевод 5000 тг"):
    return {"raw_text": text, "regions": [], "mean_confidence": 0.85, "word_count": len(text.split())}


def _make_signal(sid, severity, category="structural"):
    return {
        "signal_id": sid, "category": category, "severity": severity,
        "title_ru": "Тест", "description_ru": "Тестовый сигнал",
        "evidence_text": "цитата", "confidence": "high",
        "method": "test",
    }


def test_report_end_to_end():
    ocr = _make_ocr()
    structural = [_make_signal("DATE_IMPOSSIBLE", "high")]
    llm = [_make_signal("AMOUNT_CONTEXT_MISMATCH", "medium", "semantic")]
    risk = calculate_risk(structural + llm)
    report = build_report(ocr, structural, llm, risk, context="тест", image_filename="test.png")

    assert report["risk_level"] == "high"  # 30+15=45 >= 40 → high
    assert report["signal_count"] == 2
    assert report["context_provided"] is True
    assert report["image_filename"] == "test.png"
    assert "report_id" in report
    assert "timestamp" in report
    assert report["disclaimer_ru"].startswith("Система выявляет")
    assert "structural_check" in report["analysis_methods"]
    assert "llm_consistency" in report["analysis_methods"]


def test_report_signals_sorted_by_severity():
    ocr = _make_ocr()
    structural = [
        _make_signal("LOW_SIG", "low"),
        _make_signal("HIGH_SIG", "high"),
        _make_signal("MED_SIG", "medium"),
    ]
    risk = calculate_risk(structural)
    report = build_report(ocr, structural, [], risk)
    severities = [s["severity"] for s in report["signals"]]
    assert severities == ["high", "medium", "low"]


def test_report_no_context():
    ocr = _make_ocr()
    risk = calculate_risk([])
    report = build_report(ocr, [], [], risk, context="")
    assert report["context_provided"] is False
    assert report["risk_level"] == "clear"
    assert report["signal_count"] == 0


def test_report_uncertain():
    ocr = _make_ocr()
    signals = [_make_signal("OCR_LOW_QUALITY", "low")]
    risk = calculate_risk(signals)
    report = build_report(ocr, signals, [], risk)
    assert report["risk_level"] == "uncertain"


if __name__ == "__main__":
    import subprocess
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], cwd=base)
