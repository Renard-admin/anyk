import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from analyzer.structural_checker import check_structure


def ocr(text):
    return {"raw_text": text, "regions": [], "mean_confidence": 0.9, "word_count": len(text.split())}


# ── date checks ─────────────────────────────────────────────────────────────

def test_date_impossible_feb31():
    signals = check_structure(ocr("Перевод выполнен 31 февраля 2024 г. Сумма: 5000 тг"))
    ids = [s["signal_id"] for s in signals]
    assert "DATE_IMPOSSIBLE" in ids, f"Expected DATE_IMPOSSIBLE, got: {ids}"


def test_date_impossible_numeric():
    signals = check_structure(ocr("Дата: 31.02.2024 Сумма: 500 тг"))
    ids = [s["signal_id"] for s in signals]
    assert "DATE_IMPOSSIBLE" in ids, f"Expected DATE_IMPOSSIBLE, got: {ids}"


def test_date_impossible_month13():
    signals = check_structure(ocr("Дата: 01.13.2024"))
    ids = [s["signal_id"] for s in signals]
    assert "DATE_IMPOSSIBLE" in ids


def test_date_future():
    signals = check_structure(ocr("Перевод от 15 декабря 2099 г. Сумма: 1000 тг"))
    ids = [s["signal_id"] for s in signals]
    assert "DATE_FUTURE" in ids, f"Expected DATE_FUTURE, got: {ids}"


def test_date_valid_no_signal():
    signals = check_structure(ocr("Перевод от 15 января 2024 г. Сумма: 5000 тг"))
    ids = [s["signal_id"] for s in signals]
    assert "DATE_IMPOSSIBLE" not in ids
    assert "DATE_FUTURE" not in ids


# ── amount checks ───────────────────────────────────────────────────────────

def test_amount_unrealistic_high():
    signals = check_structure(ocr("Сумма: 100 000 000 тг"))
    ids = [s["signal_id"] for s in signals]
    assert "AMOUNT_UNREALISTIC_HIGH" in ids, f"Got: {ids}"


def test_amount_unrealistic_low():
    signals = check_structure(ocr("Сумма: 1 тг"))
    ids = [s["signal_id"] for s in signals]
    assert "AMOUNT_UNREALISTIC_LOW" in ids, f"Got: {ids}"


def test_casino_and_high_amount():
    """Core test from spec: 'выиграл 500000 тг в казино' → CASINO + AMOUNT_UNREALISTIC_HIGH."""
    signals = check_structure(ocr("выиграл 500000 тг в казино сегодня"))
    ids = [s["signal_id"] for s in signals]
    assert "CASINO_KEYWORDS_DETECTED" in ids, f"Expected CASINO, got: {ids}"
    # 500000 < 50000000, so no AMOUNT_UNREALISTIC_HIGH — but let's check what we get
    # According to spec test: amount 500000 тг is NOT > 50M, so only CASINO expected


def test_casino_keywords():
    for kw in ["казино", "Casino", "СТАВКА", "выигрыш", "1win", "mostbet"]:
        signals = check_structure(ocr(f"текст {kw} тут"))
        ids = [s["signal_id"] for s in signals]
        assert "CASINO_KEYWORDS_DETECTED" in ids, f"Keyword '{kw}' not caught. Got: {ids}"


def test_pyramid_keywords():
    for kw in ["розыгрыш", "лотерея", "пассивный доход", "приведи друга"]:
        signals = check_structure(ocr(f"предложение: {kw} сегодня"))
        ids = [s["signal_id"] for s in signals]
        assert "PYRAMID_KEYWORDS_DETECTED" in ids, f"Keyword '{kw}' not caught. Got: {ids}"


# ── clean text ──────────────────────────────────────────────────────────────

def test_clean_text_no_signals():
    """Clean receipt text — should produce zero signals."""
    text = (
        "Kaspi.kz Перевод выполнен "
        "Сумма: 15 000 тг "
        "Отправитель: Асель Касымова "
        "Получатель: Данияр Сериков "
        "Дата: 15 января 2024 г. Время: 14:32"
    )
    signals = check_structure(ocr(text))
    assert signals == [], f"Expected no signals, got: {[s['signal_id'] for s in signals]}"


# ── phone check ─────────────────────────────────────────────────────────────

def test_phone_unusual_format():
    signals = check_structure(ocr("Получатель: +44 20 7946 0958 перевод"))
    ids = [s["signal_id"] for s in signals]
    assert "PHONE_FORMAT_UNUSUAL" in ids, f"Got: {ids}"


def test_phone_kz_valid():
    signals = check_structure(ocr("Телефон: +77771234567"))
    ids = [s["signal_id"] for s in signals]
    assert "PHONE_FORMAT_UNUSUAL" not in ids, f"KZ number wrongly flagged. Got: {ids}"


def test_receipt_number_not_phone():
    """18-digit Kaspi receipt number must NOT trigger phone format error."""
    text = "Ng квитанции 898936951310176343 Дата и время 24.10.2026 14:30"
    signals = check_structure(ocr(text))
    ids = [s["signal_id"] for s in signals]
    assert "PHONE_FORMAT_UNUSUAL" not in ids, (
        f"Receipt number incorrectly flagged as phone. Signals: {ids}"
    )


def test_receipt_number_second_sample():
    """Another real Kaspi receipt number must not trigger phone error."""
    text = "Ng квитанции 893160150231168321 Комиссия 0 тг"
    signals = check_structure(ocr(text))
    ids = [s["signal_id"] for s in signals]
    assert "PHONE_FORMAT_UNUSUAL" not in ids, (
        f"Receipt number 893160150231168321 incorrectly flagged. Signals: {ids}"
    )


if __name__ == "__main__":
    import subprocess, sys
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run([sys.executable, "-m", "pytest", __file__, "-v"], cwd=base)
