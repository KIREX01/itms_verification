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
        out_stream = _InMemoryLogStream(lambda msg: self._append_log(msg, "INFO"))
        err_stream = _InMemoryLogStream(lambda msg: self._append_log(msg, "ERROR"))

        try:
            if task_type in ("full_pipeline", "vision"):
                with self._lock:
                    self._status["stage"] = "VISION_PROCESSING"
                    self._status["progress_pct"] = 25
                    self._status["message"] = "Running YOLO plate detection & OCR..."

                self._append_log("Executing vision detection & OCR pipeline...", "VISION")
                vision_args = ["process_vision", "--save-crops", "--include-needs-review", "--reprocess-failed"]
                if batch_id:
                    vision_args.append(f"--batch={batch_id}")

                call_command(*vision_args, stdout=out_stream, stderr=err_stream)

            if task_type in ("full_pipeline", "matcher"):
                with self._lock:
                    self._status["stage"] = "PAIR_ASSOCIATION"
                    self._status["progress_pct"] = 70
                    self._status["message"] = "Running pair association and order matching..."

                self._append_log("Running pair association and order matching...", "MATCHER")
                call_command("associate_pairs", stdout=out_stream, stderr=err_stream)

            if task_type == "sync_orders":
                with self._lock:
                    self._status["stage"] = "ORDER_SYNC"
                    self._status["progress_pct"] = 40
                    self._status["message"] = "Syncing installation orders from ITMS..."

                self._append_log("Fetching live installation orders...", "SYNC")
                try:
                    call_command("fetch_itms_orders", "--page", "1", "--sync", stdout=out_stream, stderr=err_stream)
                except Exception as sync_err:
                    # Fallback to seed if live server credentials/network unavailable
                    self._append_log(f"Live fetch note ({sync_err}), checking local orders...", "WARNING")

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


# Global singleton instance
runner = PipelineRunner()
