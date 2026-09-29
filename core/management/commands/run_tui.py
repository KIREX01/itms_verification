import os

from django.core.management.base import BaseCommand

# Must be set before core.tui.app (and therefore any ORM call) is imported --
# see the note in core/tui/app.py's module docstring for why this is safe here.
os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

from core.tui.app import ITMSOperatorApp


class Command(BaseCommand):
    help = "Launches the Textual operator dashboard (keyboard-driven review & approval UI)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--no-web",
            action="store_true",
            help="Do not start the background mobile companion web server.",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8000,
            help="Port for the background web server (default: 8000).",
        )

    def handle(self, *args, **options):
        # Ensure database tables and columns exist before querying
        from core.services.config_service import ensure_migrations_applied, ensure_operator_accounts_synced
        ensure_migrations_applied()
        ensure_operator_accounts_synced()

        no_web = options.get("no_web", False)
        port = options.get("port", 8000)

        # Launch background web server so mobile camera pairing works seamlessly while inside TUI
        if not no_web:
            from core.services.background_web_service import start_background_web_server, stop_background_web_server
            start_background_web_server(host="0.0.0.0", port=port)

        try:
            ITMSOperatorApp().run()
        finally:
            if not no_web:
                from core.services.background_web_service import stop_background_web_server
                stop_background_web_server()
