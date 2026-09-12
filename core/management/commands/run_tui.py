import os

from django.core.management.base import BaseCommand

# Must be set before core.tui.app (and therefore any ORM call) is imported --
# see the note in core/tui/app.py's module docstring for why this is safe here.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from core.tui.app import ITMSOperatorApp


class Command(BaseCommand):
    help = "Launches the Textual operator dashboard (keyboard-driven review & approval UI)."

    def handle(self, *args, **options):
        # Ensure database tables and columns exist before querying
        from core.services.config_service import ensure_migrations_applied, ensure_operator_accounts_synced
        ensure_migrations_applied()
        ensure_operator_accounts_synced()
        ITMSOperatorApp().run()
