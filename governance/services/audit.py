"""Append-only administrative audit trail."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _client_ip(request) -> str:
    if request is None:
        return ""
    try:
        from core.services.device_access_service import extract_client_ip
        return extract_client_ip(request) or ""
    except Exception:
        return request.META.get("REMOTE_ADDR", "") if hasattr(request, "META") else ""


def record(
    actor: Any,
    action: str,
    target_type: str = "",
    target_id: Any = "",
    target_label: str = "",
    bond_code: str = "",
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    reason: str = "",
    request: Any = None,
    actor_label: str = "",
):
    """Writes one AdminAuditLog row. Never raises into the caller."""
    from governance.models import AdminAuditLog

    try:
        is_user = bool(getattr(actor, "pk", None))
        return AdminAuditLog.objects.create(
            actor=actor if is_user else None,
            actor_label=actor_label or (getattr(actor, "username", "") if is_user else str(actor or "system")),
            action=action,
            target_type=target_type,
            target_id=str(target_id or ""),
            target_label=target_label or "",
            bond_code=(bond_code or "").upper(),
            before=before or {},
            after=after or {},
            reason=reason or "",
            ip_address=_client_ip(request),
        )
    except Exception as exc:  # pragma: no cover - audit must never break the action
        logger.error("Failed to write admin audit log for %s: %s", action, exc)
        return None
