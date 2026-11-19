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
    UserSessionIndex.objects.update_or_create(
        session_key=session.session_key, defaults={"user": user, "ip_address": ip}
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
