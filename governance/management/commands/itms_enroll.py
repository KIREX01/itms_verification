"""Enroll this install with the central CLOSE Copilot backoffice.

    python manage.py itms_enroll https://close.kirex.online 3F9A-11C2-7B0D
    python manage.py itms_enroll --status
    python manage.py itms_enroll --sync-now
"""
from django.core.management.base import BaseCommand, CommandError

from governance.services import policy_sync


class Command(BaseCommand):
    help = "Enroll this install with the central backoffice, or show / refresh its policy sync state."

    def add_arguments(self, parser):
        parser.add_argument("central_url", nargs="?", help="Central server URL, e.g. https://close.kirex.online")
        parser.add_argument("code", nargs="?", help="One-time enrollment code from the backoffice Installs page")
        parser.add_argument("--name", default="", help="Optional install name")
        parser.add_argument("--status", action="store_true", help="Show enrollment status")
        parser.add_argument("--sync-now", action="store_true", help="Check in with the central server now")

    def handle(self, *args, **opts):
        if opts["status"]:
            state = policy_sync.load_enrollment()
            if not state:
                self.stdout.write("Not enrolled.")
                return
            self.stdout.write(f"Central: {state.get('central_url')}\nInstall: {state.get('name')} ({state.get('install_id')})\n"
                              f"Policy version: {state.get('policy_version')}\nLast sync: {state.get('last_sync_ok_at')}\n"
                              f"Stale: {policy_sync.is_policy_stale()}")
            return
        if opts["sync_now"]:
            try:
                data = policy_sync.heartbeat_once()
            except policy_sync.PolicySyncError as exc:
                raise CommandError(str(exc))
            self.stdout.write(self.style.SUCCESS(f"Synced. Policy version {data.get('policy_version')}."))
            return
        if not opts["central_url"] or not opts["code"]:
            raise CommandError("Usage: itms_enroll <central_url> <code>   (or --status / --sync-now)")
        try:
            state = policy_sync.enroll_with_central(opts["central_url"], opts["code"], name=opts["name"])
        except policy_sync.PolicySyncError as exc:
            raise CommandError(str(exc))
        self.stdout.write(self.style.SUCCESS(
            f"Enrolled as '{state.get('name')}'. Accounts, roles and kill switches now come from {state['central_url']}."))
