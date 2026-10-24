# АНЫҚ — Анализатор платёжных документов
## SafeNet KZ Hackathon 2026

АНЫҚ — инструмент предварительного анализа платёжных скриншотов и PDF-чеков (Kaspi.kz и аналогов) для выявления текстуальных несоответствий: невозможных дат, нереальных сумм, казино-ключевых слов и структурных аномалий. Система помогает аналитику быстро обнаружить сигналы подозрительного контента, не устанавливая факт мошенничества.

Решение представлено на треках «Розыгрыши», «Казино» и «Финансовые пирамиды» SafeNet KZ.

## Быстрый старт

```bash
git clone <repo-url>
cd anyk
pip install -r requirements.txt
cp .env.example .env  # добавь GEMINI_API_KEY (опционально)
uvicorn main:app --reload
# Открой http://localhost:8000
```

> Без `GEMINI_API_KEY` система работает на structural-проверках (без LLM).

## Поддерживаемые форматы

| Формат | Метод извлечения текста |
|---|---|
| PNG / JPG / WEBP | OCR (EasyOCR, ru+en) |
| PDF (image-only, напр. Kaspi.kz iOS) | Рендеринг страницы → OCR |
| PDF (с текстовым слоем) | Прямое извлечение + OCR |

**Ограничения:** максимум 20 МБ, до 5 страниц PDF.

## Способы загрузки

- **Файловый диалог** — нажмите на зону загрузки
- **Drag & drop** — перетащите файл в зону
- **Ctrl+V / Cmd+V** — вставьте скопированный скриншот из буфера обмена
- **Мобильная камера** — кнопка «Сфотографировать» на смартфонах

## Что делает

- **OCR** — EasyOCR (ru + en, кириллица)
- **Structural check** — 5 типов детерминированных проверок:
  - Невозможные / будущие даты
  - Нереальные суммы (< 10 ₸ или > 50 млн ₸)
  - Нестандартный формат телефона (не KZ)
  - Ключевые слова казино / ставок
  - Ключевые слова пирамид / розыгрышей
- **LLM-анализ** — Gemini 2.0 Flash Lite: семантическая согласованность с контекстом переписки
- **PDF metadata** — Author, Producer, CreationDate / ModDate
- **Risk scoring** — CLEAR / MEDIUM / HIGH / UNCERTAIN
- **Подробный отчёт** — сигналы, доказательства, метаданные, ограничения, следующие шаги
- **Экспорт TXT** — через `/report/{id}/export`

## Что НЕ делает (ограничения)

- Не устанавливает, произошла ли транзакция на самом деле
- Не определяет, является ли изображение сгенерированным AI или отредактированным
- Не хранит данные между сессиями (in-memory хранилище, MVP)
- Наличие Author=Kaspi.kz или корректных дат не является доказательством подлинности

## Стек

Python · FastAPI · EasyOCR · PyMuPDF · Gemini API (Google) · Pillow · OpenCV · Vanilla JS

## Метрики (синтетический датасет, без LLM)

| Метрика | Значение |
|---|---|
| Precision | **1.00** |
| Recall | **0.80** |
| False Positive Rate | **0.00** |
| Accuracy | **0.88** |
| Датасет | 30 изображений (10 consistent / 15 inconsistent / 5 neutral) |

> FN=3 — чеки с одинаковым timestamp (требует контекста нескольких чеков).

## Тесты

```bash
cd anyk
venv/Scripts/python -m pytest tests/test_structural.py tests/test_pdf.py -v
```

Результаты на реальных данных: **22/22 passed**.

Тесты покрывают:
- 13 unit-тестов structural_checker (даты, суммы, казино, пирамиды, телефон)
- 4 теста валидации PDF (пустой, неверная сигнатура, слишком большой, корректный)
- 3 теста на реальных PDF-образцах Kaspi.kz (requires `data/samples/`)
- 1 тест report_builder с PDF metadata
- 1 тест magic-byte detection

## Структура проекта

```
anyk/
├── main.py                     # FastAPI: эндпоинты /analyze, /report/{id}, /report/{id}/export
├── analyzer/
│   ├── pdf_ingestor.py         # PDF: валидация, рендеринг страниц, метаданные
│   ├── preprocessor.py         # Pillow + OpenCV CLAHE
│   ├── ocr_engine.py           # EasyOCR singleton
│   ├── structural_checker.py   # 5 типов детерминированных проверок
│   ├── llm_analyzer.py         # Gemini 2.0 Flash Lite, strict JSON schema
│   ├── risk_scorer.py          # CLEAR/MEDIUM/HIGH/UNCERTAIN
│   └── report_builder.py       # Сборка отчёта + PDF metadata
├── templates/
│   ├── index.html              # Загрузка, paste, мобильная камера
│   └── result.html             # Отчёт: сигналы, метаданные PDF, таблица проверок
├── static/
│   ├── style.css               # Адаптивный (320–1440px), тёмная тема через CSS vars
│   └── app.js                  # Paste, drag&drop, PDF preview, мобиль
├── tests/
│   ├── test_structural.py      # 13 тестов
│   ├── test_pdf.py             # 9 тестов (новые)
│   └── evaluate.py             # Оценка на датасете
└── data/
    ├── samples/                # PDF-образцы (не коммитить реальные чеки)
    └── test_dataset/           # 30 синтетических изображений + labels.json
```

## Треки SafeNet KZ

✅ Трек 1: Розыгрыши · ✅ Трек 2: Казино · ✅ Трек 3: Финансовые пирамиды
