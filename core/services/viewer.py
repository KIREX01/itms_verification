"""
Side-by-side visual evidence viewer.

Launches a composed front|rear comparison image so an operator can
visually confirm vehicle identity (color, model, damage) before
approving a pair -- OCR/orientation confidence alone isn't enough for a
human sign-off.

Two display modes:
  - OpenCV window (cv2.imshow) when a display is available.
  - Fallback: writes the composed comparison image to a temp file and
    opens it with the OS's default image viewer, for headless-TUI
    setups (e.g. run over SSH without X11 forwarding but with a local
    file-open capability), or simply returns the path if neither works
    so the TUI can show it inline via terminal graphics protocols.
"""
import os
import platform
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

HEADER_HEIGHT = 40
GUTTER = 8


def _label(img: np.ndarray, text: str) -> np.ndarray:
    """Add a labeled header bar above an image."""
    h, w = img.shape[:2]
    bar = np.zeros((HEADER_HEIGHT, w, 3), dtype=np.uint8)
    bar[:] = (40, 40, 40)
    cv2.putText(
        bar, text, (10, HEADER_HEIGHT - 12),
        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2, cv2.LINE_AA,
    )
    return np.vstack([bar, img])


def compose_side_by_side(front_path: str, rear_path: str, target_height: int = 480) -> np.ndarray:
    """Build a single image: [FRONT header+photo] | gutter | [REAR header+photo]."""
    front = cv2.imread(front_path, cv2.IMREAD_COLOR)
    rear = cv2.imread(rear_path, cv2.IMREAD_COLOR)

    if front is None:
        front = np.zeros((target_height, target_height, 3), dtype=np.uint8)
    if rear is None:
        rear = np.zeros((target_height, target_height, 3), dtype=np.uint8)

    def _resize(img):
        h, w = img.shape[:2]
        scale = target_height / float(h)
        return cv2.resize(img, (int(w * scale), target_height))

    front, rear = _resize(front), _resize(rear)
    front = _label(front, "FRONT")
    rear = _label(rear, "REAR")

    gutter = np.full((front.shape[0], GUTTER, 3), 200, dtype=np.uint8)
    return np.hstack([front, gutter, rear])


def show_side_by_side(front_path: str, rear_path: str, window_title: str = "Evidence Review") -> Optional[str]:
    """
    Attempts to display the composed comparison interactively. Returns the
    path of the composed image on disk (always written, so the TUI can
    reference it regardless of which display path succeeds).
    """
    composed = compose_side_by_side(front_path, rear_path)

    tmp_path = os.path.join(tempfile.gettempdir(), "itms_evidence_compare.png")
    cv2.imwrite(tmp_path, composed)

    if os.environ.get("DISPLAY") or platform.system() in ("Windows", "Darwin"):
        try:
            cv2.imshow(window_title, composed)
            cv2.waitKey(1)  # non-blocking; TUI event loop keeps running
            return tmp_path
        except Exception:
            pass

    # Headless fallback: open with OS default viewer
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", tmp_path])
        elif platform.system() == "Windows":
            os.startfile(tmp_path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", tmp_path])
    except Exception:
        pass  # Caller can still use the returned path (e.g. terminal image protocol)

    return tmp_path
