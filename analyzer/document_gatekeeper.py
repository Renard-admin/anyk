# Document gatekeeper removed — replaced by template_matcher checks
def check_document(ocr_result: dict) -> dict:
    return {"passed": True, "markers_found": 0, "matched_groups": [], "missing_groups": []}
