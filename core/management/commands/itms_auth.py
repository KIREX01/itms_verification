"""
ITMS Web App Authentication & Connectivity Management Command.

Use this command to:
  1. Ping https://stock.itms.ug and check server availability / SSL health.
  2. Authenticate and retrieve access & refresh tokens.
  3. Inspect active token validity and remaining TTL.
  4. Test token refresh mechanics via the refresh token.
  5. Clear stored credentials and logout.
"""
from django.core.management.base import BaseCommand

from core.services.itms_client import ITMSAuthError, ITMSConnectionError, default_client


class Command(BaseCommand):
    help = "Manage authentication and verify connectivity to the ITMS web app (https://stock.itms.ug)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--ping", action="store_true",
            help="Check network reachability, SSL certificate, and server response time.",
        )
        parser.add_argument(
            "--login", action="store_true",
            help="Authenticate with ITMS and store access + refresh tokens.",
        )
        parser.add_argument(
            "--status", action="store_true",
            help="Display stored token status, user email, and expiration time.",
        )
        parser.add_argument(
            "--refresh", action="store_true",
            help="Test refreshing the access token using the stored refresh token.",
        )
        parser.add_argument(
            "--logout", action="store_true",
            help="Clear stored access and refresh tokens from disk and memory.",
        )
        parser.add_argument(
            "--username", type=str, default=None,
            help="Username / email for ITMS login (overrides ITMS_USERNAME in .env).",
        )
        parser.add_argument(
            "--password", type=str, default=None,
            help="Password for ITMS login (overrides ITMS_PASSWORD in .env).",
        )
        parser.add_argument(
            "--endpoint", type=str, default=None,
            help="Custom login endpoint path (e.g. /api/auth/login or /site/login).",
        )

    def handle(self, *args, **options):
        client = default_client

        # If no specific action specified, default to showing status and pinging
        no_action = not any([
            options["ping"], options["login"], options["status"],
            options["refresh"], options["logout"],
        ])

        if options["ping"] or no_action:
            self.stdout.write(f"Connecting to ITMS server ({client.base_url})...")
            result = client.test_connection()
            if result.get("success"):
                self.stdout.write(
                    self.style.SUCCESS(
                        f"SUCCESS: {result['message']} (Server: {result.get('server')})"
                    )
                )
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"CONNECTION FAILED: {result.get('error', 'Unknown error')}"
                    )
                )

        if options["login"]:
            self.stdout.write(f"Authenticating against {client.base_url}...")
            try:
                tokens = client.login(
                    email=options["username"],
                    password=options["password"],
                    login_endpoint=options["endpoint"],
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"LOGIN SUCCESSFUL!\n"
                        f"  User email     : {tokens.user_email or 'N/A'}\n"
                        f"  Access token   : {tokens.access_token[:15]}... ({len(tokens.access_token)} chars)\n"
                        f"  Refresh token  : {tokens.refresh_token[:15]}... ({len(tokens.refresh_token)} chars) if tokens.refresh_token else 'None'\n"
                        f"  Token type     : {tokens.token_type}\n"
                        f"  Tokens saved to: {client.token_store.storage_path}"
                    )
                )
            except (ITMSAuthError, ITMSConnectionError) as exc:
                self.stdout.write(self.style.ERROR(f"LOGIN FAILED: {exc}"))

        if options["refresh"]:
            self.stdout.write("Refreshing access token using stored refresh token...")
            try:
                tokens = client.refresh_access_token()
                self.stdout.write(
                    self.style.SUCCESS(
                        f"TOKEN REFRESH SUCCESSFUL!\n"
                        f"  New Access Token: {tokens.access_token[:15]}...\n"
                        f"  Expires At      : {tokens.expires_at}"
                    )
                )
            except ITMSAuthError as exc:
                self.stdout.write(self.style.ERROR(f"REFRESH FAILED: {exc}"))

        if options["logout"]:
            client.token_store.clear()
            self.stdout.write(self.style.SUCCESS("Stored ITMS tokens cleared."))

        if options["status"] or no_action:
            status = client.get_auth_status()
            self.stdout.write("")
            self.stdout.write("--- ITMS Authentication Status ---")
            self.stdout.write(f"Target URL          : {client.base_url}")
            self.stdout.write(f"User email          : {status['user_email']}")
            self.stdout.write(f"Has Access Token    : {'YES' if status['has_access_token'] else 'NO'}")
            self.stdout.write(f"Access Token Valid  : {'YES' if status['access_token_valid'] else 'NO / Expired'}")
            self.stdout.write(f"Has Refresh Token   : {'YES' if status['has_refresh_token'] else 'NO'}")
            if status['expires_in_seconds'] is not None:
                self.stdout.write(f"Expires In          : {status['expires_in_seconds']} seconds")
            self.stdout.write(f"Token Storage File  : {status['token_file']}")
