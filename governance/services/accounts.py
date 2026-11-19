"""
Account lifecycle: suspend, reinstate and burn. All take effect immediately.

  suspend   - reversible: account locked, every session killed, tokens invalidated.
  reinstate - lifts a suspension (never a burn).
  burn      - permanent ban: everything suspend does, plus every device revoked, ITMS session
              cookies cleared, all roles removed, company email revoked, and both the login and
              company emails added to BurnedIdentity so they can never come back.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.contrib.sessions.models import Session
from django.db import transaction
from django.utils import timezone

from governance.models import (
    AccountState,
    BondMembership,
    BurnedIdentity,
    CompanyEmailLink,
    UserSessionIndex,
)
from governance.services import audit

logger = logging.getLogger(__name__)


class AccountLifecycleError(ValueError):
    pass


def get_state(user) -> AccountState:
    state, _ = AccountState.objects.get_or_create(user=user)
    return state


def security_stamp(user) -> str:
    """Current security stamp; tokens that embed it die when it rotates (suspend/burn)."""
    if not getattr(user, "pk", None):
        return ""
    stamp = AccountState.objects.filter(user_id=user.pk).values_list("security_stamp", flat=True).first()
    return stamp or get_state(user).security_stamp


def kill_sessions(user) -> int:
    """Deletes every Django session belonging to the user (indexed + a full scan fallback)."""
    keys = set(UserSessionIndex.objects.filter(user=user).values_list("session_key", flat=True))
    # Fallback scan for sessions created before the index existed.
    for session in Session.objects.filter(expire_date__gt=timezone.now()).iterator():
        try:
            if str(session.get_decoded().get("_auth_user_id")) == str(user.pk):
                keys.add(session.session_key)
        except Exception:
            continue
    deleted, _ = Session.objects.filter(session_key__in=keys).delete()
    UserSessionIndex.objects.filter(user=user).delete()
    return deleted


def _clear_remembered_tui_session(user) -> None:
    try:
        from core.services import auth_service

        remembered = auth_service.get_remembered_session()
        if remembered and getattr(remembered, "pk", None) == user.pk:
            auth_service.clear_remembered_session()
    except Exception as exc:
        logger.debug("Could not clear remembered TUI session: %s", exc)


@transaction.atomic
def suspend(user, reason: str, actor=None, request=None) -> AccountState:
    state = get_state(user)
    if state.status == AccountState.Status.BURNED:
        raise AccountLifecycleError("This account is burned; it cannot be suspended or reinstated.")
    before = {"status": state.status}
    state.status = AccountState.Status.SUSPENDED
    state.reason = reason or ""
    state.changed_by = actor if getattr(actor, "pk", None) else None
    state.changed_at = timezone.now()
    state.rotate_stamp()
    state.save()
    killed = kill_sessions(user)
    _clear_remembered_tui_session(user)
    audit.record(actor, "account.suspend", target_type="user", target_id=user.pk, target_label=user.username,
                 before=before, after={"status": state.status, "sessions_killed": killed},
                 reason=reason, request=request)
    return state


@transaction.atomic
def reinstate(user, reason: str = "", actor=None, request=None) -> AccountState:
    state = get_state(user)
    if state.status == AccountState.Status.BURNED:
        raise AccountLifecycleError("A burned account can never be reinstated.")
    before = {"status": state.status}
    state.status = AccountState.Status.ACTIVE
    state.reason = reason or ""
    state.changed_by = actor if getattr(actor, "pk", None) else None
    state.changed_at = timezone.now()
    state.save()
    audit.record(actor, "account.reinstate", target_type="user", target_id=user.pk, target_label=user.username,
                 before=before, after={"status": state.status}, reason=reason, request=request)
    return state


@transaction.atomic
def burn(user, reason: str, actor=None, request=None) -> AccountState:
    """Permanent ban. Irreversible."""
    if not (reason or "").strip():
        raise AccountLifecycleError("A reason is required to burn an account.")
    if getattr(actor, "pk", None) and actor.pk == user.pk:
        raise AccountLifecycleError("You cannot burn your own account.")

    from core.models import AuthorizedDevice, EmailAccessRequest, OperatorITMSSession

    state = get_state(user)
    before = {"status": state.status, "is_active": user.is_active}

    state.status = AccountState.Status.BURNED
    state.reason = reason
    state.changed_by = actor if getattr(actor, "pk", None) else None
    state.changed_at = timezone.now()
    state.rotate_stamp()
    state.save()

    user.is_active = False
    user.save(update_fields=["is_active"])

    killed = kill_sessions(user)
    _clear_remembered_tui_session(user)

    login_email = (user.email or "").strip().lower()
    link = CompanyEmailLink.objects.filter(user=user).first()
    company_email = link.email if link else ""

    device_q = AuthorizedDevice.objects.filter(user=user)
    if login_email:
        device_q = device_q | AuthorizedDevice.objects.filter(authorized_email__iexact=login_email)
    devices = device_q.update(status=AuthorizedDevice.Status.REVOKED, block_reason=f"Account burned: {reason}"[:255])

    OperatorITMSSession.objects.filter(user=user).update(cookies={}, csrf_token="", is_authenticated=False)

    memberships = BondMembership.objects.filter(user=user).exclude(status=BondMembership.Status.REMOVED)
    removed_roles = list(memberships.values_list("bond_code", "role"))
    memberships.update(status=BondMembership.Status.REMOVED, status_reason="Account burned")

    if link:
        link.status = CompanyEmailLink.Status.REVOKED
        link.save(update_fields=["status", "updated_at"])

    for email in {login_email, company_email} - {""}:
        BurnedIdentity.objects.get_or_create(
            email=email,
            defaults={"burned_user_id": user.pk, "reason": reason,
                      "burned_by": actor if getattr(actor, "pk", None) else None},
        )
        EmailAccessRequest.objects.filter(email__iexact=email).update(status=EmailAccessRequest.Status.BLOCKED)

    audit.record(actor, "account.burn", target_type="user", target_id=user.pk, target_label=user.username,
                 before=before,
                 after={"status": state.status, "sessions_killed": killed, "devices_revoked": devices,
                        "roles_removed": [f"{r}@{b}" for b, r in removed_roles],
                        "emails_burned": sorted({login_email, company_email} - {""})},
                 reason=reason, request=request)
    return state
