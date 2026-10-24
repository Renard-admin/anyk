import os
import uuid
import logging
import tempfile

logger = logging.getLogger(__name__)


def preprocess(image_path: str) -> str:
    from PIL import Image
    import cv2
    import numpy as np

    img = Image.open(image_path)

    # Ensure RGB
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "RGB":
        pass

    w, h = img.size
    min_side = min(w, h)
    if min_side < 640:
        scale = 640 / min_side
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        logger.info("Upscaled %dx%d → %dx%d", w, h, new_w, new_h)

    # Grayscale
    gray = img.convert("L")

    # CLAHE via OpenCV
    arr = np.array(gray)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(arr)

    out_path = os.path.join(tempfile.gettempdir(), f"anyk_pre_{uuid.uuid4().hex}.png")
    Image.fromarray(enhanced).save(out_path)
    logger.info("Preprocessed → %s", out_path)
    return out_path
