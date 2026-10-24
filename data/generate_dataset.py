"""
Synthetic Kaspi receipt dataset generator.
Produces 30 PNG images + labels.json in data/test_dataset/.

Run from anyk/ directory:
    venv/Scripts/python data/generate_dataset.py
"""

import json
import os
import sys

# Resolve paths relative to this file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "test_dataset", "images")
LABELS_PATH = os.path.join(SCRIPT_DIR, "test_dataset", "labels.json")
os.makedirs(OUTPUT_DIR, exist_ok=True)

from PIL import Image, ImageDraw, ImageFont

# ── Font loading ────────────────────────────────────────────────────────────
# Must use TTF — PIL bitmap font gives unreadable glyphs for EasyOCR

def _load_font(size: int, bold: bool = False):
    candidates = [
        (r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
        r"C:\Windows\Fonts\calibrib.ttf" if bold else r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\times.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    print(f"WARNING: No TTF font found for size={size} bold={bold}, using bitmap default")
    return ImageFont.load_default()


# Preload fonts once
_FONT_HEADER   = _load_font(22, bold=True)
_FONT_STATUS   = _load_font(16)
_FONT_LABEL    = _load_font(14)
_FONT_VALUE    = _load_font(15)
_FONT_AMOUNT   = _load_font(18, bold=True)
_FONT_FOOTER   = _load_font(11)

# ── Receipt renderer ────────────────────────────────────────────────────────

def create_kaspi_receipt_image(params: dict) -> Image.Image:
    sender   = params.get("sender", "Неизвестно")
    receiver = params.get("receiver", "Неизвестно")
    amount   = params.get("amount", "0 тг")
    date     = params.get("date", "01 января 2024 г.")
    time_str = params.get("time", "12:00")
    status   = params.get("status", "Перевод выполнен")

    W, H = 400, 600
    img = Image.new("RGB", (W, H), "#FFFFFF")
    draw = ImageDraw.Draw(img)

    # Header background
    draw.rectangle([0, 0, W, 80], fill="#E8F4FD")
    # "Kaspi.kz" centered in header
    _draw_centered(draw, "Kaspi.kz", W // 2, 40, _FONT_HEADER, "#1B4F72")

    # Status line
    _draw_centered(draw, status, W // 2, 105, _FONT_STATUS, "#1A7A4A")

    # Separator
    draw.line([24, 125, W - 24, 125], fill="#DDE2EA", width=1)

    # Fields
    fields = [
        ("Сумма:",        amount,   _FONT_AMOUNT,  "#1A1A2E"),
        ("Отправитель:",  sender,   _FONT_VALUE,   "#1A1A2E"),
        ("Получатель:",   receiver, _FONT_VALUE,   "#1A1A2E"),
        ("Дата:",         date,     _FONT_VALUE,   "#1A1A2E"),
        ("Время:",        time_str, _FONT_VALUE,   "#1A1A2E"),
    ]
    y = 145
    for label, value, val_font, val_color in fields:
        draw.text((28, y), label, font=_FONT_LABEL, fill="#9AA3B2")
        draw.text((28, y + 20), value, font=val_font, fill=val_color)
        y += 72

    # Separator
    draw.line([24, y + 5, W - 24, y + 5], fill="#DDE2EA", width=1)

    # Footer
    _draw_centered(draw, "Чек является подтверждением операции",
                   W // 2, H - 30, _FONT_FOOTER, "#9AA3B2")

    return img


def _draw_centered(draw, text, cx, cy, font, color):
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((cx - tw // 2, cy - th // 2), text, font=font, fill=color)


def _neutral_image(text_lines: list[str]) -> Image.Image:
    """Simple chat-like text block (no payment data)."""
    W, H = 400, 300
    img = Image.new("RGB", (W, H), "#F0F2F5")
    draw = ImageDraw.Draw(img)
    y = 20
    for line in text_lines:
        draw.text((20, y), line, font=_FONT_VALUE, fill="#1A1A2E")
        y += 28
    return img


# ── Dataset definitions ─────────────────────────────────────────────────────

CONSISTENT_SAMPLES = [
    dict(sender="Асель Касымова",   receiver="Данияр Сериков",  amount="15 000 тг", date="15 января 2024 г.",   time="14:32", status="Перевод выполнен"),
    dict(sender="Марат Джаксыбеков",receiver="Айгерим Нурова",  amount="5 000 тг",  date="03 февраля 2024 г.",  time="09:15", status="Перевод выполнен"),
    dict(sender="Иван Петров",       receiver="Сергей Козлов",   amount="22 500 тг", date="20 марта 2024 г.",    time="18:44", status="Перевод выполнен"),
    dict(sender="Динара Абенова",    receiver="Нурлан Сейткали", amount="7 800 тг",  date="11 апреля 2024 г.",   time="11:02", status="Перевод выполнен"),
    dict(sender="Алексей Смирнов",   receiver="Ольга Иванова",   amount="500 тг",    date="05 мая 2024 г.",      time="16:20", status="Перевод выполнен"),
    dict(sender="Жанар Сатыбалди",   receiver="Бауыржан Омаров", amount="45 000 тг", date="18 июня 2024 г.",     time="08:55", status="Перевод выполнен"),
    dict(sender="Светлана Ким",      receiver="Алма Байжанова",  amount="3 200 тг",  date="29 июля 2024 г.",     time="13:17", status="Перевод выполнен"),
    dict(sender="Арман Телеулов",    receiver="Гульнара Ахметова",amount="12 000 тг",date="07 августа 2024 г.", time="10:30", status="Перевод выполнен"),
    dict(sender="Наталья Соколова",  receiver="Владимир Попов",  amount="50 000 тг", date="14 сентября 2024 г.", time="19:05", status="Перевод выполнен"),
    dict(sender="Айдана Сарсенова",  receiver="Тимур Аубакиров", amount="9 999 тг",  date="22 октября 2024 г.", time="07:45", status="Перевод выполнен"),
]

# date_impossible: 3 samples
INCONSISTENT_DATE_IMPOSSIBLE = [
    dict(sender="Асель Нурова",   receiver="Марат Алиев",  amount="10 000 тг", date="31 февраля 2024 г.",  time="14:00", status="Перевод выполнен"),
    dict(sender="Динара Кожа",    receiver="Арман Бек",    amount="5 500 тг",  date="31 ноября 2024 г.",   time="10:22", status="Перевод выполнен"),
    dict(sender="Иван Сидоров",   receiver="Петр Кузнецов",amount="18 000 тг", date="30 февраля 2024 г.",  time="08:11", status="Перевод выполнен"),
]

# date_future: 3 samples
INCONSISTENT_DATE_FUTURE = [
    dict(sender="Айгерим Омарова",receiver="Нурлан Берик", amount="7 000 тг",  date="15 марта 2027 г.",    time="15:33", status="Перевод выполнен"),
    dict(sender="Сергей Волков",  receiver="Анна Павлова", amount="3 000 тг",  date="01 января 2030 г.",   time="00:01", status="Перевод выполнен"),
    dict(sender="Жасулан Алтай",  receiver="Камила Ибраева",amount="25 000 тг",date="20 декабря 2028 г.", time="22:15", status="Перевод выполнен"),
]

# amount_unrealistic: 3 samples
INCONSISTENT_AMOUNT = [
    dict(sender="Асель Касымова", receiver="Данияр Бек",   amount="0 тг",         date="10 марта 2024 г.",  time="12:00", status="Перевод выполнен"),
    dict(sender="Марат Алиев",    receiver="Айгерим Нур",  amount="500 000 000 тг",date="15 апреля 2024 г.",time="09:45", status="Перевод выполнен"),
    dict(sender="Ольга Козлова",  receiver="Иван Новиков", amount="0.50 тг",       date="20 мая 2024 г.",   time="16:30", status="Перевод выполнен"),
]

# casino_context: 3 samples
INCONSISTENT_CASINO = [
    dict(sender="1win платформа", receiver="Асель Нурова",  amount="50 000 тг", date="05 июня 2024 г.",    time="21:00", status="Выигрыш в казино"),
    dict(sender="Mostbet Casino", receiver="Марат Алиев",   amount="100 000 тг",date="12 июля 2024 г.",    time="23:55", status="Бонус за депозит"),
    dict(sender="Олимп Бет",      receiver="Динара Сейт",   amount="25 000 тг", date="08 августа 2024 г.", time="20:30", status="Выигрыш по ставке"),
]

# amount_mismatch (same time): 3 samples — identical time, suggesting copy
_MISMATCH_TIME = "14:32"
INCONSISTENT_MISMATCH = [
    dict(sender="Асель Касымова", receiver="Данияр Сериков",  amount="5 000 тг",   date="15 января 2024 г.", time=_MISMATCH_TIME, status="Перевод выполнен"),
    dict(sender="Асель Касымова", receiver="Айгерим Нурова",  amount="15 000 тг",  date="15 января 2024 г.", time=_MISMATCH_TIME, status="Перевод выполнен"),
    dict(sender="Асель Касымова", receiver="Жанар Сатыбалди", amount="35 000 тг",  date="15 января 2024 г.", time=_MISMATCH_TIME, status="Перевод выполнен"),
]

NEUTRAL_LINES = [
    ["Привет! Ты уже перевёл деньги?",   "Жду подтверждения.", "Пришли скриншот когда сделаешь."],
    ["Окей всё понял, сделаю сейчас.",    "Подожди пару минут.", ""],
    ["Спасибо, получил!",                 "Всё пришло нормально.", "Увидимся завтра."],
    ["Когда планируешь отправить?",       "Мне нужно до вечера.", "Срочно нужно."],
    ["Переслал тебе контакт для перевода.","Там написано куда.", "Позвони если что."],
]

# ── Generation ──────────────────────────────────────────────────────────────

def generate_dataset():
    labels = []
    idx = 1

    def save(img: Image.Image, label: str, expected: list, desc: str):
        nonlocal idx
        fname = f"img_{idx:03d}.png"
        img.save(os.path.join(OUTPUT_DIR, fname))
        labels.append({
            "filename": fname,
            "label": label,
            "expected_signals": expected,
            "description": desc,
        })
        idx += 1

    # Group 1 — consistent
    for s in CONSISTENT_SAMPLES:
        img = create_kaspi_receipt_image(s)
        save(img, "consistent", [], f"Валидный чек, отправитель {s['sender']}")

    # Group 2 — inconsistent
    for s in INCONSISTENT_DATE_IMPOSSIBLE:
        img = create_kaspi_receipt_image(s)
        save(img, "inconsistent", ["DATE_IMPOSSIBLE"], f"Невозможная дата: {s['date']}")

    for s in INCONSISTENT_DATE_FUTURE:
        img = create_kaspi_receipt_image(s)
        save(img, "inconsistent", ["DATE_FUTURE"], f"Дата в будущем: {s['date']}")

    for s in INCONSISTENT_AMOUNT:
        img = create_kaspi_receipt_image(s)
        save(img, "inconsistent", ["AMOUNT_UNREALISTIC_HIGH", "AMOUNT_UNREALISTIC_LOW"],
             f"Нереальная сумма: {s['amount']}")

    for s in INCONSISTENT_CASINO:
        img = create_kaspi_receipt_image(s)
        save(img, "inconsistent", ["CASINO_KEYWORDS_DETECTED"], f"Казино/ставки: {s['status']}")

    for s in INCONSISTENT_MISMATCH:
        img = create_kaspi_receipt_image(s)
        save(img, "inconsistent", ["AMOUNT_CONTEXT_MISMATCH"],
             f"Одинаковое время {_MISMATCH_TIME} у разных переводов")

    # Group 3 — neutral
    for lines in NEUTRAL_LINES:
        img = _neutral_image(lines)
        save(img, "neutral", [], "Переписка без платёжных данных")

    # Save labels
    with open(LABELS_PATH, "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)

    return labels


if __name__ == "__main__":
    print(f"Generating dataset → {OUTPUT_DIR}")
    labels = generate_dataset()
    print(f"Done. Generated {len(labels)} images.")
    print(f"Labels saved to: {LABELS_PATH}")
    print()
    print("First 3 entries:")
    for entry in labels[:3]:
        print(json.dumps(entry, ensure_ascii=False))
    print()

    # Count by label
    from collections import Counter
    counts = Counter(e["label"] for e in labels)
    print("Counts by label:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")

    # Verify files exist
    missing = [e["filename"] for e in labels if not os.path.exists(os.path.join(OUTPUT_DIR, e["filename"]))]
    if missing:
        print(f"\nERROR: Missing files: {missing}")
        sys.exit(1)
    else:
        print(f"\nAll {len(labels)} files verified OK.")
