import re
import json
import time
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Ты — инструмент предварительного анализа документов. Твоя единственная задача — найти текстовые и смысловые несоответствия в данных, извлечённых из скриншота предполагаемого платёжного документа.

РОЛЬ: Аналитик, выявляющий признаки. Не судья, не детектор мошенничества.

ЖЁСТКИЕ ПРАВИЛА:
1. Анализируй ТОЛЬКО то, что явно написано в предоставленных текстах.
2. Каждый signal_id должен содержать цитату evidence_text — дословный фрагмент из входных данных. Если цитаты нет — сигнал не создавай.
3. НЕ интерпретируй отсутствие информации как несоответствие.
4. НЕ используй формулировки «мошенничество», «подделка», «фейк», «обман» — только «несоответствие», «нетипично», «требует проверки».
5. Если несоответствий нет — верни пустой массив [].
6. Отвечай ТОЛЬКО валидным JSON. Никакого вводного текста, объяснений или markdown.
7. Анализируй текст на русском, казахском и смешанном языке одинаково.

ЧТО ИСКАТЬ (только при наличии явных доказательств в тексте):
- Имя отправителя или получателя в скриншоте противоречит имени в контексте переписки
- Сумма в скриншоке не совпадает с суммой, упомянутой в контексте
- Дата или время в скриншоте противоречит датировке контекста
- Назначение или описание платежа противоречит смыслу переписки
- Язык документа неожиданно переключается внутри одного поля
- Текст содержит признаки продвижения азартных игр, нереалистичной доходности или схем вовлечения

ФОРМАТ ОТВЕТА — только JSON-массив:
[
  {
    "signal_id": "SNAKE_CASE_IDENTIFIER",
    "severity": "low|medium|high",
    "title_ru": "Краткое название (до 6 слов)",
    "description_ru": "Конкретное объяснение несоответствия (1-2 предложения)",
    "evidence_text": "дословная цитата из входного текста",
    "confidence": "low|medium|high"
  }
]"""

USER_PROMPT_TEMPLATE = """OCR-ТЕКСТ СКРИНШОТА:
---
{ocr_text}
---

КОНТЕКСТ ПЕРЕПИСКИ:
---
{context_text}
---

Найди смысловые несоответствия между данными скриншота и контекстом переписки. Верни JSON-массив. Если несоответствий нет или контекст не предоставлен — верни []."""

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-1.5-flash:generateContent"
)


def analyze_consistency(ocr_result: dict, context: str = "") -> list[dict]:
    ocr_text = ocr_result.get("raw_text", "").strip()
    context_text = context.strip() if context.strip() else "Контекст не предоставлен."

    if not ocr_text:
        logger.warning("LLM analyzer: empty OCR text, skipping")
        return []

    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        logger.warning("GEMINI_API_KEY not set, skipping LLM analysis")
        return []

    user_prompt = USER_PROMPT_TEMPLATE.format(
        ocr_text=ocr_text,
        context_text=context_text,
    )

    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 1024,
        },
    }

    start = time.time()
    try:
        import httpx
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                _GEMINI_URL,
                headers={"X-goog-api-key": key, "Content-Type": "application/json"},
                json=payload,
            )
        resp.raise_for_status()
        data = resp.json()
        raw = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        elapsed = time.time() - start
        logger.info("Gemini response in %.2fs, len=%d", elapsed, len(raw))
    except Exception as e:
        logger.error("Gemini API error: %s", e)
        return []

    return _parse_signals(raw)


def _parse_signals(raw: str) -> list[dict]:
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())

    # Level 1: direct parse
    try:
        signals = json.loads(raw)
        if isinstance(signals, list):
            return _validate_signals(signals)
        if isinstance(signals, dict):
            return _validate_signals([signals])
    except json.JSONDecodeError:
        pass

    # Level 2: extract JSON array from surrounding text
    match = re.search(r'\[.*?\]', raw, re.DOTALL)
    if match:
        try:
            signals = json.loads(match.group())
            if isinstance(signals, list):
                logger.warning("LLM returned text+JSON, extracted via regex")
                return _validate_signals(signals)
        except json.JSONDecodeError:
            pass

    # Level 3: recover individual objects
    objects = re.findall(r'\{[^{}]+\}', raw, re.DOTALL)
    if objects:
        signals = []
        for obj_str in objects:
            try:
                signals.append(json.loads(obj_str))
            except json.JSONDecodeError:
                continue
        if signals:
            logger.warning("LLM: recovered %d objects from invalid JSON", len(signals))
            return _validate_signals(signals)

    logger.error("LLM returned unparseable response:\n%s", raw[:300])
    return []


def _validate_signals(signals: list) -> list[dict]:
    valid = []
    valid_severities = {"low", "medium", "high"}
    valid_confidences = {"low", "medium", "high"}

    for s in signals:
        if not isinstance(s, dict):
            continue
        if not s.get("evidence_text", "").strip():
            logger.warning("Discarding signal without evidence_text: %s", s.get("signal_id"))
            continue
        s.setdefault("signal_id", "UNKNOWN_SIGNAL")
        s.setdefault("severity", "low")
        s.setdefault("confidence", "low")
        s.setdefault("title_ru", "Несоответствие")
        s.setdefault("description_ru", "")
        if s["severity"] not in valid_severities:
            s["severity"] = "low"
        if s["confidence"] not in valid_confidences:
            s["confidence"] = "low"
        s["category"] = "semantic"
        s["method"] = "llm_consistency_check"
        valid.append(s)

    return valid
