# Trust scorer removed — trust score now computed in template_matcher
def calculate_trust(signals, ocr_result=None, pdf_info=None, gatekeeper_result=None) -> dict:
    return {"trust_score": 0, "trust_label": "", "trust_label_en": "",
            "trust_explanation": "", "trust_disclaimer": ""}
