"""
Clipboard & External Plate Importer Service.

Provides bulletproof clipboard ingestion for Windows & cross-platform environments:
1. Native Windows ctypes API (bypasses terminal keystroke limitations and buffer overflows)
2. pyperclip fallback
3. PowerShell fallback
4. Direct Excel (.xlsx, .xls), CSV, and TXT file import for 1,200+ plates
"""
import csv
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, List, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)


def get_clipboard_text() -> str:
    """
    Reads text directly from the operating system clipboard without relying on
    terminal keystroke emulation or bracketed paste.
    Handles 1,200+ lines (15KB+) instantaneously.
    """
    # 1. On Windows, try native ctypes user32 clipboard first (fastest, zero dependencies)
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            CF_UNICODETEXT = 13

            # OpenClipboard expects HWND (None = current task)
            if user32.OpenClipboard(None):
                try:
                    handle = user32.GetClipboardData(CF_UNICODETEXT)
                    if handle:
                        kernel32.GlobalLock.restype = ctypes.c_void_p
                        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
                        ptr = kernel32.GlobalLock(handle)
                        if ptr:
                            try:
                                text = ctypes.c_wchar_p(ptr).value or ""
                                return text
                            finally:
                                kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
                                kernel32.GlobalUnlock(handle)
                finally:
                    user32.CloseClipboard()
        except Exception as exc:
            logger.debug("ctypes clipboard error: %s", exc)

    # 2. Try pyperclip
    try:
        import pyperclip
        val = pyperclip.paste()
        if val:
            return val
    except Exception as exc:
        logger.debug("pyperclip error: %s", exc)

    # 3. Windows PowerShell fallback
    if sys.platform == "win32":
        try:
            import subprocess
            res = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                capture_output=True,
                text=True,
                check=False,
                timeout=3.0,
            )
            if res.returncode == 0 and res.stdout:
                return res.stdout
        except Exception as exc:
            logger.debug("powershell clipboard error: %s", exc)

    return ""


def set_clipboard_text(text: str) -> bool:
    """Copies text to the system clipboard."""
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import subprocess
            subprocess.run(
                ["clip.exe"],
                input=text,
                text=True,
                check=False,
                timeout=2.0,
            )
            return True
        except Exception:
            pass

    return False


def get_clipboard_plates() -> Tuple[List[str], int, List[str], str]:
    """
    Reads clipboard and parses all valid Ugandan license plates.
    Returns: (clean_plates, duplicate_count, duplicate_plates, raw_text)
    """
    from core.services import stock_monitoring_service
    raw_text = get_clipboard_text()
    if not raw_text.strip():
        return [], 0, [], ""

    clean_plates, dup_count, dup_plates = stock_monitoring_service.parse_plate_input_with_stats(raw_text)
    return clean_plates, dup_count, dup_plates, raw_text


def read_plates_from_file(file_path: Union[str, Path]) -> Tuple[List[str], int, List[str]]:
    """
    Reads plates from an Excel (.xlsx, .xls), CSV, or plain text file.
    Supports single or multi-column spreadsheets.
    """
    from core.services import stock_monitoring_service

    p = Path(file_path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"File not found: {p}")

    ext = p.suffix.lower()
    raw_text_chunks: List[str] = []

    if ext in (".xlsx", ".xls"):
        # Try openpyxl
        try:
            import openpyxl
            wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
            for sheet in wb.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for cell in row:
                        if cell is not None:
                            raw_text_chunks.append(str(cell))
            wb.close()
        except ImportError:
            # Fallback: if openpyxl not installed, read as text
            logger.warning("openpyxl not available, attempting text fallback for %s", p)
            raw_text_chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
    elif ext == ".csv":
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            for row in reader:
                for cell in row:
                    if cell.strip():
                        raw_text_chunks.append(cell.strip())
    else:
        # Plain text
        raw_text_chunks.append(p.read_text(encoding="utf-8", errors="replace"))

    combined_input = "\n".join(raw_text_chunks)
    return stock_monitoring_service.parse_plate_input_with_stats(combined_input)
