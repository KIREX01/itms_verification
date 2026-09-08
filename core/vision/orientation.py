"""
Front vs. Rear vehicle orientation classification.

No labeled Ugandan training set exists yet for a learned classifier, so
this implements a deterministic, explainable visual-cue heuristic:

  - REAR cue: taillights are strongly red. We measure the fraction of
    pixels in the upper-half band of the frame that fall in a tight red
    hue range (in HSV), concentrated near the left/right edges (where
    taillights sit) rather than the center.
  - FRONT cue: headlights/grille are usually white/yellow/chrome with
    high value + low-to-mid saturation, again concentrated near the
    left/right edges.

Whichever cue's edge-weighted score is higher wins; the confidence is the
normalized margin between the two scores. This is intentionally a
drop-in replacement point: swap this module's `classify_orientation` for
a trained CNN later without touching any caller.
"""
from dataclasses import dataclass

import cv2
import numpy as np

# HSV ranges (OpenCV: H 0-179, S/V 0-255)
_RED_RANGES = [
    ((0, 80, 80), (10, 255, 255)),
    ((170, 80, 80), (179, 255, 255)),
]
_WHITE_YELLOW_RANGE = ((0, 0, 180), (40, 90, 255))


@dataclass
class OrientationResult:
    orientation: str  # "FRONT", "REAR", or "UNKNOWN"
    confidence: float
    front_score: float
    rear_score: float


def _edge_weighted_mask_score(hsv: np.ndarray, ranges) -> float:
    """
    Fraction of pixels matching `ranges`, weighted toward the left/right
    thirds of the frame (where lights sit) and the upper 2/3 (above the
    plate/bumper line).
    """
    h, w = hsv.shape[:2]
    mask_total = np.zeros((h, w), dtype=np.uint8)
    for lower, upper in ranges:
        mask_total |= cv2.inRange(hsv, np.array(lower), np.array(upper))

    weight = np.zeros((h, w), dtype=np.float32)
    left_third, right_third = w // 3, 2 * w // 3
    upper_two_thirds = int(h * 0.66)

    weight[:upper_two_thirds, :left_third] = 1.5
    weight[:upper_two_thirds, right_third:] = 1.5
    weight[:upper_two_thirds, left_third:right_third] = 0.5
    weight[upper_two_thirds:, :] = 0.2

    matched_weight = float(np.sum((mask_total > 0).astype(np.float32) * weight))
    total_weight = float(np.sum(weight))
    return matched_weight / total_weight if total_weight > 0 else 0.0


def _detect_central_taillight(hsv: np.ndarray) -> tuple[bool, float]:
    """
    Motorcycles feature a prominent red taillight mounted on the
    vertical centerline of the vehicle in the upper 70% of the frame.
    Returns (detected: bool, estimated_confidence: float).
    """
    h, w = hsv.shape[:2]
    mask_red = np.zeros((h, w), dtype=np.uint8)
    for lower, upper in _RED_RANGES:
        mask_red |= cv2.inRange(hsv, np.array(lower), np.array(upper))

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    cleaned = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
    cnts, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_conf = 0.0
    found = False
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        area = bw * bh
        cx = x + bw / 2.0
        center_offset = abs(cx - w / 2.0) / (w / 2.0)  # 0.0 at center, 1.0 at edge
        is_centered = center_offset <= 0.45
        is_upper = (y + bh / 2.0) <= 0.75 * h
        if area >= 1800 and bw >= 45 and is_centered and is_upper:
            found = True
            # Compute continuous confidence:
            # - size bonus: up to 0.18 for full-size lamp (6000 px)
            # - centering bonus: up to 0.10 for perfect center
            # - base confidence: 0.68
            size_factor = min(1.0, area / 6000.0)
            center_factor = max(0.0, 1.0 - (center_offset / 0.45))
            conf = 0.68 + 0.18 * size_factor + 0.10 * center_factor
            if conf > best_conf:
                best_conf = conf

    return found, min(0.98, best_conf)


def classify_orientation(image: np.ndarray) -> OrientationResult:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # 1. First check for a motorcycle central taillight cue
    has_taillight, taillight_conf = _detect_central_taillight(hsv)
    if has_taillight:
        rear_score = _edge_weighted_mask_score(hsv, _RED_RANGES)
        front_score = _edge_weighted_mask_score(hsv, [_WHITE_YELLOW_RANGE])
        return OrientationResult(
            orientation="REAR",
            confidence=round(taillight_conf, 3),
            front_score=round(front_score, 4),
            rear_score=round(max(0.15, rear_score), 4),
        )

    rear_score = _edge_weighted_mask_score(hsv, _RED_RANGES)
    front_score = _edge_weighted_mask_score(hsv, [_WHITE_YELLOW_RANGE])

    total = front_score + rear_score
    if total < 1e-6:
        return OrientationResult("UNKNOWN", 0.0, front_score, rear_score)

    if front_score >= rear_score:
        confidence = front_score / total
        orientation = "FRONT"
    else:
        confidence = rear_score / total
        orientation = "REAR"

    # A wafer-thin margin means the cues weren't distinctive -> flag as unknown
    if confidence < 0.55:
        orientation = "UNKNOWN"

    return OrientationResult(orientation, round(confidence, 3), round(front_score, 4), round(rear_score, 4))
