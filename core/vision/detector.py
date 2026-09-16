"""
License plate localization: dual-mode architecture.

Primary:  YOLOv8/YOLOv11 object detector (via ultralytics), configured with
          settings.PLATE_YOLO_WEIGHTS. Ships pointed at a generic COCO
          checkpoint by default -- for real accuracy on number plates you
          MUST fine-tune a YOLO model on a plate dataset and point
          PLATE_YOLO_WEIGHTS at those weights (see README "Training your
          own plate detector").
Fallback: OpenCV heuristic detector (Sobel gradients + rectangular contour
          aspect-ratio filtering). Used automatically if ultralytics/torch
          is not installed, the weights fail to load, or YOLO finds no
          candidate above threshold.

Both paths return a common `Detection` shape so downstream code (OCR,
normalizer) never needs to know which backend produced the box.
"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

# Ensure Ultralytics operates 100% offline without network checks
os.environ["ULTRALYTICS_AUTOINSTALL"] = "0"
os.environ["YOLO_VERBOSE"] = "False"

import cv2
import numpy as np

try:
    from django.conf import settings
    _CONF_THRESHOLD = float(getattr(settings, "PLATE_DETECTOR_CONF_THRESHOLD", 0.35))
    _YOLO_WEIGHTS = getattr(settings, "PLATE_YOLO_WEIGHTS", "models/license-plate-finetune-v1n.pt")
except Exception:
    # Allows this module to be imported/tested outside a configured Django project.
    _CONF_THRESHOLD = 0.35
    _YOLO_WEIGHTS = "models/license-plate-finetune-v1n.pt"


def _resolve_weights_path(weights_name: str) -> str:
    """Resolve model weights path locally or fetch from Hugging Face if needed."""
    if os.path.isabs(weights_name) and os.path.exists(weights_name):
        return weights_name

    # Project root is 2 levels up from core/vision/
    project_root = Path(__file__).resolve().parent.parent.parent
    local_path = project_root / weights_name
    if local_path.exists():
        return str(local_path)

    # Check models/ directory inside project root
    alt_local = project_root / "models" / Path(weights_name).name
    if alt_local.exists():
        return str(alt_local)

    # If specified as Hugging Face repo or fine-tuned license plate model
    if "yolov11" in weights_name.lower() or "morsetechlab" in weights_name.lower() or "/" in weights_name or "license-plate-finetune" in weights_name.lower():
        repo_id = "morsetechlab/yolov11-license-plate-detection"
        fname = "license-plate-finetune-v1s.pt" if "v1s" in weights_name.lower() else "license-plate-finetune-v1n.pt"
        models_dir = project_root / "models"
        models_dir.mkdir(parents=True, exist_ok=True)
        dest = models_dir / fname
        if dest.exists():
            return str(dest)
        try:
            from huggingface_hub import hf_hub_download
            import shutil
            downloaded = hf_hub_download(repo_id=repo_id, filename=fname)
            shutil.copy(downloaded, dest)
            return str(dest)
        except Exception:
            pass

    return weights_name


class YOLOvv11:
    """YOLOv11 model wrapper providing the user-requested from_pretrained API:
    model = YOLOvv11.from_pretrained("morsetechlab/yolov11-license-plate-detection")
    """
    @classmethod
    def from_pretrained(
        cls,
        repo_or_path: str = "morsetechlab/yolov11-license-plate-detection",
        filename: str = "license-plate-finetune-v1n.pt",
        **kwargs,
    ):
        from ultralytics import YOLO
        resolved = _resolve_weights_path(repo_or_path)
        if not os.path.exists(resolved) and "/" in repo_or_path:
            try:
                from huggingface_hub import hf_hub_download
                resolved = hf_hub_download(repo_id=repo_or_path, filename=filename)
            except Exception:
                pass
        return YOLO(resolved, **kwargs)


YOLO11 = YOLOvv11

# Monkey-patch into ultralytics module for compatibility with `from ultralytics import YOLOvv11`
try:
    import ultralytics
    ultralytics.YOLOvv11 = YOLOvv11
    ultralytics.YOLO11 = YOLOvv11
    if hasattr(ultralytics, "YOLO") and not hasattr(ultralytics.YOLO, "from_pretrained"):
        setattr(
            ultralytics.YOLO,
            "from_pretrained",
            classmethod(lambda cls, repo="morsetechlab/yolov11-license-plate-detection", **kw: YOLOvv11.from_pretrained(repo, **kw)),
        )
except Exception:
    pass


@dataclass
class Detection:
    bbox: List[int]              # [x1, y1, x2, y2] pixel coords
    confidence: float
    backend: str                 # "yolo" or "heuristic"


@lru_cache(maxsize=1)
def _get_yolo_model():
    """Lazily load the YOLO model exactly once per process. Returns None if unavailable."""
    try:
        from ultralytics import YOLO
    except ImportError:
        return None
    try:
        weights_path = _resolve_weights_path(_YOLO_WEIGHTS)
        return YOLO(weights_path)
    except Exception:
        return None


def _detect_with_yolo(image: np.ndarray) -> Optional[Detection]:
    model = _get_yolo_model()
    if model is None:
        return None

    try:
        import torch
        with torch.inference_mode():
            results = model.predict(source=image, conf=_CONF_THRESHOLD, verbose=False)
    except Exception:
        try:
            results = model.predict(source=image, conf=_CONF_THRESHOLD, verbose=False)
        except Exception:
            return None

    if not results:
        return None

    best_box, best_conf = None, 0.0
    for r in results:
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            continue
        for box, conf in zip(boxes.xyxy.tolist(), boxes.conf.tolist()):
            x1, y1, x2, y2 = [int(v) for v in box]
            w_box, h_box = x2 - x1, y2 - y1
            if w_box < 25 or h_box < 10:
                continue
            if conf > best_conf:
                best_conf = conf
                best_box = [x1, y1, x2, y2]

    if best_box is None or best_conf < _CONF_THRESHOLD:
        return None

    return Detection(bbox=best_box, confidence=float(best_conf), backend="yolo")


def _detect_with_heuristic(image: np.ndarray) -> Optional[Detection]:
    """
    Classic plate-localization heuristic:
    1. Sobel-X gradient (plates have dense vertical edges from characters) ->
       threshold -> morphological close -> contour filtering.
    2. High-contrast / bright plate region detection (plates are distinct bright
       rectangles containing dense character edges, especially on motorcycles).
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    h_img, w_img = gray.shape[:2]

    candidates = []

    # ── Strategy 1: Sobel-X vertical edges ─────────────────────────
    sobel_x = cv2.Sobel(blurred, cv2.CV_16S, 1, 0)
    sobel_x = cv2.convertScaleAbs(sobel_x)

    _, thresh_sobel = cv2.threshold(sobel_x, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    kernel_sobel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 3))
    closed_sobel = cv2.morphologyEx(thresh_sobel, cv2.MORPH_CLOSE, kernel_sobel)
    closed_sobel = cv2.erode(closed_sobel, None, iterations=1)
    closed_sobel = cv2.dilate(closed_sobel, None, iterations=1)

    contours_sobel, _ = cv2.findContours(closed_sobel, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for c in contours_sobel:
        x, y, w, h = cv2.boundingRect(c)
        if h == 0:
            continue
        # Reject candidates in the bottom ground area (gravel / pebbles / asphalt)
        if y > 0.78 * h_img and (y + h) >= 0.96 * h_img:
            continue
        aspect_ratio = w / float(h)
        area_ratio = (w * h) / float(w_img * h_img)
        if 1.0 <= aspect_ratio <= 6.5 and 0.005 <= area_ratio <= 0.55:
            dist_car = abs(aspect_ratio - 4.5) / 4.5
            dist_moto = abs(aspect_ratio - 1.45) / 1.45
            score = max(0.0, 1.0 - min(dist_car, dist_moto))
            if 1.2 <= aspect_ratio <= 1.8:
                score += 0.20
            if area_ratio >= 0.08:
                score += 0.20
            candidates.append((score, [x, y, x + w, y + h]))

    # ── Strategy 2: High-contrast bright plate region ──────────────
    _, thresh_bright = cv2.threshold(blurred, 140, 255, cv2.THRESH_BINARY)
    kernel_bright = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    closed_bright = cv2.morphologyEx(thresh_bright, cv2.MORPH_CLOSE, kernel_bright)
    contours_bright, _ = cv2.findContours(closed_bright, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)

    for c in contours_bright:
        x, y, w, h = cv2.boundingRect(c)
        if h == 0:
            continue
        # Reject candidates in the bottom ground area (gravel / pebbles / asphalt)
        if y > 0.78 * h_img and (y + h) >= 0.96 * h_img:
            continue
        aspect_ratio = w / float(h)
        area_ratio = (w * h) / float(w_img * h_img)
        if 1.0 <= aspect_ratio <= 3.8 and 0.015 <= area_ratio <= 0.55:
            roi_edges = sobel_x[y:y + h, x:x + w]
            density = float(np.mean(roi_edges > 40))
            if density > 0.07:
                dist_car = abs(aspect_ratio - 4.5) / 4.5
                dist_moto = abs(aspect_ratio - 1.45) / 1.45
                shape_match = max(0.0, 1.0 - min(dist_car, dist_moto))
                score = 0.85 + 0.25 * shape_match + 0.10 * min(1.0, density / 0.15)
                if area_ratio >= 0.08:
                    score += 0.25
                candidates.append((score, [x, y, x + w, y + h]))

    # ── Strategy 3: Plate character cluster detection ─────────────
    # Distinct characters forming a cluster on the plate
    for dark_val in (80, 95, 110):
        _, dark_th = cv2.threshold(blurred, dark_val, 255, cv2.THRESH_BINARY_INV)
        cnts_dark, _ = cv2.findContours(dark_th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        blobs = []
        for c in cnts_dark:
            bx, by, bw, bh = cv2.boundingRect(c)
            if 8 <= bw <= 75 and 18 <= bh <= 85:
                blobs.append((bx, by, bw, bh))
        if len(blobs) >= 4:
            clusters = []
            for b in blobs:
                added = False
                for cl in clusters:
                    cl_cx = sum(x + w / 2 for x, y, w, h in cl) / len(cl)
                    cl_cy = sum(y + h / 2 for x, y, w, h in cl) / len(cl)
                    if abs(b[0] + b[2] / 2 - cl_cx) < 130 and abs(b[1] + b[3] / 2 - cl_cy) < 90:
                        cl.append(b)
                        added = True
                        break
                if not added:
                    clusters.append([b])
            for cl in clusters:
                if len(cl) >= 4:
                    min_x = max(0, min(b[0] for b in cl) - 30)
                    max_x = min(w_img, max(b[0] + b[2] for b in cl) + 20)
                    min_y = max(0, min(b[1] for b in cl) - 20)
                    max_y = min(h_img, max(b[1] + b[3] for b in cl) + 20)
                    w, h = max_x - min_x, max_y - min_y
                    if h > 0 and 1.1 <= (w / float(h)) <= 2.8:
                        candidates.append((1.5 + len(cl) * 0.01, [min_x, min_y, max_x, max_y]))

    if not candidates:
        return None

    candidates.sort(key=lambda t: t[0], reverse=True)
    best_score, best_bbox = candidates[0]
    confidence = round(0.30 + 0.40 * min(1.0, best_score), 3)
    return Detection(bbox=best_bbox, confidence=confidence, backend="heuristic")


def detect_plate(image: np.ndarray) -> Optional[Detection]:
    """
    Detect the most likely license-plate bounding box in `image`.
    Tries YOLO first; transparently falls back to the OpenCV heuristic.
    """
    detection = _detect_with_yolo(image)
    if detection is not None:
        return detection
    return _detect_with_heuristic(image)


def crop_detection(image: np.ndarray, detection: Detection, padding: int = 4) -> np.ndarray:
    """Return the padded pixel crop for a Detection, clamped to image bounds."""
    h, w = image.shape[:2]
    x1, y1, x2, y2 = detection.bbox
    x1 = max(0, x1 - padding)
    y1 = max(0, y1 - padding)
    x2 = min(w, x2 + padding)
    y2 = min(h, y2 + padding)
    return image[y1:y2, x1:x2]


def crop_raw_detection(
    raw_image: np.ndarray,
    pre_image: np.ndarray,
    detection: Detection,
    padding: int = 8,
) -> np.ndarray:
    """
    Crops the detection bounding box from full-resolution `raw_image`
    by projecting coordinates from normalized `pre_image` back to `raw_image`.
    Preserves maximum character sharpness and pixel density for OCR.
    """
    if raw_image is None or raw_image.size == 0:
        return crop_detection(pre_image, detection, padding=padding)

    h_raw, w_raw = raw_image.shape[:2]
    h_pre, w_pre = pre_image.shape[:2]

    if h_raw == h_pre and w_raw == w_pre:
        return crop_detection(raw_image, detection, padding=padding)

    scale_x = w_raw / float(w_pre)
    scale_y = h_raw / float(h_pre)

    x1, y1, x2, y2 = detection.bbox
    rx1 = max(0, int(x1 * scale_x) - padding)
    ry1 = max(0, int(y1 * scale_y) - padding)
    rx2 = min(w_raw, int(x2 * scale_x) + padding)
    ry2 = min(h_raw, int(y2 * scale_y) + padding)

    if ry2 > ry1 and rx2 > rx1:
        return raw_image[ry1:ry2, rx1:rx2]
    return crop_detection(pre_image, detection, padding=padding)

