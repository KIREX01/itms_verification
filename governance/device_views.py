"""/device/: an unknown device asks the administrator for access."""
from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from governance.device_gate import _blocked, _local_install_id, set_device_cookie
from governance.services import device_admission


@require_http_methods(["GET", "POST"])
def device_access_view(request: HttpRequest) -> HttpResponse:
    if device_admission.admitted_device(request) is not None:
        return redirect("/")
    if request.method == "POST":
        if (request.POST.get("website") or "").strip():  # honeypot
            return _blocked(request, waiting=True)
        try:
            report = json.loads(request.POST.get("client_report") or "{}")
            if not isinstance(report, dict):
                report = {}
        except ValueError:
            report = {}
        name = (request.POST.get("name") or "").strip()
        if not name:
            return _blocked(request, error="Enter your name so the administrator knows whose device this is.")
        try:
            dev, cookie = device_admission.request_access(
                request, name=name, email=(request.POST.get("email") or "").strip(),
                note=(request.POST.get("note") or "").strip(), client_report=report,
                install_id=_local_install_id(),
            )
        except ValueError as exc:
            return _blocked(request, error=str(exc))
        response = redirect("/device/")
        set_device_cookie(response, request, cookie)
        return response
    return _blocked(request)
