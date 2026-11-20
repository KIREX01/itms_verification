"""Reset an administrator's 2-step sign-in (lost phone). Run on the server:

    python manage.py itms_admin_2fa reset close_admin
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from governance.services import audit, totp


class Command(BaseCommand):
    help = "Reset an administrator's authenticator-app 2-step sign-in."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["reset", "status"])
        parser.add_argument("username")

    def handle(self, *args, **opts):
        user = User.objects.filter(username=opts["username"]).first()
        if user is None:
            raise CommandError(f"No user {opts['username']}.")
        if opts["action"] == "status":
            self.stdout.write(f"{user.username}: 2-step sign-in {'ON' if totp.is_enrolled(user) else 'not set up'}")
            return
        totp.reset(user)
        audit.record("cli", "admin.2fa_reset", target_type="user", target_id=user.pk, target_label=user.username,
                     actor_label="cli")
        self.stdout.write(self.style.SUCCESS(f"2-step sign-in reset for {user.username}; set it up again at "
                                             "the next backoffice visit."))
