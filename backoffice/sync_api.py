"""Central policy endpoints called by enrolled installs (HMAC-authenticated, no session)."""
from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from governance.services import policy_sync


def _ip(request: HttpRequest) -> str:
    try:
        from core.services.device_access_service import extract_client_ip
        return extract_client_ip(request) or ""
    except Exception:
        return request.META.get("REMOTE_ADDR", "")


def _central_only():
    if getattr(settings, "DEPLOYMENT_ROLE", "edge") != "central":
        return JsonResponse({"success": False, "error": "This server is not the central backoffice."}, status=404)
    return None


@csrf_exempt
@require_POST
def install_enroll(request: HttpRequest) -> JsonResponse:
    blocked = _central_only()
    if blocked:
        return blocked
    try:
        data = json.loads(request.body.decode() or "{}")
        result = policy_sync.enroll(data.get("code", ""), app_version=str(data.get("app_version", "")), ip=_ip(request),
                                    machine_fingerprint=str(data.get("machine_fingerprint", "")),
                                    device_info=data.get("device_info") if isinstance(data.get("device_info"), dict) else None,
                                    name=str(data.get("name", ""))[:120])
    except (ValueError, policy_sync.PolicySyncError) as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    return JsonResponse({"success": True, **result})


@csrf_exempt
@require_POST
def install_heartbeat(request: HttpRequest) -> JsonResponse:
    blocked = _central_only()
    if blocked:
        return blocked
    install_id = request.headers.get("X-Close-Install", "")
    try:
        payload = policy_sync.handle_heartbeat(
            install_id, request.body, request.headers.get("X-Close-Timestamp", ""),
            request.headers.get("X-Close-Signature", ""), ip=_ip(request),
        )
    except policy_sync.PolicySyncError as exc:
        return JsonResponse({"success": False, "error": str(exc), "code": exc.code}, status=403)
    except ValueError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=403)
    from governance.models import InstallRegistration
    secret = InstallRegistration.objects.filter(install_id=install_id).values_list("secret", flat=True).first()
    body = json.dumps(payload).encode()
    ts, sig = policy_sync.sign(secret, body)
    response = JsonResponse(payload)
    response.content = body
    response["X-Close-Timestamp"] = ts
    response["X-Close-Signature"] = sig
    return response


def _central_url(request: HttpRequest) -> str:
    configured = (getattr(settings, "CLOSE_CENTRAL_URL", "") or "").rstrip("/")
    return configured or request.build_absolute_uri("/").rstrip("/")


@require_GET
def installer_script(request: HttpRequest, code: str, filename: str):
    """Token-gated installer: the install script with the enrollment token and server URL baked in."""
    from django.http import HttpResponse
    from governance.services import fleet

    blocked = _central_only()
    if blocked:
        return blocked
    kind = {"install.ps1": "ps1", "install.sh": "sh"}.get(filename)
    if kind is None:
        return HttpResponse("Not found.\n", status=404, content_type="text/plain")
    ip = _ip(request)
    from django.core.cache import cache
    fail_key = f"close:installer-fail:{ip}"
    if int(cache.get(fail_key, 0) or 0) >= 20:
        return HttpResponse("Too many invalid tokens from this address. Try again later.\n", status=429,
                            content_type="text/plain")
    try:
        script = fleet.installer_script(kind, code, _central_url(request), request=request)
    except fleet.FleetError as exc:
        cache.set(fail_key, int(cache.get(fail_key, 0) or 0) + 1, 3600)
        msg = f"CLOSE Copilot installer: {exc}\nAsk your administrator for a new install link.\n"
        if kind == "ps1":
            body = f'Write-Host "{msg.strip()}" -ForegroundColor Red\n'
        else:
            body = f'echo "{msg.strip()}" >&2; exit 1\n'
        return HttpResponse(body, status=403, content_type="text/plain; charset=utf-8")
    response = HttpResponse(script, content_type="text/plain; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def release_manifest(request: HttpRequest) -> JsonResponse:
    """Public release manifest: latest version, minimum allowed version, pinned archive checksum."""
    from governance.services import fleet
    from core.services.update_service import release_branch

    blocked = _central_only()
    if blocked:
        return blocked
    policy = fleet.release_policy()
    return JsonResponse({
        "product": "CLOSE Copilot",
        "latest_version": policy["latest_version"],
        "min_allowed_version": policy["min_version"],
        "zip_sha256": policy["zip_sha256"],
        "channels": {"windows": "release/windows", "linux": "release/linux", "this_server": release_branch()},
    })

