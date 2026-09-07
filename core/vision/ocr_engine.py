"""
Plate character recognition.

Primary:  PaddleOCR (angle-classification + recognition), tuned for
          alphanumeric plate fonts via `rec` model only (detection is
          skipped since the crop already isolates the plate).
Fallback: pytesseract with a restricted alphanumeric whitelist, used if
          PaddleOCR/paddlepaddle is not installed on the host machine
          (it's a heavy, platform-specific dependency).

Both paths return a common OCRResult(text, confidence) so the caller
never needs to branch on which engine ran.
"""
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np

try:
    from django.conf import settings
    _USE_PADDLE = bool(getattr(settings, "USE_PADDLEOCR", True))
except Exception:
    _USE_PADDLE = True

PLATE_CHAR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass
class OCRResult:
    text: str
    confidence: float
    backend: str  # "paddleocr" or "tesseract"


@lru_cache(maxsize=1)
def _get_paddle_engine():
    if not _USE_PADDLE:
        return None
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        return None
    try:
        # use_angle_cls handles plates photographed at a slight tilt;
        # det=False because we've already localized+cropped the plate.
        return PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    except Exception:
        return None


def _ocr_with_paddle(plate_crop: np.ndarray) -> Optional[OCRResult]:
    engine = _get_paddle_engine()
    if engine is None:
        return None
    try:
        result = engine.ocr(plate_crop, cls=True)
    except Exception:
        return None

    if not result or not result[0]:
        return None

    # Concatenate all detected text fragments left-to-right (handles two-line plates),
    # weighting overall confidence by each fragment's own score.
    fragments = result[0]
    fragments.sort(key=lambda f: f[0][0][0])  # sort by top-left x coordinate

    texts, confidences = [], []
    for _box, (text, conf) in fragments:
        texts.append(text)
        confidences.append(conf)

    if not texts:
        return None

    combined_text = "".join(texts)
    avg_conf = float(sum(confidences) / len(confidences))
    return OCRResult(text=combined_text, confidence=avg_conf, backend="paddleocr")


import os
import shutil

