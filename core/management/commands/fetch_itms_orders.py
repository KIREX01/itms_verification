"""
Management command to query and synchronize installation orders from the ITMS Web Application.

Usage:
  python manage.py fetch_itms_orders
  python manage.py fetch_itms_orders --page 2
  python manage.py fetch_itms_orders --plate "UMA 835DS"
  python manage.py fetch_itms_orders --vin "LC6PCJBJ8S0052136"
  python manage.py fetch_itms_orders --sync
"""
from django.core.management.base import BaseCommand
from core.services.itms_web_client import get_web_client


class Command(BaseCommand):
    help = "Fetches and optionally syncs installation orders from ITMS WebApp (stock.itms.ug)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--page",
            type=int,
            default=1,
            help="Page number to fetch (default: 1). Results 1-20 are on page 1, 21-40 on page 2.",
        )
        parser.add_argument(
            "--plate",
            type=str,
            default=None,
            help="Filter by registration/plate number (e.g. 'UMA 835DS').",
        )
        parser.add_argument(
            "--vin",
            type=str,
            default=None,
            help="Filter by Vehicle VIN/Chassis number.",
        )
        parser.add_argument(
            "--status",
            type=str,
            default=None,
            help="Filter by status code (1: Ready for installation, 2: Under installation, 3: Ready for approve).",
        )
        parser.add_argument(
            "--query-url",
            type=str,
            default=None,
            help="Paste full ITMS search URL or query string directly (e.g. from browser address bar).",
        )
        parser.add_argument(
            "--info",
            type=str,
            default=None,
            help="Fetch full order info & photos by ITMS UUID, order number, plate, or URL.",
        )
        parser.add_argument(
            "--download-photos",
            action="store_true",
            help="Download the plate photos to the local vault when fetching order info.",
        )
        parser.add_argument(
            "--archive",
            action="store_true",
            help="Query the completed installation orders archive (/installation-orders/archive) instead of active orders.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Synchronize fetched orders into the local InstallationOrder database registry.",
        )

    def handle(self, *args, **options):
        client = get_web_client()
        info_target = options.get("info")
        download_photos = options.get("download_photos", False)
        do_sync = options["sync"]

        # ──────────────────────────────────────────────────────────────────────
        # Branch 1: Specific Order Info & Photo Inspection (/installation-orders/info)
        # ──────────────────────────────────────────────────────────────────────
        if info_target:
            self.stdout.write(f"Querying order details and photos for '{info_target}'...")
            res = client.fetch_order_info(info_target, download_photos=download_photos)
            if not res.get("success"):
                self.stderr.write(self.style.ERROR(f"Error fetching order info: {res.get('error')}"))
                return

            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"=== ITMS Order Details: #{res.get('order_number')} ==="))
            self.stdout.write(f"  • ITMS UUID:           {res.get('order_uuid')}")
            self.stdout.write(f"  • Registration Plate:  {res.get('registration_number')}")
            self.stdout.write(f"  • VIN / Chassis No:    {res.get('vin')}")
            self.stdout.write(f"  • Installed By:        {res.get('installed_by')}")
            self.stdout.write(f"  • Warehouse:           {res.get('warehouse')}")
            self.stdout.write(f"  • Info URL:            {res.get('url')}")
            self.stdout.write("-" * 75)

            # Hardware Inventory
            front_p = res.get("front_plate", {})
            rear_p = res.get("rear_plate", {})
            gps_t = res.get("gps_tracker", {})
            front_b = res.get("front_beacon", {})
            rear_b = res.get("rear_beacon", {})

            self.stdout.write(self.style.WARNING("Hardware & Fitment Inventory:"))
            self.stdout.write(f"  • Front License Plate: Type={front_p.get('type')}, Barcode/Serial={front_p.get('serial')}, Text={front_p.get('plate')}")
            self.stdout.write(f"  • Rear License Plate:  Type={rear_p.get('type')}, Barcode/Serial={rear_p.get('serial')}, Text={rear_p.get('plate')}")
            self.stdout.write(f"  • GPS Tracker:         Type={gps_t.get('type')}, Device ID={gps_t.get('device_id')}")
            self.stdout.write(f"  • Front Beacon:        Type={front_b.get('type')}, Device ID={front_b.get('device_id')}")
            self.stdout.write(f"  • Rear Beacon:         Type={rear_b.get('type')}, Device ID={rear_b.get('device_id')}")
            self.stdout.write("-" * 75)

            # Photos
            photos = res.get("photos", [])
            self.stdout.write(self.style.WARNING(f"Evidence Photos ({len(photos)} detected):"))
            for idx, p in enumerate(photos, 1):
                dl_status = f" -> Saved: {p['local_path']}" if p.get("local_path") else ""
                self.stdout.write(f"  [{idx}] {p.get('label')}: {p.get('url')}{dl_status}")

            if not photos:
                self.stdout.write("  (No photo attachments recorded in this order)")

            # Optional DB Sync
            if do_sync:
                sync_res = client.sync_order_info_to_local_db(res, order_uuid=res.get("order_uuid", ""))
                if sync_res.get("success"):
                    created_str = "Created new" if sync_res.get("created") else "Updated existing"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"\n[SYNC COMPLETE] {created_str} InstallationOrder #{sync_res['order_number']} with full hardware and photo metadata."
                        )
                    )
                else:
                    self.stderr.write(self.style.ERROR(f"Sync error: {sync_res.get('error')}"))

            return
        page = options["page"]
        plate = options.get("plate")
        vin = options.get("vin")
        status = options.get("status")
        query_url = options.get("query_url")
        is_archive = options["archive"]
        do_sync = options["sync"]

        if query_url:
            search_params = query_url.strip()
            if "/archive" in search_params:
                is_archive = True
        else:
            search_params = {}
            if plate:
                search_params["registration_number"] = plate.strip()
            if vin:
                search_params["vin"] = vin.strip()
            if status:
                search_params["status"] = status.strip()

        client = get_web_client()
        endpoint = "/installation-orders/archive" if is_archive else "/installation-orders/index"
        target_name = "Archive (Completed / Installed)" if is_archive else "Active Fitment Orders"
        self.stdout.write(f"Connecting to {client.base_url}{endpoint} [{target_name}] (Page {page})...")

        res = client.fetch_installation_orders(page=page, search_params=search_params, archive=is_archive)
        if not res.get("success"):
            self.stderr.write(self.style.ERROR(f"Error fetching orders: {res.get('error')}"))
            return

        orders = res.get("orders", [])
        self.stdout.write(self.style.SUCCESS(f"Retrieved {len(orders)} {target_name} order(s) on Page {page}:"))
        self.stdout.write("-" * 115)
        if is_archive:
            self.stdout.write(
                f"{'# (Order)':20} | {'Plate':11} | {'VIN / Chassis':18} | {'Order Status':14} | {'Reg Status':10} | {'Officer':16} | {'Date'}"
            )
        else:
            self.stdout.write(
                f"{'# (Order)':20} | {'Plate':11} | {'VIN / Chassis':18} | {'Status':18} | {'Sales Order'}"
            )
        self.stdout.write("-" * 115)

        for o in orders:
            if is_archive:
                self.stdout.write(
                    f"{o['order_number']:20} | {o['registration_number']:11} | {o['vin']:18} | {o['order_status']:14} | {o['registration_status']:10} | {o['officer']:16} | {o['installation_date']}"
                )
            else:
                self.stdout.write(
                    f"{o['order_number']:20} | {o['registration_number']:11} | {o['vin']:18} | {o['status']:18} | {o['sales_order']}"
                )

        if not orders:
            self.stdout.write("  (No orders returned matching the criteria)")

        if res.get("summary"):
            self.stdout.write(f"\nSummary: {res['summary']}")

        if do_sync and orders:
            sync_res = client.sync_orders_to_local_db(orders)
            verified_msg = f" | Verified Installed: {sync_res.get('installed_verified', 0)}" if is_archive else ""
            self.stdout.write(
                self.style.SUCCESS(
                    f"\n[SYNC COMPLETE] Created: {sync_res['created']} new | Updated: {sync_res['updated']} existing{verified_msg} in local database."
                )
            )
