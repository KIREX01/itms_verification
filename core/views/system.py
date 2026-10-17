"""
System configuration, pipeline execution, media serving, and updater REST APIs.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.models import EvidenceImage, VehicleInstallationPair
from core.services import config_service, date_service, vault_service
from core.services.pipeline_runner import runner as pipeline_runner
from core.services.update_service import update_service
from core.version import __version__

logger = logging.getLogger(__name__)

FORBIDDEN_MEDIA_EXTENSIONS = {
    ".py", ".pyc", ".pyd", ".env", ".json", ".sqlite3", ".db", ".key", ".pem",
    ".log", ".bak", ".ini", ".conf", ".cfg", ".sh", ".bat", ".ps1", ".cmd",
    ".exe", ".dll", ".so", ".bin"
}


def is_prohibited_system_directory(p: Union[Path, str]) -> bool:
    """Verifies that a folder path does not point to critical OS or system directories (CWE-22/CWE-552/S6549)."""
    try:
        raw_str = str(p).strip()
        if not raw_str or "\x00" in raw_str:
            return True
        norm_str = raw_str.replace("/", "\\")
        parts = [part for part in norm_str.split("\\") if part]
        if any(part in (".", "..") for part in parts):
            return True
        norm_upper = os.path.normpath(norm_str).upper()
        blocked_roots = ("C:\\WINDOWS", "C:\\PROGRAM FILES", "C:\\PROGRAM FILES (X86)", "\\ETC", "\\BIN", "\\SBIN", "\\USR", "\\SYS", "\\PROC", "\\DEV")
        if any(norm_upper == root or norm_upper.startswith(root + "\\") for root in blocked_roots):
            return True

        resolved = Path(raw_str).resolve()
        if len(resolved.parts) <= 1:  # Root directory (e.g. C:\ or /)
            return True
        prohibited_prefixes = [
            Path(os.environ.get("SystemRoot", "C:\\Windows")).resolve(),
            Path(os.environ.get("ProgramFiles", "C:\\Program Files")).resolve(),
            Path(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")).resolve(),
            Path("/etc").resolve(),
            Path("/bin").resolve(),
            Path("/sbin").resolve(),
            Path("/usr").resolve(),
            Path("/boot").resolve(),
            Path("/root").resolve(),
            Path("/sys").resolve(),
            Path("/proc").resolve(),
            Path("/dev").resolve(),
        ]
        for pref in prohibited_prefixes:
            try:
                if resolved == pref or resolved.is_relative_to(pref):
                    return True
            except (ValueError, OSError):
                continue
    except (ValueError, OSError):
        return True
    return False


@require_POST
def api_run_pipeline(request: HttpRequest) -> JsonResponse:
    """Triggers background AI vision detection and pair matching."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    task_type = request.POST.get("task_type", "full_pipeline").strip()
    batch_id = request.POST.get("batch_id", "").strip() or None
    if task_type not in ("full_pipeline", "vision", "matcher"):
        task_type = "full_pipeline"

    started = pipeline_runner.start_pipeline(task_type, batch_id=batch_id)
    if not started:
        return JsonResponse({
            "success": False,
            "message": "A pipeline task is already currently running.",
        }, status=409)

    return JsonResponse({
        "success": True,
        "message": f"Started {task_type.replace('_', ' ')} in background.",
    })


