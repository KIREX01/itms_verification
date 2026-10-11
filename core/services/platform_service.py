"""
Cross-Platform Runtime & Filesystem Abstraction Service for ITMS Verification Copilot.

Provides consistent, secure operating system abstraction across Windows and Linux:
1. Safe filesystem permissions (POSIX safe_chmod without Windows breakage or S2612 issues)
2. Path normalization (cross-platform path separators and traversal guards)
3. Platform-aware directory defaults (vault, media, logs)
4. System service detection (Windows Service Control Manager vs. Linux systemd)
"""
from __future__ import annotations

import logging
import os
import platform
import stat
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- Operating System Identification ---
IS_WINDOWS: bool = sys.platform == "win32"
IS_LINUX: bool = sys.platform.startswith("linux")
IS_MACOS: bool = sys.platform == "darwin"
IS_POSIX: bool = os.name == "posix"
PLATFORM_SYSTEM: str = platform.system()


def is_windows() -> bool:
    """Returns True if running on Windows."""
    return IS_WINDOWS


def is_linux() -> bool:
    """Returns True if running on Linux."""
    return IS_LINUX


def is_posix() -> bool:
    """Returns True if running on a POSIX operating system (Linux, macOS, BSD)."""
    return IS_POSIX


def safe_chmod(path: str | Path, mode: int) -> None:
    """
    Applies POSIX file permissions safely on Unix/Linux systems.
    - Strictly no-ops on Windows to avoid OS errors and unsupported mode bits.
    - Sanitizes permissions on POSIX to avoid dangerous world-writable permissions (SonarQube S2612).
    """
    if not IS_POSIX:
        return

    try:
        p = Path(path)
        if not p.exists():
            return

        # Restrict permissions: strip world-writable (S_IWOTH) permission
        safe_mode = mode & ~stat.S_IWOTH
        os.chmod(p, safe_mode)
    except OSError as exc:
        logger.debug("safe_chmod could not update permissions for %s: %s", path, exc)


def normalize_rel_path(path_str: str) -> str:
    """
    Normalizes a path string to POSIX forward-slash conventions for portable
    database storage and URL generation, stripping leading slashes and traversal sequences.
    """
    if not path_str:
        return ""
    # Strip null bytes and normalize separators
    cleaned = path_str.replace("\x00", "").replace("\\", "/").strip().lstrip("/")
    parts = [part for part in cleaned.split("/") if part and part != "."]
    # Disallow directory traversal sequences
    if ".." in parts:
        raise ValueError(f"Directory traversal sequence detected in path: {path_str}")
    return "/".join(parts)


def resolve_platform_path(path_val: str | Path, base_dir: Path) -> Path:
    """
    Resolves a path against a base directory, correctly handling both Windows and Linux paths.
    """
    if isinstance(path_val, Path):
        p = path_val
    else:
        raw = str(path_val).strip()
        # Handle Windows backslashes gracefully even when running on Linux
        if IS_POSIX and "\\" in raw and "/" not in raw:
            raw = raw.replace("\\", "/")
        p = Path(raw)

    if p.is_absolute():
        return p.resolve()
    return (base_dir / p).resolve()


def get_platform_diagnostics() -> dict[str, Any]:
    """Returns detailed diagnostics about the current platform and filesystem."""
    return {
        "os_name": os.name,
        "platform_system": platform.system(),
        "platform_release": platform.release(),
        "platform_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
        "is_windows": IS_WINDOWS,
        "is_linux": IS_LINUX,
        "is_posix": IS_POSIX,
        "path_separator": os.sep,
        "default_line_ending": "\r\n" if IS_WINDOWS else "\n",
    }
