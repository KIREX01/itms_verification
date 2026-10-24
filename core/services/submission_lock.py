"""
Cross-Process and Cross-Thread Concurrency Lock for ITMS Order Submissions.

Ensures that order submissions execute strictly one at a time across all
Gunicorn worker processes and threads, preventing upstream ITMS socket saturation,
session cookie collisions, and race conditions during batch uploads.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Tuple

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# In-memory thread lock for intra-process coordination
_THREAD_LOCK = threading.RLock()

# Lockfile location (persisted across Gunicorn worker processes)
def _get_lockfile_path() -> Path:
    lock_dir = None
    try:
        from django.conf import settings
        lock_dir = getattr(settings, "MEDIA_ROOT", None)
    except Exception:
        lock_dir = None

    if lock_dir and os.path.exists(lock_dir):
        target_dir = Path(lock_dir) / "locks"
    else:
        target_dir = Path(tempfile.gettempdir()) / "itms_locks"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        target_dir = Path(tempfile.gettempdir())
    return target_dir / "submission_worker.lock"


def _get_lockinfo_path() -> Path:
    lockfile = _get_lockfile_path()
    return lockfile.with_suffix(".json")


class SubmissionLockError(Exception):
    """Raised when submission lock cannot be acquired."""
    def __init__(self, message: str, lock_info: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.lock_info = lock_info or {}


class CrossProcessLock:
    """
    Robust file descriptor lock supporting both Windows (msvcrt) and Linux (fcntl).
    Auto-releases on process termination or context exit.
    """

    def __init__(self, lockfile_path: Path):
        self.path = lockfile_path
        self._fd: Optional[int] = None

    def acquire(self, blocking: bool = False, timeout: float = 0.0) -> bool:
        start_time = time.time()
        while True:
            try:
                # Open with read/write access, creating if necessary
                self._fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT | os.O_TRUNC)

                if sys.platform == "win32":
                    import msvcrt
                    # Lock first byte in non-blocking mode
                    msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    # Exclusive non-blocking lock on Linux
                    fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                return True
            except (IOError, OSError):
                if self._fd is not None:
                    try:
                        os.close(self._fd)
                    except Exception:
                        pass
                    self._fd = None

                if not blocking or (timeout > 0 and (time.time() - start_time) >= timeout):
                    return False

                time.sleep(0.25)

    def release(self) -> None:
        if self._fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        os.lseek(self._fd, 0, os.SEEK_SET)
                        msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(self._fd, fcntl.LOCK_UN)
                    except Exception:
                        pass
            finally:
                try:
                    os.close(self._fd)
                except Exception:
                    pass
                self._fd = None


def is_submission_active() -> Tuple[bool, Dict[str, Any]]:
    """
    Checks if an order submission is currently active.
    Returns (is_locked, lock_metadata).
    Automatically recovers from stale locks older than 180 seconds.
    """
    lock_file = _get_lockfile_path()
    info_file = _get_lockinfo_path()

    lock_info: Dict[str, Any] = {}
    if info_file.exists():
        try:
            with open(info_file, "r", encoding="utf-8") as f:
                lock_info = json.load(f)
        except Exception:
            lock_info = {}

    started_at = lock_info.get("started_at", 0)
    now_ts = time.time()
    elapsed = now_ts - started_at if started_at > 0 else 0

    # Auto-expire locks older than 3 minutes (stale process crash recovery)
    if started_at > 0 and elapsed > 180:
        logger.warning("Auto-clearing stale submission lock held for %.1fs by pid %s", elapsed, lock_info.get("pid"))
        try:
            if info_file.exists():
                info_file.unlink(missing_ok=True)
            if lock_file.exists():
                lock_file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug("Could not remove stale lock files: %s", exc)
        return False, {}

    # Probe file lock without blocking
    probe_lock = CrossProcessLock(lock_file)
    acquired = probe_lock.acquire(blocking=False)
    if acquired:
        probe_lock.release()
        return False, {}

    lock_info["elapsed_seconds"] = round(elapsed, 1)
    return True, lock_info


def record_lock_metadata(
    pair_id: Optional[int] = None,
    order_number: str = "",
    registration_number: str = "",
    operator: str = "",
) -> None:
    """Writes metadata about the current active submission to disk for telemetry."""
    info_file = _get_lockinfo_path()
    try:
        data = {
            "pid": os.getpid(),
            "pair_id": pair_id,
            "order_number": order_number,
            "registration_number": registration_number,
            "operator": operator or "System",
            "started_at": time.time(),
            "started_iso": timezone.now().isoformat(),
        }
        with open(info_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as exc:
        logger.debug("Failed writing submission lock metadata: %s", exc)


def clear_lock_metadata() -> None:
    """Cleans up submission metadata file."""
    info_file = _get_lockinfo_path()
    try:
        if info_file.exists():
            info_file.unlink(missing_ok=True)
    except Exception as exc:
        logger.debug("Failed clearing submission lock metadata: %s", exc)


@contextmanager
def submission_lock(
    pair_id: Optional[int] = None,
    order_number: str = "",
    registration_number: str = "",
    operator: str = "",
    blocking: bool = False,
    timeout: float = 10.0,
) -> Generator[Dict[str, Any], None, None]:
    """
    Context manager to execute submission work under exclusive system lock.

    Args:
        pair_id: VehicleInstallationPair ID being processed.
        order_number: InstallationOrder number.
        registration_number: Plate registration number.
        operator: Username or email of operator initiating submit.
        blocking: If True, waits up to `timeout` seconds before giving up.
        timeout: Maximum seconds to wait if blocking is True.

    Raises:
        SubmissionLockError: If lock is already held and cannot be acquired.
    """
    lock_file = _get_lockfile_path()
    cross_lock = CrossProcessLock(lock_file)

    # 1. Acquire thread lock first
    if blocking:
        thread_acquired = _THREAD_LOCK.acquire(blocking=True, timeout=timeout)
    else:
        thread_acquired = _THREAD_LOCK.acquire(blocking=False)
    if not thread_acquired:
        active, info = is_submission_active()
        active_op = info.get("operator", "another operator")
        active_plate = info.get("registration_number") or info.get("order_number", "")
        detail = f" (submitting {active_plate} by {active_op})" if active_plate else ""
        raise SubmissionLockError(
            f"Another submission is currently in progress{detail}. Submissions must execute sequentially.",
            lock_info=info,
        )

    try:
        # 2. Acquire cross-process file lock
        acquired = cross_lock.acquire(blocking=blocking, timeout=timeout)
        if not acquired:
            active, info = is_submission_active()
            active_op = info.get("operator", "another operator")
            active_plate = info.get("registration_number") or info.get("order_number", "")
            detail = f" (submitting {active_plate} by {active_op})" if active_plate else ""
            raise SubmissionLockError(
                f"Another submission is currently in progress{detail}. Submissions must execute sequentially.",
                lock_info=info,
            )

        # 3. Record metadata
        record_lock_metadata(
            pair_id=pair_id,
            order_number=order_number,
            registration_number=registration_number,
            operator=operator,
        )

        yield {
            "pair_id": pair_id,
            "order_number": order_number,
            "registration_number": registration_number,
            "operator": operator,
        }

    finally:
        clear_lock_metadata()
        cross_lock.release()
        _THREAD_LOCK.release()
