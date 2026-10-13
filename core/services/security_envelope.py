"""
Security & Capability Request Envelope Layer.
Enforces zero-trust isolation: no facility data (orders, kits, stock) can be queried,
synced, or displayed unless the operator possesses a validated, active ITMS connection.
"""
from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from django.http import HttpRequest, HttpResponse

from core.services import config_service
from core.services.api_envelope import (
    create_error_detail,
    format_api_response,
    generate_trace_id,
)


@dataclass(frozen=True)
class OperatorRequestEnvelope:
    """Immutable capability token and operational context attached to incoming requests."""
    # 1. Local Identity
    user_id: Optional[int]
    username: str

    # 2. ITMS Identity & Proof of Authentication
    itms_email: str             # e.g., "e.akena@itms-ug.com"
    itms_user_uuid: str         # Extracted from official ITMS session cookie
    itms_auth_status: str       # "ACTIVE", "EXPIRED", "UNLINKED", "ANONYMOUS"

    # 3. Geographical / Facility Scope
    facility_code: str          # e.g., "AGM", "SPIRO", "BOND1"

    # 4. Request Metadata
    request_id: str             # Unique UUID for audit tracing
    trace_id: str               # Alias for request_id / distributed tracing
    client_ip: str
    timestamp: float            # Unix timestamp
    is_valid: bool              # True only if user is authenticated AND has live ITMS session

    @property
    def iso_timestamp(self) -> str:
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()


def extract_client_ip(request: HttpRequest) -> str:
    """Extracts client IP address respecting reverse proxies."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return str(xff).split(",")[0].strip()
    return str(request.META.get("REMOTE_ADDR") or "127.0.0.1")


def build_operator_envelope(request: HttpRequest) -> OperatorRequestEnvelope:
    """
    Constructs an OperatorRequestEnvelope for the given request.
    Verifies user authentication, isolated OperatorITMSSession in DB,
    and resolves the current facility context from OperatorProfile or session.
    """
    req_id = getattr(request, "trace_id", None) or generate_trace_id(request)
    now_ts = time.time()
    client_ip = extract_client_ip(request)
    active_facility = config_service.get_active_bond().get("code", "AGM")

    user = getattr(request, "user", None)
    if not (user and getattr(user, "is_authenticated", False)):
        return OperatorRequestEnvelope(
            user_id=None,
            username="anonymous",
            itms_email="",
            itms_user_uuid="",
            itms_auth_status="ANONYMOUS",
            facility_code=active_facility,
            request_id=req_id,
            trace_id=req_id,
            client_ip=client_ip,
            timestamp=now_ts,
            is_valid=False,
        )

    # User is authenticated on Copilot - inspect their active facility preference
    try:
        from core.models import OperatorProfile
        profile = OperatorProfile.objects.filter(user=user).first()
        if profile and profile.active_facility:
            active_facility = profile.active_facility.strip().upper()
    except Exception:
        pass

    # Inspect their isolated ITMS session
    try:
        from core.models import OperatorITMSSession
        itms_sess = OperatorITMSSession.objects.filter(user=user).first()
        facility = (itms_sess.active_bond_code if itms_sess and itms_sess.active_bond_code else active_facility)

        if itms_sess and itms_sess.is_authenticated and itms_sess.is_cookie_valid():
            return OperatorRequestEnvelope(
                user_id=user.pk,
                username=user.username,
                itms_email=itms_sess.itms_email or "",
                itms_user_uuid=itms_sess.itms_user_uuid or "",
                itms_auth_status="ACTIVE",
                facility_code=facility,
                request_id=req_id,
                trace_id=req_id,
                client_ip=client_ip,
                timestamp=now_ts,
                is_valid=True,
            )
        elif itms_sess and itms_sess.itms_email:
            # Session was previously linked but cookie has expired
            return OperatorRequestEnvelope(
                user_id=user.pk,
                username=user.username,
                itms_email=itms_sess.itms_email,
                itms_user_uuid=itms_sess.itms_user_uuid or "",
                itms_auth_status="EXPIRED",
                facility_code=facility,
                request_id=req_id,
                trace_id=req_id,
                client_ip=client_ip,
                timestamp=now_ts,
                is_valid=False,
            )
    except Exception:
        pass

    # Authenticated Copilot user without an ITMS session
    return OperatorRequestEnvelope(
        user_id=user.pk,
        username=user.username,
        itms_email="",
        itms_user_uuid="",
        itms_auth_status="UNLINKED",
        facility_code=active_facility,
        request_id=req_id,
        trace_id=req_id,
        client_ip=client_ip,
        timestamp=now_ts,
        is_valid=False,
    )


def require_itms_envelope(
    view_func: Optional[Callable[..., HttpResponse]] = None,
    allow_expired: bool = False,
) -> Callable[..., HttpResponse]:
    """
    Decorator for views requiring an active, verified ITMS connection.
    Blocks unlinked and expired operators from accessing facility data.
    """
    def decorator(func: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
        @functools.wraps(func)
        def wrapper(request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
            envelope: Optional[OperatorRequestEnvelope] = getattr(request, "envelope", None)
            if envelope is None:
                envelope = build_operator_envelope(request)
                setattr(request, "envelope", envelope)

            if not envelope.is_valid:
                if envelope.itms_auth_status == "ANONYMOUS":
                    error = create_error_detail(
                        code="AUTHENTICATION_REQUIRED",
                        message="Please sign in to the ITMS Verification Copilot.",
                        field="session",
                    )
                    return format_api_response(
                        errors=[error],
                        status_code=401,
                        request=request,
                    )

                if envelope.itms_auth_status == "EXPIRED" and not allow_expired:
                    error = create_error_detail(
                        code="ITMS_SESSION_EXPIRED",
                        message="Your official ITMS session has expired. Please sign in to ITMS again in Tab 2.",
                        field="itms_session",
                        details={"itms_email": envelope.itms_email, "status": "EXPIRED"},
                    )
                    return format_api_response(
                        errors=[error],
                        status_code=412,
                        request=request,
                    )

                # UNLINKED / NOT LINKED
                error = create_error_detail(
                    code="ITMS_ACCOUNT_NOT_LINKED",
                    message="Please connect your official ITMS account in Tab 2 to access orders and kits.",
                    field="itms_session",
                    details={
                        "status": "UNLINKED",
                        "facility_code": envelope.facility_code,
                        "legacy_code": "ITMS_CONNECTION_REQUIRED",
                    },
                )
                return format_api_response(
                    errors=[error],
                    status_code=412,
                    request=request,
                )

            return func(request, *args, **kwargs)

        return wrapper

    if view_func is not None:
        return decorator(view_func)
    return decorator