@require_GET
def api_pipeline_status(request: HttpRequest) -> JsonResponse:
    """Returns current execution progress, stage, and recent logs from background runner."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    status = pipeline_runner.get_status()
    return JsonResponse(status)


@require_GET
def api_stream_events(request: HttpRequest) -> HttpResponse:
    """Streams real-time pipeline events and stats via Server-Sent Events (SSE)."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return HttpResponse("Unauthorized", status=401)

    import time

    def event_stream():
        for _ in range(25):  # Stream for up to ~25 seconds per connection
            status_data = pipeline_runner.get_status()
            payload = {
                "timestamp": timezone.now().isoformat(),
                "pipeline": status_data,
            }
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(1)

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_GET
def api_export_report(request: HttpRequest) -> HttpResponse:
    """Exports shift verification report as a clean downloadable CSV file."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return HttpResponse("Unauthorized", status=401)

    from django.db.models import Q
    target_date = request.GET.get("date") or request.GET.get("date_suffix") or request.GET.get("suffix")

    pairs = (
        VehicleInstallationPair.objects.all()
        .select_related("order", "front_image", "rear_image")
        .order_by("-updated_at")
    )

    is_all = (target_date or "").strip().upper() == "ALL"
    if is_all:
        selected_suffix = "ALL"
    else:
        work_d, selected_suffix = date_service.resolve_date_and_suffix(target_date)
        pairs = pairs.filter(
            Q(order__order_number__endswith=selected_suffix) |
            Q(created_at__date=work_d) |
            Q(front_image__ingested_at__date=work_d) |
            Q(rear_image__ingested_at__date=work_d)
        )

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Pair ID",
        "Detected Plate",
        "Verification Status",
        "Match Type",
        "Match Score (%)",
        "Matched Via",
        "Order Number",
        "Order Plate",
        "VIN",
        "Warehouse",
        "Front Photo",
        "Rear Photo",
        "Manual Override",
        "Submitted At (EAT)",
        "Updated At (EAT)",
    ])

    for p in pairs:
        writer.writerow([
            p.id,
            p.registration_number_detected,
            p.verification_status,
            p.match_type,
            f"{p.match_score:.1f}" if p.match_score is not None else "",
            p.matched_via,
            p.order.order_number if p.order else "",
            p.order.registration_number if p.order else "",
            p.order.vin if p.order else "",
            p.order.warehouse_name if p.order else "",
            p.front_image.vault_file if p.front_image else "",
            p.rear_image.vault_file if p.rear_image else "",
            "YES" if p.is_manual_override else "NO",
            date_service.format_itms_timestamp(p.submitted_at),
            date_service.format_itms_timestamp(p.updated_at),
        ])

    exports_dir = Path(settings.BASE_DIR) / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    date_str = date_service.now_kampala().strftime("%Y%m%d_%H%M")
    filename = f"itms_shift_report_{selected_suffix}_{date_str}.csv" if selected_suffix != "ALL" else f"itms_shift_report_all_{date_str}.csv"
    csv_data = out.getvalue()
    (exports_dir / filename).write_text(csv_data, encoding="utf-8-sig")

    response = HttpResponse(csv_data.encode("utf-8-sig"), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


def api_settings(request: HttpRequest) -> JsonResponse:
    """
    GET: Returns current system configuration, user settings, developer settings, and active engine.
    POST: Updates configuration parameters dynamically.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    is_admin = bool(request.user and request.user.is_authenticated and request.user.is_superuser)

    if request.method == "POST":
        if not request.user.is_authenticated:
            return JsonResponse({"success": False, "error": "Authentication required to update system settings."}, status=401)
        updated_keys = []
        payload = {}
        if request.content_type == "application/json" and request.body:
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except Exception:
                pass
        else:
            payload = dict(request.POST.items())

        items_to_set = []
        if "key" in payload and "value" in payload:
            items_to_set.append((str(payload["key"]).strip(), payload["value"]))
        elif "settings" in payload and isinstance(payload["settings"], dict):
            for k, v in payload["settings"].items():
                items_to_set.append((str(k).strip(), v))
        else:
            for k, v in payload.items():
                if k not in ("csrfmiddlewaretoken",):
                    items_to_set.append((str(k).strip(), v))

        # Restrict developer and infrastructure settings to superusers
        DEV_PREFIXES = ("crawl.", "database.", "network.", "vision.", "matcher.", "storage.", "simulation.", "system.developer_mode")
        for k, v in items_to_set:
            if any(k.startswith(p) for p in DEV_PREFIXES) and not is_admin:
                return JsonResponse({
                    "success": False,
                    "error": f"Setting '{k}' requires administrative access. Configure via the TUI console.",
                }, status=403)

            config_service.set_setting(k, v)
            if "yolo_weights" in k:
                try:
                    from core.vision import detector
                    detector.set_yolo_weights(v)
                except Exception:
                    pass
            updated_keys.append(k)

        cfg = config_service.load_config()
        db_info = config_service.get_active_database_info()
        return JsonResponse({
            "success": True,
            "message": f"Updated {len(updated_keys)} setting(s).",
            "updated_keys": updated_keys,
            "dry_run": cfg.get("submission", {}).get("dry_run_mode", True),
            "submit_step3": cfg.get("submission", {}).get("submit_step3", True),
            "developer_mode": config_service.is_developer_mode() if is_admin else False,
            "user_settings": config_service.get_user_settings(),
            "developer_settings": config_service.get_developer_settings() if is_admin else {},
            "active_shift_date": date_service.get_active_shift_info(),
            "database": db_info.get("display", "SQLite") if is_admin else "Connected & Synchronized",
            "active_engine": db_info.get("vendor", "sqlite") if is_admin else "connected",
        })

    cfg = config_service.load_config()
    db_info = config_service.get_active_database_info()
    return JsonResponse({
        "success": True,
        "dry_run": cfg.get("submission", {}).get("dry_run_mode", True),
        "submit_step3": cfg.get("submission", {}).get("submit_step3", True),
        "developer_mode": config_service.is_developer_mode() if is_admin else False,
        "user_settings": config_service.get_user_settings(),
        "developer_settings": config_service.get_developer_settings() if is_admin else {},
        "active_bond": config_service.get_active_bond(),
        "active_shift_date": date_service.get_active_shift_info(),
        "database": db_info.get("display", "SQLite") if is_admin else "Connected & Synchronized",
        "active_engine": db_info.get("vendor", "sqlite") if is_admin else "connected",
        "version": __version__,
        "version_tag": f"v{__version__}",
    })