def _configure_tesseract() -> bool:
    try:
        import pytesseract
    except ImportError:
        return False

    try:
        from django.conf import settings
        tesseract_setting = getattr(settings, "TESSERACT_CMD", None)
    except Exception:
        tesseract_setting = None

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    local_tesseract = os.path.join(project_root, "tools", "tesseract", "tesseract.exe")

    candidates = [
        tesseract_setting,
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        shutil.which("tesseract"),
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        local_tesseract,
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            # Probe candidate with a quick --version check to guarantee it executes cleanly
            try:
                import subprocess
                probe = subprocess.run([c, "--version"], capture_output=True, timeout=3)
                if probe.returncode != 0:
                    continue
            except Exception:
                continue

            pytesseract.pytesseract.tesseract_cmd = c
            tessdata = os.path.join(os.path.dirname(c), "tessdata")
            if os.path.isdir(tessdata):
                os.environ["TESSDATA_PREFIX"] = tessdata
            return True
    return False


def _ocr_with_tesseract(plate_crop: np.ndarray) -> Optional[OCRResult]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError:
        return None

    if not _configure_tesseract():
        return None

    import cv2
    from core.vision.normalizer import normalize_plate

    h, w = plate_crop.shape[:2]
    aspect_ratio = w / float(h) if h > 0 else 0.0

    # We collect all candidates and pick the best one at the end.
    # Each candidate is (priority, text, confidence) where higher priority wins.
    # Priority tiers:  3 = valid plate, 7-8 chars
    #                  2 = valid plate, other length
    #                  1 = non-valid, 7-8 chars
    #                  0 = non-valid, other
    all_candidates = []

    def _score_candidate(text, conf):
        norm = normalize_plate(text)
        if norm["is_valid"] and len(norm["canonical"]) in (7, 8):
            # All Ugandan motorcycles start with UMA (only the 3rd letter changes in future: UMB...)
            moto_match = 1 if norm["canonical"].startswith("UM") else 0
            return (4, moto_match, len(norm["canonical"]), conf, norm["canonical"])
        if norm["is_valid"]:
            return (3, 0, len(norm["canonical"]), conf, norm["canonical"])
        if len(text) in (7, 8):
            return (2, 0, len(text), conf, text)
        return (1, 0, len(text), conf, text)

    # ── 1. For squarish crops (aspect ratio < 2.8), try 2-line recognition ──
    # Typical for Ugandan motorcycle plates: top line has letters (e.g. UMA),
    # bottom line has digits and suffix (e.g. 145PD), with a flag/emblem on the left.
    if 0.8 <= aspect_ratio < 2.8:
        # --- Inner plate refinement: isolate the white plate rectangle ---
        # The heuristic detector bbox can include surrounding dark regions
        # (reflectors, exhaust, frame). Find the largest bright contour and
        # crop to it so Tesseract sees only the plate interior.
        work_crop = plate_crop
        gray_ref = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY) if plate_crop.ndim == 3 else plate_crop
        _, th_ref = cv2.threshold(gray_ref, 130, 255, cv2.THRESH_BINARY)
        cnts_ref, _ = cv2.findContours(th_ref, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        total_area = w * h
        bright_boxes = [
            cv2.boundingRect(c) for c in cnts_ref
            if cv2.contourArea(c) > 0.25 * total_area
        ]
        if bright_boxes:
            bright_boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
            bx, by, bw, bh = bright_boxes[0]
            pad_ref = 8
            ry1 = max(0, by - pad_ref)
            ry2 = min(h, by + bh + pad_ref)
            rx1 = max(0, bx - pad_ref)
            rx2 = min(w, bx + bw + pad_ref)
            work_crop = plate_crop[ry1:ry2, rx1:rx2]

        wh, ww = work_crop.shape[:2]
        for split_ratio in (0.46, 0.48, 0.50):
            mid_y = int(wh * split_ratio)
            raw_top = work_crop[:mid_y, :]
            raw_bot = work_crop[mid_y:, :]

            # The Ugandan flag/emblem is only on the top line (left ~18-26%).
            # The bottom line (digits + suffix) starts near the left plate edge (~2-6%).
            for top_m in (0.18, 0.22, 0.26):
                top_x = int(ww * top_m)
                top_half = raw_top[:, top_x:]

                for bot_m in (0.03, 0.06, top_m):
                    bot_x = int(ww * bot_m)
                    bot_half = raw_bot[:, bot_x:]

                    # Add white padding around sublines -- Tesseract reads small
                    # crops much more reliably when characters aren't jammed
                    # against the image edge.
                    top_padded = cv2.copyMakeBorder(
                        top_half, 10, 10, 10, 10,
                        cv2.BORDER_CONSTANT, value=[255, 255, 255],
                    )
                    bot_padded = cv2.copyMakeBorder(
                        bot_half, 10, 10, 10, 10,
                        cv2.BORDER_CONSTANT, value=[255, 255, 255],
                    )

                    for psm in ("7", "8"):
                        cfg = f"--psm {psm} -c tessedit_char_whitelist={PLATE_CHAR_WHITELIST}"
                        try:
                            t1 = pytesseract.image_to_string(top_padded, config=cfg).strip()
                            t2 = pytesseract.image_to_string(bot_padded, config=cfg).strip()
                            if t1 and t2:
                                comb = t1 + t2
                                all_candidates.append(_score_candidate(comb, 0.88))

                            # Motorcycle plate logic:
                            # In Uganda, all motorcycles start with UMA (only the 3rd letter changes in future).
                            # Line 2 contains the digits + suffix (e.g. 145PD).
                            if t2:
                                import re
                                bot_match = re.search(r"(\d{3}[A-Z]{1,2})", t2)
                                if bot_match:
                                    bot_code = bot_match.group(1)
                                    top_clean = re.sub(r"[^A-Z]", "", t1.upper()) if t1 else ""
                                    if len(top_clean) == 3 and top_clean.startswith("UM"):
                                        pfx = top_clean
                                    elif len(top_clean) == 3 and top_clean[0] in ("V", "W") and top_clean[1] in ("W", "H", "M"):
                                        pfx = f"UM{top_clean[2]}"
                                    else:
                                        pfx = "UMA"
                                    all_candidates.append(_score_candidate(f"{pfx}{bot_code}", 0.95))
                        except Exception:
                            continue

    # ── 2. Standard single/multi-line recognition on the whole crop ──────
    gray = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY) if plate_crop.ndim == 3 else plate_crop
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    for img_variant in (plate_crop, thresh, gray):
        for psm in ("7", "6", "11"):
            config = (
                f"--psm {psm} "
                f"-c tessedit_char_whitelist={PLATE_CHAR_WHITELIST}"
            )
            try:
                data = pytesseract.image_to_data(img_variant, config=config, output_type=Output.DICT)
            except Exception:
                continue

            words, confs = [], []
            for text, conf in zip(data.get("text", []), data.get("conf", [])):
                text = text.strip()
                conf = float(conf)
                if text and conf > 0:
                    words.append(text)
                    confs.append(conf)

            if words:
                combined_text = "".join(words)
                avg_conf = (sum(confs) / len(confs)) / 100.0
                all_candidates.append(_score_candidate(combined_text, avg_conf))

    # ── 3. Pick the best candidate ───────────────────────────────────────
    if not all_candidates:
        return None

    # Sort by (tier DESC, moto_match DESC, length DESC, confidence DESC)
    all_candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
    best = all_candidates[0]
    return OCRResult(text=best[4], confidence=best[3], backend="tesseract")


def read_plate_text(plate_crop: np.ndarray) -> Optional[OCRResult]:
    """Run OCR on an already-localized plate crop. Returns None if both engines fail."""
    if plate_crop is None or plate_crop.size == 0:
        return None

    result = _ocr_with_paddle(plate_crop)
    if result is not None:
        return result
    return _ocr_with_tesseract(plate_crop)
