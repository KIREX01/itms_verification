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

        if User.objects.filter(is_superuser=True).exists():
            self.stdout.write("A superuser already exists; skipping.")
            return

        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin").strip()
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "admin").strip()

        if not username or not password:
            username = "admin"
            password = "admin"

        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' created successfully."))
