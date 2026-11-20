"""Friendly page for a failed CSRF check (stale page, other tab, expired session)."""
import logging

from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme

logger = logging.getLogger("django.security.csrf")


def csrf_failure(request, reason=""):
    logger.warning("CSRF failure on %s %s: %s (referer=%r, origin=%r)", request.method, request.path, reason,
                   request.META.get("HTTP_REFERER", ""), request.META.get("HTTP_ORIGIN", ""))
    back = request.path if request.method == "POST" else "/"
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = "/"
    return render(request, "governance/csrf_failure.html", {"back_url": back}, status=403)
