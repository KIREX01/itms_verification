"""
Thread-safe background pipeline runner for the Web Operator UI.

Runs long-running commands (e.g. process_vision, associate_pairs, fetch_itms_orders)
in background threads while allowing web clients to poll progress, status, and activity logs.
"""
import io
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

from django.core.management import call_command

logger = logging.getLogger(__name__)


class _InMemoryLogStream(io.StringIO):
    """Captures stream output and appends to runner logs in real-time."""

    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def write(self, s: str):
        super().write(s)
        stripped = s.strip()
        if stripped:
            for line in stripped.splitlines():
                if line.strip():
                    self.callback(line.strip())
        return len(s)


def _python_executable() -> str:
    """The project's interpreter (a Windows service's sys.executable may be pythonservice.exe)."""
    import sys
    from pathlib import Path
    from django.conf import settings

    base = Path(settings.BASE_DIR)
    for cand in (base / ".venv" / "Scripts" / "python.exe", base / ".venv" / "bin" / "python"):
        if cand.is_file():
            return str(cand)
    exe = Path(sys.executable)
    if exe.name.lower().startswith("pythonservice"):
        alt = exe.with_name("python.exe")
        if alt.is_file():
            return str(alt)
    return sys.executable


def _use_subprocess() -> bool:
    from django.conf import settings
    from core.services import config_service

    if getattr(settings, "IS_TESTING", False):
        return False
    return bool(config_service.get_setting("vision.pipeline_in_subprocess", True))


def run_management_command(args, out_stream, err_stream) -> None:
    """
    Runs a heavy management command (vision / pairing). By default in a separate process with lowered CPU
    priority, so YOLO / OCR never hold the web server's GIL: the web UI and phone uploads stay responsive.
    Output lines stream into the runner log as before. Raises RuntimeError when the command fails.
    """
    if not _use_subprocess():
        call_command(*args, stdout=out_stream, stderr=err_stream)
        return

    import os
    import subprocess
    import sys
    from django.conf import settings

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    kwargs = {}
    if sys.platform == "win32":
        BELOW_NORMAL_PRIORITY_CLASS, CREATE_NO_WINDOW = 0x00004000, 0x08000000
        kwargs["creationflags"] = BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
    else:
        kwargs["preexec_fn"] = lambda: os.nice(10)
    cmd = [_python_executable(), str(Path_(settings.BASE_DIR) / "manage.py"), *args]
    proc = subprocess.Popen(cmd, cwd=str(settings.BASE_DIR), env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **kwargs)
    tail = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        if line:
            out_stream.write(line + "\n")
            tail = (tail + [line])[-5:]
    code = proc.wait()
    if code != 0:
        err_stream.write(f"{args[0]} exited with code {code}\n")
        raise RuntimeError(f"{args[0]} failed (exit {code}): {tail[-1] if tail else 'no output'}")


def Path_(p):
    from pathlib import Path
    return Path(p)


