"""
Management command to launch the ITMS Web Operator Console and Mobile Companion.
Provides built-in, 100% offline self-signed HTTPS support to unlock modern mobile
browser Secure Context (isSecureContext) for live camera and barcode/QR code scanning.

Usage:
    python manage.py run_web                     # Dual mode (HTTP on 8000, HTTPS on 443)
    python manage.py run_web --ssl              # Primary HTTPS mode on 443
    python manage.py run_web --port 8080        # Custom HTTP port
    python manage.py run_web --ssl-port 9443    # Custom HTTPS port
    python manage.py run_web --no-ssl           # Plain HTTP only
"""
import logging
import os
import ssl
import sys
import threading
import time
import webbrowser
from typing import List

from django.conf import settings
from django.contrib.staticfiles.handlers import StaticFilesHandler
from django.core.handlers.wsgi import WSGIHandler
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.core.servers.basehttp import ThreadedWSGIServer, WSGIRequestHandler

from core.services import config_service, network_service, ssl_service

logger = logging.getLogger(__name__)


class SecureWSGIRequestHandler(WSGIRequestHandler):
    """WSGI request handler that marks incoming requests as secure HTTPS."""

    def get_environ(self):
        env = super().get_environ()
        env["HTTPS"] = "on"
        env["wsgi.url_scheme"] = "https"
        return env


