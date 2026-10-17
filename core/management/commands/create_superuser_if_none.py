import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        "Idempotently creates a Django superuser from DJANGO_SUPERUSER_* environment "
        "variables, if no superuser exists yet. Safe to run on every deploy/setup."
    )

    def handle(self, *args, **options):
        User = get_user_model()

        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip() or "admin"
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "").strip()

        if User.objects.filter(is_superuser=True).exists():
            if email:
                su = User.objects.filter(is_superuser=True).first()
                if su and su.email != email:
                    su.email = email
                    su.save(update_fields=["email"])
                    self.stdout.write(self.style.SUCCESS(f"Updated existing superuser '{su.username}' email to '{email}'."))
            self.stdout.write("A superuser already exists; skipping.")
            return

        if not password:
            self.stdout.write(self.style.WARNING("DJANGO_SUPERUSER_PASSWORD not set; skipping superuser creation."))
            return

        User.objects.create_superuser(username=username, email=email or "admin@example.com", password=password)
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' created successfully."))
