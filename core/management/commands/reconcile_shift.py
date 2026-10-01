import os
from pathlib import Path
from django.core.management.base import BaseCommand

from core.services import stock_monitoring_service


class Command(BaseCommand):
    help = (
        "Reconciles daily installation shift plates against ITMS active orders and completed archive, "
        "identifies unallocated plates, synchronizes local InstallationKit inventory, "
        "updates DailyStockLedger, and exports categorized CSV reports."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--suffix",
            type=str,
            default=None,
            help="6-digit date suffix (e.g. 300926). Defaults to today's date.",
        )
        parser.add_argument(
            "--morning-plates",
            type=str,
            default=None,
            help="Newline/comma separated plate numbers or path to a text file containing morning scanned plates.",
        )
        parser.add_argument(
            "--return-plates",
            type=str,
            default=None,
            help="Newline/comma separated plate numbers or path to a text file containing returned plates.",
        )
        parser.add_argument(
            "--sync-itms",
            action="store_true",
            help="Synchronize ITMS active orders and today's archive before performing reconciliation.",
        )
        parser.add_argument(
            "--operator",
            type=str,
            default="Operator",
            help="Operator name or initials for the audit log.",
        )
        parser.add_argument(
            "--notes",
            type=str,
            default="",
            help="Optional notes for this shift reconciliation.",
        )
        parser.add_argument(
            "--export-dir",
            type=str,
            default=None,
            help="Destination directory for CSV exports (defaults to 'exports/').",
        )

    def handle(self, *args, **options):
        suffix = options.get("suffix")
        morning_input = options.get("morning_plates")
        return_input = options.get("return_plates")
        sync_itms = options.get("sync_itms")
        operator_name = options.get("operator") or "Operator"
        notes = options.get("notes") or ""
        export_dir = options.get("export_dir")

        # Load from file if input points to an existing file
        if morning_input and os.path.isfile(morning_input):
            self.stdout.write(f"Reading morning plates from file: {morning_input}")
            morning_input = Path(morning_input).read_text(encoding="utf-8")

        if return_input and os.path.isfile(return_input):
            self.stdout.write(f"Reading return plates from file: {return_input}")
            return_input = Path(return_input).read_text(encoding="utf-8")

        if sync_itms:
            self.stdout.write(self.style.NOTICE("Synchronizing ITMS active orders and shift archive..."))
            try:
                from core.services.order_sync import OrderSyncService
                sync_service = OrderSyncService()
                sync_res = sync_service.sync_shift_scoped(target_date_suffix=suffix)
                act_cnt = sync_res.get("active_orders", {}).get("total_synced", 0)
                arch_cnt = sync_res.get("archive_orders", {}).get("total_synced", 0)
                self.stdout.write(self.style.SUCCESS(f"  ✓ Synced {act_cnt} active orders and {arch_cnt} archived orders from ITMS."))
            except Exception as sync_err:
                self.stdout.write(self.style.WARNING(f"  ⚠️ Warning during ITMS sync: {sync_err}"))

        self.stdout.write(self.style.NOTICE(f"Executing shift reconciliation for suffix '{suffix or 'today'}'..."))
        result = stock_monitoring_service.reconcile_and_update_shift(
            morning_plates=morning_input,
            return_plates=return_input,
            target_date_suffix=suffix,
            operator_name=operator_name,
            notes=notes,
            auto_create_kits=True,
            export_csvs=True,
            exports_dir=export_dir,
        )

        summary = result.get("summary", {})
        fmt_date = result.get("formatted_date", "")
        wh_name = result.get("warehouse_name", "")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"=================================================="))
        self.stdout.write(self.style.SUCCESS(f" SHIFT RECONCILIATION COMPLETE — {fmt_date} ({result['work_date_suffix']})"))
        self.stdout.write(self.style.SUCCESS(f" Facility: {wh_name}"))
        self.stdout.write(self.style.SUCCESS(f"=================================================="))
        self.stdout.write(f"  • Morning Counted / Dispatched : {summary.get('dispatched_count', 0):>4}")
        self.stdout.write(self.style.SUCCESS(f"  • Reconciled Installed (Archive): {summary.get('reconciled_installed_count', 0):>4}"))
        self.stdout.write(self.style.WARNING(f"  • On-Line Active (Pending Order): {summary.get('on_line_active_count', 0):>4}"))
        self.stdout.write(f"  • Returned to Safe Room Storage : {summary.get('returned_count', 0):>4}")
        self.stdout.write(self.style.ERROR(f"  • UNALLOCATED DISCREPANCY (New) : {summary.get('unallocated_count', 0):>4}"))
        self.stdout.write("--------------------------------------------------")
        self.stdout.write(f"  • InstallationKit Records Updated: {summary.get('kits_updated', 0)}")
        self.stdout.write(f"  • InstallationKit Records Created: {summary.get('kits_created', 0)}")

        unallocated_list = result.get("unallocated_plates", [])
        if unallocated_list:
            self.stdout.write("")
            self.stdout.write(self.style.ERROR(f"--- Unallocated Number Plates ({len(unallocated_list)}) ---"))
            self.stdout.write(self.style.ERROR("These plates were physically issued, but have NO order in ITMS:"))
            for idx, p in enumerate(unallocated_list, start=1):
                self.stdout.write(self.style.ERROR(f"  {idx:2d}. {p}"))

        exported_files = result.get("exported_files", {})
        if exported_files:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("--- Standard Shift CSV Exports Generated ---"))
            for key, path in exported_files.items():
                self.stdout.write(f"  • {key.upper():12} -> {path}")

        self.stdout.write(self.style.SUCCESS("=================================================="))
