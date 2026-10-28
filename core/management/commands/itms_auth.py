"""
ITMS Web App Authentication, Connectivity & Device/Email Access Control Command.

Use this command to:
  1. Ping https://stock.itms.ug and check server availability / SSL health.
  2. Authenticate and retrieve access & refresh tokens.
  3. Inspect active token validity and remaining TTL.
  4. Test token refresh mechanics via the refresh token.
  5. Clear stored credentials and logout.
  6. Manage Device (IP, CIDR, MAC, Device ID) & Email Access Control from CLI.
"""
from django.core.management.base import BaseCommand

from core.models import AuthorizedDevice, EmailAccessRequest
from core.services import device_access_service
from core.services.itms_client import ITMSAuthError, ITMSConnectionError, default_client


class Command(BaseCommand):
    help = "Manage ITMS authentication and Device/IP/MAC & Email Access Control."

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

        # Device & Email Access Control CLI Flags
        parser.add_argument(
            "--list-devices", action="store_true",
            help="List registered devices, pending email access requests, and blocked IPs/MACs.",
        )
        parser.add_argument(
            "--preapprove", type=str, default=None,
            help="Pre-approve an operator email address and generate a 6-digit activation code.",
        )
        parser.add_argument(
            "--approve", type=str, default=None,
            help="Approve a pending email address or EmailAccessRequest ID.",
        )
        parser.add_argument(
            "--block", type=str, default=None,
            help="Block an IP address, CIDR subnet, MAC address (AA:BB:CC:DD:EE:FF), or device_id.",
        )
        parser.add_argument(
            "--unblock", type=str, default=None,
            help="Unblock an IP address, CIDR subnet, MAC address, or device_id.",
        )
        parser.add_argument(
            "--mode", type=str, choices=["STANDARD", "STRICT"], default=None,
            help="Set access control mode: STANDARD (blocklist + shared devices) or STRICT (require email pre-approval).",
        )

    def _handle_security_options(self, options) -> bool:
        handled = False

        if options.get("mode"):
            mode = options["mode"].upper()
            strict = mode == "STRICT"
            device_access_service.update_security_access_config({
                "mode": mode,
                "require_email_approval": strict,
            })
            self.stdout.write(self.style.SUCCESS(f"Security access mode updated to {mode} (require_email_approval={strict})."))
            handled = True

        if options.get("preapprove"):
            email = options["preapprove"].strip().lower()
            req = device_access_service.preapprove_operator_email(email, reviewed_by="cli_admin")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Pre-approved email: {req.email}\n"
                    f"  6-Digit Activation Code: {req.activation_code}\n"
                    f"  Status                 : {req.status}"
                )
            )
            handled = True

        if options.get("approve"):
            target = options["approve"].strip()
            req = (
                EmailAccessRequest.objects.filter(pk=int(target)).first()
                if target.isdigit()
                else EmailAccessRequest.objects.filter(email__iexact=target).first()
            )
            if req:
                device_access_service.approve_access_request(req, reviewed_by="cli_admin")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Approved email access request for {req.email} (Activation Code: {req.activation_code})."
                    )
                )
            else:
                req = device_access_service.preapprove_operator_email(target, reviewed_by="cli_admin")
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Created & approved email entry for {req.email} (Activation Code: {req.activation_code})."
                    )
                )
            handled = True

        if options.get("block"):
            ok, msg = device_access_service.block_device_or_network_target(
                options["block"], reason="Blocked via itms_auth CLI", actor="cli_admin"
            )
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(style(msg))
            handled = True

        if options.get("unblock"):
            ok, msg = device_access_service.unblock_device_or_target(
                options["unblock"], actor="cli_admin"
            )
            style = self.style.SUCCESS if ok else self.style.ERROR
            self.stdout.write(style(msg))
            handled = True

        if options.get("list_devices"):
            cfg = device_access_service.get_security_access_config()
            self.stdout.write("")
            self.stdout.write("=== Device & Email Access Control Status ===")
            self.stdout.write(f"Mode                   : {cfg.get('mode')} (Strict Email Approval: {cfg.get('require_email_approval')})")
            self.stdout.write(f"Max Devices / Account  : {cfg.get('max_devices_per_account')}")
            self.stdout.write(f"Blocked IPs            : {', '.join(cfg.get('blocked_ips') or []) or 'None'}")
            self.stdout.write(f"Blocked Subnets (CIDR) : {', '.join(cfg.get('blocked_cidrs') or []) or 'None'}")
            self.stdout.write(f"Blocked MAC Addresses  : {', '.join(cfg.get('blocked_macs') or []) or 'None'}")
            self.stdout.write("")
            self.stdout.write("--- Email Access Requests ---")
            for r in EmailAccessRequest.objects.all()[:30]:
                self.stdout.write(
                    f"  [{r.id}] {r.email:<30} | Status: {r.status:<12} | Code: {r.activation_code or '-':<6} | IP: {r.ip_address or '-'} | MAC: {r.mac_address or '-'}"
                )
            self.stdout.write("")
            self.stdout.write("--- Registered & Monitored Devices ---")
            for d in AuthorizedDevice.objects.select_related("user").all()[:40]:
                uname = d.user.username if d.user else "-"
                self.stdout.write(
                    f"  [{d.status:<8}] {d.device_name:<24} | User: {uname:<14} | IP: {d.ip_address or '-':<15} | MAC: {d.mac_address or '-':<17} | Via: {d.linked_via} | ID: {d.device_id[:12]}"
                )
            handled = True

        return handled

    def handle(self, *args, **options):
        if self._handle_security_options(options):
            return

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
                ref_info = f"{tokens.refresh_token[:15]}... ({len(tokens.refresh_token)} chars)" if tokens.refresh_token else "None"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"LOGIN SUCCESSFUL!\n"
                        f"  User email     : {tokens.user_email or 'N/A'}\n"
                        f"  Access token   : {tokens.access_token[:15]}... ({len(tokens.access_token)} chars)\n"
                        f"  Refresh token  : {ref_info}\n"
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
