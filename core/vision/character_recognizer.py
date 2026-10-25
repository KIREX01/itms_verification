"""
Direct Character Recognition (ALPR) for License Plate Crops.

Bypasses heavy, generic OCR pipelines (PaddleOCR / Tesseract) by recognizing
characters directly from localized license plate crops.

Architecture & Capabilities:
  1. Direct character detection and classification on plate crops using
     lightweight neural weights (ONNX Runtime / YOLO character detector).
  2. Spatial 2-row clustering & ordering:
     - Handles standard 1-row plates (e.g. UBB 456C) via horizontal sorting.
     - Handles Ugandan 2-row motorcycle plates (top row e.g. UMA, bottom row e.g. 123A)
       via vertical center clustering + left-to-right sorting within each row.
  3. Overlap / Non-Maximum Suppression (NMS) to eliminate duplicate character detections.
  4. Integration with Ugandan plate syntax validator (normalizer.is_valid_plate).
  5. Guaranteed Graceful Fallback:
     - If direct recognition is disabled, missing model weights, yields low confidence,
       or fails Ugandan plate syntax validation, it returns None.
     - The calling pipeline (ocr_engine.read_plate_text) then seamlessly falls through
       to the full multi-stage OCR pipeline (PaddleOCR -> Tesseract -> Ensemble).
"""
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Tuple

import cv2
import numpy as np

from core.vision.normalizer import is_valid_plate, normalize_plate

logger = logging.getLogger(__name__)


@dataclass
class CharacterBox:
    """Represents a localized character detection on a license plate crop."""
    char: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def xc(self) -> float:
        return (self.x1 + self.x2) / 2.0

    @property
    def yc(self) -> float:
        return (self.y1 + self.y2) / 2.0

    @property
    def width(self) -> float:
        return max(0.0, self.x2 - self.x1)

    @property
    def height(self) -> float:
        return max(0.0, self.y2 - self.y1)


def filter_overlapping_boxes(boxes: List[CharacterBox], iou_threshold: float = 0.45) -> List[CharacterBox]:
    """Filters duplicate or highly overlapping character detections, keeping higher confidence."""
    if len(boxes) <= 1:
        return list(boxes)

    # Sort descending by confidence
    sorted_boxes = sorted(boxes, key=lambda b: b.confidence, reverse=True)
    kept: List[CharacterBox] = []

    for box in sorted_boxes:
        overlap = False
        for k in kept:
            inter_x1 = max(box.x1, k.x1)
            inter_y1 = max(box.y1, k.y1)
            inter_x2 = min(box.x2, k.x2)
            inter_y2 = min(box.y2, k.y2)

            inter_w = max(0.0, inter_x2 - inter_x1)
            inter_h = max(0.0, inter_y2 - inter_y1)
            inter_area = inter_w * inter_h

            if inter_area > 0:
                area_box = box.width * box.height
                area_k = k.width * k.height
                min_area = min(area_box, area_k)
                if min_area > 0 and (inter_area / min_area) > iou_threshold:
                    overlap = True
                    break
        if not overlap:
            kept.append(box)

    return kept


def cluster_and_order_characters(boxes: List[CharacterBox]) -> Tuple[str, float]:
    """
    Orders character boxes into reading order (left-to-right, top-to-bottom).
    Handles both 1-row plates and 2-row motorcycle plates.
    """
    if not boxes:
        return "", 0.0
    if len(boxes) == 1:
        return boxes[0].char.upper(), boxes[0].confidence

    heights = [b.height for b in boxes]
    median_h = float(np.median(heights)) if heights else 1.0
    if median_h <= 0:
        median_h = 1.0

    y_centers = [b.yc for b in boxes]
    y_span = max(y_centers) - min(y_centers)

    # If vertical center span is >= 35% of character height, consider multi-row
    if y_span >= 0.35 * median_h:
        by_y = sorted(boxes, key=lambda b: b.yc)
        gaps = [by_y[i + 1].yc - by_y[i].yc for i in range(len(by_y) - 1)]
        max_gap = max(gaps) if gaps else 0.0
        split_idx = gaps.index(max_gap)

        # Confirm that the maximum gap is substantial (at least 25% of character height)
        # and leaves at least 2 characters in one row and at least 2 in the other
        if max_gap >= 0.25 * median_h and split_idx >= 1 and (len(by_y) - split_idx - 1) >= 2:
            row1 = by_y[:split_idx + 1]
            row2 = by_y[split_idx + 1:]
            row1_sorted = sorted(row1, key=lambda b: b.xc)
            row2_sorted = sorted(row2, key=lambda b: b.xc)
            ordered_boxes = row1_sorted + row2_sorted
        else:
            ordered_boxes = sorted(boxes, key=lambda b: b.xc)
    else:
        ordered_boxes = sorted(boxes, key=lambda b: b.xc)

    raw_text = "".join(b.char.upper() for b in ordered_boxes)
    avg_conf = float(sum(b.confidence for b in ordered_boxes) / len(ordered_boxes))
    return raw_text, avg_conf


