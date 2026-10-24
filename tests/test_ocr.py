"""
OCR smoke test — downloads a Cyrillic image and runs extract_text.
Run from the anyk/ directory:
    venv\Scripts\python -m tests.test_ocr
or:
    venv\Scripts\python tests/test_ocr.py
"""
import sys
import os
import time
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

# Make sure anyk/ is on the path when run directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analyzer.ocr_engine import extract_text

# We generate a synthetic Cyrillic PNG locally instead of downloading,
# so the test works offline and we control the content.
def create_test_image(path: str):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (600, 350), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    lines = [
        "Kaspi.kz",
        "Перевод выполнен",
        "Сумма: 15 000 тг",
        "Отправитель: Асель Касымова",
        "Получатель: Данияр Сериков",
        "Дата: 15 января 2024 г.",
        "Время: 14:32",
    ]
    # Try to use a real TTF font for good OCR quality
    font = None
    for fp in [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\times.ttf",
               "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
        if os.path.exists(fp):
            try:
                from PIL import ImageFont
                font = ImageFont.truetype(fp, 24)
                break
            except Exception:
                pass
    y = 20
    for line in lines:
        draw.text((30, y), line, fill=(20, 20, 20), font=font)
        y += 45
    img.save(path)
    print(f"Test image created: {path}")


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mktemp(suffix=".png")
    create_test_image(tmp)

    print("\n--- Running extract_text ---")
    t0 = time.time()
    result = extract_text(tmp)
    elapsed = time.time() - t0

    print(f"\nraw_text      : {result['raw_text']}")
    print(f"word_count    : {result['word_count']}")
    print(f"mean_confidence: {result['mean_confidence']}")
    print(f"low_quality   : {result.get('low_quality', False)}")
    print(f"regions found : {len(result['regions'])}")
    print(f"total time    : {elapsed:.2f}s")

    print("\n--- Regions ---")
    for r in result["regions"]:
        print(f"  [{r['confidence']:.3f}] {r['text']}")

    # Critical gate check
    if result["mean_confidence"] < 0.3:
        print("\n⚠️  КРИТИЧЕСКАЯ ПРОБЛЕМА: mean_confidence < 0.3")
        print("   EasyOCR плохо читает кириллицу на этом изображении.")
        print("   Нужна альтернатива (pytesseract или Claude Vision).")
        sys.exit(1)
    elif result["mean_confidence"] < 0.5:
        print("\n⚠️  ПРЕДУПРЕЖДЕНИЕ: mean_confidence < 0.5 — качество ниже среднего.")
    else:
        print(f"\n✅ OCR работает нормально (confidence={result['mean_confidence']:.3f})")

    os.unlink(tmp)
