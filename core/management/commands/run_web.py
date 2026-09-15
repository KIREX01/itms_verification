"""
Management command to launch the ITMS Web Operator Console and auto-open the browser.

Usage:
    python manage.py run_web
    python manage.py run_web --port 8080 --no-browser
"""
import sys
import threading
import time
import webbrowser
from django.core.management import call_command
from django.core.management.base import BaseCommand

from core.services import config_service


class Command(BaseCommand):
    help = "Launches the Web Operator Dashboard and automatically opens your default web browser."

    def add_arguments(self, parser):
        parser.add_argument(
            "--port",
            type=int,
            default=8000,
            help="Port to run the web server on (default: 8000).",
        )
        parser.add_argument(
            "--no-browser",
            action="store_true",
            help="Do not automatically launch the web browser on startup.",
        )

    def handle(self, *args, **options):
        port = options["port"]
        no_browser = options["no_browser"]
        host = "127.0.0.1"
        url = f"http://{host}:{port}/"

        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("  ITMS VERIFICATION COPILOT - WEB OPERATOR CONSOLE"))
        self.stdout.write(self.style.SUCCESS("=" * 65))

        # 1. Ensure migrations and initial operator setup
        self.stdout.write("Checking database schema and migrations...")
        config_service.ensure_migrations_applied()
        config_service.ensure_operator_accounts_synced()

        db_info = config_service.get_active_database_info()
        self.stdout.write(f"Active Database: {db_info.get('vendor', 'sqlite').upper()} ({db_info.get('name', 'db.sqlite3')})")

        # 2. Timer to auto-launch browser once server is listening
        if not no_browser:
            def _launch_browser():
                time.sleep(1.2)
                try:
                    webbrowser.open(url)
                except Exception as exc:
                    print(f"Note: Could not open browser automatically: {exc}")

            threading.Thread(target=_launch_browser, daemon=True).start()
            self.stdout.write(self.style.NOTICE(f"Opening browser at: {url}"))
        else:
            self.stdout.write(f"Web server ready at: {url}")

        self.stdout.write("Press Ctrl+C to stop the web server.")
        self.stdout.write(self.style.SUCCESS("-" * 65))

        # 3. Start Django Server
        try:
            call_command("runserver", f"{host}:{port}", insecure_serving=True)
        except KeyboardInterrupt:
            self.stdout.write("\nWeb server stopped cleanly.")
            sys.exit(0)
