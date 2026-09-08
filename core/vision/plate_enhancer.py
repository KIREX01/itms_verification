"""
Plate-aware image enhancement for the ITMS vision pipeline.

Two-stage enhancement strategy:

Stage 1 — Whole-image enhancement (at ingestion time):
    Uses Pillow (PIL) to dynamically normalize exposure, contrast, saturation,
    and sharpness based on tone analysis of the input photo. This produces a
    visually cleaner image that benefits ALL downstream vision stages (YOLO
    detection, orientation classification, and OCR).

Stage 2 — Plate-crop enhancement (post-YOLO detection, pre-OCR):
    Uses OpenCV on the already-localized plate crop with aggressive, plate-
    specific processing: HSV-based plate color classification (white vs
    yellow Uganda plates), targeted CLAHE, gamma correction, unsharp mask,
    and adaptive thresholding. Generates multiple OCR-ready variants so the
    OCR engine can pick the best one.

Both stages are independent and composable — each can be used alone or
together for maximum plate-reading yield.
"""
import logging
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# HSV ranges for Uganda plate background classification
# White plates (private vehicles): low saturation, high value
_WHITE_PLATE_HSV = {
    "h_range": (0, 180),    # any hue
    "s_range": (0, 50),     # very low saturation
    "v_range": (180, 255),  # bright
}

# Yellow plates (commercial, rear): mid hue, high saturation, high value
_YELLOW_PLATE_HSV = {
    "h_range": (15, 40),    # yellow hue band
    "s_range": (80, 255),   # saturated
    "v_range": (150, 255),  # bright
}


# ═══════════════════════════════════════════════════════════════════════════
# Stage 1: Whole-Image Enhancement (Pillow, runs at ingestion)
# ═══════════════════════════════════════════════════════════════════════════

def enhance_whole_image_array(
    image_bgr: np.ndarray,
    saturation_factor: float = 1.15,
    sharpness_factor: float = 1.3,
) -> np.ndarray:
    """Enhance a BGR numpy image dynamically using Pillow based on tone analysis.

    Applies in order:
      1. Autocontrast (histogram stretch with 0.5% clip)
      2. Dynamic contrast scaling (based on luminance variance)
      3. Color saturation boost (1.15x for realistic richness)
      4. Sharpness enhancement (1.3x for crisp plate characters)
      5. Dynamic brightness normalization (under/overexposure correction)

    Args:
        image_bgr: Input OpenCV BGR array.
        saturation_factor: Color richness multiplier (1.0 = original).
        sharpness_factor: Detail crispness multiplier (1.0 = original).

    Returns:
        Enhanced BGR numpy array.
    """
    from PIL import Image, ImageEnhance, ImageOps

    if image_bgr is None or image_bgr.size == 0:
        return image_bgr

    # Convert BGR -> RGB for Pillow
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)

    # 1. Normalize dynamic range (Autocontrast)
    img = ImageOps.autocontrast(img, cutoff=0.5)

    # 2. Dynamic Contrast Scaling based on image luminance variance
    gray_arr = np.array(img.convert("L"), dtype=np.float32)
    std_dev = float(np.std(gray_arr))

    if std_dev > 60:
        contrast_factor = 1.05
    elif std_dev < 30:
        contrast_factor = 1.25
    else:
        contrast_factor = 1.12

    img = ImageEnhance.Contrast(img).enhance(contrast_factor)

    # 3. Enhance Color (Vibrance/Saturation)
    img = ImageEnhance.Color(img).enhance(saturation_factor)

    # 4. Enhance Sharpness
    img = ImageEnhance.Sharpness(img).enhance(sharpness_factor)

    # 5. Dynamic Brightness Adjustment
    mean_brightness = float(np.mean(gray_arr))
    if mean_brightness < 90:      # Underexposed
        brightness_factor = 1.12
    elif mean_brightness > 180:   # Overexposed
        brightness_factor = 0.92
    else:
        brightness_factor = 1.0

    if brightness_factor != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness_factor)

    # Convert RGB back to BGR numpy array
    enhanced_rgb = np.array(img)
    return cv2.cvtColor(enhanced_rgb, cv2.COLOR_RGB2BGR)