@require_POST
def api_toggle_dry_run(request: HttpRequest) -> JsonResponse:
    """Toggles or sets safe simulation / dry-run mode for ITMS submissions."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    mode_param = request.POST.get("mode") or request.POST.get("dry_run")
    if mode_param is not None and str(mode_param).strip() != "":
        new_val = str(mode_param).strip().lower() in ("true", "1", "yes", "dry_run", "dry")
    else:
        curr = config_service.get_setting("submission.dry_run_mode", True)
        new_val = not curr

    config_service.set_setting("submission.dry_run_mode", new_val)
    settings.ITMS_WEB_DRY_RUN = new_val
    return JsonResponse({
        "success": True,
        "dry_run": new_val,
        "dry_run_mode": new_val,
        "message": f"Simulation mode {'ENABLED' if new_val else 'DISABLED'}.",
    })


@require_GET
def api_check_updates(request: HttpRequest) -> JsonResponse:
    """Checks GitHub Releases for updates."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    force = request.GET.get("force", "false").lower() in ("true", "1", "yes")
    result = update_service.check_for_updates(force=force)
    return JsonResponse(result)


@require_POST
def api_apply_update(request: HttpRequest) -> JsonResponse:
    """Safely applies pending update from GitHub Releases."""
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "error": "Authentication required."}, status=401)

    download_url = None
    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            download_url = body.get("download_url")
        except Exception:
            pass
    if not download_url:
        download_url = request.POST.get("download_url")

    if download_url and not update_service.validate_download_url(download_url):
        return JsonResponse({"success": False, "error": "Untrusted or invalid download URL."}, status=400)

    result = update_service.apply_update(download_url=download_url)
    status_code = 200 if result.get("success") else 400
    return JsonResponse(result, status=status_code)


def _get_dir_disk_stats(path: Path) -> dict:
    try:
        usage = shutil.disk_usage(path)
        return {
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
        }
    except Exception:
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0}


