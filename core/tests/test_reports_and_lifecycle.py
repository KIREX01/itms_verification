import json
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationKit,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import report_service
from core.services.plate_lifecycle_service import (
    resolve_plate_lifecycle,
    format_lifecycle_card,
)


class ReportsAndLifecycleTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.today = timezone.localdate()

        # Create batch
        self.batch = IngestionBatch.objects.create(
            batch_id="BATCH-TEST-001",
            source_type=IngestionBatch.SourceType.CLI,
            source_label="Shift Alpha",
            total_files=2,
            ingested_count=2,
        )

        # Create evidence images
        self.front_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="test_hash_front_001",
            vault_file="vault/front_001.jpg",
            orientation=EvidenceImage.Orientation.FRONT,
            detected_plate="UMA 300PW",
            status=EvidenceImage.Status.PLATE_DETECTED,
        )
        self.rear_img = EvidenceImage.objects.create(
            batch=self.batch,
            file_hash="test_hash_rear_001",
            vault_file="vault/rear_001.jpg",
            orientation=EvidenceImage.Orientation.REAR,
            detected_plate="UMA 300PW",
            status=EvidenceImage.Status.PLATE_DETECTED,
        )

        # Create pair
        self.pair = VehicleInstallationPair.objects.create(
            front_image=self.front_img,
            rear_image=self.rear_img,
            registration_number_detected="UMA 300PW",
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        )

        # Create an unallocated kit
        self.kit = InstallationKit.objects.create(
            kit_code="IK-UMA300PW",
            registration_number="UMA 300PW",
            status="New",
            front_plate="001198122",
            rear_plate="001198123",
            front_tracker="8ADC470FE5F7",
            rear_tracker="8ADC470FE4BB",
            gps_tracker="8BAE4707B340",
            sim_serial="892561000177037377",
            warehouse="AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)",
            created_date="24.09.2026",
            created_by_user="CAROL NANTEZA",
        )

        # Create an active order
        self.active_order = InstallationOrder.objects.create(
            order_number="PO-UMA696PU-260926",
            registration_number="UMA 696PU",
            vin="L6XRZS1A9T0031381",
            order_status="Ready for installation",
            itms_stage="STAGE_1_INSTALLATION",
            is_archived=False,
            warehouse_name="AGM (INSTALLATION) SOLUTIONS UGA",
        )

        # Create an archived order
        self.archive_order = InstallationOrder.objects.create(
            order_number="PO-UMA667PU-260926",
            registration_number="UMA 667PU",
            vin="L6XRZS1A9T0023328",
            order_status="Installed",
            installation_officer="ROLLAND MUYIIRA",
            installation_date="26.09.2026 - 17:27",
            is_archived=True,
            warehouse_name="AGM (INSTALLATION) SOLUTIONS UGA",
        )

    def test_report_service_totals_calculation(self):
        """Verifies report_service.get_system_totals aggregates accurate counts."""
        totals = report_service.get_system_totals(scope="ALL")
        self.assertEqual(totals["photos"]["total"], 2)
        self.assertEqual(totals["photos"]["front"], 1)
        self.assertEqual(totals["photos"]["rear"], 1)
        self.assertEqual(totals["batches"]["total"], 1)
        self.assertEqual(totals["pairs"]["total"], 1)
        self.assertEqual(totals["orders"]["total"], 2)
        self.assertEqual(totals["orders"]["active"], 1)
        self.assertEqual(totals["orders"]["archive"], 1)
        self.assertEqual(totals["kits"]["total"], 1)
        self.assertEqual(totals["kits"]["new_unallocated"], 1)
        self.assertEqual(len(totals["kits"]["warehouses"]), 1)
        self.assertEqual(totals["kits"]["warehouses"][0]["new"], 1)

    def test_api_reports_totals_endpoint(self):
        """Verifies GET /api/reports/totals/ returns valid JSON."""
        url = reverse("core:api_reports_totals")
        response = self.client.get(url, {"scope": "ALL"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        totals = data.get("totals")
        self.assertIsNotNone(totals)
        self.assertEqual(totals["kits"]["total"], 1)
        self.assertEqual(totals["kits"]["new_unallocated"], 1)

    def test_api_itms_plate_lifecycle_endpoint(self):
        """Verifies GET /api/itms/plate-lifecycle/?plate=... returns lifecycle state."""
        url = reverse("core:api_itms_plate_lifecycle")

        # 1. Unallocated Kit
        res = self.client.get(url, {"plate": "UMA 300PW", "live": "false"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        result = data.get("result")
        self.assertEqual(result["lifecycle_state"], "UNALLOCATED_KIT")
        self.assertTrue(result["is_unallocated"])
        self.assertEqual(result["kit_code"], "IK-UMA300PW")

        # 2. Active Order
        res2 = self.client.get(url, {"plate": "UMA 696PU", "live": "false"})
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["result"]["lifecycle_state"], "ALLOCATED_ORDER")
        self.assertTrue(data2["result"]["is_allocated"])

        # 3. Installed Archive
        res3 = self.client.get(url, {"plate": "UMA 667PU", "live": "false"})
        self.assertEqual(res3.status_code, 200)
        data3 = res3.json()
        self.assertEqual(data3["result"]["lifecycle_state"], "INSTALLED_ARCHIVE")
        self.assertTrue(data3["result"]["is_installed"])

    def test_api_itms_kits_endpoint(self):
        """Verifies GET /api/itms/kits/ returns kit list and filters."""
        url = reverse("core:api_itms_kits")
        res = self.client.get(url, {"q": "300PW"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("total"), 1)
        self.assertEqual(data["kits"][0]["kit_code"], "IK-UMA300PW")

    def test_plate_quick_entry_modal_with_unallocated_kit(self):
        """Verifies PlateQuickEntryModal flags unallocated kit as UNREGISTERED with warning and NO fake order."""
        from unittest.mock import MagicMock
        from core.tui.dialogs import PlateQuickEntryModal

        modal = PlateQuickEntryModal(self.pair)
        modal.notify = MagicMock()
        modal.dismiss = MagicMock()

        # Mock widgets
        mock_input = MagicMock()
        mock_input.value = "UMA 300PW"
        mock_table = MagicMock()
        mock_table.has_focus = False

        def mock_query_one(selector, *args, **kwargs):
            if selector == "#input-plate":
                return mock_input
            elif selector == "#table-active-orders":
                return mock_table
            elif selector == "#plate-lifecycle-card":
                return MagicMock()
            elif selector == "#active-orders-label":
                return MagicMock()
            return MagicMock()

        modal.query_one = mock_query_one

        from unittest.mock import patch
        with patch("core.services.plate_lifecycle_service.get_web_client") as mock_get_client:
            mock_client = MagicMock()
            mock_client.fetch_archive_orders.return_value = {"success": True, "orders": []}
            mock_client.fetch_installation_orders.return_value = {"success": True, "orders": []}
            mock_get_client.return_value = mock_client

            # Execute confirm plate
            modal.action_confirm_plate()

        # Verify pair was updated as UNREGISTERED without a fake dummy order
        self.pair.refresh_from_db()
        self.assertEqual(self.pair.registration_number_detected, "UMA300PW")
        self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.UNREGISTERED)
        self.assertIsNone(self.pair.order)
        self.assertIn("UNALLOCATED", self.pair.operator_note)
        self.assertIn("IK-UMA300PW", self.pair.operator_note)
        self.assertTrue(modal.dismiss.called)
        dismiss_arg = modal.dismiss.call_args[0][0]
        self.assertTrue(dismiss_arg.get("unallocated"))
        self.assertIsNone(dismiss_arg.get("order"))
        self.assertTrue(modal.notify.called)


    def test_extract_order_date_suffix_and_formatting(self):
        """Verifies date suffix extraction and readable formatting."""
        from core.services.report_service import (
            extract_order_date_suffix,
            format_date_suffix_readable,
            parse_target_date_suffix,
        )

        self.assertEqual(extract_order_date_suffix("PO-UMA667PU-260926"), "260926")
        self.assertEqual(extract_order_date_suffix("#PO-UMA696PU-260926"), "260926")
        self.assertEqual(extract_order_date_suffix("PO-UMA824PS-230926"), "230926")
        self.assertIsNone(extract_order_date_suffix("PO-INVALID"))
        self.assertIsNone(extract_order_date_suffix(""))

        self.assertEqual(format_date_suffix_readable("260926"), "26.09.2026")
        self.assertEqual(format_date_suffix_readable("230926"), "23.09.2026")

        self.assertEqual(parse_target_date_suffix("260926"), "260926")
        self.assertEqual(parse_target_date_suffix("26.09.2026"), "260926")
        self.assertEqual(parse_target_date_suffix("26-09-2026"), "260926")
        self.assertIsNone(parse_target_date_suffix("ALL"))

    def test_get_available_order_dates(self):
        """Verifies distinct order date discovery."""
        dates = report_service.get_available_order_dates()
        suffixes = [d["suffix"] for d in dates]
        self.assertIn("260926", suffixes)
        target = next(d for d in dates if d["suffix"] == "260926")
        self.assertEqual(target["order_count"], 2)
        self.assertEqual(target["formatted_date"], "26.09.2026")

    def test_date_driven_plate_totals_categories(self):
        """
        Verifies the 3 core date-driven categories:
        1. Category 1: Installed / In Archive
        2. Category 2: Pending Installation
        3. Category 3: Unallocated Plates (dispatched to floor without ITMS order/archive)
        """
        from core.models import StockDispatchScan, PlateCategory

        # Physically dispatch UMA 300PW to the line for this shift
        StockDispatchScan.objects.create(
            registration_number="UMA300PW",
            work_date_suffix="260926",
            plate_category=PlateCategory.PSV,
        )

        dd = report_service.get_date_driven_plate_totals(target_date="260926")

        self.assertEqual(dd["selected_date_suffix"], "260926")
        self.assertEqual(dd["selected_date_formatted"], "26.09.2026")

        # Category 1: Installed / Archive
        inst = dd["installed_archive"]
        self.assertEqual(inst["count"], 1)
        self.assertEqual(inst["items"][0]["plate"], "UMA 667PU")
        self.assertEqual(inst["items"][0]["officer"], "ROLLAND MUYIIRA")

        # Category 2: Pending Installation
        pend = dd["pending_orders"]
        self.assertEqual(pend["count"], 1)
        self.assertEqual(pend["items"][0]["plate"], "UMA 696PU")
        self.assertEqual(pend["items"][0]["stage"], "STAGE_1_INSTALLATION")

        # Category 3: Unallocated Plates (strictly floor dispatches)
        unalloc = dd["unallocated_plates"]
        self.assertEqual(unalloc["total_count"], 1)
        unalloc_plates = [it["plate"] for it in unalloc["items"]]
        self.assertIn("UMA300PW", unalloc_plates)

    def test_warehouse_safe_room_stock_kits_not_included_in_unallocated_table(self):
        """Verifies warehouse stock kits (status='New') in safe room are NOT included in floor unallocated discrepancy table."""
        # Create a new warehouse kit in stock that is NOT dispatched
        InstallationKit.objects.create(
            kit_code="IK-UMA999NEW",
            registration_number="UMA 999NEW",
            status="New",
            warehouse="AGM Bonded Warehouse",
        )
        dd = report_service.get_date_driven_plate_totals(target_date="260926")
        unalloc = dd["unallocated_plates"]
        unalloc_plates = [it["plate"] for it in unalloc["items"]]

        # Warehouse stock kit must NOT appear in floor unallocated discrepancy table
        self.assertNotIn("UMA999NEW", unalloc_plates)

    def test_unallocated_plates_excludes_invalid_ocr_noise(self):
        """Verifies unlinked photo pairs with OCR noise that do NOT match physical kits are excluded."""
        from core.models import StockDispatchScan, PlateCategory

        # Pair with OCR misread / noise
        VehicleInstallationPair.objects.create(
            registration_number_detected="BLURRY999",
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        )

        # Scanned floor dispatch
        StockDispatchScan.objects.create(
            registration_number="UMA 555PW",
            work_date_suffix="260926",
            plate_category=PlateCategory.PSV,
        )

        dd = report_service.get_date_driven_plate_totals(target_date="260926")
        unalloc = dd["unallocated_plates"]
        unalloc_plates = [it["plate"] for it in unalloc["items"]]

        # Scanned floor dispatch must be present
        self.assertIn("UMA555PW", unalloc_plates)

        # Invalid OCR noise with no matching kit must NOT be present
        self.assertNotIn("BLURRY999", unalloc_plates)


    def test_api_reports_totals_with_date_param(self):
        """Verifies GET /api/reports/totals/?date=260926 returns date_driven payload."""
        from core.models import StockDispatchScan, PlateCategory

        StockDispatchScan.objects.create(
            registration_number="UMA888PW",
            work_date_suffix="260926",
            plate_category=PlateCategory.PSV,
        )

        url = reverse("core:api_reports_totals")
        response = self.client.get(url, {"scope": "ALL", "date": "260926"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        dd = data.get("totals", {}).get("date_driven", {})
        self.assertEqual(dd.get("selected_date_suffix"), "260926")
        self.assertEqual(dd.get("installed_archive", {}).get("count"), 1)
        self.assertEqual(dd.get("pending_orders", {}).get("count"), 1)
        self.assertGreaterEqual(dd.get("unallocated_plates", {}).get("total_count"), 1)

    def test_date_suffix_search_in_quick_entry_modal(self):
        """Verifies typing a 6-digit date suffix into quick entry modal filters orders by work date."""
        from unittest.mock import MagicMock
        from core.tui.dialogs import PlateQuickEntryModal

        modal = PlateQuickEntryModal(self.pair)
        mock_card = MagicMock()
        mock_input = MagicMock()
        mock_input.value = "260926"
        mock_table = MagicMock()

        def mock_query_one(selector, expected_type=None):
            if selector == "#plate-lifecycle-card":
                return mock_card
            elif selector == "#input-plate":
                return mock_input
            elif selector == "#table-active-orders":
                return mock_table
            return MagicMock()

        modal.query_one = mock_query_one
        modal._update_lifecycle_card("260926")

        # Confirm card updated with date search banner
        self.assertTrue(mock_card.update.called)
        card_text = mock_card.update.call_args[0][0]
        self.assertIn("WORK DATE SEARCH", card_text)
        self.assertIn("26.09.2026", card_text)

        # Confirm filtering by date suffix finds orders ending in 260926
        modal._filter_items_for_query("260926")
        order_codes = [it["code"] for it in modal._filtered_items if it.get("type") == "ORDER"]
        self.assertTrue(any("260926" in code for code in order_codes))

    def test_external_officer_order_closure_detection(self):
        """Verifies that when an active order is closed externally by another officer,
        sync detects disappearance, queries archive for officer/date, marks pair SUBMITTED,
        and creates ARCHIVE_VERIFY audit trail."""
        from unittest.mock import MagicMock
        from core.services.order_sync import OrderSyncService
        from core.models import SubmissionAuditLog

        # Create active order
        order = InstallationOrder.objects.create(
            order_number="PO-UMA999ZZ-260926",
            registration_number="UMA 999ZZ",
            order_status="Ready for installation",
            is_active_on_itms=True,
            is_archived=False,
            status=InstallationOrder.Status.PENDING,
        )
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA 999ZZ",
            order=order,
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        )

        # Mock client
        mock_client = MagicMock()
        # Active orders return self.active_order (meaning PO-UMA999ZZ-260926 disappeared from active index!)
        mock_client.fetch_installation_orders.side_effect = lambda page=1, search_params="", archive=False: (
            {
                "success": True,
                "orders": [{
                    "order_number": self.active_order.order_number,
                    "registration_number": self.active_order.registration_number,
                    "order_status": "Ready for installation",
                    "action_url": "/installation-orders/installation?id=uuid-active",
                }],
                "has_next_page": False,
            }
            if not archive else {
                "success": True,
                "orders": [{
                    "order_number": "PO-UMA999ZZ-260926",
                    "registration_number": "UMA 999ZZ",
                    "order_status": "Installed",
                    "registration_status": "Active",
                    "officer": "ROLLAND MUYIIRA",
                    "installation_date": "26.09.2026 - 17:27",
                    "is_archived": True,
                }],
                "has_next_page": False,
            }
        )

        svc = OrderSyncService(client=mock_client)
        res = svc.sync_active_orders(force=True)

        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("disappeared_from_active"), 1)

        # Confirm order was updated to INSTALLED with external officer attribution
        order.refresh_from_db()
        self.assertTrue(order.is_archived)
        self.assertFalse(order.is_active_on_itms)
        self.assertEqual(order.order_status, "Installed")
        self.assertEqual(order.installation_officer, "ROLLAND MUYIIRA")
        self.assertEqual(order.installation_date, "26.09.2026 - 17:27")

        # Confirm pair was finalized as SUBMITTED so operator cannot resubmit
        pair.refresh_from_db()
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.SUBMITTED)
        self.assertIn("ROLLAND MUYIIRA", pair.operator_note)

        # Confirm audit log
        audit = SubmissionAuditLog.objects.filter(pair=pair, action=SubmissionAuditLog.Action.ARCHIVE_VERIFY).first()
        self.assertIsNotNone(audit)
        self.assertIn("ROLLAND MUYIIRA", audit.message)

    def test_sync_archive_orders_scoped(self):
        """Verifies sync_archive_orders_scoped fetches date-bounded archive orders and syncs them."""
        from unittest.mock import MagicMock
        from core.services.order_sync import OrderSyncService

        mock_client = MagicMock()
        mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": [{
                "order_number": "PO-TEST1-260926",
                "registration_number": "UMA 111AA",
                "order_status": "Installed",
                "registration_status": "Active",
                "officer": "OFFICER TEST",
                "installation_date": "26.09.2026",
                "is_archived": True,
            }],
            "has_next_page": False,
        }
        mock_client.sync_orders_to_local_db.return_value = {"created": 1, "updated": 0}

        svc = OrderSyncService(client=mock_client)
        res = svc.sync_archive_orders_scoped(target_date="260926", force=True)

        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("total_fetched"), 1)
        mock_client.fetch_installation_orders.assert_called_with(page=1, search_params="260926", archive=True)

    def test_sync_installation_kits_scoped(self):
        """Verifies sync_installation_kits_scoped fetches bounded kit records and syncs them."""
        from unittest.mock import MagicMock
        from core.services.order_sync import OrderSyncService

        mock_client = MagicMock()
        mock_client.fetch_installation_kits.return_value = {
            "success": True,
            "kits": [{
                "kit_code": "IK-UMA777ZZ",
                "registration_number": "UMA 777ZZ",
                "status": "New",
                "warehouse": "AGM SPIRO",
            }],
            "has_next_page": False,
        }
        mock_client.sync_kits_to_local_db.return_value = {"created": 1, "updated": 0}

        svc = OrderSyncService(client=mock_client)
        res = svc.sync_installation_kits_scoped(plate_or_code="UMA 777ZZ", force=True)

        self.assertTrue(res.get("success"))
        self.assertEqual(res.get("total_fetched"), 1)
        mock_client.fetch_installation_kits.assert_called_with(page=1, search_params="UMA 777ZZ")


