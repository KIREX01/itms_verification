"""
Side-by-side visual evidence viewer.

Launches a composed front|rear comparison image so an operator can
visually confirm vehicle identity (color, model, damage, plate placement)
before approving a pair.

Architecture:
- Standalone, highly responsive Python GUI window powered by OpenCV HighGUI.
- When invoked from the TUI, it runs as a non-blocking background Python process,
  ensuring the TUI event loop stays 100% responsive with zero lag or freezes.
- Active message loop at 50 Hz ensures the window is smooth, resizable, and
  never triggers Windows "Not Responding" alerts.
- Single-key close: [Esc], [Q], or clicking the window close button.
"""
import argparse
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

HEADER_HEIGHT = 44
FOOTER_HEIGHT = 32
GUTTER_WIDTH = 10


def _create_header_bar(width: int, text: str, bg_bgr: tuple) -> np.ndarray:
    """Creates a colored banner bar with clean typography."""
    bar = np.zeros((HEADER_HEIGHT, width, 3), dtype=np.uint8)
    bar[:] = bg_bgr
    cv2.putText(
        bar, text, (14, HEADER_HEIGHT - 14),
        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (255, 255, 255), 2, cv2.LINE_AA,
    )
    return bar


def _create_footer_bar(width: int, left_text: str, right_text: str) -> np.ndarray:
    """Creates a dark guidance footer bar at the bottom of the comparison."""
    bar = np.zeros((FOOTER_HEIGHT, width, 3), dtype=np.uint8)
    bar[:] = (28, 28, 28)
    cv2.putText(
        bar, left_text, (12, FOOTER_HEIGHT - 10),
        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (200, 200, 200), 1, cv2.LINE_AA,
    )
    # Right-aligned text calculation
    (w, _), _ = cv2.getTextSize(right_text, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
    cv2.putText(
        bar, right_text, (max(12, width - w - 14), FOOTER_HEIGHT - 10),
        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (140, 180, 240), 1, cv2.LINE_AA,
    )
    return bar


def _load_viewer_image(path: str) -> Optional[np.ndarray]:
    """Loads an image with automatic EXIF transpose to ensure upright display."""
    if not path or not os.path.isfile(path):
        return None
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            rgb = np.array(img.convert("RGB"))
            return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        return cv2.imread(path, cv2.IMREAD_COLOR)


def compose_side_by_side(
    front_path: str,
    rear_path: str,
    target_height: int = 560,
    title_label: str = "",
) -> np.ndarray:
    """
    Builds a high-visibility composed canvas:
      [ FRONT EVIDENCE BANNER ]  │  [ REAR EVIDENCE BANNER ]
      [ Front Photograph      ]  │  [ Rear Photograph       ]
      [ Guidance Footer: Esc/Q to Close | ITMS Forensic Audit ]
    """
    front = _load_viewer_image(front_path)
    rear = _load_viewer_image(rear_path)

    # Fallback placeholder for missing photo
    if front is None:
        front = np.zeros((target_height, int(target_height * 1.33), 3), dtype=np.uint8)
        cv2.putText(front, "FRONT PHOTO NOT FOUND", (40, target_height // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 220), 2, cv2.LINE_AA)

    if rear is None:
        rear = np.zeros((target_height, int(target_height * 1.33), 3), dtype=np.uint8)
        cv2.putText(rear, "REAR PHOTO NOT FOUND", (40, target_height // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 220), 2, cv2.LINE_AA)

    # Uniform height scaling
    def _scale(img):
        h, w = img.shape[:2]
        scale = target_height / float(h)
        return cv2.resize(img, (max(1, int(w * scale)), target_height), interpolation=cv2.INTER_AREA)

    front_scaled = _scale(front)
    rear_scaled = _scale(rear)

    # Headers: Green for Front (34, 139, 34), Blue for Rear (180, 105, 38 in BGR)
    front_header = _create_header_bar(front_scaled.shape[1], "▶ FRONT EVIDENCE", (34, 139, 34))
    rear_header = _create_header_bar(rear_scaled.shape[1], "▶ REAR EVIDENCE", (180, 105, 38))

    front_col = np.vstack([front_header, front_scaled])
    rear_col = np.vstack([rear_header, rear_scaled])

    # Gutter separator
    gutter = np.full((front_col.shape[0], GUTTER_WIDTH, 3), 40, dtype=np.uint8)

    # Combined top section
    top_grid = np.hstack([front_col, gutter, rear_col])

    # Footer
    label = f"Evidence ID: {title_label}" if title_label else "ITMS Forensic Inspection"
    footer = _create_footer_bar(
        top_grid.shape[1],
        "[ESC] or [Q] to Close  │  Resize window freely",
        label,
    )

    return np.vstack([top_grid, footer])


def _run_window(front_path: str, rear_path: str, window_title: str) -> None:
    """Runs a dedicated GUI window with an active Windows event loop."""
    composed = compose_side_by_side(front_path, rear_path, title_label=window_title)

    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)

    # Scale initial window to comfortable desktop dimensions
    h, w = composed.shape[:2]
    max_w, max_h = 1200, 680
    scale = min(max_w / float(w), max_h / float(h), 1.0)
    cv2.resizeWindow(window_title, int(w * scale), int(h * scale))

    cv2.imshow(window_title, composed)

    # Continuous active event loop (50 Hz) - keeps the window 100% responsive
    while True:
        key = cv2.waitKey(20) & 0xFF
        if key in (27, ord("q"), ord("Q"), ord("x"), ord("X")):
            break
        try:
            # Detect user clicking the window 'X' close button
            if cv2.getWindowProperty(window_title, cv2.WND_PROP_VISIBLE) < 1:
                break
        except Exception:
            break

    cv2.destroyAllWindows()


def compose_single_image(
    image_path: str,
    bbox: Optional[list] = None,
    title_label: str = "",
    plate_label: str = "",
    orient_label: str = "",
    target_height: int = 650,
) -> np.ndarray:
    image = _load_viewer_image(image_path)
    if image is None:
        image = np.zeros((target_height, int(target_height * 1.33), 3), dtype=np.uint8)
        cv2.putText(image, "IMAGE NOT FOUND ON DISK", (40, target_height // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 220), 2, cv2.LINE_AA)
        return image

    img_vis = image.copy()

    # Draw plate bounding box if present
    if bbox and len(bbox) == 4:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        # Cyan / Green box around plate
        cv2.rectangle(img_vis, (x1, y1), (x2, y2), (0, 255, 0), 3)
        tag = f"PLATE: {plate_label}" if plate_label else "LICENSE PLATE"
        (tw, th), _ = cv2.getTextSize(tag, cv2.FONT_HERSHEY_SIMPLEX, 0.65, 2)
        tag_y = max(th + 6, y1 - 8)
        cv2.rectangle(img_vis, (x1, tag_y - th - 6), (x1 + tw + 12, tag_y + 4), (0, 255, 0), -1)
        cv2.putText(img_vis, tag, (x1 + 6, tag_y), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 0, 0), 2, cv2.LINE_AA)

    # Scale to comfortable height
    h, w = img_vis.shape[:2]
    scale = target_height / float(h)
    img_scaled = cv2.resize(img_vis, (max(1, int(w * scale)), target_height), interpolation=cv2.INTER_AREA)

    # Colored top header banner
    orient_str = orient_label.upper() if orient_label else "EVIDENCE PHOTO"
    color = (34, 139, 34) if "FRONT" in orient_str else (180, 105, 38) if "REAR" in orient_str else (60, 60, 60)
    banner_text = f"▶ {orient_str}"
    if plate_label:
        banner_text += f"  │  DETECTED: {plate_label}"
    header = _create_header_bar(img_scaled.shape[1], banner_text, color)

    # Footer
    footer_right = title_label or "ITMS Vision Pipeline Analysis"
    footer = _create_footer_bar(
        img_scaled.shape[1],
        "[ESC] or [Q] to Close  │  Resize window freely",
        footer_right,
    )

    return np.vstack([header, img_scaled, footer])


def _run_single_window(
    image_path: str,
    bbox: Optional[list],
    window_title: str,
    plate_label: str,
    orient_label: str,
) -> None:
    composed = compose_single_image(
        image_path, bbox=bbox, title_label=window_title, plate_label=plate_label, orient_label=orient_label
    )
    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
    h, w = composed.shape[:2]
    max_w, max_h = 1200, 720
    scale = min(max_w / float(w), max_h / float(h), 1.0)
    cv2.resizeWindow(window_title, int(w * scale), int(h * scale))
    cv2.imshow(window_title, composed)

    while True:
        key = cv2.waitKey(20) & 0xFF
        if key in (27, ord("q"), ord("Q"), ord("x"), ord("X")):
            break
        try:
            if cv2.getWindowProperty(window_title, cv2.WND_PROP_VISIBLE) < 1:
                break
        except Exception:
            break

    cv2.destroyAllWindows()


def show_side_by_side(
    front_path: str,
    rear_path: str,
    window_title: str = "ITMS Evidence Comparison",
    block: bool = False,
) -> Optional[str]:
    """Displays a side-by-side comparison in a responsive standalone window."""
    composed = compose_side_by_side(front_path, rear_path, title_label=window_title)
    tmp_path = os.path.join(tempfile.gettempdir(), "itms_evidence_comparison.png")
    cv2.imwrite(tmp_path, composed)

    if block:
        _run_window(front_path, rear_path, window_title)
        return tmp_path

    cmd = [
        sys.executable,
        "-m",
        "core.services.viewer",
        front_path,
        rear_path,
        "--title",
        window_title,
    ]

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return tmp_path
    except Exception:
        _run_window(front_path, rear_path, window_title)
        return tmp_path


def show_pair_evidence(pair, window_title: Optional[str] = None, block: bool = False) -> Optional[str]:
    """Automatically determines whether to show side-by-side or single photo for a pair.

    - If both front and rear images exist on disk: opens side-by-side comparison.
    - If only one image exists on disk: opens single image viewer with plate bbox & orientation.
    - If neither exists on disk: returns None.
    """
    from django.conf import settings
    front_img = getattr(pair, "front_image", None)
    rear_img = getattr(pair, "rear_image", None)

    front_path = os.path.join(settings.MEDIA_ROOT, front_img.vault_file) if front_img and front_img.vault_file else ""
    rear_path = os.path.join(settings.MEDIA_ROOT, rear_img.vault_file) if rear_img and rear_img.vault_file else ""

    has_front = bool(front_path and os.path.isfile(front_path))
    has_rear = bool(rear_path and os.path.isfile(rear_path))

    title = window_title or getattr(pair, "registration_number_detected", "ITMS Evidence")

    if has_front and has_rear:
        return show_side_by_side(front_path, rear_path, window_title=f"{title} (Front | Rear)", block=block)
    elif has_front:
        return show_single_image(
            front_path,
            bbox=front_img.bbox,
            window_title=f"{title} - Front Evidence",
            plate_label=getattr(pair, "registration_number_detected", ""),
            orient_label=front_img.orientation or "FRONT",
            block=block,
        )
    elif has_rear:
        return show_single_image(
            rear_path,
            bbox=rear_img.bbox,
            window_title=f"{title} - Rear Evidence",
            plate_label=getattr(pair, "registration_number_detected", ""),
            orient_label=rear_img.orientation or "REAR",
            block=block,
        )
    return None


def show_single_image(
    image_path: str,
    bbox: Optional[list] = None,
    window_title: str = "ITMS Vision Analysis",
    plate_label: str = "",
    orient_label: str = "",
    block: bool = False,
) -> Optional[str]:
    """Displays a single image with bounding box in a responsive standalone window."""
    composed = compose_single_image(
        image_path, bbox=bbox, title_label=window_title, plate_label=plate_label, orient_label=orient_label
    )
    tmp_path = os.path.join(tempfile.gettempdir(), "itms_single_evidence.png")
    cv2.imwrite(tmp_path, composed)

    if block:
        _run_single_window(image_path, bbox, window_title, plate_label, orient_label)
        return tmp_path

    cmd = [
        sys.executable,
        "-m",
        "core.services.viewer",
        "--single",
        image_path,
        "--title",
        window_title,
    ]
    if bbox and len(bbox) == 4:
        cmd.extend(["--bbox"] + [str(int(v)) for v in bbox])
    if plate_label:
        cmd.extend(["--plate", plate_label])
    if orient_label:
        cmd.extend(["--orient", orient_label])

    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return tmp_path
    except Exception:
        _run_single_window(image_path, bbox, window_title, plate_label, orient_label)
        return tmp_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ITMS Side-by-Side & Single Evidence Viewer")
    parser.add_argument("--single", action="store_true", help="View a single image with bounding box")
    parser.add_argument("--bbox", nargs=4, type=int, default=None, help="Bounding box x1 y1 x2 y2")
    parser.add_argument("--plate", default="", help="Detected plate text")
    parser.add_argument("--orient", default="", help="Orientation label (FRONT/REAR)")
    parser.add_argument("--title", default="ITMS Evidence Review", help="Window title")
    parser.add_argument("paths", nargs="*", help="Image paths: 1 for single, 2 for front/rear")

    args = parser.parse_args()
    if args.single:
        target = args.paths[0] if args.paths else ""
        _run_single_window(target, args.bbox, args.title, args.plate, args.orient)
    elif len(args.paths) >= 2:
        _run_window(args.paths[0], args.paths[1], args.title)
    elif len(args.paths) == 1:
        _run_single_window(args.paths[0], args.bbox, args.title, args.plate, args.orient)
    else:
        print("Usage: python -m core.services.viewer <front> <rear> [title] OR --single <image>")

