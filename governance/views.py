"""Operator-facing governance endpoints (capabilities for UI rendering)."""
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET

from governance.services import authz


@require_GET
def api_me_capabilities(request: HttpRequest) -> JsonResponse:
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated):
        return JsonResponse({"success": False, "error": "Authentication required."}, status=401)
    summary = authz.capability_summary(user)
    summary.update({"success": True, "username": user.username})
    return JsonResponse(summary)
