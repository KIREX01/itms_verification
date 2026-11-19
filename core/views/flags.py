"""Quality flag REST endpoints (Quality Inspector raises, Team Leader resolves)."""
from __future__ import annotations

import json

from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from core.models import QualityFlag
from core.services import quality_flag_service as qfs
from governance import capabilities as C
from governance.services import authz


def _body(request: HttpRequest) -> dict:
    if request.content_type == "application/json" and request.body:
        try:
            return json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}
    return request.POST.dict()


@require_GET
def api_flags_list(request: HttpRequest) -> JsonResponse:
    """Flags at the bonds where the user can view the queue or raise flags.
    Filters: ?status=OPEN|ALL, ?pair_id=, ?order_id=, ?kit_id="""
    user = request.user
    if authz.is_platform_admin(user) or not authz.enforcement_enabled():
        qs = QualityFlag.objects.all()
    else:
        bonds = authz.bonds_for(user, C.QUEUE_VIEW) | authz.bonds_for(user, C.FLAG_RAISE)
        qs = QualityFlag.objects.filter(bond_code__in=bonds)
    status = (request.GET.get("status") or "OPEN").upper()
    if status != "ALL":
        qs = qs.filter(status=status)
    for key in ("pair_id", "order_id", "kit_id"):
        val = request.GET.get(key)
        if val and val.isdigit():
            qs = qs.filter(**{key: int(val)})
    qs = qs.select_related("raised_by", "resolved_by", "order", "kit")[:500]
    flags = [qfs.flag_to_dict(f) for f in qs]
    return JsonResponse({
        "success": True,
        "flags": flags,
        "count": len(flags),
        "reasons": [{"code": c, "label": l} for c, l in QualityFlag.Reason.choices],
        "can_resolve": authz.has_capability(user, C.FLAG_RESOLVE),
    })


@require_POST
def api_flag_raise(request: HttpRequest) -> JsonResponse:
    data = _body(request)
    try:
        flag = qfs.raise_flag(
            request.user,
            target_type=data.get("target_type", ""),
            target_id=data.get("target_id"),
            reason_code=data.get("reason_code", ""),
            note=data.get("note", ""),
            suggested_plate=data.get("suggested_plate", ""),
        )
    except authz.CapabilityDenied as exc:
        return JsonResponse({"success": False, "error": str(exc), "code": "PERMISSION_DENIED"}, status=403)
    except qfs.QualityFlagError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    return JsonResponse({"success": True, "flag": qfs.flag_to_dict(flag),
                         "message": f"Flag #{flag.pk} raised. Submission is blocked until the Team Leader resolves it."})


@require_POST
def api_flag_resolve(request: HttpRequest, flag_id: int) -> JsonResponse:
    data = _body(request)
    flag = QualityFlag.objects.filter(pk=flag_id).first()
    if flag is None:
        return JsonResponse({"success": False, "error": "Flag not found."}, status=404)
    try:
        flag = qfs.resolve_flag(request.user, flag, outcome=data.get("outcome", ""), note=data.get("note", ""))
    except authz.CapabilityDenied as exc:
        return JsonResponse({"success": False, "error": str(exc), "code": "PERMISSION_DENIED"}, status=403)
    except qfs.QualityFlagError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    return JsonResponse({"success": True, "flag": qfs.flag_to_dict(flag)})
