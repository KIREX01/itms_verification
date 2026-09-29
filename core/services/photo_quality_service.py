"""
Photo Quality and Defocus Detection Service.
Analyzes captured photos for blur (Laplacian variance), defocus,
underexposure, and glare, providing actionable correction guidance.
"""
import io
import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

from PIL import Image

logger = logging.getLogger(__name__)

# Laplacian Variance Thresholds
# Standard focused mobile shots typically have variance > 120
# Acceptable quality is 60 - 120
# Below 60 indicates severe motion blur, defocus, or smudge on lens
BLUR_THRESHOLD_CRITICAL = 60.0
BLUR_THRESHOLD_WARNING = 100.0

# Brightness Thresholds (0 - 255)
UNDEREXPOSURE_THRESHOLD = 35.0
OVEREXPOSURE_THRESHOLD = 230.0


def assess_photo_quality(file_bytes: bytes) -> Dict[str, Any]:
    """
    Evaluates image sharpness, focus, and illumination balance.
    
    Returns:
        dict containing:
        - is_valid: bool
        - is_blurry: bool
        - sharpness_score: float
        - sharpness_label: str
        - is_underexposed: bool
        - is_overexposed: bool
        - brightness_score: float
        - exposure_label: str
        - quality_grade: "EXCELLENT" | "ACCEPTABLE" | "WARNING" | "CRITICAL"
        - has_issues: bool
        - issues: List[str]
        - correction_tips: List[str]
    """
    default_res = {
        "is_valid": True,
        "is_blurry": False,
        "sharpness_score": 100.0,
        "sharpness_label": "Sharp ✓",
        "is_underexposed": False,
        "is_overexposed": False,
        "brightness_score": 128.0,
        "exposure_label": "Normal",
        "quality_grade": "ACCEPTABLE",
        "has_issues": False,
        "issues": [],
        "correction_tips": [],
    }

    if not file_bytes:
        default_res["is_valid"] = False
        default_res["has_issues"] = True
        default_res["issues"].append("Empty photo data.")
        return default_res

    try:
        # Load image via Pillow or OpenCV
        img_np = None
        if cv2 is not None:
            nparr = np.frombuffer(file_bytes, np.uint8)
            img_np = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if img_np is None:
            # Fallback to Pillow
            pil_img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            img_np = np.array(pil_img)
            if cv2 is not None:
                img_np = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

        if img_np is None or img_np.size == 0:
            default_res["is_valid"] = False
            default_res["has_issues"] = True
            default_res["issues"].append("Could not decode image format.")
            return default_res

        # Convert to Grayscale
        if len(img_np.shape) == 3:
            if cv2 is not None:
                gray = cv2.cvtColor(img_np, cv2.COLOR_BGR2GRAY)
            else:
                gray = np.dot(img_np[..., :3], [0.2989, 0.5870, 0.1140]).astype(np.uint8)
        else:
            gray = img_np

        # 1. Sharpness / Blur Assessment via Laplacian Variance
        sharpness_score = 0.0
        if cv2 is not None:
            laplacian = cv2.Laplacian(gray, cv2.CV_64F)
            sharpness_score = float(laplacian.var())
        else:
            # Simple gradient fallback if cv2 is not available
            gy, gx = np.gradient(gray.astype(float))
            sharpness_score = float(np.var(gx) + np.var(gy))

        # 2. Exposure & Illumination Assessment
        mean_brightness = float(np.mean(gray))

        is_blurry = sharpness_score < BLUR_THRESHOLD_CRITICAL
        is_soft = BLUR_THRESHOLD_CRITICAL <= sharpness_score < BLUR_THRESHOLD_WARNING
        is_underexposed = mean_brightness < UNDEREXPOSURE_THRESHOLD
        is_overexposed = mean_brightness > OVEREXPOSURE_THRESHOLD

        issues: List[str] = []
        tips: List[str] = []

        if is_blurry:
            issues.append(f"Photo is out of focus / blurry (Sharpness: {sharpness_score:.0f} / 100).")
            tips.append("Hold the phone steady with both hands and tap the screen on the plate before taking photo.")
            tips.append("Check if the phone camera lens has smudges or fingerprints.")
        elif is_soft:
            issues.append(f"Focus is slightly soft (Sharpness: {sharpness_score:.0f} / 100).")
            tips.append("Ensure you are within 1.5 - 2 meters from the motorcycle.")

        if is_underexposed:
            issues.append("Photo is too dark (Underexposed).")
            tips.append("Turn on flashlight or position motorcycle under workshop lighting.")
        elif is_overexposed:
            issues.append("Severe glare or reflection detected (Overexposed).")
            tips.append("Adjust angle slightly to avoid direct spotlight reflection on the retro-reflective plate.")

        # Assign Quality Grade
        if is_blurry or (is_underexposed and is_soft):
            quality_grade = "CRITICAL"
        elif is_soft or is_underexposed or is_overexposed:
            quality_grade = "WARNING"
        elif sharpness_score > 150.0:
            quality_grade = "EXCELLENT"
        else:
            quality_grade = "ACCEPTABLE"

        has_issues = len(issues) > 0

        # Labels
        if sharpness_score >= BLUR_THRESHOLD_WARNING:
            sharpness_label = "Sharp ✓"
        elif sharpness_score >= BLUR_THRESHOLD_CRITICAL:
            sharpness_label = "Acceptable"
        else:
            sharpness_label = "Out of Focus ⚠️"

        if mean_brightness < UNDEREXPOSURE_THRESHOLD:
            exposure_label = "Too Dark ⚠️"
        elif mean_brightness > OVEREXPOSURE_THRESHOLD:
            exposure_label = "Glare / Overexposed ⚠️"
        else:
            exposure_label = "Balanced ✓"

        return {
            "is_valid": True,
            "is_blurry": is_blurry,
            "is_soft": is_soft,
            "sharpness_score": round(sharpness_score, 1),
            "sharpness_label": sharpness_label,
            "is_underexposed": is_underexposed,
            "is_overexposed": is_overexposed,
            "brightness_score": round(mean_brightness, 1),
            "exposure_label": exposure_label,
            "quality_grade": quality_grade,
            "has_issues": has_issues,
            "issues": issues,
            "correction_tips": tips,
        }

    except Exception as exc:
        logger.warning("Error evaluating photo quality: %s", exc)
        default_res["issues"].append(f"Quality analyzer error: {str(exc)}")
        return default_res
