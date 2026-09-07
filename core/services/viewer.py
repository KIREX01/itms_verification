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
    front = cv2.imread(front_path, cv2.IMREAD_COLOR) if os.path.isfile(front_path) else None
    rear = cv2.imread(rear_path, cv2.IMREAD_COLOR) if os.path.isfile(rear_path) else None

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


def show_side_by_side(
    front_path: str,
    rear_path: str,
    window_title: str = "ITMS Evidence Review",
    block: bool = False,
) -> Optional[str]:
    """
    Displays the composed comparison in a standalone, responsive Python window.
    Saves the composed image to a temp file and launches a lightweight Python
    subprocess so the calling TUI never freezes or becomes unresponsive.
    """
    # Always write temporary preview image
    composed = compose_side_by_side(front_path, rear_path, title_label=window_title)
    tmp_path = os.path.join(tempfile.gettempdir(), "itms_evidence_compare.png")
    cv2.imwrite(tmp_path, composed)

    if block:
        # Run blocking in-process (e.g. from CLI scripts)
        _run_window(front_path, rear_path, window_title)
        return tmp_path

    # Launch non-blocking background Python process (pure Python, NO Windows Photo Viewer)
    try:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "core.services.viewer",
                front_path,
                rear_path,
                window_title,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return tmp_path
    except Exception:
        # Fallback to direct window if subprocess spawn fails
        _run_window(front_path, rear_path, window_title)
        return tmp_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ITMS Side-by-Side Evidence Viewer")
    parser.add_argument("front", help="Path to front photo")
    parser.add_argument("rear", help="Path to rear photo")
    parser.add_argument("title", nargs="?", default="ITMS Evidence Review", help="Window title")
    args = parser.parse_args()
    _run_window(args.front, args.rear, args.title)