class SecureThreadedWSGIServer(ThreadedWSGIServer):
    """Threaded WSGI server wrapping socket with self-signed SSL/TLS context."""

    def __init__(self, server_address, RequestHandlerClass, ssl_context=None, *args, **kwargs):
        self.ssl_context = ssl_context
        super().__init__(server_address, RequestHandlerClass, *args, **kwargs)
        if self.ssl_context:
            self.socket = self.ssl_context.wrap_socket(self.socket, server_side=True)

    def handle_error(self, request, client_address):
        """Silently handle transient client SSL handshake resets / browser cancellations."""
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type and issubclass(exc_type, (ssl.SSLError, ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def create_secure_server(bind_host: str, port: int, ssl_context: ssl.SSLContext) -> SecureThreadedWSGIServer:
    """Creates a configured static-file-serving WSGI server running on HTTPS."""
    server = SecureThreadedWSGIServer((bind_host, port), SecureWSGIRequestHandler, ssl_context=ssl_context)
    # StaticFilesHandler serves /static/ assets in development identical to runserver
    server.set_app(StaticFilesHandler(WSGIHandler()))
    return server


class Command(BaseCommand):
    help = "Launches the Web Operator Dashboard & Mobile Companion with built-in self-signed HTTPS."

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
            help="HTTP port for the desktop web operator console (default: 8000).",
        )
        parser.add_argument(
            "--ssl",
            action="store_true",
            help="Prioritizes HTTPS as the primary desktop entrypoint.",
        )
        parser.add_argument(
            "--ssl-port",
            type=int,
            default=443,
            help="HTTPS port for the secure mobile scanner (default: 443).",
        )
        parser.add_argument(
            "--no-ssl",
            action="store_true",
            help="Disables the background HTTPS listener (HTTP only).",
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
        port = 443
        ssl_port = 443
        enable_ssl_primary = True
        no_ssl = False
        bind_host = options.get("host", "0.0.0.0")
        no_browser = options["no_browser"]
        noreload = options["noreload"]

        self.stdout.write(self.style.SUCCESS("=" * 68))
        self.stdout.write(self.style.SUCCESS("  ITMS VERIFICATION COPILOT - WEB & MOBILE OPERATOR CONSOLE"))
        self.stdout.write(self.style.SUCCESS("=" * 68))

        # 1. Ensure migrations and initial operator setup
        self.stdout.write("Checking database schema and migrations...")
        config_service.ensure_migrations_applied()
        config_service.ensure_operator_accounts_synced()

        db_info = config_service.get_active_database_info()
        self.stdout.write(f"Active Database: {db_info.get('vendor', 'sqlite').upper()} ({db_info.get('name', 'db.sqlite3')})")

        # 2. Network & Mobile Companion discovery
        interfaces = network_service.get_local_ipv4_addresses()
        san_ips: List[str] = [i["ip"] for i in interfaces if i.get("ip")]

        # Prepare SSL context if not disabled
        ssl_context = None
        if not no_ssl:
            try:
                ssl_context = ssl_service.get_ssl_context(san_ips=san_ips)
            except Exception as ssl_err:
                self.stdout.write(self.style.WARNING(f"[!] Warning: Could not initialize SSL context: {ssl_err}"))

        net_info = network_service.get_mobile_connection_info(
            port=port,
            ssl_port=ssl_port,
            use_https=(ssl_context is not None),
        )

        # 3. Display Connection Endpoints
        desktop_url = f"https://127.0.0.1:{ssl_port}/" if enable_ssl_primary else f"http://127.0.0.1:{port}/"
        self.stdout.write(self.style.SUCCESS(f"[>] Desktop Console:       {desktop_url}"))

        if ssl_context:
            self.stdout.write(self.style.SUCCESS(f"[+] Mobile Scanner (HTTPS): {net_info['primary_url']}"))
            self.stdout.write(self.style.NOTICE("    * Secure Context active: Live Camera & QR scanning unlocked"))
            self.stdout.write(f"    [i] Plain HTTP fallback:  {net_info['http_primary_url']}")
        else:
            self.stdout.write(self.style.NOTICE(f"[i] Mobile Companion:       {net_info['primary_url']}"))

        if net_info.get("hotspot_detected"):
            self.stdout.write(self.style.NOTICE("    [+] Windows Mobile Hotspot detected (192.168.137.1)"))

        # 4. Timer to auto-launch browser once server is listening
        is_reloader_child = os.environ.get("RUN_MAIN") == "true"
        is_noreload = noreload or ("--noreload" in sys.argv)
        should_open_browser = not no_browser and (is_reloader_child or is_noreload)

        if should_open_browser:
            def _launch_browser():
                time.sleep(1.0)
                try:
                    webbrowser.open(desktop_url)
                except Exception as exc:
                    logger.debug("Could not open browser automatically: %s", exc)

            threading.Thread(target=_launch_browser, daemon=True).start()
            self.stdout.write(self.style.NOTICE(f"Opening browser at: {desktop_url}"))
        elif not no_browser and not is_reloader_child:
            self.stdout.write(self.style.NOTICE(f"Web server starting at: {desktop_url}"))
        else:
            self.stdout.write(f"Web server ready at: {desktop_url}")

        self.stdout.write("Press Ctrl+C to stop the web server.")
        self.stdout.write(self.style.SUCCESS("-" * 68))

        # 5. Dual-mode background HTTPS listener
        # Spawn HTTPS server in daemon thread only in active worker process (to prevent reloader conflicts)
        if ssl_context and not enable_ssl_primary and (is_reloader_child or is_noreload):
            def _run_https_background():
                try:
                    https_server = create_secure_server(bind_host, ssl_port, ssl_context)
                    https_server.serve_forever()
                except OSError as os_err:
                    if getattr(os_err, "winerror", None) == 10048 or "Address already in use" in str(os_err):
                        pass
                    else:
                        logger.warning("[SSL Server] Could not bind HTTPS port %s: %s", ssl_port, os_err)
                except Exception as exc:
                    logger.warning("[SSL Server] Error in HTTPS daemon: %s", exc)

            t = threading.Thread(target=_run_https_background, daemon=True, name="ITMS-HTTPS-Listener")
            t.start()

        # Start background warehouse installation kits sync daemon (3-hour periodic sync)
        if (is_reloader_child or is_noreload) and config_service.get_setting("sync.auto_sync_enabled", True):
            try:
                from core.services import kit_provisioning_service
                kit_daemon = kit_provisioning_service.MorningKitSyncDaemon.get_instance()
                kit_daemon.start()
                self.stdout.write(self.style.NOTICE("    [✓] Background Kit Sync Daemon active (3-hour cycle, min 100 pages)"))
            except Exception as d_err:
                logger.debug("Could not start MorningKitSyncDaemon in Web: %s", d_err)

        # 6. Start Primary Server
        # Force standard local launches to automatically use runserver_plus on 443 with cert to fulfill HTTPS requirement
        cert_file, key_file = ssl_service.ensure_ssl_certificates(san_ips=san_ips)
        runserver_args = {
            "use_reloader": not is_noreload,
            "cert_path": str(cert_file),
            "key_file_path": str(key_file),
        }

        try:
            call_command("runserver_plus", f"{bind_host}:{port}", **runserver_args)
        except KeyboardInterrupt:
            self.stdout.write("\nWeb server stopped cleanly.")
            sys.exit(0)
