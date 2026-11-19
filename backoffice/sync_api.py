"""Central policy endpoints called by enrolled installs (HMAC-authenticated, no session)."""
from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

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
        result = policy_sync.enroll(data.get("code", ""), app_version=str(data.get("app_version", "")), ip=_ip(request))
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
    except (ValueError, policy_sync.PolicySyncError) as exc:
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
