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
import sys

_TESSERACT_INITIALIZED: bool = False
_TESSERACT_AVAILABLE: bool = False
_TESSERACT_PATH: Optional[str] = None


def _suppress_windows_error_dialogs() -> None:
    """Suppress blocking Windows GUI error dialogs (e.g. missing DLLs) for subprocesses."""
    if sys.platform == "win32":
        try:
            import ctypes
            # SEM_FAILCRITICALERRORS (0x0001) | SEM_NOGPFAULTERRORBOX (0x0002) | SEM_NOOPENFILEERRORBOX (0x8000)
            ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002 | 0x8000)
        except Exception:
            pass


def _configure_tesseract() -> bool:
    global _TESSERACT_INITIALIZED, _TESSERACT_AVAILABLE, _TESSERACT_PATH
    if _TESSERACT_INITIALIZED:
        return _TESSERACT_AVAILABLE

    _suppress_windows_error_dialogs()

    try:
        import pytesseract
    except ImportError:
        _TESSERACT_INITIALIZED = True
        _TESSERACT_AVAILABLE = False
        return False

    try:
        from django.conf import settings
        tesseract_setting = getattr(settings, "TESSERACT_CMD", None)
    except Exception:
        tesseract_setting = None

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    local_tesseract = os.path.join(project_root, "tools", "tesseract", "tesseract.exe")

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    user_tesseract = os.path.join(local_app_data, "Programs", "Tesseract-OCR", "tesseract.exe") if local_app_data else None

    # Order candidates with valid system-wide installations and explicit settings FIRST.
    # Relative tools/ paths are checked last to prevent probing incomplete portable extractions.
    candidates = [
        tesseract_setting,
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        user_tesseract,
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
        local_tesseract,
    ]

    creationflags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW

    for c in candidates:
        if c and os.path.isfile(c):
            # Probe candidate with a quick --version check to guarantee it executes cleanly without missing DLLs
            try:
                import subprocess
                probe = subprocess.run(
                    [c, "--version"],
                    capture_output=True,
                    timeout=3,
                    creationflags=creationflags,
                )
                if probe.returncode != 0:
                    continue
            except Exception:
                continue

            pytesseract.pytesseract.tesseract_cmd = c
            tessdata = os.path.join(os.path.dirname(c), "tessdata")
            if os.path.isdir(tessdata):
                os.environ["TESSDATA_PREFIX"] = tessdata
            _TESSERACT_INITIALIZED = True
            _TESSERACT_AVAILABLE = True
            _TESSERACT_PATH = c
            return True

    _TESSERACT_INITIALIZED = True
    _TESSERACT_AVAILABLE = False
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
    import re
    from core.vision.normalizer import normalize_plate, is_valid_plate

    h, w = plate_crop.shape[:2]
    aspect_ratio = w / float(h) if h > 0 else 0.0

    digit_map = {
        "T": "1", "I": "1", "J": "1", "L": "1",
        "O": "0", "D": "0", "Q": "0",
        "B": "8", "S": "5", "Z": "2",
        "G": "6", "E": "5",
    }

    all_candidates = []

    def _parse_motorcycle_sublines(t1: str, t2: str) -> Optional[OCRResult]:
        if not t2:
            return None
        cleaned2 = re.sub(r"[^A-Z0-9]", "", t2.upper())
        m_bot = re.search(r"([0-9TIJLOBSZGE]{3})([A-Z]{1,2})", cleaned2)
        if not m_bot:
            return None
        d_raw, sfx = m_bot.groups()
        digits = "".join(digit_map.get(c, c) for c in d_raw)
        top_clean = re.sub(r"[^A-Z]", "", t1.upper()) if t1 else ""
        if len(top_clean) == 3 and top_clean.startswith("UM"):
            pfx = top_clean
        elif len(top_clean) == 3 and top_clean[0] in ("V", "W") and top_clean[1] in ("W", "H", "M"):
            pfx = f"UM{top_clean[2]}"
        else:
            pfx = "UMA"
        norm = normalize_plate(f"{pfx}{digits}{sfx}")
        if norm["is_valid"]:
            return OCRResult(text=norm["canonical"], confidence=0.95, backend="tesseract")
        return None

    # ── 1. For squarish crops (aspect ratio < 2.8), try 2-line recognition ──
    if 0.8 <= aspect_ratio < 2.8:
        work_crop = plate_crop
        gray_ref = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY) if plate_crop.ndim == 3 else plate_crop
        _, th_ref = cv2.threshold(gray_ref, 130, 255, cv2.THRESH_BINARY)
        cnts_ref, _ = cv2.findContours(th_ref, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        bright_boxes = [cv2.boundingRect(c) for c in cnts_ref if cv2.contourArea(c) > 0.25 * (w * h)]
        if bright_boxes:
            bright_boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
            bx, by, bw, bh = bright_boxes[0]
            pad_ref = 8
            work_crop = plate_crop[max(0, by - pad_ref):min(h, by + bh + pad_ref), max(0, bx - pad_ref):min(w, bx + bw + pad_ref)]

        wh, ww = work_crop.shape[:2]

        # Pass 1A: Connected-Components character cluster segmentation
        try:
            gray_cc = cv2.cvtColor(work_crop, cv2.COLOR_BGR2GRAY) if work_crop.ndim == 3 else work_crop
            _, bin_cc = cv2.threshold(gray_cc, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
            n_labels, _, cc_stats, _ = cv2.connectedComponentsWithStats(bin_cc)
            top_boxes, bot_boxes = [], []
            for i in range(1, n_labels):
                cx, cy, cw_c, ch_c, carea = cc_stats[i]
                if 0.10 * wh < ch_c < 0.60 * wh and 0.02 * ww < cw_c < 0.45 * ww and carea > 200:
                    if cy + ch_c * 0.5 < wh * 0.50:
                        top_boxes.append((cx, cy, cw_c, ch_c))
                    else:
                        bot_boxes.append((cx, cy, cw_c, ch_c))

            if top_boxes and bot_boxes:
                min_tx = max(0, min(b[0] for b in top_boxes) - 10)
                max_tx = min(ww, max(b[0] + b[2] for b in top_boxes) + 10)
                min_ty = max(0, min(b[1] for b in top_boxes) - 8)
                max_ty = min(wh, max(b[1] + b[3] for b in top_boxes) + 8)

                min_bx = max(0, min(b[0] for b in bot_boxes) - 10)
                max_bx = min(ww, max(b[0] + b[2] for b in bot_boxes) + 10)
                min_by = max(0, min(b[1] for b in bot_boxes) - 8)
                max_by = min(wh, max(b[1] + b[3] for b in bot_boxes) + 8)

                cc_top = work_crop[min_ty:max_ty, min_tx:max_tx]
                cc_bot = work_crop[min_by:max_by, min_bx:max_bx]

                valid_candidates = []
                for psm_t in ("8", "7", "6"):
                    t1 = pytesseract.image_to_string(cc_top, config=f"--psm {psm_t} -c tessedit_char_whitelist={PLATE_CHAR_WHITELIST}").strip()
                    if not t1:
                        continue
                    for psm_b in ("7", "8", "6"):
                        t2 = pytesseract.image_to_string(cc_bot, config=f"--psm {psm_b} -c tessedit_char_whitelist={PLATE_CHAR_WHITELIST}").strip()
                        res = _parse_motorcycle_sublines(t1, t2)
                        if res and is_valid_plate(res.text):
                            c_len = len(res.text.replace(" ", ""))
                            if c_len == 8:
                                return res  # Full 8-char Ugandan plate found!
                            valid_candidates.append(res)
                        if t1 and t2:
                            all_candidates.append((t1 + t2, 0.80))
                if valid_candidates:
                    valid_candidates.sort(key=lambda r: len(r.text.replace(" ", "")), reverse=True)
                    return valid_candidates[0]
        except Exception:
            pass

        # Pass 1B: Multi-ratio split passes with PSM 8, 7, 6
        for bot_trim in (0.08, 0.0):
            for split_ratio in (0.44, 0.46, 0.48):
                mid_y = int(wh * split_ratio)
                raw_top = work_crop[:mid_y, :]
                raw_bot = work_crop[mid_y:, :]

                top_half = raw_top[:, int(ww * 0.15):]
                top_padded = cv2.copyMakeBorder(top_half, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])

                bot_sub = raw_bot[int(raw_bot.shape[0] * bot_trim):, int(ww * 0.03):]
                bot_padded = cv2.copyMakeBorder(bot_sub, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=[255, 255, 255])
                bot_gray = cv2.cvtColor(bot_padded, cv2.COLOR_BGR2GRAY) if bot_padded.ndim == 3 else bot_padded
                _, bot_otsu = cv2.threshold(bot_gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

                for psm in ("8", "7", "6"):
                    cfg_line = f"--psm {psm} -c tessedit_char_whitelist={PLATE_CHAR_WHITELIST}"
                    try:
                        t1 = pytesseract.image_to_string(top_padded, config=cfg_line).strip()
                    except Exception:
                        t1 = ""

                    for b_img in (bot_otsu, bot_gray):
                        try:
                            t2 = pytesseract.image_to_string(b_img, config=cfg_line).strip()
                            res = _parse_motorcycle_sublines(t1, t2)
                            if res and is_valid_plate(res.text):
                                return res  # Early exit on valid syntax!
                            if t1 and t2:
                                all_candidates.append((t1 + t2, 0.75))
                        except Exception:
                            pass

    # ── 2. Standard single/multi-line recognition on the whole crop ──────
    gray = cv2.cvtColor(plate_crop, cv2.COLOR_BGR2GRAY) if plate_crop.ndim == 3 else plate_crop
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    variants = [thresh, gray]
    try:
        from core.vision.plate_enhancer import prepare_ocr_variants
        extra_variants = prepare_ocr_variants(plate_crop)
        for var in extra_variants:
            variants.append(var)
    except Exception:
        pass

    for img_variant in variants:
        for psm in ("7", "6", "8"):
            cfg = f"--psm {psm} -c tessedit_char_whitelist={PLATE_CHAR_WHITELIST}"
            try:
                txt = pytesseract.image_to_string(img_variant, config=cfg).strip()
                norm = normalize_plate(txt)
                if norm["is_valid"]:
                    return OCRResult(text=norm["canonical"], confidence=0.92, backend="tesseract")
                if txt:
                    clean_c = "".join(c for c in txt.upper() if c.isalnum())
                    # Only accept candidate if not absurdly long noise
                    if len(clean_c) <= 12:
                        all_candidates.append((norm["canonical"] if norm["canonical"] else clean_c, 0.60))
            except Exception:
                pass

    if all_candidates:
        def _score_candidate(cand_tuple):
            txt, conf = cand_tuple
            cleaned = re.sub(r"[^A-Z0-9]", "", txt.upper())
            n = normalize_plate(cleaned)
            if n["is_valid"]:
                return (100, conf)
            l = len(cleaned)
            len_dist = min(abs(l - 7), abs(l - 8)) if l > 0 else 99
            if l > 10:
                len_dist += 40
            return (-len_dist, conf)

        all_candidates.sort(key=_score_candidate, reverse=True)
        best_txt, best_conf = all_candidates[0]
        norm_best = normalize_plate(best_txt)
        return OCRResult(
            text=norm_best["canonical"] if norm_best["canonical"] else best_txt[:10],
            confidence=best_conf,
            backend="tesseract",
        )

    return None


def _run_ensemble_voting(plate_crop: np.ndarray, existing_candidates: list) -> Optional[OCRResult]:
    """Multi-variant adaptive contrast ensemble voting.

    When baseline OCR passes fail to reach a syntactically valid plate,
    we test diverse adaptive binarization, CLAHE, and inverted variants across engines,
    and perform character-by-character majority voting over candidate strings.
    """
    from collections import Counter
    from core.vision.normalizer import normalize_plate, is_valid_plate
    import cv2

    variants = []
    try:
        from core.vision.plate_enhancer import prepare_ocr_variants
        variants = prepare_ocr_variants(plate_crop)
    except Exception:
        pass

    collected = []
    for cand in existing_candidates:
        if cand and cand.text:
            collected.append(cand.text)

    # 1. Test variants with PaddleOCR if available
    for var in variants:
        var_bgr = cv2.cvtColor(var, cv2.COLOR_GRAY2BGR) if var.ndim == 2 else var
        res = _ocr_with_paddle(var_bgr)
        if res and res.text:
            norm = normalize_plate(res.text)
            if norm["is_valid"]:
                return OCRResult(text=norm["canonical"], confidence=0.94, backend="ensemble_variant")
            collected.append(norm["canonical"] if norm["canonical"] else res.text)

    # 2. Position-wise character voting if we have candidates of similar length
    # Filter candidates to common Ugandan plate length (7 or 8 alphanumeric chars)
    canonical_candidates = []
    for txt in collected:
        norm = normalize_plate(txt)
        cleaned = norm["canonical"]
        if len(cleaned) in (7, 8):
            canonical_candidates.append(cleaned)

    if len(canonical_candidates) >= 2:
        # Determine dominant target length
        lengths = [len(c) for c in canonical_candidates]
        target_len = Counter(lengths).most_common(1)[0][0]
        matching_len_cands = [c for c in canonical_candidates if len(c) == target_len]

        voted_chars = []
        for i in range(target_len):
            col_chars = [c[i] for c in matching_len_cands]
            best_char = Counter(col_chars).most_common(1)[0][0]
            voted_chars.append(best_char)

        voted_text = "".join(voted_chars)
        norm_voted = normalize_plate(voted_text)
        if norm_voted["is_valid"]:
            return OCRResult(text=norm_voted["canonical"], confidence=0.88, backend="ensemble_voting")

    return None


def read_plate_text(plate_crop: np.ndarray) -> Optional[OCRResult]:
    """Run OCR on an already-localized plate crop with multi-stage enhancement.

    Strategy:
      1. Primary: PaddleOCR on raw crop. If valid Ugandan syntax, return immediately.
      2. Plate-specific enhanced crop (CLAHE, gamma, unsharp mask, saturation suppression).
         If PaddleOCR on enhanced crop yields valid syntax, return immediately.
      3. Fallback: Tesseract with motorcycle 2-line splitting and adaptive binarization.
         If valid syntax, return immediately.
      4. Ensemble: Adaptive contrast ensemble voting for shadowed/dusty plates.
      5. Return best candidate available if no strict match.
    """
    if plate_crop is None or plate_crop.size == 0:
        return None

    from core.vision.normalizer import is_valid_plate

    # 1. Primary: PaddleOCR on raw crop
    res_raw = _ocr_with_paddle(plate_crop)
    if res_raw is not None and is_valid_plate(res_raw.text):
        return res_raw

    # 2. Enhanced crop (targeted white/yellow background and black text optimization)
    res_enh = None
    try:
        from core.vision.plate_enhancer import enhance_plate_crop
        enhanced_crop = enhance_plate_crop(plate_crop)
        res_enh = _ocr_with_paddle(enhanced_crop)
        if res_enh is not None and is_valid_plate(res_enh.text):
            return res_enh
    except Exception:
        pass

    # 3. Tesseract fallback (with 2-line split & adaptive Gaussian thresholding)
    tess_result = _ocr_with_tesseract(plate_crop)
    if tess_result is not None and is_valid_plate(tess_result.text):
        return tess_result

    # 4. Adaptive contrast ensemble voting
    candidates = [r for r in (res_raw, res_enh, tess_result) if r and r.text]
    ensemble_result = _run_ensemble_voting(plate_crop, candidates)
    if ensemble_result is not None and is_valid_plate(ensemble_result.text):
        return ensemble_result

    # 5. Return best candidate available if no strict match
    if ensemble_result and ensemble_result.text:
        candidates.append(ensemble_result)
    if candidates:
        candidates.sort(key=lambda r: (r.confidence, len(r.text)), reverse=True)
        return candidates[0]

    return None