def enhance_whole_image(
    image_path: str,
    output_path: Optional[str] = None,
    saturation_factor: float = 1.15,
    sharpness_factor: float = 1.3,
) -> str:
    """Enhance a photo dynamically on disk using Pillow.

    Args:
        image_path: Absolute path to the input image.
        output_path: Where to save the result. If None, overwrites in-place.
        saturation_factor: Color richness multiplier (1.0 = original).
        sharpness_factor: Detail crispness multiplier (1.0 = original).

    Returns:
        The output path of the enhanced image.
    """
    from PIL import Image, ImageEnhance, ImageOps

    if output_path is None:
        output_path = image_path

    try:
        img = Image.open(image_path)
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except Exception as exc:
        logger.warning("enhance_whole_image: cannot open %s: %s", image_path, exc)
        return image_path  # return original path unchanged

    # 1. Autocontrast
    img = ImageOps.autocontrast(img, cutoff=0.5)

    # 2. Dynamic contrast scaling
    gray_arr = np.array(img.convert("L"), dtype=np.float32)
    std_dev = float(np.std(gray_arr))

    if std_dev > 60:
        contrast_factor = 1.05
    elif std_dev < 30:
        contrast_factor = 1.25
    else:
        contrast_factor = 1.12

    img = ImageEnhance.Contrast(img).enhance(contrast_factor)

    # 3. Enhance Color (Vibrance/Saturation)
    img = ImageEnhance.Color(img).enhance(saturation_factor)

    # 4. Enhance Sharpness
    img = ImageEnhance.Sharpness(img).enhance(sharpness_factor)

    # 5. Dynamic Brightness
    mean_brightness = float(np.mean(gray_arr))
    if mean_brightness < 90:
        brightness_factor = 1.12
    elif mean_brightness > 180:
        brightness_factor = 0.92
    else:
        brightness_factor = 1.0

    if brightness_factor != 1.0:
        img = ImageEnhance.Brightness(img).enhance(brightness_factor)

    try:
        img.save(output_path, quality=95)
        logger.debug("Enhanced image saved to %s (contrast=%.2f, brightness=%.2f)",
                      output_path, contrast_factor, brightness_factor)
    except Exception as exc:
        logger.warning("enhance_whole_image: save failed for %s: %s", output_path, exc)
        return image_path

    return output_path


# ═══════════════════════════════════════════════════════════════════════════
# Stage 2: Plate-Crop Enhancement (OpenCV, runs post-YOLO, pre-OCR)
# ═══════════════════════════════════════════════════════════════════════════

def detect_plate_color(crop: np.ndarray) -> str:
    """Classify the plate background as 'white', 'yellow', or 'unknown'.

    Uses HSV color-space masking on the centre 60% of the crop (to ignore
    frame edges and mounting bolts) to determine the dominant plate surface.
    """
    if crop is None or crop.size == 0:
        return "unknown"

    h, w = crop.shape[:2]
    # Focus on the centre region to avoid border noise
    cx1, cy1 = int(w * 0.2), int(h * 0.2)
    cx2, cy2 = int(w * 0.8), int(h * 0.8)
    centre = crop[cy1:cy2, cx1:cx2]

    if centre.size == 0:
        return "unknown"

    hsv = cv2.cvtColor(centre, cv2.COLOR_BGR2HSV)
    total_px = hsv.shape[0] * hsv.shape[1]
    if total_px == 0:
        return "unknown"

    # White mask
    w_lo = np.array([_WHITE_PLATE_HSV["h_range"][0], _WHITE_PLATE_HSV["s_range"][0], _WHITE_PLATE_HSV["v_range"][0]])
    w_hi = np.array([_WHITE_PLATE_HSV["h_range"][1], _WHITE_PLATE_HSV["s_range"][1], _WHITE_PLATE_HSV["v_range"][1]])
    white_pct = float(cv2.countNonZero(cv2.inRange(hsv, w_lo, w_hi))) / total_px

    # Yellow mask
    y_lo = np.array([_YELLOW_PLATE_HSV["h_range"][0], _YELLOW_PLATE_HSV["s_range"][0], _YELLOW_PLATE_HSV["v_range"][0]])
    y_hi = np.array([_YELLOW_PLATE_HSV["h_range"][1], _YELLOW_PLATE_HSV["s_range"][1], _YELLOW_PLATE_HSV["v_range"][1]])
    yellow_pct = float(cv2.countNonZero(cv2.inRange(hsv, y_lo, y_hi))) / total_px

    if white_pct > 0.25:
        return "white"
    elif yellow_pct > 0.15:
        return "yellow"
    return "unknown"


def _apply_clahe_crop(crop: np.ndarray, clip_limit: float = 4.0, tile: tuple = (4, 4)) -> np.ndarray:
    """Aggressive CLAHE on the L-channel, tuned for small plate crops."""
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    l_ch, a_ch, b_ch = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile)
    l_eq = clahe.apply(l_ch)
    merged = cv2.merge((l_eq, a_ch, b_ch))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def _gamma_correct(image: np.ndarray, gamma: float) -> np.ndarray:
    """Apply gamma correction. gamma < 1.0 brightens; > 1.0 darkens."""
    if abs(gamma - 1.0) < 0.01:
        return image
    inv_gamma = 1.0 / gamma
    table = np.array([(i / 255.0) ** inv_gamma * 255 for i in range(256)], dtype=np.uint8)
    return cv2.LUT(image, table)