def api_vault_folder(request: HttpRequest) -> JsonResponse:
    """
    GET: Returns current active Evidence Vault folder, disk space, and presets.
    POST: Updates and persists the Evidence Vault storage location in config.json.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    is_admin = bool(request.user and request.user.is_authenticated and request.user.is_superuser)
    default_vault = (settings.BASE_DIR / "media" / "vault").resolve()
    docs_vault = (Path.home() / "Documents" / "ITMS_Vault").resolve()
    pics_vault = (Path.home() / "Pictures" / "ITMS_Vault").resolve()

    if request.method == "POST":
        if not request.user.is_authenticated:
            return JsonResponse({"success": False, "error": "Authentication required."}, status=401)
        if not is_admin:
            return JsonResponse({"success": False, "error": "Evidence vault folder configuration is restricted to the TUI console."}, status=403)
        new_path_raw = ""
        migrate_files = False
        if request.content_type == "application/json" and request.body:
            try:
                body = json.loads(request.body.decode("utf-8"))
                new_path_raw = str(body.get("path", "")).strip()
                migrate_files = bool(body.get("migrate", False))
            except Exception:
                pass
        if not new_path_raw:
            new_path_raw = request.POST.get("path", "").strip()
            migrate_files = request.POST.get("migrate", "").lower() in ("true", "1", "yes")

        if not new_path_raw:
            return JsonResponse({"success": False, "error": "Folder path cannot be empty."}, status=400)

        if is_prohibited_system_directory(Path(new_path_raw)):
            return JsonResponse({
                "success": False,
                "error": "Specified folder cannot be used as an Evidence Vault because it is a protected system directory."
            }, status=400)

        # Handle presets and safe custom vault paths
        if new_path_raw.lower() in ("default", "media/vault", "media\\vault"):
            target_path = default_vault
        elif new_path_raw.lower() in ("documents", "docs"):
            target_path = docs_vault
        elif new_path_raw.lower() in ("pictures", "pics"):
            target_path = pics_vault
        else:
            media_root = (settings.BASE_DIR / "media").resolve()
            leaf_name = Path(new_path_raw).name
            cand = (media_root / leaf_name).resolve()
            try:
                cand.relative_to(media_root)
                target_path = cand
            except ValueError:
                return JsonResponse({
                    "success": False,
                    "error": "Custom vault directory must reside within authorized application folders."
                }, status=400)

        if is_prohibited_system_directory(target_path):
            return JsonResponse({
                "success": False,
                "error": f"Directory '{target_path}' cannot be used as an Evidence Vault because it is a protected system directory."
            }, status=400)

        old_vault = vault_service.get_vault_root().resolve()

        try:
            target_path.mkdir(parents=True, exist_ok=True)
            # Test write access
            test_probe = target_path / f".write_test_{uuid.uuid4().hex[:6]}"
            test_probe.write_text("ok", encoding="utf-8")
            test_probe.unlink(missing_ok=True)
        except Exception as exc:
            return JsonResponse({"success": False, "error": f"Cannot write to specified directory: {exc}"}, status=400)

        # Migrate existing files if requested
        migrated_count = 0
        if migrate_files and old_vault != target_path and old_vault.is_dir():
            try:
                for item in old_vault.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(old_vault)
                        dest = target_path / rel
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if not dest.exists():
                            shutil.copy2(item, dest)
                            migrated_count += 1
            except Exception as exc:
                logger.warning("Error migrating vault files: %s", exc)

        # Set and persist new vault path
        vault_service.set_vault_root(target_path)
        stats = _get_dir_disk_stats(target_path)

        return JsonResponse({
            "success": True,
            "message": f"Evidence vault location updated to '{target_path}'.",
            "vault_path": str(target_path),
            "is_default": target_path == default_vault,
            "migrated_count": migrated_count,
            "stats": stats,
        })

    # GET request
    current_vault = vault_service.get_vault_root().resolve()
    stats = _get_dir_disk_stats(current_vault)
    is_default = (current_vault == default_vault)

    try:
        photo_count = sum(1 for p in current_vault.rglob("*") if p.suffix.lower() in vault_service.VALID_EXTENSIONS)
    except Exception:
        photo_count = EvidenceImage.objects.count()

    if not is_admin:
        return JsonResponse({
            "success": True,
            "vault_path": "Server Managed",
            "is_default": is_default,
            "photo_count": photo_count,
            "stats": {"free_gb": stats.get("free_gb", 0), "total_gb": stats.get("total_gb", 0)},
            "presets": {},
            "is_restricted": True,
        })

    return JsonResponse({
        "success": True,
        "vault_path": str(current_vault),
        "is_default": is_default,
        "photo_count": photo_count,
        "stats": stats,
        "presets": {
            "default": str(default_vault),
            "documents": str(docs_vault),
            "pictures": str(pics_vault),
        },
    })


def api_browse_vault_folder(request: HttpRequest) -> JsonResponse:
    """
    Launches the host operating system's native folder browser dialog
    and returns the selected folder path with input sanitization and command injection defense.
    """
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "error": "Authentication required to open native folder browser."}, status=401)
    if not request.user.is_superuser:
        return JsonResponse({"success": False, "error": "Folder browser is restricted to server administrators."}, status=403)

    current_vault = vault_service.get_vault_root().resolve()
    selected_path = None

    try:
        if sys.platform == "win32":
            # Modern Windows FolderBrowserDialog via PowerShell with safe environment parameter passing
            env = os.environ.copy()
            env["ITMS_INITIAL_DIR"] = str(current_vault)
            ps_script = """
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Select Evidence Vault Storage Folder'
$dialog.ShowNewFolderButton = $true
$initDir = $env:ITMS_INITIAL_DIR
if ($initDir -and (Test-Path -LiteralPath $initDir)) {
    $dialog.SelectedPath = $initDir
}
$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true
$res = $dialog.ShowDialog($form)
if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $dialog.SelectedPath
}
"""
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
                check=False,
            )
            out = proc.stdout.strip()
            if out and os.path.isdir(out):
                selected_path = out
        elif sys.platform == "darwin":
            clean_initial = str(current_vault).replace('"', '\\"').replace('\\', '\\\\')
            as_cmd = f'POSIX path of (choose folder with prompt "Select Evidence Vault Storage Folder" default location POSIX file "{clean_initial}")'
            proc = subprocess.run(["osascript", "-e", as_cmd], capture_output=True, text=True, timeout=120, check=False)
            out = proc.stdout.strip()
            if out and os.path.isdir(out):
                selected_path = out
        elif sys.platform == "linux":
            if shutil.which("zenity"):
                proc = subprocess.run(
                    ["zenity", "--file-selection", "--directory", f"--filename={current_vault}/", "--title=Select Evidence Vault Storage Folder"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                )
                out = proc.stdout.strip()
                if out and os.path.isdir(out):
                    selected_path = out
    except subprocess.TimeoutExpired:
        return JsonResponse({"success": False, "canceled": True, "error": "Folder selection timed out."})
    except Exception as exc:
        logger.warning("Native folder picker invocation error: %s", exc)

    if selected_path:
        return JsonResponse({
            "success": True,
            "selected_path": selected_path,
            "canceled": False,
        })
    else:
        return JsonResponse({
            "success": True,
            "selected_path": None,
            "canceled": True,
        })


def serve_media(request: HttpRequest, path: str) -> HttpResponse:
    """
    Dynamically serves photographic evidence and crops with strict path traversal protection.
    Resolves vault files against the active vault root, even if outside MEDIA_ROOT.
    Includes strict path traversal guards to prevent arbitrary file disclosure.
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return HttpResponse("Unauthorized media access", status=401, content_type="text/plain")

    import mimetypes

    from django.http import FileResponse, Http404

    clean_path = path.replace("\\", "/").strip("/")

    # Reject null bytes, empty paths, and path traversal attempts
    if not clean_path or "\x00" in clean_path or ".." in clean_path.split("/"):
        raise Http404("Invalid media path")

    target_suffix = Path(clean_path).suffix.lower()
    if target_suffix in FORBIDDEN_MEDIA_EXTENSIONS:
        raise Http404("Forbidden media file type")

    vault_root = vault_service.get_vault_root().resolve()
    media_root = Path(settings.MEDIA_ROOT).resolve()

    # Normalize clean_path if it starts with 'media/'
    normalized_path = clean_path
    if normalized_path.startswith("media/"):
        normalized_path = normalized_path[6:].lstrip("/")

    candidate_files = []

    # Check if path starts with 'vault/'
    if normalized_path.startswith("vault/"):
        sub_rel = normalized_path[6:].lstrip("/")
        candidate_files.append((vault_root / sub_rel, vault_root))

    # Check vault root directly
    candidate_files.append((vault_root / normalized_path, vault_root))

    # Fallback to settings.MEDIA_ROOT (e.g. for crops/)
    candidate_files.append((media_root / normalized_path, media_root))
    candidate_files.append((media_root / clean_path, media_root))

    # Delegate to vault_service.resolve_vault_path for comprehensive resolution
    service_resolved = vault_service.resolve_vault_path(normalized_path)
    if service_resolved != vault_root and service_resolved.is_file():
        candidate_files.append((service_resolved, vault_root if service_resolved.is_relative_to(vault_root) else media_root))

    for target, base_dir in candidate_files:
        try:
            resolved_target = target.resolve()
            if (
                resolved_target.is_file()
                and (resolved_target.is_relative_to(vault_root) or resolved_target.is_relative_to(media_root) or resolved_target.is_relative_to(base_dir))
                and not resolved_target.name.startswith(".")
                and resolved_target.suffix.lower() not in FORBIDDEN_MEDIA_EXTENSIONS
            ):
                mime, _ = mimetypes.guess_type(str(resolved_target))
                return FileResponse(resolved_target.open("rb"), content_type=mime or "image/jpeg")
        except (ValueError, RuntimeError, OSError):
            continue

    raise Http404("Media file not found")




