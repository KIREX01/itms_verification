"""
Onboarding: request access -> administrator grant -> signup with a single-use invite.

  submit_request(...)                 public; creates NO account
  invite_by_team_leader(tl, ...)      TL invites a Helper / QI to their own bond (admin still grants)
  grant(req, actor, ...)              admin: issues a 6-digit code + link, valid N hours, single use
  reject(req, actor, note)            admin
  reissue_invite(req, actor)          admin: new code + link (old ones die)
  find_live_invite(token)             AccessRequest for a valid, unexpired invite link
  redeem_invite(token, code, ...)     creates the account ACTIVE with role, bond and company email

The 6-digit code is used only here, at signup. Codes and tokens are stored as HMAC hashes.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import timedelta
from typing import Optional, Tuple

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from governance.capabilities import COMPANY_EMAIL_ROLES, TL_INVITABLE_ROLES, TEAM_INVITE, Role
from governance.models import AccessRequest, BondMembership, CompanyEmailLink
from governance.services import audit, authz, identity
from governance.services.flags import get_flag

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
REQUESTS_PER_IP_PER_HOUR = 10
REQUESTS_PER_EMAIL_PER_DAY = 3


class OnboardingError(ValueError):
    pass


def _hash(value: str) -> str:
    key = str(getattr(settings, "SECRET_KEY", "close-copilot")).encode()
    return hmac.new(key, (value or "").encode(), hashlib.sha256).hexdigest()


def _throttled(key: str, limit: int, window_seconds: int) -> bool:
    count = cache.get(key, 0) or 0
    if count >= limit:
        return True
    cache.set(key, count + 1, window_seconds)
    return False


def _client_ip(request) -> str:
    if request is None:
        return ""
    try:
        from core.services.device_access_service import extract_client_ip
        return extract_client_ip(request) or ""
    except Exception:
        return request.META.get("REMOTE_ADDR", "")


def _validate(full_name: str, email: str, role: str, bond: str, company_email: str) -> None:
    if not (full_name or "").strip():
        raise OnboardingError("Please enter your full name.")
    if not EMAIL_RE.match(email or ""):
        raise OnboardingError("Please enter a valid email address.")
    if role not in Role.values:
        raise OnboardingError("Please choose the role you need.")
    if not (bond or "").strip():
        raise OnboardingError("Please choose your bond / warehouse.")
    if role in COMPANY_EMAIL_ROLES:
        if not company_email:
            raise OnboardingError(
                f"{Role(role).label} access needs your company email "
                f"(@{', @'.join(identity.allowed_company_domains())})."
            )
        if not identity.is_company_email(company_email):
            raise OnboardingError(
                f"The company email must end with @{', @'.join(identity.allowed_company_domains())}."
            )


def submit_request(full_name: str, email: str, requested_role: str, bond_code: str,
                   company_email: str = "", phone: str = "", message: str = "",
                   request=None) -> Optional[AccessRequest]:
    """
    Public request. Raises OnboardingError for fixable form errors. Returns None (silently) when
    the request is swallowed (burned email, rate limit) so callers show the same "received" page.
    """
    email = identity.normalize_email(email)
    company_email = identity.normalize_email(company_email)
    _validate(full_name, email, requested_role, bond_code, company_email)

    ip = _client_ip(request)
    if ip and _throttled(f"close:onb:ip:{ip}", REQUESTS_PER_IP_PER_HOUR, 3600):
        logger.warning("Access request throttled for IP %s", ip)
        return None
    if _throttled(f"close:onb:email:{email}", REQUESTS_PER_EMAIL_PER_DAY, 86400):
        return None
    if identity.is_burned_email(email) or (company_email and identity.is_burned_email(company_email)):
        logger.warning("Swallowed access request from burned identity %s", email)
        return None

    existing = AccessRequest.objects.filter(email=email, status=AccessRequest.Status.PENDING).first()
    req = existing or AccessRequest(email=email)
    req.full_name = full_name.strip()
    req.phone = (phone or "").strip()
    req.requested_role = requested_role
    req.bond_code = bond_code
    req.company_email = company_email
    req.message = (message or "").strip()
    req.client_ip = ip
    req.source = AccessRequest.Source.SELF
    req.save()
    _notify_admins(req, request)
    return req


def invite_by_team_leader(tl, full_name: str, email: str, role: str, company_email: str = "",
                          phone: str = "", message: str = "", bond_code: Optional[str] = None,
                          request=None) -> AccessRequest:
    bond = (bond_code or authz.active_bond_code(tl) or "").upper()
    authz.require(tl, TEAM_INVITE, bond)
    if role not in TL_INVITABLE_ROLES:
        raise OnboardingError("Team Leaders can invite Helpers and Quality Inspectors only.")
    email = identity.normalize_email(email)
    company_email = identity.normalize_email(company_email)
    _validate(full_name, email, role, bond, company_email)
    if identity.is_burned_email(email):
        raise OnboardingError("This person can't be invited.")
    req = AccessRequest.objects.create(
        full_name=full_name.strip(), email=email, phone=(phone or "").strip(), requested_role=role,
        bond_code=bond, company_email=company_email, message=(message or "").strip(),
        source=AccessRequest.Source.TEAM_LEADER, invited_by=tl, client_ip=_client_ip(request),
    )
    audit.record(tl, "onboarding.tl_invite", target_type="access_request", target_id=req.pk,
                 target_label=email, bond_code=bond, after={"role": role}, request=request)
    _notify_admins(req, request)
    return req


def _notify_admins(req: AccessRequest, request=None) -> None:
    try:
        from django.contrib.auth.models import User
        from core.services import email_service

        admins = list(User.objects.filter(is_superuser=True, is_active=True).exclude(email="")
                      .values_list("email", flat=True))
        if not admins:
            return
        review_url = f"/backoffice/access/{req.pk}/"
        if request is not None:
            review_url = request.build_absolute_uri(review_url)
        email_service.send_access_request_admin_alert(
            admin_emails=admins, requester_email=req.email,
            requester_name=f"{req.full_name} · {req.get_requested_role_display()} @ {req.bond_code}",
            client_ip=req.client_ip, mac_address="", device_name="", activation_code="",
            approve_url=review_url,
        )
    except Exception as exc:
        logger.warning("Could not notify administrators about access request %s: %s", req.pk, exc)


def _issue_invite(req: AccessRequest) -> Tuple[str, str]:
    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1_000_000):06d}"
    hours = int(get_flag("onboarding.invite_valid_hours", default=72) or 72)
    req.invite_token_hash = _hash(token)
    req.invite_code_hash = _hash(code)
    req.invite_expires_at = timezone.now() + timedelta(hours=hours)
    req.invite_attempts = 0
    return token, code


def _send_invite(req: AccessRequest, token: str, code: str, request=None) -> None:
    try:
        from core.services import email_service

        link = f"/signup/?invite={token}"
        if request is not None:
            link = request.build_absolute_uri(link)
        email_service.send_2fa_verification_code(req.email, code, username=req.full_name, verify_url=link)
    except Exception as exc:
        logger.warning("Could not send invite email for request %s: %s", req.pk, exc)


@transaction.atomic
def grant(req: AccessRequest, actor, role: Optional[str] = None, bond_code: Optional[str] = None,
          company_email: Optional[str] = None, note: str = "", request=None) -> Tuple[AccessRequest, str, str]:
    if not authz.is_platform_admin(actor):
        raise authz.CapabilityDenied("platform.access_grant")
    if req.status not in (AccessRequest.Status.PENDING, AccessRequest.Status.EXPIRED):
        raise OnboardingError(f"This request is already {req.get_status_display().lower()}.")
    role = role or req.requested_role
    bond = (bond_code or req.bond_code or "").upper()
    company = identity.normalize_email(company_email if company_email is not None else req.company_email)
    _validate(req.full_name, req.email, role, bond, company)
    if identity.is_burned_email(req.email) or (company and identity.is_burned_email(company)):
        raise OnboardingError("This identity is burned and can never be granted access.")
    if company and CompanyEmailLink.objects.filter(email=company).exists():
        raise OnboardingError("That company email is already attached to another account.")

    req.granted_role = role
    req.granted_bond = bond
    req.company_email = company
    req.status = AccessRequest.Status.GRANTED
    req.reviewed_by = actor
    req.reviewed_at = timezone.now()
    req.review_note = note or ""
    token, code = _issue_invite(req)
    req.save()
    audit.record(actor, "onboarding.grant", target_type="access_request", target_id=req.pk,
                 target_label=req.email, bond_code=bond,
                 after={"role": role, "bond": bond, "company_email": company}, reason=note, request=request)
    _send_invite(req, token, code, request)
    return req, token, code


def reject(req: AccessRequest, actor, note: str = "", request=None) -> AccessRequest:
    if not authz.is_platform_admin(actor):
        raise authz.CapabilityDenied("platform.access_grant")
    if req.status in (AccessRequest.Status.USED,):
        raise OnboardingError("This request already created an account.")
    req.status = AccessRequest.Status.REJECTED
    req.reviewed_by = actor
    req.reviewed_at = timezone.now()
    req.review_note = note or ""
    req.invite_token_hash = ""
    req.invite_code_hash = ""
    req.save()
    audit.record(actor, "onboarding.reject", target_type="access_request", target_id=req.pk,
                 target_label=req.email, reason=note, request=request)
    return req


def reissue_invite(req: AccessRequest, actor, request=None) -> Tuple[AccessRequest, str, str]:
    if not authz.is_platform_admin(actor):
        raise authz.CapabilityDenied("platform.access_grant")
    if req.status not in (AccessRequest.Status.GRANTED, AccessRequest.Status.EXPIRED) or not req.granted_role:
        raise OnboardingError("Only granted requests can have their invite re-sent.")
    token, code = _issue_invite(req)
    req.status = AccessRequest.Status.GRANTED
    req.save()
    audit.record(actor, "onboarding.reissue", target_type="access_request", target_id=req.pk,
                 target_label=req.email, request=request)
    _send_invite(req, token, code, request)
    return req, token, code


def find_live_invite(token: str) -> Optional[AccessRequest]:
    if not token:
        return None
    req = AccessRequest.objects.filter(invite_token_hash=_hash(token), status=AccessRequest.Status.GRANTED).first()
    if req is None:
        return None
    if req.invite_expires_at and req.invite_expires_at <= timezone.now():
        req.status = AccessRequest.Status.EXPIRED
        req.save(update_fields=["status", "updated_at"])
        return None
    return req


def suggest_username(full_name: str, email: str = "") -> str:
    """e.g. "Okello Samuel" -> "okello.samuel" (or okello.samuel2 if taken)."""
    import re
    from django.contrib.auth.models import User

    base = ".".join(re.sub(r"[^a-z0-9]+", " ", (full_name or "").lower()).split()[:2])
    if len(base) < 3:
        base = re.sub(r"[^a-z0-9._-]", "", (email or "").split("@")[0].lower())
    base = (base or "user")[:26].strip("._-") or "user"
    candidate, n = base, 2
    while User.objects.filter(username__iexact=candidate).exists():
        candidate, n = f"{base}{n}", n + 1
    return candidate


def redeem_invite(token: str, code: str, username: str, password: str, password_confirm: str,
                  request=None, full_name: str = ""):
    """Creates the account. Returns the new User; raises OnboardingError with a user-facing message.
    (Not one big transaction: failed code attempts must persist even though we raise.)"""
    from django.contrib.auth.models import User

    req = find_live_invite(token)
    if req is None:
        raise OnboardingError("This invite link is invalid, used or expired. Ask the administrator to re-send it.")

    max_attempts = int(get_flag("onboarding.max_code_attempts", default=5) or 5)
    if not hmac.compare_digest(req.invite_code_hash, _hash((code or "").strip())):
        req.invite_attempts += 1
        if req.invite_attempts >= max_attempts:
            req.status = AccessRequest.Status.EXPIRED
            req.invite_token_hash = ""
            req.save()
            raise OnboardingError("Too many wrong codes. This invite is now locked; ask the administrator to re-send it.")
        req.save(update_fields=["invite_attempts", "updated_at"])
        left = max_attempts - req.invite_attempts
        raise OnboardingError(f"That code is not correct. {left} attempt(s) left.")

    if password != password_confirm:
        raise OnboardingError("The passwords don't match.")
    if len(password or "") < 8:
        raise OnboardingError("Choose a password of at least 8 characters.")
    if identity.is_burned_email(req.email):
        raise OnboardingError("This identity can no longer be used.")
    if User.objects.filter(email__iexact=req.email).exists():
        raise OnboardingError("An account with this email already exists. Sign in instead.")

    full_name = " ".join((full_name or "").split())
    if full_name and len(full_name) < 3:
        raise OnboardingError("Enter your full name.")
    with transaction.atomic():
        return _create_account_from_invite(req, username, password, request, full_name=full_name[:150])


def _link_devices(user, req: AccessRequest) -> int:
    """Devices that asked for access or were unlocked with this email now belong to the new account, by name."""
    from django.db.models import Q
    from core.models import AuthorizedDevice

    emails = {e.lower() for e in (req.email, req.company_email) if e}
    q = Q()
    for e in emails:
        q |= Q(authorized_email__iexact=e) | Q(request_email__iexact=e) | Q(access_code_email__iexact=e)
    return AuthorizedDevice.objects.filter(q, user__isnull=True).update(
        user=user, request_name=(user.get_full_name() or user.username)[:150], authorized_email=user.email)


def _create_account_from_invite(req: AccessRequest, username: str, password: str, request=None,
                                full_name: str = ""):
    from core.services import auth_service

    req = AccessRequest.objects.select_for_update().get(pk=req.pk)
    if req.status != AccessRequest.Status.GRANTED:
        raise OnboardingError("This invite was already used.")
    if full_name:
        req.full_name = full_name  # corrected at sign-up; saved with the request below
    user, err = auth_service.create_operator_account(
        username=username, password=password, full_name=req.full_name, email=req.email,
    )
    if user is None:
        raise OnboardingError(err)
    _link_devices(user, req)
    from governance.models import AccountState
    state, _ = AccountState.objects.get_or_create(user=user)
    state.assigned_roles = [req.granted_role]
    state.save(update_fields=["assigned_roles"])

    BondMembership.objects.update_or_create(
        user=user, bond_code=req.granted_bond, role=req.granted_role,
        defaults={"status": BondMembership.Status.ACTIVE, "granted_by": req.reviewed_by,
                  "status_reason": f"Granted via access request #{req.pk}"},
    )
    if req.company_email:
        via = (CompanyEmailLink.VerifiedVia.SIGNUP_CODE if req.company_email == req.email
               else CompanyEmailLink.VerifiedVia.ADMIN_GRANT)
        identity.attach_company_email(user, req.company_email, via, actor=req.reviewed_by, request=request)
    try:
        from core.models import OperatorProfile
        OperatorProfile.objects.update_or_create(
            user=user, defaults={"active_facility": req.granted_bond, "allowed_facilities": [req.granted_bond]},
        )
    except Exception as exc:
        logger.debug("Could not create operator profile: %s", exc)

    req.status = AccessRequest.Status.USED
    req.used_at = timezone.now()
    req.created_user = user
    req.invite_token_hash = ""
    req.invite_code_hash = ""
    req.save()
    audit.record(user, "onboarding.signup", target_type="user", target_id=user.pk, target_label=user.username,
                 bond_code=req.granted_bond, after={"role": req.granted_role, "access_request": req.pk},
                 request=request)
    return user


# ---------------------------------------------------------------------------
# Sign-up after the device code proved the email (no invite needed). The account starts with no role:
# the administrator assigns the role; bonds come from the person's ITMS access.
# ---------------------------------------------------------------------------
SIGNUP_SESSION_KEY = "close_verified_signup"
SIGNUP_SESSION_MINUTES = 30


def start_verified_signup(request, email: str, first_name: str, last_name: str, device_id: str) -> None:
    request.session[SIGNUP_SESSION_KEY] = {
        "email": email.lower(), "first_name": first_name, "last_name": last_name,
        "device_id": device_id, "at": timezone.now().isoformat(),
    }


def verified_signup(request) -> Optional[dict]:
    from django.utils.dateparse import parse_datetime

    data = request.session.get(SIGNUP_SESSION_KEY)
    if not isinstance(data, dict):
        return None
    at = parse_datetime(str(data.get("at") or ""))
    if not at or (timezone.now() - at).total_seconds() > SIGNUP_SESSION_MINUTES * 60:
        request.session.pop(SIGNUP_SESSION_KEY, None)
        return None
    return data


def validate_new_password(password: str, password_confirm: str, username: str = "", email: str = "",
                          first_name: str = "", last_name: str = "") -> None:
    from django.contrib.auth.models import User
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError

    if password != password_confirm:
        raise OnboardingError("The passwords don't match.")
    probe = User(username=username, email=email, first_name=first_name, last_name=last_name)
    try:
        validate_password(password, user=probe)
    except ValidationError as exc:
        raise OnboardingError(" ".join(exc.messages))


def clean_name(value: str, label: str) -> str:
    import re
    value = " ".join((value or "").split())
    if len(value) < 2 or len(value) > 60 or not re.fullmatch(r"[^\W\d_][\w' .-]*", value):
        raise OnboardingError(f"Enter your {label}.")
    return value[:1].upper() + value[1:]


def self_signup(request, username: str, password: str, password_confirm: str,
                first_name: str, last_name: str):
    """Creates the account for the email proven by the device code. Returns the new User."""
    from django.contrib.auth.models import User
    from core.services import auth_service
    from governance.models import AccountState

    data = verified_signup(request)
    if data is None:
        raise OnboardingError("Your email check has expired. Start again on the device page.")
    email = data["email"]
    first_name = clean_name(first_name or data.get("first_name", ""), "first name")
    last_name = clean_name(last_name or data.get("last_name", ""), "last name")
    if identity.is_burned_email(email):
        raise OnboardingError("This email can no longer be used.")
    if User.objects.filter(email__iexact=email).exists():
        raise OnboardingError("An account with this email already exists. Sign in instead.")
    validate_new_password(password, password_confirm, username, email, first_name, last_name)

    invite = (AccessRequest.objects.filter(email__iexact=email, status=AccessRequest.Status.GRANTED)
              .order_by("-reviewed_at").first())
    with transaction.atomic():
        user, err = auth_service.create_operator_account(
            username=username, password=password, full_name=f"{first_name} {last_name}", email=email)
        if user is None:
            raise OnboardingError(err)
        user.first_name, user.last_name = first_name, last_name
        user.save(update_fields=["first_name", "last_name"])
        state, _ = AccountState.objects.get_or_create(user=user)
        if identity.is_company_email(email):
            # The device code proved this company email.
            identity.attach_company_email(user, email, CompanyEmailLink.VerifiedVia.SIGNUP_CODE, actor=user,
                                          request=request)
        if invite is not None and invite.granted_role:
            # An approved access request for this email: apply its role and bond.
            BondMembership.objects.update_or_create(
                user=user, bond_code=invite.granted_bond, role=invite.granted_role,
                defaults={"status": BondMembership.Status.ACTIVE, "granted_by": invite.reviewed_by,
                          "status_reason": f"Granted via access request #{invite.pk}"})
            state.assigned_roles = [invite.granted_role]
            state.save(update_fields=["assigned_roles"])
            invite.status, invite.used_at, invite.created_user = AccessRequest.Status.USED, timezone.now(), user
            invite.invite_token_hash = invite.invite_code_hash = ""
            invite.save()
        fake_req = AccessRequest(email=email, company_email="")
        _link_devices(user, fake_req)
    request.session.pop(SIGNUP_SESSION_KEY, None)
    audit.record(user, "onboarding.self_signup", target_type="user", target_id=user.pk, target_label=user.username,
                 after={"email": email, "role_from_invite": getattr(invite, "granted_role", "")}, request=request)
    _alert_admins_new_signup(user)
    return user


def _alert_admins_new_signup(user) -> None:
    try:
        from django.contrib.auth.models import User
        from core.services import email_service
        admins = list(User.objects.filter(is_superuser=True, is_active=True).exclude(email="")
                      .values_list("email", flat=True))
        for to in admins:
            email_service.send_new_signup_alert(to, user)
    except Exception as exc:
        logger.warning("New sign-up alert not sent: %s", exc)

