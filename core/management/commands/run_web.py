"""
Management command to launch the ITMS Web Operator Console and auto-open the browser.

Usage:
    python manage.py run_web
    python manage.py run_web --port 8080 --no-browser
"""
import os
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
            "--host",
            type=str,
            default="0.0.0.0",
            help="IP address to bind the web server to (default: 0.0.0.0 for LAN/Hotspot mobile access).",
        )
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
        parser.add_argument(
            "--noreload",
            action="store_true",
            help="Tells Django to NOT use the auto-reloader.",
        )

    def handle(self, *args, **options):
        port = options["port"]
        bind_host = options.get("host", "0.0.0.0")
        no_browser = options["no_browser"]
        noreload = options["noreload"]
        local_url = f"http://127.0.0.1:{port}/"

        self.stdout.write(self.style.SUCCESS("=" * 65))
        self.stdout.write(self.style.SUCCESS("  ITMS VERIFICATION COPILOT - WEB OPERATOR CONSOLE"))
        self.stdout.write(self.style.SUCCESS("=" * 65))

        # 1. Ensure migrations and initial operator setup
        self.stdout.write("Checking database schema and migrations...")
        config_service.ensure_migrations_applied()
        config_service.ensure_operator_accounts_synced()

        db_info = config_service.get_active_database_info()
        self.stdout.write(f"Active Database: {db_info.get('vendor', 'sqlite').upper()} ({db_info.get('name', 'db.sqlite3')})")

        # 2. Network & Mobile Companion discovery
        from core.services import network_service
        net_info = network_service.get_mobile_connection_info(port=port)
        self.stdout.write(self.style.SUCCESS(f"[Mobile] Mobile Companion: {net_info['primary_url']}"))
        if net_info.get("hotspot_detected"):
            self.stdout.write(self.style.NOTICE("   [+] Windows Mobile Hotspot detected (192.168.137.1)"))

        # 3. Timer to auto-launch browser once server is listening
        is_reloader_child = os.environ.get("RUN_MAIN") == "true"
        is_noreload = noreload or ("--noreload" in sys.argv)
        should_open_browser = not no_browser and (is_reloader_child or is_noreload)

        if should_open_browser:
            def _launch_browser():
                time.sleep(1.0)
                try:
                    webbrowser.open(local_url)
                except Exception as exc:
                    print(f"Note: Could not open browser automatically: {exc}")

            threading.Thread(target=_launch_browser, daemon=True).start()
            self.stdout.write(self.style.NOTICE(f"Opening browser at: {local_url}"))
        elif not no_browser and not is_reloader_child:
            self.stdout.write(self.style.NOTICE(f"Web server starting at: {local_url}"))
        else:
            self.stdout.write(f"Web server ready at: {local_url}")

        self.stdout.write("Press Ctrl+C to stop the web server.")
        self.stdout.write(self.style.SUCCESS("-" * 65))

        # 4. Start Django Server
        try:
            call_command(
                "runserver",
                f"{bind_host}:{port}",
                use_reloader=not is_noreload,
                insecure_serving=True,
            )
        except KeyboardInterrupt:
            self.stdout.write("\nWeb server stopped cleanly.")
            sys.exit(0)