class DirectCharacterRecognizer:
    """Direct plate character recognizer with spatial clustering and syntax validation."""

    def __init__(
        self,
        weights_path: Optional[str] = None,
        min_confidence: Optional[float] = None,
    ):
        self._weights_path = weights_path
        self._min_confidence = min_confidence
        self._model = None
        self._mock_boxes: Optional[List[CharacterBox]] = None

    def set_mock_boxes(self, boxes: Optional[List[CharacterBox]]) -> None:
        """Injects mock character bounding boxes for deterministic testing."""
        self._mock_boxes = boxes

    def is_enabled(self) -> bool:
        """Checks if direct character reader is enabled via configuration."""
        try:
            from core.services import config_service
            cfg = config_service.load_config()
            return bool(cfg.get("vision", {}).get("use_direct_character_reader", True))
        except Exception:
            return True

    def get_min_confidence(self) -> float:
        """Returns the configured minimum character recognition confidence threshold."""
        if self._min_confidence is not None:
            return self._min_confidence
        try:
            from core.services import config_service
            cfg = config_service.load_config()
            return float(cfg.get("vision", {}).get("direct_char_min_confidence", 0.65))
        except Exception:
            return 0.65

    def resolve_weights_path(self) -> Optional[Path]:
        """Resolves the path to character model weights file on disk."""
        target = self._weights_path
        if not target:
            try:
                from core.services import config_service
                cfg = config_service.load_config()
                target = cfg.get("vision", {}).get("character_model_weights", "models/plate_char_reader.onnx")
            except Exception:
                target = "models/plate_char_reader.onnx"

        if not target:
            return None

        p = Path(target)
        if p.is_absolute() and p.exists():
            return p

        # Check relative to project root
        try:
            from django.conf import settings
            root = Path(settings.BASE_DIR)
        except Exception:
            root = Path(__file__).resolve().parent.parent.parent

        local_p = root / target
        if local_p.exists():
            return local_p

        alt_p = root / "models" / Path(target).name
        if alt_p.exists():
            return alt_p

        return None

    def get_model(self) -> Any:
        """Loads and caches the character recognition model (YOLO or ONNX Runtime)."""
        if self._model is not None:
            return self._model

        weights_file = self.resolve_weights_path()
        if not weights_file or not weights_file.exists():
            return None

        weights_str = str(weights_file)

        # 1. Try Ultralytics YOLO (supports both .pt and .onnx)
        try:
            from ultralytics import YOLO
            self._model = YOLO(weights_str)
            logger.info("Loaded Direct Character Recognizer via Ultralytics: %s", weights_str)
            return self._model
        except Exception as exc:
            logger.debug("Ultralytics load failed for %s: %s", weights_str, exc)

        # 2. Try ONNX Runtime directly
        if weights_str.endswith(".onnx"):
            try:
                import onnxruntime as ort
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                self._model = ort.InferenceSession(weights_str, sess_options=opts)
                logger.info("Loaded Direct Character Recognizer via ONNX Runtime: %s", weights_str)
                return self._model
            except Exception as exc:
                logger.debug("ONNX Runtime load failed for %s: %s", weights_str, exc)

        return None

    def _run_inference(self, model: Any, plate_crop: np.ndarray) -> List[CharacterBox]:
        """Runs model inference across YOLO or ONNX formats."""
        boxes: List[CharacterBox] = []

        # 1. Ultralytics YOLO model (.pt or .onnx)
        if hasattr(model, "predict"):
            results = model.predict(plate_crop, conf=0.25, verbose=False)
            if results and len(results) > 0:
                det = results[0]
                if hasattr(det, "boxes") and det.boxes is not None:
                    names = getattr(model, "names", {})
                    for box in det.boxes:
                        coords = box.xyxy[0].tolist()
                        cls_id = int(box.cls[0].item())
                        cls_name = names.get(cls_id, str(cls_id))
                        conf = float(box.conf[0].item())
                        boxes.append(
                            CharacterBox(
                                char=str(cls_name),
                                confidence=conf,
                                x1=float(coords[0]),
                                y1=float(coords[1]),
                                x2=float(coords[2]),
                                y2=float(coords[3]),
                            )
                        )
            return boxes

        # 2. ONNX Runtime InferenceSession
        if hasattr(model, "run") and hasattr(model, "get_inputs"):
            boxes = self._run_onnx_session(model, plate_crop)
            return boxes

        return boxes

    def _run_onnx_session(self, session: Any, plate_crop: np.ndarray) -> List[CharacterBox]:
        """Executes raw ONNX inference session and parses character bounding boxes."""
        boxes: List[CharacterBox] = []
        try:
            input_meta = session.get_inputs()[0]
            input_name = input_meta.name
            input_shape = input_meta.shape

            # Expected shape [1, 3, H, W]
            target_h = input_shape[2] if len(input_shape) >= 4 and isinstance(input_shape[2], int) else 160
            target_w = input_shape[3] if len(input_shape) >= 4 and isinstance(input_shape[3], int) else 320

            orig_h, orig_w = plate_crop.shape[:2]
            resized = cv2.resize(plate_crop, (target_w, target_h))
            blob = resized.astype(np.float32) / 255.0
            blob = np.transpose(blob, (2, 0, 1))  # HWC to CHW
            blob = np.expand_dims(blob, axis=0)   # BCHW

            outputs = session.run(None, {input_name: blob})
            if not outputs:
                return boxes

            # Parse standard YOLO [1, 4 + classes, anchors] tensor
            output0 = outputs[0]
            if output0.ndim == 3 and output0.shape[0] == 1:
                preds = np.transpose(output0[0], (1, 0))  # [anchors, 4 + classes]
                x_scale = orig_w / float(target_w)
                y_scale = orig_h / float(target_h)

                for row in preds:
                    class_scores = row[4:]
                    cls_id = int(np.argmax(class_scores))
                    max_score = float(class_scores[cls_id])
                    if max_score >= 0.25:
                        cx, cy, bw, bh = row[0], row[1], row[2], row[3]
                        x1 = (cx - bw / 2.0) * x_scale
                        y1 = (cy - bh / 2.0) * y_scale
                        x2 = (cx + bw / 2.0) * x_scale
                        y2 = (cy + bh / 2.0) * y_scale
                        # Map standard 36 classes (0-9, A-Z)
                        chars_vocab = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                        char_val = chars_vocab[cls_id] if cls_id < len(chars_vocab) else str(cls_id)
                        boxes.append(
                            CharacterBox(
                                char=char_val,
                                confidence=max_score,
                                x1=float(x1),
                                y1=float(y1),
                                x2=float(x2),
                                y2=float(y2),
                            )
                        )
        except Exception as exc:
            logger.debug("Raw ONNX parsing failed: %s", exc)

        return boxes

    def recognize(self, plate_crop: np.ndarray) -> Optional[Any]:
        """
        Recognize characters directly on a localized plate crop.

        Returns:
            OCRResult with backend="direct_char_reader" if recognition succeeds
            and yields a valid Ugandan registration number with confidence >= threshold.
            Returns None otherwise, signaling that the caller should fall back to OCR.
        """
        if plate_crop is None or not isinstance(plate_crop, np.ndarray) or plate_crop.size == 0:
            return None

        if not self.is_enabled():
            return None

        min_conf = self.get_min_confidence()

        boxes: List[CharacterBox] = []

        # 1. Check for injected mock predictions (used in test suites)
        if self._mock_boxes is not None:
            boxes = list(self._mock_boxes)
        else:
            model = self.get_model()
            if model is None:
                # No weights file found or model unavailable -> seamless fallback
                return None
            try:
                boxes = self._run_inference(model, plate_crop)
            except Exception as exc:
                logger.debug("Direct character inference error: %s", exc)
                return None

        if not boxes:
            return None

        # 2. Filter duplicate / overlapping detections
        filtered_boxes = filter_overlapping_boxes(boxes, iou_threshold=0.45)

        # 3. Require at least 5 character detections (shortest Ugandan plate is e.g. 6-7 chars)
        if len(filtered_boxes) < 5:
            return None

        # 4. Spatial 2D reading order (row clustering + left-to-right sorting)
        raw_text, avg_conf = cluster_and_order_characters(filtered_boxes)
        if not raw_text:
            return None

        # 5. Check confidence threshold
        if avg_conf < min_conf:
            logger.debug(
                "Direct character reader rejected candidate '%s': confidence %.2f < threshold %.2f",
                raw_text,
                avg_conf,
                min_conf,
            )
            return None

        # 6. Normalize and validate against Uganda plate syntax
        norm = normalize_plate(raw_text)
        canonical_text = norm.get("canonical", "")
        if not norm.get("is_valid", False) or not is_valid_plate(canonical_text):
            logger.debug(
                "Direct character reader candidate '%s' (raw: '%s') does not match valid Uganda syntax",
                canonical_text,
                raw_text,
            )
            return None

        from core.vision.ocr_engine import OCRResult
        return OCRResult(
            text=canonical_text,
            confidence=round(float(avg_conf), 3),
            backend="direct_char_reader",
        )


# Global singleton instance
_GLOBAL_RECOGNIZER: Optional[DirectCharacterRecognizer] = None


def get_character_recognizer() -> DirectCharacterRecognizer:
    """Returns the singleton instance of DirectCharacterRecognizer."""
    global _GLOBAL_RECOGNIZER
    if _GLOBAL_RECOGNIZER is None:
        _GLOBAL_RECOGNIZER = DirectCharacterRecognizer()
    return _GLOBAL_RECOGNIZER


def recognize_plate_characters(plate_crop: np.ndarray) -> Optional[Any]:
    """
    Module-level entry point called by ocr_engine.read_plate_text.
    Returns OCRResult if direct character recognition succeeds with valid syntax;
    otherwise returns None for seamless fallback to PaddleOCR/Tesseract.
    """
    recognizer = get_character_recognizer()
    return recognizer.recognize(plate_crop)
