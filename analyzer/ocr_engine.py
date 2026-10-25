# OCR engine removed — replaced by template_matcher (no external API / no heavy models)
def extract_text(image_path: str) -> dict:
    return {"raw_text": "", "regions": [], "mean_confidence": 0.0,
            "word_count": 0, "source": "disabled"}
