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
            "--installation",
            type=str,
            default=None,
            help="Fetch Step 1 installation form (/installation-orders/installation?id=...) by plate, order number, or UUID.",
        )
        parser.add_argument(
            "--validate-step1",
            action="store_true",
            help="Perform AJAX ActiveForm validation on Step 1 hardware inputs.",
        )
        parser.add_argument(
            "--submit-step1",
            action="store_true",
            help="Submit Step 1 installation form ('Save and continue') to advance to Step 2 (Photos).",
        )
        parser.add_argument(
            "--approve",
            type=str,
            default=None,
            help="Fetch Step 2 photo approval form (/installation-orders/approve?id=...) by plate, order number, or UUID.",
        )
        parser.add_argument(
            "--upload-step2",
            action="store_true",
            help="Upload evidence photos for Step 2 to advance to Step 3 (Confirmation).",
        )
        parser.add_argument(
            "--front-photo",
            type=str,
            default=None,
            help="Path to front plate evidence photo (for Step 2 photo upload).",
        )
        parser.add_argument(
            "--rear-photo",
            type=str,
            default=None,
            help="Path to rear plate evidence photo (for Step 2 photo upload).",
        )
        parser.add_argument(
            "--checklist",
            type=str,
            default=None,
            help="Path to checklist document (optional for first install/motorcycle).",
        )
        parser.add_argument(
            "--confirmation",
            type=str,
            default=None,
            help="Fetch Step 3 order confirmation / summary (/installation-orders/confirmation?id=...) by plate, order number, or UUID.",
        )
        parser.add_argument(
            "--submit-step3",
            action="store_true",
            help="Submit Step 3 confirmation to finalize and complete the installation order.",
        )
        parser.add_argument(
            "--live-commit",
            action="store_true",
            help="Explicitly authorizes live mutation requests to stock.itms.ug. If omitted, ALL submissions run in DRY RUN mode.",
        )
        parser.add_argument(
            "--workflow",
            type=str,
            default=None,
            help="Run end-to-end installation wizard (Step 1 + Step 2 + optional Step 3) for an order or plate number.",
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
            "--view-photos",
            action="store_true",
            help="Display evidence photos in side-by-side or photo viewer GUI.",
        )
        parser.add_argument(
            "--replace-front",
            type=str,
            default=None,
            help="Replacement front plate photo path (for Step 3 confirmation).",
        )
        parser.add_argument(
            "--replace-rear",
            type=str,
            default=None,
            help="Replacement rear plate photo path (for Step 3 confirmation).",
        )
        parser.add_argument(
            "--replace-checklist",
            type=str,
            default=None,
            help="Replacement checklist photo path (for Step 3 confirmation).",
        )
        parser.add_argument(
            "--detect-stage",
            type=str,
            default=None,
            help="Detect active workflow stage (Step 1, Step 2, Step 3, or Archived) for an order or plate.",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Synchronize fetched orders into the local InstallationOrder database registry.",
        )

    def handle(self, *args, **options):
        client = get_web_client()
        info_target = options.get("info")
        installation_target = options.get("installation")
        approve_target = options.get("approve")
        confirmation_target = options.get("confirmation")
        workflow_target = options.get("workflow")
        do_validate_step1 = options.get("validate_step1", False)
        do_submit_step1 = options.get("submit_step1", False)
        do_upload_step2 = options.get("upload_step2", False)
        do_submit_step3 = options.get("submit_step3", False)
        live_commit = options.get("live_commit", False)
        dry_run = not live_commit
        download_photos = options.get("download_photos", False)
        do_sync = options["sync"]

        if dry_run and (do_submit_step1 or do_upload_step2 or do_submit_step3 or workflow_target):
            self.stdout.write(
                self.style.WARNING(
                    "[DRY RUN SAFETY MODE ACTIVE] No changes will be submitted or committed to stock.itms.ug.\n"
                    "   (Pass --live-commit to authorize live submissions)."
                )
            )

        # ──────────────────────────────────────────────────────────────────────
        # Branch -1: Detect Workflow Stage (--detect-stage)
        # ──────────────────────────────────────────────────────────────────────
        detect_stage_target = options.get("detect_stage")
        if detect_stage_target:
            self.stdout.write(f"Inspecting active workflow lifecycle stage for '{detect_stage_target}'...")
            res = client.detect_order_stage(detect_stage_target)
            if not res.get("success"):
                self.stderr.write(self.style.ERROR(f"Stage detection error: {res.get('error')}"))
                return

            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"=== ITMS Order Stage Analysis: #{res.get('order_number')} ==="))
            self.stdout.write(f"  - Stage:               {res.get('stage')}")
            self.stdout.write(f"  - Step:                {res.get('step')} ({res.get('stage_name')})")
            self.stdout.write(f"  - ITMS UUID:           {res.get('order_uuid')}")
            self.stdout.write(f"  - Registration Plate:  {res.get('registration_number')}")
            self.stdout.write(f"  - VIN / Chassis No:    {res.get('vin')}")
            self.stdout.write(f"  - Order Status:        {res.get('order_status')}")
            self.stdout.write(f"  - Action URL:          {res.get('action_url')}")
            self.stdout.write(f"  - Evidence Photos:     {res.get('photos_count')} photo(s) on ITMS")
            self.stdout.write(f"  - Message:             {res.get('message')}")
            self.stdout.write("-" * 75)

            if options.get("view_photos") and res.get("photos_count"):
                client.fetch_order_info(res["order_uuid"], download_photos=True)
                from core.services import viewer
                viewer.show_order_confirmation_photos(res, block=False)
                self.stdout.write(self.style.SUCCESS("[OK] Launched photo viewer for order evidence photos."))
            return

        # ──────────────────────────────────────────────────────────────────────
        # Branch 0: End-to-End Workflow (--workflow)
        # ──────────────────────────────────────────────────────────────────────
        if workflow_target:
            self.stdout.write(f"Executing end-to-end ITMS installation wizard for '{workflow_target}' (dry_run={dry_run})...")
            front_p = options.get("front_photo")
            rear_p = options.get("rear_photo")
            check_p = options.get("checklist")

            wf_res = client.execute_installation_order_workflow(
                order_identifier=workflow_target,
                front_photo_path=front_p,
                rear_photo_path=rear_p,
                checklist_path=check_p,
                dry_run=dry_run,
                submit_step3=do_submit_step3,
            )
            if not wf_res.get("success"):
                self.stderr.write(self.style.ERROR(f"Workflow stopped: {wf_res.get('error')}"))
                if wf_res.get("details"):
                    self.stderr.write(str(wf_res["details"]))
                return

            mode_tag = "[DRY RUN] " if dry_run else ""
            self.stdout.write(self.style.SUCCESS(f"\n{mode_tag}[WORKFLOW ADVANCED] {wf_res.get('message')}"))
            self.stdout.write(f"  • Order UUID:   {wf_res.get('order_uuid')}")
            self.stdout.write(f"  • Redirect URL: {wf_res.get('redirect_url')}")
            if wf_res.get("is_finalized"):
                self.stdout.write(self.style.SUCCESS("  • Order is now FINALIZED on ITMS!"))
            elif wf_res.get("step3_ready"):
                self.stdout.write(self.style.SUCCESS("  • Order has reached Step 3: Confirmation! (Pass --submit-step3 to finalize)."))
            return

        # ──────────────────────────────────────────────────────────────────────
        # Branch 1A: Step 1 Installation Form & Validation (/installation-orders/installation)
        # ──────────────────────────────────────────────────────────────────────
        if installation_target:
            self.stdout.write(f"Querying Step 1 installation form for '{installation_target}'...")
            res = client.fetch_installation_step1(installation_target)
            if not res.get("success"):
                self.stderr.write(self.style.ERROR(f"Error fetching installation form: {res.get('error')}"))
                return

            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"=== ITMS Step 1 Installation Form: #{res.get('order_number')} ==="))
            self.stdout.write(f"  • ITMS UUID:           {res.get('order_uuid')}")
            self.stdout.write(f"  • Registration Plate:  {res.get('registration_number')}")
            self.stdout.write(f"  • VIN / Chassis No:    {res.get('vin')}")
            self.stdout.write(f"  • Old Plate:           {res.get('old_plate') or '(None)'}")
            self.stdout.write(f"  • Form Action URL:     {res.get('form_action')}")
            self.stdout.write(f"  • Validation URL:      {res.get('validation_url')}")
            self.stdout.write("-" * 75)

            # Hardware Selection
            front_p = res.get("front_plate", {})
            rear_p = res.get("rear_plate", {})
            tracker = res.get("tracker", {})

            self.stdout.write(self.style.WARNING("Configured Hardware Fitment:"))
            self.stdout.write(f"  • Front License Plate: Selected ID={front_p.get('selected_id')}, Serial={front_p.get('selected_text')}")
            self.stdout.write(f"  • Rear License Plate:  Selected ID={rear_p.get('selected_id')}, Serial={rear_p.get('selected_text')}")
            self.stdout.write(f"  • GPS Tracker:         Selected ID={tracker.get('selected_id')}, Code={tracker.get('selected_text')}")
            self.stdout.write(f"  • Front Beacon Stub:   {res.get('front_beacon')}")
            self.stdout.write(f"  • Rear Beacon Stub:    {res.get('rear_beacon')}")
            self.stdout.write(f"  • Kit UUID:            {res.get('kit_id')}")
            self.stdout.write("-" * 75)

            # Sync to local DB if requested
            if do_sync:
                sync_res = client.sync_installation_step1_to_local_db(res, order_uuid=res.get("order_uuid", ""))
                if sync_res.get("success"):
                    created_str = "Created new" if sync_res.get("created") else "Updated existing"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[SYNC COMPLETE] {created_str} InstallationOrder #{sync_res['order_number']} with Step 1 hardware fitment."
                        )
                    )

            # Validate Step 1 if requested
            if do_validate_step1 or do_submit_step1:
                self.stdout.write("\nTriggering AJAX ActiveForm validation (POST /installation-orders/validate-installation)...")
                val_res = client.validate_installation_step1(
                    order_uuid=res["order_uuid"],
                    front_plate_id=front_p.get("selected_id", ""),
                    back_plate_id=rear_p.get("selected_id", ""),
                    tracker_id=tracker.get("selected_id", ""),
                    csrf_token=res.get("csrf_token"),
                    validation_url=res.get("validation_url"),
                )
                if val_res.get("success") and val_res.get("valid"):
                    self.stdout.write(self.style.SUCCESS("✓ AJAX Form Validation PASSED (Returned [])"))
                else:
                    self.stderr.write(self.style.ERROR(f"✗ AJAX Form Validation FAILED: {val_res.get('error')}"))
                    if not do_submit_step1:
                        return

            # Submit Step 1 if requested
            if do_submit_step1:
                mode_desc = "[DRY RUN] Simulating" if dry_run else "Submitting LIVE"
                self.stdout.write(f"\n{mode_desc} Step 1 installation form ('Save and continue')...")
                sub_res = client.submit_installation_step1(
                    order_uuid=res["order_uuid"],
                    front_plate_id=front_p.get("selected_id", ""),
                    back_plate_id=rear_p.get("selected_id", ""),
                    tracker_id=tracker.get("selected_id", ""),
                    csrf_token=res.get("csrf_token"),
                    just_save=False,
                    run_validation_first=False,  # Already validated
                    dry_run=dry_run,
                )
                if sub_res.get("success"):
                    mode_tag = "[DRY RUN] " if sub_res.get("dry_run") else ""
                    self.stdout.write(self.style.SUCCESS(f"✓ {mode_tag}Step 1 SUBMITTED (HTTP {sub_res.get('status_code')})!"))
                    self.stdout.write(f"  • Redirect URL: {sub_res.get('redirect_url')}")
                    self.stdout.write(self.style.SUCCESS("  • Step 2 (Photos) is now ready to receive photo uploads."))
                else:
                    self.stderr.write(self.style.ERROR(f"✗ Step 1 Submission FAILED: {sub_res.get('error')}"))

            return

        # ──────────────────────────────────────────────────────────────────────
        # Branch 1A-2: Step 2 Photo Approval Page (--approve)
        # ──────────────────────────────────────────────────────────────────────
        if approve_target:
            self.stdout.write(f"Querying Step 2 photo approval form for '{approve_target}'...")
            res = client.fetch_approve_step2(approve_target)
            if not res.get("success"):
                self.stderr.write(self.style.ERROR(f"Error fetching approve form: {res.get('error')}"))
                return

            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"=== ITMS Step 2 Photo Approval: #{res.get('order_number')} ==="))
            self.stdout.write(f"  • ITMS UUID:           {res.get('order_uuid')}")
            self.stdout.write(f"  • Vehicle Type:        {res.get('vehicle_type')}")
            self.stdout.write(f"  • Form Action URL:     {res.get('form_action')}")
            self.stdout.write(f"  • Alert Message:       {res.get('alert_message') or '(None)'}")
            self.stdout.write(f"  • Requires Checklist:  {'Yes' if res.get('requires_checklist') else 'No (First install / Motorcycle)'}")
            self.stdout.write("-" * 75)

            if do_upload_step2:
                front_p = options.get("front_photo")
                rear_p = options.get("rear_photo")
                check_p = options.get("checklist")

                # If paths not passed, try resolving from matched pair in DB
                if not front_p or not rear_p:
                    from core.models import InstallationOrder, VehicleInstallationPair
                    order_obj = InstallationOrder.objects.filter(itms_order_uuid=res["order_uuid"]).first()
                    if order_obj:
                        pair = VehicleInstallationPair.objects.filter(order=order_obj).first()
                        if pair:
                            if not front_p and pair.front_image:
                                front_p = pair.front_image.vault_file
                            if not rear_p and pair.rear_image:
                                rear_p = pair.rear_image.vault_file

                if not front_p or not rear_p:
                    self.stderr.write(self.style.ERROR("Error: Front and rear photo files required (--front-photo, --rear-photo or matched pair in DB)."))
                    return

                mode_desc = "[DRY RUN] Simulating upload of" if dry_run else "Uploading LIVE"
                self.stdout.write(f"{mode_desc} Step 2 evidence photos for Order #{res.get('order_number')}...")
                self.stdout.write(f"  • Front Photo: {front_p}")
                self.stdout.write(f"  • Rear Photo:  {rear_p}")
                if check_p:
                    self.stdout.write(f"  • Checklist:   {check_p}")

                up_res = client.upload_installation_step2_photos(
                    order_uuid=res["order_uuid"],
                    front_photo_path=front_p,
                    rear_photo_path=rear_p,
                    checklist_path=check_p,
                    csrf_token=res.get("csrf_token"),
                    vehicle_type=res.get("vehicle_type", "M"),
                    dry_run=dry_run,
                )
                if up_res.get("success"):
                    mode_tag = "[DRY RUN] " if up_res.get("dry_run") else ""
                    self.stdout.write(self.style.SUCCESS(f"✓ {mode_tag}Step 2 Photos PROCESSED (HTTP {up_res.get('status_code')})!"))
                    self.stdout.write(f"  • Redirect URL: {up_res.get('redirect_url')}")
                    self.stdout.write(self.style.SUCCESS("  • Order has advanced to Step 3: Confirmation!"))
                else:
                    self.stderr.write(self.style.ERROR(f"✗ Step 2 Upload FAILED: {up_res.get('error')}"))
            return

        # ──────────────────────────────────────────────────────────────────────
        # Branch 1A-3: Step 3 Confirmation & Summary Page (--confirmation)
        # ──────────────────────────────────────────────────────────────────────
        if confirmation_target:
            self.stdout.write(f"Querying Step 3 confirmation summary for '{confirmation_target}'...")
            res = client.fetch_confirmation_step3(confirmation_target, download_photos=download_photos or options.get("view_photos", False))
            if not res.get("success"):
                self.stderr.write(self.style.ERROR(f"Error fetching confirmation page: {res.get('error')}"))
                return

            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS(f"=== ITMS Step 3 Confirmation Summary: #{res.get('order_number')} ==="))
            self.stdout.write(f"  - ITMS UUID:           {res.get('order_uuid')}")
            self.stdout.write(f"  - Service Type:        {res.get('service_type')}")
            self.stdout.write(f"  - Registration Plate:  {res.get('registration_number')}")
            self.stdout.write(f"  - VIN / Chassis No:    {res.get('vin')}")
            self.stdout.write(f"  - Old Plate:           {res.get('old_plate') or '(None)'}")
            self.stdout.write(f"  - Front Plate Serial:  {res.get('front_plate')}")
            self.stdout.write(f"  - Rear Plate Serial:   {res.get('rear_plate')}")
            self.stdout.write(f"  - GPS Tracker ID:      {res.get('tracker')}")
            self.stdout.write(f"  - Front Beacon ID:     {res.get('front_beacon')}")
            self.stdout.write(f"  - Rear Beacon ID:      {res.get('rear_beacon')}")
            if res.get("synthesized_from_info"):
                self.stdout.write(self.style.WARNING("  - Source:              Synthesized from existing uploaded photos."))
            self.stdout.write("-" * 75)

            # Photos
            photo_cards = res.get("photo_cards", [])
            self.stdout.write(self.style.WARNING(f"Uploaded Evidence Photo Cards ({len(photo_cards)}):"))
            for p in photo_cards:
                local_hint = f" [Saved: {p.get('local_path')}]" if p.get("local_path") else ""
                self.stdout.write(f"  - {p.get('label'):24}: File={p.get('filename') or '(None)'} | URL={p.get('img_src') or '(None)'}{local_hint}")
            self.stdout.write("-" * 75)

            # Launch photo viewer if --view-photos requested
            if options.get("view_photos"):
                from core.services import viewer
                viewer_res = viewer.show_order_confirmation_photos(res, block=False)
                if viewer_res:
                    self.stdout.write(self.style.SUCCESS(f"[OK] Launched side-by-side evidence viewer for Order #{res.get('order_number')}!"))
                else:
                    self.stdout.write(self.style.WARNING("Photos not available on disk to view. Pass --download-photos --view-photos to download and view."))

            # Sync to local DB if requested
            if do_sync:
                sync_res = client.sync_confirmation_step3_to_local_db(res, order_uuid=res.get("order_uuid", ""))
                if sync_res.get("success"):
                    created_str = "Created new" if sync_res.get("created") else "Updated existing"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[SYNC COMPLETE] {created_str} InstallationOrder #{sync_res['order_number']} with Step 3 confirmation metadata."
                        )
                    )

            if do_submit_step3:
                rep_front = options.get("replace_front") or options.get("front_photo")
                rep_rear = options.get("replace_rear") or options.get("rear_photo")
                rep_check = options.get("replace_checklist") or options.get("checklist")

                if dry_run:
                    self.stdout.write(self.style.WARNING("[DRY RUN] Simulating Step 3 confirmation submission..."))
                else:
                    self.stdout.write(self.style.WARNING("Submitting LIVE Step 3 confirmation to stock.itms.ug..."))

                sub_res = client.submit_confirmation_step3(
                    order_uuid=res["order_uuid"],
                    front_photo_path=rep_front,
                    rear_photo_path=rep_rear,
                    checklist_path=rep_check,
                    csrf_token=res.get("csrf_token"),
                    dry_run=dry_run,
                )
                if sub_res.get("success"):
                    mode_tag = "[DRY RUN] " if sub_res.get("dry_run") else ""
                    self.stdout.write(self.style.SUCCESS(f"[OK] {mode_tag}Step 3 Confirmation SUCCEEDED (HTTP {sub_res.get('status_code')})!"))
                    self.stdout.write(f"  - Redirect URL: {sub_res.get('redirect_url')}")
                    if sub_res.get("has_replacements"):
                        self.stdout.write(f"  - Replaced Photos: {', '.join(sub_res.get('replacements', []))}")
                    self.stdout.write(self.style.SUCCESS(f"  - {sub_res.get('message')}"))
                else:
                    self.stderr.write(self.style.ERROR(f"[FAIL] Step 3 Confirmation FAILED: {sub_res.get('error')}"))

            return

        # ──────────────────────────────────────────────────────────────────────
        # Branch 1B: Specific Order Info & Photo Inspection (/installation-orders/info)
        # ──────────────────────────────────────────────────────────────────────
        if info_target:
            self.stdout.write(f"Querying order details and photos for '{info_target}'...")
            res = client.fetch_order_info(info_target, download_photos=download_photos or options.get("view_photos", False))
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

            # Launch photo viewer if --view-photos requested
            if options.get("view_photos") and photos:
                from core.services import viewer
                viewer_res = viewer.show_order_confirmation_photos(res, block=False)
                if viewer_res:
                    self.stdout.write(self.style.SUCCESS(f"✓ Launched side-by-side evidence viewer for Order #{res.get('order_number')}!"))
                else:
                    self.stdout.write(self.style.WARNING("Photos not available on disk to view."))

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
            from core.services.order_sync import OrderSyncService
            sync_svc = OrderSyncService(client=client)
            if not is_archive and not options.get("search"):
                sync_res = sync_svc.sync_active_orders(force=True)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n[ACTIVE SYNC COMPLETE] {sync_res['message']}"
                    )
                )
            else:
                sync_res = client.sync_orders_to_local_db(orders)
                verified_msg = f" | Verified Installed: {sync_res.get('installed_verified', 0)}" if is_archive else ""
                self.stdout.write(
                    self.style.SUCCESS(
                        f"\n[SYNC COMPLETE] Created: {sync_res['created']} new | Updated: {sync_res['updated']} existing{verified_msg} in local database."
                    )
                )
