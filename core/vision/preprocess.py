"""
Image preprocessing for the plate-detection pipeline.

Pipeline: EXIF auto-rotation -> bilateral denoise -> CLAHE contrast
enhancement (LAB space) -> Hough-line based deskew -> standardized
height scaling.

All functions take/return numpy BGR arrays (OpenCV convention) so they can
be chained directly with the detector and viewer modules.
"""
import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

TARGET_HEIGHT = 720

# ---------------------------------------------------------------------------
# EXIF orientation map.  Tag 0x0112 encodes how the camera was held.
# cv2.imread() ignores this, so smartphone portrait-mode photos appear
# rotated 90°/180°/270° unless we correct them before any vision step.
# ---------------------------------------------------------------------------
_EXIF_ORIENTATION_OPS = {
    2: (1, None),           # flipped horizontally
    3: (None, cv2.ROTATE_180),
    4: (1, cv2.ROTATE_180),  # flipped + 180
    5: (1, cv2.ROTATE_90_COUNTERCLOCKWISE),
    6: (None, cv2.ROTATE_90_CLOCKWISE),        # most common portrait shot
    7: (1, cv2.ROTATE_90_CLOCKWISE),
    8: (None, cv2.ROTATE_90_COUNTERCLOCKWISE),
}


def _read_exif_orientation(path: str) -> int:
    """
    Extract the EXIF Orientation tag (0x0112) from a JPEG/TIFF without
    needing Pillow.  Falls back to 1 (normal) if anything goes wrong.
    """
    try:
        from PIL import Image as _PILImage
        with _PILImage.open(path) as pil_img:
            exif = pil_img.getexif()
            return exif.get(0x0112, 1)
    except Exception:
        pass
    return 1  # no EXIF or Pillow unavailable — assume upright


def auto_orient(image: np.ndarray, exif_orientation: int) -> np.ndarray:
    """
    Rotate/flip a BGR array according to a standard EXIF orientation value.
    Returns the image unchanged if orientation is 1 (normal) or unknown.
    """
    ops = _EXIF_ORIENTATION_OPS.get(exif_orientation)
    if ops is None:
        return image

    flip_code, rotation = ops
    if flip_code is not None:
        image = cv2.flip(image, flip_code)
    if rotation is not None:
        image = cv2.rotate(image, rotation)
    return image


def denoise(image: np.ndarray) -> np.ndarray:
    """Bilateral filter: reduces noise while keeping plate edges sharp."""
    return cv2.bilateralFilter(image, d=9, sigmaColor=75, sigmaSpace=75)


def enhance_contrast_clahe(image: np.ndarray, clip_limit: float = 2.5, tile_grid=(8, 8)) -> np.ndarray:
    """Apply CLAHE to the L channel in LAB color space (handles uneven field lighting)."""
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=tile_grid)
    l_eq = clahe.apply(l_channel)
    merged = cv2.merge((l_eq, a_channel, b_channel))
    return cv2.cvtColor(merged, cv2.COLOR_LAB2BGR)


def estimate_skew_angle(image: np.ndarray) -> float:
    """Estimate dominant line-angle skew via Hough transform. Returns degrees."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=120)
    if lines is None:
        return 0.0

    angles = []
    for rho_theta in lines[:50]:
        _, theta = rho_theta[0]
        angle_deg = (theta * 180.0 / np.pi) - 90.0
        # Only trust near-horizontal candidates (plates are wider than tall)
        if -45 < angle_deg < 45:
            angles.append(angle_deg)

    if not angles:
        return 0.0
    return float(np.median(angles))


def deskew(image: np.ndarray, max_correction_deg: float = 15.0) -> np.ndarray:
    """Rotate the image to correct for camera tilt, clamped to a safe range."""
    angle = estimate_skew_angle(image)
    angle = max(-max_correction_deg, min(max_correction_deg, angle))
    if abs(angle) < 0.5:
        return image

    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image, matrix, (w, h),
        flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE,
    )


def normalize_resolution(image: np.ndarray, target_height: int = TARGET_HEIGHT) -> np.ndarray:
    """Scale image so its height matches target_height, preserving aspect ratio."""
    h, w = image.shape[:2]
    if h == target_height:
        return image
    scale = target_height / float(h)
    new_w = max(1, int(w * scale))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
    return cv2.resize(image, (new_w, target_height), interpolation=interp)


def preprocess_pipeline(image: np.ndarray, enhance_whole: bool = True) -> np.ndarray:
    """Full preprocessing chain applied before plate detection.

    Steps:
      1. Resolution normalization (standardize height to 720p)
      2. Dynamic tone enhancement (Pillow autocontrast, luminance variance scaling,
         saturation boost, sharpness, brightness normalization)
      3. Bilateral denoising (reduces noise while keeping plate edges sharp)
      4. CLAHE contrast enhancement (in LAB color space)
      5. Hough-line deskewing (compensates for camera tilt)
    """
    image = normalize_resolution(image)
    if enhance_whole:
        try:
            from core.vision.plate_enhancer import enhance_whole_image_array
            image = enhance_whole_image_array(image)
        except Exception as exc:
            logger.debug("enhance_whole_image_array skipped: %s", exc)
    image = denoise(image)
    image = enhance_contrast_clahe(image)
    image = deskew(image)
    return image


def load_image(path: str) -> np.ndarray:
    """Load an image from disk, guaranteeing correct upright orientation via Pillow EXIF transpose.

    Smartphone cameras store the raw sensor raster and rely on EXIF orientation
    tags (0x0112). Using Pillow's ``exif_transpose`` physically rotates the pixel
    buffer upright so that all downstream vision stages (YOLO plate detection,
    OCR, vehicle orientation classification) receive standing upright photos.
    """
    from PIL import Image, ImageOps
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            rgb = np.array(img.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        image = cv2.imread(path, cv2.IMREAD_COLOR)
        if image is None:
            raise FileNotFoundError(f"Could not read image (unsupported format or missing file): {path}")
        return image
