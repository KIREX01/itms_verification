"""
Edge lifecycle-event outbox (standard library only, so scripts/bootstrap.py can use it before Django starts).

Events are appended to secure/telemetry_outbox.json and sent to the central server with the next check-in.
Offline machines keep them until they reconnect (capped, oldest dropped first).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

MAX_EVENTS = 200
_LOCK = threading.Lock()
EVENT_TYPES = frozenset({
    "INSTALL_STARTED", "INSTALL_COMPLETED", "INSTALL_FAILED", "UPDATE_APPLIED", "UPDATE_FAILED",
    "ROLLBACK_APPLIED", "SERVICE_STARTED", "UNINSTALL_COMPLETED",
})


def _path(base_dir: Optional[Path] = None) -> Path:
    if base_dir is None and os.environ.get("CLOSE_TELEMETRY_DIR"):
        base_dir = Path(os.environ["CLOSE_TELEMETRY_DIR"])
    elif base_dir is None and _under_tests():
        import tempfile
        base_dir = Path(tempfile.gettempdir()) / f"close-telemetry-test-{os.getpid()}"
    base = Path(base_dir) if base_dir else Path(__file__).resolve().parents[2]
    return base / "secure" / "telemetry_outbox.json"


def _under_tests() -> bool:
    import sys
    return "pytest" in sys.modules or any(a == "test" for a in sys.argv[1:2])


def _read(path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record_event(event_type: str, ok: bool = True, from_version: str = "", to_version: str = "",
                 detail: str = "", metadata: Optional[Dict[str, Any]] = None, base_dir: Optional[Path] = None) -> None:
    """Queues one event. Never raises: telemetry must not break installs, updates or start-up."""
    if event_type not in EVENT_TYPES:
        return
    try:
        path = _path(base_dir)
        with _LOCK:
            events = _read(path)
            events.append({
                "event_type": event_type, "ok": bool(ok), "from_version": str(from_version)[:32],
                "to_version": str(to_version)[:32], "detail": str(detail)[:2000],
                "metadata": metadata or {}, "occurred_at": datetime.now(timezone.utc).isoformat(),
            })
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(events[-MAX_EVENTS:]), encoding="utf-8")
            os.replace(tmp, path)
    except Exception:
        pass


def pending(base_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    return _read(_path(base_dir))


def clear(sent: int, base_dir: Optional[Path] = None) -> None:
    """Drops the first `sent` events (the ones the central server acknowledged)."""
    try:
        path = _path(base_dir)
        with _LOCK:
            events = _read(path)[sent:]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(events), encoding="utf-8")
            os.replace(tmp, path)
    except Exception:
        pass