def api_browse_path(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"success": False, "error": "Authentication required"}, status=401)
        
    try:
        import json
        data = json.loads(request.body)
        browse_type = data.get("type", "folder")
        title = data.get("title", "Select Path")
        initial_dir = data.get("initial_dir", "")
    except Exception:
        browse_type = request.GET.get("type", "folder")
        title = request.GET.get("title", "Select Path")
        initial_dir = request.GET.get("initial_dir", "")

    if sys.platform == "win32":
        import subprocess
        env = os.environ.copy()
        env["ITMS_INITIAL_DIR"] = str(initial_dir)
        env["ITMS_TITLE"] = str(title)
        
        if browse_type == "file":
            ps_script = '''
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = $env:ITMS_TITLE
$dialog.Filter = "All Files (*.*)|*.*|PyTorch/ONNX Models (*.pt;*.onnx)|*.pt;*.onnx"
if ($env:ITMS_INITIAL_DIR -and (Test-Path -LiteralPath $env:ITMS_INITIAL_DIR)) {
    $dialog.InitialDirectory = $env:ITMS_INITIAL_DIR
}
$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true
$res = $dialog.ShowDialog($form)
if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $dialog.FileName
}
'''
        else:
            ps_script = '''
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:ITMS_TITLE
$dialog.ShowNewFolderButton = $true
if ($env:ITMS_INITIAL_DIR -and (Test-Path -LiteralPath $env:ITMS_INITIAL_DIR)) {
    $dialog.SelectedPath = $env:ITMS_INITIAL_DIR
}
$form = New-Object System.Windows.Forms.Form
$form.TopMost = $true
$res = $dialog.ShowDialog($form)
if ($res -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $dialog.SelectedPath
}
'''
        try:
            result = subprocess.run(["powershell", "-STA", "-WindowStyle", "Hidden", "-NoProfile", "-Command", ps_script], capture_output=True, text=True, env=env)
            out = result.stdout.strip()
            if out:
                return JsonResponse({"success": True, "selected_path": out})
            return JsonResponse({"success": False, "canceled": True})
        except Exception as e:
            return JsonResponse({"success": False, "error": str(e)})
            
    return JsonResponse({"success": False, "error": "Native browsing not supported on this OS"})