def _unsharp_mask(image: np.ndarray, sigma: float = 1.0, strength: float = 1.5) -> np.ndarray:
    """Sharpen character edges via unsharp masking."""
    blurred = cv2.GaussianBlur(image, (0, 0), sigma)
    sharpened = cv2.addWeighted(image, 1.0 + strength, blurred, -strength, 0)
    return np.clip(sharpened, 0, 255).astype(np.uint8)


def _suppress_saturation(crop: np.ndarray, factor: float = 0.3) -> np.ndarray:
    """Reduce saturation to flatten color noise on plate surface."""
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = hsv[:, :, 1] * factor
    hsv = np.clip(hsv, 0, 255).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def enhance_plate_crop(crop: np.ndarray) -> np.ndarray:
    """Full plate-crop enhancement chain optimized for Uganda plates.

    Steps:
      1. Classify plate background colour (white vs yellow)
      2. Colour-specific parameter tuning
      3. Saturation suppression (flatten colour noise)
      4. Aggressive CLAHE on crop L-channel
      5. Conditional gamma correction
      6. Unsharp mask for character edge sharpening

    Returns the enhanced BGR crop suitable for OCR.
    """
    if crop is None or crop.size == 0:
        return crop

    plate_color = detect_plate_color(crop)

    # Colour-specific parameters
    if plate_color == "white":
        clahe_clip = 4.0
        gamma = 0.85       # slight darken to make black text pop
        sat_factor = 0.25   # aggressively flatten white surface
        sharp_strength = 1.8
    elif plate_color == "yellow":
        clahe_clip = 3.5
        gamma = 1.1        # slight brighten to normalize yellow
        sat_factor = 0.35
        sharp_strength = 1.5
    else:
        clahe_clip = 3.0
        gamma = 1.0
        sat_factor = 0.4
        sharp_strength = 1.3

    # 1. Suppress saturation
    enhanced = _suppress_saturation(crop, factor=sat_factor)

    # 2. Aggressive CLAHE on the small plate crop
    enhanced = _apply_clahe_crop(enhanced, clip_limit=clahe_clip, tile=(4, 4))

    # 3. Gamma correction
    enhanced = _gamma_correct(enhanced, gamma)

    # 4. Sharpen character edges
    enhanced = _unsharp_mask(enhanced, sigma=1.0, strength=sharp_strength)

    return enhanced


def adaptive_binarize(gray: np.ndarray, block_size: int = 31, c: int = 10) -> np.ndarray:
    """Adaptive Gaussian thresholding — better than global Otsu for uneven
    plate illumination (shadows, glare patches, curved surfaces).

    Args:
        gray: Single-channel grayscale image.
        block_size: Neighbourhood size for adaptive threshold (must be odd).
        c: Constant subtracted from the mean.

    Returns:
        Binary image (white text on black, or vice versa depending on plate).
    """
    if gray.ndim == 3:
        gray = cv2.cvtColor(gray, cv2.COLOR_BGR2GRAY)

    # Ensure block_size is odd and >= 3
    if block_size % 2 == 0:
        block_size += 1
    block_size = max(3, block_size)

    binary = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        block_size, c,
    )
    return binary


def prepare_ocr_variants(crop: np.ndarray) -> list:
    """Generate multiple OCR-ready variants of a plate crop.

    Gives the OCR engine several enhanced versions to choose from:
      1. Enhanced grayscale (CLAHE + gamma + sharpened)
      2. Adaptive binary (Gaussian thresholding)
      3. Adaptive binary inverted
      4. Global Otsu (fallback, same as current pipeline)
      5. Raw grayscale (original, no enhancement)

    Returns a list of single-channel numpy arrays, all from the same crop.
    """
    if crop is None or crop.size == 0:
        return []

    # Enhanced version
    enhanced = enhance_plate_crop(crop)
    enhanced_gray = cv2.cvtColor(enhanced, cv2.COLOR_BGR2GRAY)

    # Adaptive binary variants
    adaptive_bin = adaptive_binarize(enhanced_gray, block_size=31, c=10)
    adaptive_inv = cv2.bitwise_not(adaptive_bin)

    # Global Otsu (current pipeline baseline)
    _, otsu = cv2.threshold(enhanced_gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)

    # Raw grayscale from original crop (unenhanced)
    raw_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop.copy()

    return [enhanced_gray, adaptive_bin, adaptive_inv, otsu, raw_gray]
