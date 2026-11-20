"""
Service control: status and actions for the background services every CLOSE Copilot machine runs.

  status_all()                      -> {service: {...}} for this machine (reported by installs on check-in)
  run_action(action, by, install)   -> runs an allowed action now (in the background), logged as a ServiceRun
  ACTIONS                           -> the only actions that exist (also the remote-command whitelist)
  CENTRAL_SETTINGS / set_central_setting(...)  -> sync & vision settings pushed to every machine

Services:  order_sync, kit_sync (ITMS sync daemons), vision (pipeline + models), outbox, policy_sync.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

SERVICE_LABELS = {
    "order_sync": "ITMS order sync",
    "kit_sync": "ITMS kit sync",
    "vision": "Vision pipeline & models",
    "outbox": "Offline outbox",
    "policy_sync": "Central policy check-in",
}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
def _safe(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return fn()
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _order_sync_status() -> Dict[str, Any]:
    from core.services.order_sync import ShiftOrderSyncDaemon
    from core.services import config_service

    d = ShiftOrderSyncDaemon.get_instance().get_status()
    return {"running": bool(d.get("is_running")), "enabled": bool(config_service.get_setting("sync.auto_sync_enabled", True)),
            "interval_seconds": d.get("interval_seconds"), "last_run": d.get("last_run_time"),
            "runs": d.get("run_count"), "last_result": _short(d.get("last_result")), **_itms_account()}


def _itms_account() -> Dict[str, Any]:
    """The ITMS session background sync uses (the most recently connected one when no user is involved)."""
    try:
        from core.models import OperatorITMSSession
        s = (OperatorITMSSession.objects.filter(is_authenticated=True).order_by("-saved_at").first()
             or OperatorITMSSession.objects.order_by("-saved_at").first())
        if s is None:
            return {"account": "", "account_note": "No ITMS account connected: sync can't reach ITMS."}
        return {"account": s.itms_email or "", "account_connected": bool(s.is_authenticated),
                "account_saved": s.saved_at.isoformat() if s.saved_at else ""}
    except Exception:
        return {}


def _kit_sync_status() -> Dict[str, Any]:
    from core.services import config_service, kit_provisioning_service

    d = kit_provisioning_service.MorningKitSyncDaemon.get_instance().get_status()
    return {"running": bool(d.get("is_running")), "enabled": bool(config_service.get_setting("sync.auto_sync_enabled", True)),
            "interval_seconds": d.get("interval_seconds"), "last_run": d.get("last_run_time"),
            "runs": d.get("run_count"), "last_result": _short(d.get("last_result"))}


def _vision_status() -> Dict[str, Any]:
    from core.services import config_service
    from core.services.pipeline_runner import runner

    st = runner.get_status() or {}
    cfg = config_service.load_config().get("vision", {})
    return {"running": bool(st.get("running")), "task": st.get("task_type", ""),
            "last_result": ("ERROR: " + str(st.get("message") or "failed"))[:200]
            if st.get("success") is False else str(st.get("message") or "")[:200],
            "detector": cfg.get("yolo_weights", ""), "char_reader": cfg.get("character_model_weights", ""),
            "char_reader_on": bool(cfg.get("use_direct_character_reader", True))}


def _outbox_status() -> Dict[str, Any]:
    from core.models import VehicleInstallationPair
    from core.services import config_service

    return {"enabled": bool(config_service.get_setting("outbox.enabled", True)),
            "queued": VehicleInstallationPair.objects.filter(verification_status="OFFLINE_OUTBOX").count(),
            "failed": VehicleInstallationPair.objects.filter(verification_status="FAILED").count()}


def _policy_status() -> Dict[str, Any]:
    from django.conf import settings
    from governance.services import policy_sync

    if getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central":
        return {"role": "central"}
    st = policy_sync.load_enrollment(decrypt=False)
    return {"enrolled": bool(st.get("install_id")), "last_sync": st.get("last_sync_ok_at"),
            "stale": policy_sync.is_policy_stale(), "policy_version": st.get("policy_version")}


def _short(value: Any) -> str:
    if isinstance(value, dict):
        if "success" in value and not value.get("success"):
            return ("ERROR: " + str(value.get("error") or value.get("message") or "failed"))[:200]
        return str(value.get("message") or value.get("summary") or "ok")[:200]
    return str(value or "")[:200]


def status_all() -> Dict[str, Dict[str, Any]]:
    return {
        "order_sync": _safe(_order_sync_status),
        "kit_sync": _safe(_kit_sync_status),
        "vision": _safe(_vision_status),
        "outbox": _safe(_outbox_status),
        "policy_sync": _safe(_policy_status),
        "reported_at": timezone.now().isoformat(),
    }


def failing(status: Dict[str, Dict[str, Any]]) -> list:
    """Services whose last result is an error (for Overview alerts)."""
    out = []
    for key, st in (status or {}).items():
        if isinstance(st, dict) and (str(st.get("last_result", "")).startswith("ERROR") or st.get("error")):
            out.append(SERVICE_LABELS.get(key, key))
    return out


# ---------------------------------------------------------------------------
# Actions (the whitelist; remote commands can only name one of these)
# ---------------------------------------------------------------------------
def _order_sync_now(args):
    from core.services.order_sync import ShiftOrderSyncDaemon
    return _short(ShiftOrderSyncDaemon.get_instance().trigger_sync(force=True))


def _kit_sync_now(args):
    from core.services import kit_provisioning_service
    return _short(kit_provisioning_service.MorningKitSyncDaemon.get_instance().trigger_sync(force=True))


def _set_auto_sync(enabled: bool):
    def action(args):
        from core.services import config_service, kit_provisioning_service
        from core.services.order_sync import ShiftOrderSyncDaemon
        config_service.set_setting("sync.auto_sync_enabled", enabled)
        for daemon in (ShiftOrderSyncDaemon.get_instance(), kit_provisioning_service.MorningKitSyncDaemon.get_instance()):
            daemon.start() if enabled else daemon.stop()
        return "Automatic ITMS sync " + ("resumed" if enabled else "paused")
    return action


def _vision_run(args):
    from core.services.pipeline_runner import runner
    started = runner.start_pipeline(args.get("task", "full_pipeline"), batch_id=args.get("batch_id"))
    return "Vision pipeline started" if started else "Vision pipeline already running"


def _vision_reload(args):
    from core.services import config_service
    from core.vision import character_recognizer, detector
    detector.set_yolo_weights(config_service.load_config().get("vision", {}).get("yolo_weights", ""))
    character_recognizer._GLOBAL_RECOGNIZER = None
    return "Vision models reloaded"


def _outbox_drain(args):
    from core.services.submission_worker import drain_offline_outbox
    outcomes = drain_offline_outbox()
    ok = sum(1 for o in outcomes if o.success)
    return f"Outbox drained: {ok} sent, {len(outcomes) - ok} not sent"


def _clean_storage(args):
    from core.services import maintenance_service
    return _short(maintenance_service.clean_storage_lifecycle())


def _optimize_db(args):
    from core.services import maintenance_service
    return _short(maintenance_service.optimize_database())


def _update(args):
    from core.services.update_service import update_service
    return _short(update_service.apply_update())


def _wipe_itms_session(args):
    """Remote directive: sign this machine out of ITMS (cookies + stored tokens)."""
    from pathlib import Path
    from django.conf import settings
    from core.models import OperatorITMSSession

    n = OperatorITMSSession.objects.count()
    OperatorITMSSession.objects.all().delete()
    removed = 0
    for rel in ("secure/auth/itms_tokens.json", "secure/itms_session.json", "secure/auth/itms_session.json"):
        path = Path(settings.BASE_DIR) / rel
        if path.is_file():
            path.unlink()
            removed += 1
    return f"ITMS session wiped ({n} operator session(s), {removed} token file(s))"


def _rollback(args):
    from core.services.update_service import update_service
    from core.version import __version__
    from governance.services import telemetry_outbox
    result = update_service.rollback_version(args.get("target") or None)
    if result.get("success"):
        telemetry_outbox.record_event("ROLLBACK_APPLIED", from_version=__version__,
                                      to_version=str(result.get("target") or result.get("version") or ""))
    return _short(result)


ACTIONS: Dict[str, Dict[str, Any]] = {
    "order_sync.run_now": {"service": "order_sync", "label": "Sync orders now", "fn": _order_sync_now},
    "kit_sync.run_now": {"service": "kit_sync", "label": "Sync kits now", "fn": _kit_sync_now},
    "sync.pause": {"service": "order_sync", "label": "Pause automatic sync", "fn": _set_auto_sync(False)},
    "sync.resume": {"service": "order_sync", "label": "Resume automatic sync", "fn": _set_auto_sync(True)},
    "vision.run": {"service": "vision", "label": "Run vision pipeline", "fn": _vision_run},
    "vision.reload": {"service": "vision", "label": "Reload vision models", "fn": _vision_reload},
    "outbox.drain": {"service": "outbox", "label": "Drain outbox now", "fn": _outbox_drain},
    "housekeeping.clean": {"service": "housekeeping", "label": "Clean old crops & exports", "fn": _clean_storage},
    "housekeeping.optimize_db": {"service": "housekeeping", "label": "Optimise database", "fn": _optimize_db},
    "update.apply": {"service": "update", "label": "Update to latest release", "fn": _update},
    "update.rollback": {"service": "update", "label": "Roll back to the previous version", "fn": _rollback},
    "itms.wipe_session": {"service": "itms", "label": "Wipe ITMS session (sign out of ITMS)", "fn": _wipe_itms_session},
}


def run_action(action: str, triggered_by: str = "", install=None, args: Optional[dict] = None,
               wait: bool = False, on_done: Optional[Callable[[bool, str], None]] = None):
    """Runs an allowed action in the background (or inline when wait=True). Returns the ServiceRun."""
    from governance.models import ServiceRun

    if action not in ACTIONS:
        raise ValueError(f"Unknown action {action}")
    spec = ACTIONS[action]
    run = ServiceRun.objects.create(service=spec["service"], action=action, install=install,
                                    triggered_by=triggered_by[:150])

    def _work():
        ok, summary = True, ""
        try:
            summary = str(spec["fn"](args or {}) or "done")
            ok = not summary.startswith("ERROR")
        except Exception as exc:
            ok, summary = False, f"ERROR: {exc}"[:500]
            logger.warning("Service action %s failed: %s", action, exc)
        ServiceRun.objects.filter(pk=run.pk).update(ok=ok, summary=summary[:2000], finished_at=timezone.now())
        if on_done:
            on_done(ok, summary)

    if wait:
        _work()
    else:
        threading.Thread(target=_work, name=f"close-action-{action}", daemon=True).start()
    return run


# ---------------------------------------------------------------------------
# Central settings (pushed to every machine as flags "setting.<config key>")
# ---------------------------------------------------------------------------
CENTRAL_SETTINGS: Dict[str, Dict[str, Any]] = {
    "sync.auto_sync_enabled": {"label": "Automatic ITMS sync", "type": "bool"},
    "sync.order_sync_interval_seconds": {"label": "Order sync interval (seconds)", "type": "int", "min": 120, "max": 86400},
    "sync.kit_sync_interval_seconds": {"label": "Kit sync interval (seconds)", "type": "int", "min": 600, "max": 172800},
    "outbox.auto_sync_interval_seconds": {"label": "Outbox retry interval (seconds)", "type": "int", "min": 10, "max": 3600},
    "vision.use_direct_character_reader": {"label": "Use the Ugandan character reader", "type": "bool"},
    "vision.direct_char_min_confidence": {"label": "Character reader minimum confidence", "type": "float", "min": 0.05, "max": 0.99},
    "vision.min_ocr_confidence": {"label": "OCR minimum confidence", "type": "float", "min": 0.05, "max": 0.99},
    "vision.detector_conf_threshold": {"label": "Plate detector confidence threshold", "type": "float", "min": 0.05, "max": 0.99},
    "vision.use_paddleocr": {"label": "PaddleOCR fallback", "type": "bool"},
    "vision.adaptive_ensemble_voting": {"label": "Ensemble voting", "type": "bool"},
    "vision.yolo_weights": {"label": "Plate detector model", "type": "choice", "dir": "models", "suffixes": (".pt", ".onnx")},
    "vision.character_model_weights": {"label": "Character reader model", "type": "choice", "dir": "models", "suffixes": (".pt", ".onnx")},
}


def model_choices() -> list:
    from pathlib import Path
    from django.conf import settings

    models_dir = Path(settings.BASE_DIR) / "models"
    if not models_dir.is_dir():
        return []
    return sorted(f"models/{p.name}" for p in models_dir.iterdir() if p.suffix in (".pt", ".onnx"))


def parse_setting(key: str, raw: Any) -> Any:
    spec = CENTRAL_SETTINGS[key]
    if spec["type"] == "bool":
        return raw in (True, "on", "true", "1", 1)
    if spec["type"] in ("int", "float"):
        value = (int if spec["type"] == "int" else float)(raw)
        if not spec["min"] <= value <= spec["max"]:
            raise ValueError(f"{spec['label']} must be between {spec['min']} and {spec['max']}.")
        return value
    value = str(raw).strip()
    if value not in model_choices():
        raise ValueError(f"{value} is not a model file shipped with this release.")
    return value


def apply_setting_locally(key: str, value: Any) -> None:
    from core.services import config_service

    config_service.set_setting(key, value)
    if key.startswith("vision."):
        try:
            _vision_reload({})
        except Exception as exc:
            logger.debug("Vision reload after setting change skipped: %s", exc)
    if key == "sync.auto_sync_enabled":
        try:
            _set_auto_sync(bool(value))({})
        except Exception as exc:
            logger.debug("Sync toggle after setting change skipped: %s", exc)


def set_central_setting(key: str, raw: Any, actor, request=None) -> Any:
    from governance.services import flags

    if key not in CENTRAL_SETTINGS:
        raise ValueError("That setting can't be managed centrally.")
    value = parse_setting(key, raw)
    flags.set_flag(f"setting.{key}", value, actor=actor, request=request)
    apply_setting_locally(key, value)
    return value


def central_setting_values() -> list:
    from core.services import config_service
    from governance.services import flags

    rows = []
    for key, spec in CENTRAL_SETTINGS.items():
        central = flags.get_flag(f"setting.{key}", default=None)
        rows.append({"key": key, **spec, "value": central if central is not None else config_service.get_setting(key),
                     "central": central is not None})
    return rows