@csrf_exempt
def api_shift_date(request: HttpRequest) -> JsonResponse:
    """
    GET: Returns current active shift date and available shift dates.
    POST: Sets the system-wide active operating shift date.
    When updated, all features (Reports, Stock Management, Explorer) follow this date.
    """
    if request.method == "POST":
        target = ""
        if request.content_type == "application/json" and request.body:
            try:
                body = json.loads(request.body.decode("utf-8"))
                target = body.get("target_date") or body.get("date") or body.get("suffix") or body.get("mode") or ""
            except Exception:
                pass
        else:
            target = request.POST.get("target_date") or request.POST.get("date") or request.POST.get("suffix") or request.POST.get("mode") or ""

        target_str = str(target).strip()
        if target_str.lower() in ("today", "live"):
            config_service.set_setting("sync.shift_target_date_mode", "today")
            config_service.set_setting("sync.manual_target_date", "")
        elif target_str.lower() == "yesterday":
            config_service.set_setting("sync.shift_target_date_mode", "yesterday")
            config_service.set_setting("sync.manual_target_date", "")
        else:
            parsed_d = date_service.parse_date_input(target_str)
            if parsed_d:
                config_service.set_setting("sync.shift_target_date_mode", "manual")
                config_service.set_setting("sync.manual_target_date", date_service.format_readable_date(parsed_d))
            else:
                return JsonResponse({"success": False, "error": f"Invalid date: '{target_str}'"}, status=400)

        active_shift = date_service.get_active_shift_info()
        return JsonResponse({
            "success": True,
            "message": f"Active operating shift date set to {active_shift['display']}",
            "active_shift": active_shift,
        })

    # GET
    return JsonResponse({
        "success": True,
        "active_shift": date_service.get_active_shift_info(),
        "available_dates": date_service.get_available_shift_dates(),
    })
