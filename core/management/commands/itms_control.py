"""
ITMS Master Service Switch Management Command.

Provides instant CLI control on the server to pause or resume remote ITMS
network requests, photo uploads, and sync polling without stopping the web server (itms.service).
"""
import getpass
from typing import Any
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.services import config_service


class Command(BaseCommand):
    help = "Inspect, pause, or resume remote ITMS service interactions without stopping the web server."

    def add_arguments(self, parser):
        parser.add_argument(
            "--status",
            action="store_true",
            help="Show current ITMS Master Service status and audit metadata.",
        )
        parser.add_argument(
            "--pause",
            action="store_true",
            help="Pause remote ITMS network requests, photo uploads, and background sync.",
        )
        parser.add_argument(
            "--resume",
            action="store_true",
            help="Resume normal remote ITMS network requests, photo uploads, and sync polling.",
        )
        parser.add_argument(
            "--reason",
            type=str,
            default="",
            help="Operational reason note for the pause/resume action.",
        )
        parser.add_argument(
            "--by",
            type=str,
            default="",
            help="Operator identifier initiating the action (defaults to current system user).",
        )

    def handle(self, *args: Any, **options: Any):
        pause = options.get("pause", False)
        resume = options.get("resume", False)
        reason = options.get("reason", "").strip()
        by = options.get("by", "").strip() or getpass.getuser()

        if pause and resume:
            self.stderr.write(self.style.ERROR("Error: Cannot specify both --pause and --resume at the same time."))
            return

        if pause:
            config_service.set_itms_enabled(
                enabled=False,
                paused_by=f"CLI:{by}",
                reason=reason or "Manual pause via itms_control CLI",
            )
            self.stdout.write(self.style.WARNING("⚡ [ITMS SERVICE PAUSED]"))
            self.stdout.write("All remote ITMS requests, background syncs, and photo uploads are now suspended.")
            self.stdout.write("Local database, pairs, web UI, and verification continue running normally without interruption.\n")

        elif resume:
            config_service.set_itms_enabled(
                enabled=True,
                paused_by=f"CLI:{by}",
                reason=reason or "Manual resume via itms_control CLI",
            )
            self.stdout.write(self.style.SUCCESS("✅ [ITMS SERVICE RESUMED / ONLINE]"))
            self.stdout.write("Remote ITMS communications, order synchronization, and evidence submissions are now active.\n")

        # Always print the current status at the end
        self._print_status()

    def _print_status(self):
        cfg = config_service.load_config()
        itms_cfg = cfg.get("itms", {})
        enabled = itms_cfg.get("enabled", True)
        paused_at = itms_cfg.get("paused_at", "")
        paused_by = itms_cfg.get("paused_by", "")
        pause_reason = itms_cfg.get("pause_reason", "")

        self.stdout.write("=" * 60)
        self.stdout.write("  ITMS MASTER SERVICE SWITCH STATUS")
        self.stdout.write("=" * 60)
        if enabled:
            self.stdout.write(self.style.SUCCESS("  Status:          ONLINE / ENABLED"))
            self.stdout.write("  Remote Traffic:  Permitted (API, Sync, Uploads active)")
        else:
            self.stdout.write(self.style.WARNING("  Status:          PAUSED (Local-Only Mode)"))
            self.stdout.write("  Remote Traffic:  BLOCKED / HELD (Web server running, remote calls skipped)")
            if paused_at:
                self.stdout.write(f"  Paused At:       {paused_at}")
            if paused_by:
                self.stdout.write(f"  Paused By:       {paused_by}")
            if pause_reason:
                self.stdout.write(f"  Reason:          {pause_reason}")
        self.stdout.write("=" * 60)
