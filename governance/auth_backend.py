"""Authentication backend: Django's ModelBackend plus sign-in history and password-guessing lockout."""
from django.contrib.auth.backends import ModelBackend

from governance.services import login_guard


class LockoutModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get("email") or ""
        username = _username_for(username)
        locked, _ = login_guard.lock_status(username, request)
        if locked:
            login_guard.record(username, False, request, reason="LOCKED")
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is not None and user.is_superuser and not _superuser_allowed_here(request):
            login_guard.record(username, False, request, user=user, reason="ADMIN_NOT_FROM_THIS_PC")
            return None
        if user is None:
            login_guard.record(username, False, request, reason="BAD_CREDENTIALS")
        else:
            login_guard.record(username, True, request, user=user, reason="PASSWORD_OK")
        return user


def _superuser_allowed_here(request) -> bool:
    """
    On a laptop install the local administrator is break-glass only: it signs in on the PC itself (browser on
    this computer, or the TUI), never from a phone or another computer. The central server is unaffected.
    """
    from django.conf import settings

    if getattr(settings, "DEPLOYMENT_ROLE", "edge") == "central" or getattr(settings, "IS_TESTING", False):
        return True
    if request is None:
        return True  # TUI / command line on this computer
    from governance.install_middleware import is_this_computer
    return is_this_computer(request.META.get("REMOTE_ADDR", ""))


def _username_for(login: str) -> str:
    """People may sign in with their email (login email or attached company email) instead of the username."""
    login = (login or "").strip()
    from django.contrib.auth.models import User
    if "@" not in login:
        # Usernames are case-insensitive (new ones are stored lower-case).
        exact = User.objects.filter(username=login).values_list("username", flat=True).first()
        if exact:
            return exact
        ci = list(User.objects.filter(username__iexact=login).values_list("username", flat=True)[:2])
        return ci[0] if len(ci) == 1 else login
    matches = list(User.objects.filter(email__iexact=login).values_list("username", flat=True)[:2])
    if len(matches) == 1:
        return matches[0]
    try:
        from governance.models import CompanyEmailLink
        link = CompanyEmailLink.objects.select_related("user").filter(email__iexact=login).first()
        if link:
            return link.user.username
    except Exception:
        pass
    return login