class PipelineRunner:
    """Manages background task execution for the web dashboard."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._status: Dict[str, Any] = {
            "running": False,
            "task_type": "",
            "stage": "IDLE",
            "progress_pct": 0,
            "message": "System ready.",
            "started_at": None,
            "completed_at": None,
            "success": True,
            "logs": [],
        }

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _append_log(self, message: str, level: str = "INFO"):
        with self._lock:
            entry = {
                "time": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "text": message,
            }
            self._status["logs"].append(entry)
            # Keep last 150 log entries
            if len(self._status["logs"]) > 150:
                self._status["logs"] = self._status["logs"][-150:]

    def start_pipeline(self, task_type: str = "full_pipeline", batch_id: Optional[str] = None) -> bool:
        """Starts a background pipeline task if not already running."""
        with self._lock:
            if self._status["running"]:
                return False

            self._status = {
                "running": True,
                "task_type": task_type,
                "stage": "INITIALIZING",
                "progress_pct": 5,
                "message": f"Starting {task_type}...",
                "started_at": datetime.now().isoformat(),
                "completed_at": None,
                "success": True,
                "logs": [],
            }

        self._append_log(f"Initiated {task_type} in background...", "START")

        self._thread = threading.Thread(
            target=self._run_worker,
            args=(task_type, batch_id),
            daemon=True,
        )
        self._thread.start()
        return True

    def _run_worker(self, task_type: str, batch_id: Optional[str] = None):
        """Worker thread executing management commands."""
        def _handle_log_line(msg: str, level: str = "INFO"):
            self._append_log(msg, level)
            if "Phase 1:" in msg:
                with self._lock:
                    self._status["stage"] = "PRE_PAIRING"
                    self._status["message"] = "Phase 1: Establishing physical pairs..."
            elif "Phase 2:" in msg:
                with self._lock:
                    self._status["stage"] = "JOINT_VISION"
                    self._status["message"] = "Phase 2: Running Dual-Stream Joint Vision..."
            elif "Phase 3:" in msg:
                with self._lock:
                    self._status["stage"] = "SINGLE_VISION"
                    self._status["message"] = "Phase 3: Running Single-Stream OCR..."

        out_stream = _InMemoryLogStream(lambda msg: _handle_log_line(msg, "INFO"))
        err_stream = _InMemoryLogStream(lambda msg: _handle_log_line(msg, "ERROR"))

        try:
            if task_type in ("full_pipeline", "vision"):
                with self._lock:
                    self._status["stage"] = "PRE_PAIRING"
                    self._status["progress_pct"] = 25
                    self._status["message"] = "Starting Vision Pipeline..."

                self._append_log("Executing vision detection & OCR pipeline...", "VISION")
                vision_args = ["process_vision", "--save-crops", "--include-needs-review", "--reprocess-failed"]
                if batch_id:
                    vision_args.append(f"--batch={batch_id}")

                run_management_command(vision_args, out_stream, err_stream)

            if task_type in ("full_pipeline", "matcher"):
                with self._lock:
                    self._status["stage"] = "ORDER_MATCHING"
                    self._status["progress_pct"] = 70
                    self._status["message"] = "Running pair association and order matching..."

                self._append_log("Running pair association and order matching...", "MATCHER")
                run_management_command(["associate_pairs"], out_stream, err_stream)

            if task_type == "sync_orders":
                with self._lock:
                    self._status["stage"] = "ORDER_SYNC"
                    self._status["progress_pct"] = 30
                    self._status["message"] = "Syncing active orders and shift archive from ITMS..."

                self._append_log("Starting unified shift synchronization (active queue + shift archive)...", "SYNC")
                try:
                    from core.services.order_sync import OrderSyncService
                    sync_svc = OrderSyncService()
                    res = sync_svc.sync_shift_scoped(force=True)
                    self._append_log(res.get("message", "Shift synchronization complete."), "SUCCESS")
                except Exception as sync_err:
                    self._append_log(f"Live sync note ({sync_err}), checking local orders...", "WARNING")

            with self._lock:
                self._status["running"] = False
                self._status["stage"] = "COMPLETED"
                self._status["progress_pct"] = 100
                self._status["message"] = f"{task_type.replace('_', ' ').title()} finished successfully."
                self._status["completed_at"] = datetime.now().isoformat()
                self._status["success"] = True

            self._append_log("Background task finished successfully.", "SUCCESS")

        except Exception as exc:
            logger.error("Pipeline runner error: %s", exc, exc_info=True)
            with self._lock:
                self._status["running"] = False
                self._status["stage"] = "FAILED"
                self._status["progress_pct"] = 100
                self._status["message"] = f"Error: {exc}"
                self._status["completed_at"] = datetime.now().isoformat()
                self._status["success"] = False

            self._append_log(f"Execution failed: {exc}", "ERROR")

        finally:
            try:
                from django.db import connection
                connection.close()
            except Exception:
                pass


# Global singleton instance
runner = PipelineRunner()
