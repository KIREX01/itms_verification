"""/install/: this install's activation status; the administrator enrolls it with a one-time code."""
from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from governance.services import authz, install_guard, policy_sync


@require_http_methods(["GET", "POST"])
def install_view(request: HttpRequest) -> HttpResponse:
    state = install_guard.install_state()
    user = getattr(request, "user", None)
    is_admin = bool(user and user.is_authenticated and authz.is_platform_admin(user))
    ctx = {
        "state": state,
        "locked": state in install_guard.LOCKED_STATES,
        "message": install_guard.lock_message(state),
        "fingerprint": install_guard.short_fingerprint(install_guard.machine_fingerprint()),
        "enrollment": policy_sync.load_enrollment(decrypt=False),
        "is_admin": is_admin,
        "deployment_role": getattr(settings, "DEPLOYMENT_ROLE", "edge"),
        "default_central": getattr(settings, "CLOSE_CENTRAL_URL", "https://close.kirex.online"),
    }
    if request.method == "POST":
        if not is_admin:
            ctx["error"] = "Only the administrator can activate this install."
            return render(request, "governance/install.html", ctx, status=403)
        if state == install_guard.CENTRAL:
            ctx["error"] = "This is the central server; it doesn't enroll anywhere."
            return render(request, "governance/install.html", ctx, status=400)
        central = (request.POST.get("central_url") or "").strip()
        code = (request.POST.get("code") or "").strip()
        name = (request.POST.get("name") or "").strip()
        if not central.startswith("https://") and not getattr(settings, "DEBUG", False):
            ctx["error"] = "Use the central server's https:// address."
            return render(request, "governance/install.html", ctx, status=400)
        try:
            policy_sync.enroll_with_central(central, code, name=name)
        except Exception as exc:
            ctx["error"] = f"Activation failed: {exc}"
            return render(request, "governance/install.html", ctx, status=400)
        return redirect("/")
    return render(request, "governance/install.html", ctx)
