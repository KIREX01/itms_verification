"""Keeps the session index and account state in step with Django auth events."""
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings


@receiver(user_logged_in)
def _index_session(sender, request, user, **kwargs):
    from governance.models import UserSessionIndex

    session = getattr(request, "session", None)
    if session is None:
        return
    if not session.session_key:
        session.save()
    ip = ""
    try:
        from core.services.device_access_service import extract_client_ip
        ip = extract_client_ip(request) or ""
    except Exception:
        pass
    device_id = ""
    try:
        from governance.services.device_admission import _verified_device_id
        device_id = _verified_device_id(request)
    except Exception:
        pass
    UserSessionIndex.objects.update_or_create(
        session_key=session.session_key,
        defaults={"user": user, "ip_address": ip, "device_id": device_id,
                  "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300]},
    )


@receiver(user_logged_out)
def _unindex_session(sender, request, user, **kwargs):
    from governance.models import UserSessionIndex

    session = getattr(request, "session", None)
    key = getattr(session, "session_key", None)
    if key:
        UserSessionIndex.objects.filter(session_key=key).delete()


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def _ensure_account_state(sender, instance, created, **kwargs):
    if created:
        from governance.models import AccountState
        AccountState.objects.get_or_create(user=instance)


# --- Policy version: any change installs must learn about bumps the version -------------
def _bump(*args, **kwargs):
    from governance.services.policy_sync import bump_policy_version
    bump_policy_version()


def _connect_policy_signals():
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_delete
    from governance import models as gm

    for model in (gm.AccountState, gm.BondMembership, gm.CompanyEmailLink, gm.FeatureFlag,
                  gm.BurnedIdentity, gm.ItmsWarehouseAccess, get_user_model()):
        post_save.connect(_bump, sender=model, dispatch_uid=f"policy-bump-save-{model.__name__}")
        post_delete.connect(_bump, sender=model, dispatch_uid=f"policy-bump-del-{model.__name__}")


_connect_policy_signals()


# --- Django groups follow roles, account state and the administrator flag ------------------------
def _sync_groups_for(user_id):
    from django.contrib.auth import get_user_model
    from governance.services.groups import sync_user_groups
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is not None:
        try:
            sync_user_groups(user)
        except Exception:
            pass


def _on_membership_change(sender, instance, **kwargs):
    _sync_groups_for(instance.user_id)


def _on_state_change(sender, instance, **kwargs):
    _sync_groups_for(instance.user_id)


def _on_user_save(sender, instance, created, **kwargs):
    if not kwargs.get("raw"):
        _sync_groups_for(instance.pk)


def _on_group_permissions_change(sender, instance, action, **kwargs):
    if action in ("post_add", "post_remove", "post_clear"):
        from governance.services.groups import invalidate
        invalidate()
        _bump()


def _connect_group_signals():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    from django.db.models.signals import m2m_changed, post_delete
    from governance import models as gm

    post_save.connect(_on_membership_change, sender=gm.BondMembership, dispatch_uid="groups-membership-save")
    post_delete.connect(_on_membership_change, sender=gm.BondMembership, dispatch_uid="groups-membership-del")
    post_save.connect(_on_state_change, sender=gm.AccountState, dispatch_uid="groups-state-save")
    post_save.connect(_on_user_save, sender=get_user_model(), dispatch_uid="groups-user-save")
    m2m_changed.connect(_on_group_permissions_change, sender=Group.permissions.through,
                        dispatch_uid="groups-permissions-changed")


_connect_group_signals()

