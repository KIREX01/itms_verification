"""Authentication backend: Django's ModelBackend plus sign-in history and password-guessing lockout."""
from django.contrib.auth.backends import ModelBackend

from governance.services import login_guard


class LockoutModelBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get("email") or ""
        locked, _ = login_guard.lock_status(username, request)
        if locked:
            login_guard.record(username, False, request, reason="LOCKED")
            return None
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            login_guard.record(username, False, request, reason="BAD_CREDENTIALS")
        else:
            login_guard.record(username, True, request, user=user, reason="PASSWORD_OK")
        return user
