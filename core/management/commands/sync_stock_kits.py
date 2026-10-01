"""
Management command: sync_stock_kits

Pre-provisions, synchronizes, and marks installation kits as 'New' in warehouse
physical stock so that plates scanned during morning dispatch take-for-work
are guaranteed to exist on the stock system.

Usage:
  python manage.py sync_stock_kits --morning-prep --sync-itms
  python manage.py sync_stock_kits --plates "UMA018PX, UMA040PX"
  python manage.py sync_stock_kits --daemon --interval 600
"""
import os
import time
from pathlib import Path
from django.core.management.base import BaseCommand

from core.services import kit_provisioning_service, stock_monitoring_service


class Command(BaseCommand):
    help = (
        "Synchronizes and pre-provisions installation kits as 'New' in warehouse stock "
        "from inbound deliveries, safe stock-taking audits, and ITMS central stock portal."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--morning-prep",
            action="store_true",
            help="Run full morning shift preparation to provision all delivered, audited, and ITMS kits as 'New'.",
        )
        parser.add_argument(
            "--sync-itms",
            action="store_true",
            help="Fetch recent installation kits directly from ITMS WebApp (stock.itms.ug).",
        )
        parser.add_argument(
            "--suffix",
            type=str,
            default=None,
            help="Shift date suffix (e.g. 300926). Defaults to today's date.",
        )
        parser.add_argument(
            "--plates",
            type=str,
            default=None,
            help="Explicit list of plate numbers or path to a text/CSV file to provision as 'New'.",
        )
        parser.add_argument(
            "--daemon",
            action="store_true",
            help="Run as a continuous background daemon service.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=900,
            help="Interval in seconds between periodic background syncs in daemon mode (default: 900s).",
        )
        parser.add_argument(
            "--max-pages",
            type=int,
            default=25,
            help="Maximum number of installation kit pages to crawl on ITMS (default: 25 pages, covers all 20+ pages).",
        )

    def handle(self, *args, **options):
        morning_prep = options.get("morning_prep")
        sync_itms = options.get("sync_itms", False)
        suffix = options.get("suffix")
        plates_input = options.get("plates")
        is_daemon = options.get("daemon", False)
        interval = options.get("interval", 900)
        max_pages = options.get("max_pages", 25)

        # Parse plates if provided
        source_plates = None
        if plates_input:
            p_val = str(plates_input).strip()
            if os.path.isfile(p_val):
                p_text = Path(p_val).read_text(encoding="utf-8", errors="ignore")
                source_plates = stock_monitoring_service.parse_plate_input(p_text)
            else:
                source_plates = stock_monitoring_service.parse_plate_input(p_val)

        if is_daemon:
            self.stdout.write(self.style.SUCCESS(f"🚀 Starting MorningKitSyncDaemon in background (interval: {interval}s)..."))
            daemon = kit_provisioning_service.MorningKitSyncDaemon.get_instance(interval_seconds=interval)
            daemon.start()
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Stopping MorningKitSyncDaemon..."))
                daemon.stop()
                return

        self.stdout.write("🔄 Running Installation Kit Synchronization & Morning Provisioning...")
        res = kit_provisioning_service.sync_and_provision_warehouse_kits(
            target_date_suffix=suffix,
            source_plates=source_plates,
            sync_itms=sync_itms or morning_prep,
            max_pages=max_pages,
        )

        self.stdout.write(self.style.SUCCESS(f"✓ {res['message']}"))
        self.stdout.write(f"  • Total Candidates Processed: {res['total_candidates_processed']}")
        self.stdout.write(f"  • Newly Created Kits:         {res['kits_created']}")
        self.stdout.write(f"  • Updated to 'New':           {res['kits_updated']}")
        self.stdout.write(f"  • Ready in Stock ('New'):     {res['new_kits_ready_count']}")
        self.stdout.write(f"  • Total Warehouse 'New' Stock: {res['total_warehouse_new_stock']}")
        if res.get("itms_kits_synced"):
            self.stdout.write(f"  • Synced from ITMS WebApp:   {res['itms_kits_synced']}")

        series = res.get("series_breakdown", {})
        if series:
            breakdown_str = ", ".join(f"{k}: {v}" for k, v in list(series.items())[:6])
            self.stdout.write(f"  • Series Distribution:        {breakdown_str}")
