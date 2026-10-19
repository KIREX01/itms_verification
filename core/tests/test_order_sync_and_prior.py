"""
Unit tests for Smart ITMS Order Sync, Prior-Guided Disambiguation, and Fast-Path Operator Manual Entry.
"""
from unittest.mock import MagicMock, patch
from django.test import TestCase
from django.utils import timezone

from core.models import (
    EvidenceImage,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.matcher.prior_guided import (
    disambiguate_plate_with_orders,
    get_active_orders_cache,
)
from core.matcher.order_matcher import find_best_match, match_pair_to_order
from core.services.order_sync import OrderSyncService, SYNC_COOLDOWN_SECONDS


class PriorGuidedDisambiguationTests(TestCase):
    def setUp(self):
        # Create active test orders
        self.order1 = InstallationOrder.objects.create(
            order_number="PO-UMA123PK-01",
            registration_number="UMA123PK",
            vin="VIN1234567890",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        self.order2 = InstallationOrder.objects.create(
            order_number="PO-UMA123BA-02",
            registration_number="UMA123BA",
            vin="VIN0987654321",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        self.order3 = InstallationOrder.objects.create(
            order_number="PO-UBB456C-03",
            registration_number="UBB456C",
            vin="VIN5555555555",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )

    def test_suffix_truncation_resolution(self):
        """OCR reads 'UMA123P' (missing trailing letter 'K') -> resolves to 'UMA123PK'."""
        res = disambiguate_plate_with_orders("UMA123P", "UMA123P")
        self.assertEqual(res["resolved_plate"], "UMA123PK")
        self.assertEqual(res["order_id"], self.order1.id)
        self.assertEqual(res["match_type"], "ORDER_PRIOR")
        self.assertTrue(res["was_corrected"])
        self.assertIn("Suffix truncation completed", res["reason"])

    def test_prefix_truncation_resolution(self):
        """OCR reads '123PK' or 'MA123PK' -> resolves to 'UMA123PK'."""
        res = disambiguate_plate_with_orders("123PK", "123PK")
        self.assertEqual(res["resolved_plate"], "UMA123PK")
        self.assertEqual(res["order_id"], self.order1.id)
        self.assertEqual(res["match_type"], "ORDER_PRIOR")
        self.assertTrue(res["was_corrected"])
        self.assertIn("Prefix truncation completed", res["reason"])

    def test_confusable_character_resolution(self):
        """OCR reads 'UMA1238A' (8 instead of B) -> resolves to 'UMA123BA'."""
        res = disambiguate_plate_with_orders("UMA1238A", "UMA1238A")
        self.assertEqual(res["resolved_plate"], "UMA123BA")
        self.assertEqual(res["order_id"], self.order2.id)
        self.assertEqual(res["match_type"], "ORDER_PRIOR")
        self.assertTrue(res["was_corrected"])
        self.assertIn("confusable corrected", res["reason"])

    def test_exact_match(self):
        """OCR reads 'UBB456C' -> exact match without correction."""
        res = disambiguate_plate_with_orders("UBB456C", "UBB456C")
        self.assertEqual(res["resolved_plate"], "UBB456C")
        self.assertEqual(res["order_id"], self.order3.id)
        self.assertEqual(res["match_type"], "EXACT")
        self.assertFalse(res["was_corrected"])

    def test_unregistered_rejection(self):
        """Random unrecognized plate 'UXX999ZZ' -> rejected as NONE."""
        res = disambiguate_plate_with_orders("UXX999ZZ", "UXX999ZZ")
        self.assertEqual(res["match_type"], "NONE")
        self.assertIsNone(res["order_id"])
        self.assertFalse(res["was_corrected"])

    def test_order_matcher_integrates_prior(self):
        """Test that find_best_match and match_pair_to_order use prior guidance."""
        front = EvidenceImage.objects.create(
            file_hash="hash_front_test",
            vault_file="vault/front_test.jpg",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        rear = EvidenceImage.objects.create(
            file_hash="hash_rear_test",
            vault_file="vault/rear_test.jpg",
            orientation=EvidenceImage.Orientation.REAR,
        )
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA123P",
            front_image=front,
            rear_image=rear,
            is_complete=True,
        )

        matched_pair = match_pair_to_order(pair)
        self.assertEqual(matched_pair.registration_number_detected, "UMA123PK")
        self.assertEqual(matched_pair.order, self.order1)
        self.assertEqual(matched_pair.matched_via, VehicleInstallationPair.MatchedVia.ORDER_PRIOR)
        self.assertEqual(matched_pair.verification_status, VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)


class OrderSyncServiceTests(TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.sync_svc = OrderSyncService(client=self.mock_client)

    def test_sync_active_orders_success(self):
        """Tests fetching active orders from ITMS and syncing to DB."""
        self.mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": [
                {
                    "order_number": "PO-TEST-001",
                    "registration_number": "UMA 900AA",
                    "vin": "VINTEST001",
                    "status": "Under installation",
                    "sales_order": "SO-001",
                    "warehouse": "Main Kampala",
                    "order_key": "uuid-001",
                    "action_url": "/installation-orders/installation?id=uuid-001",
                },
                {
                    "order_number": "PO-TEST-002",
                    "registration_number": "UMA 900BB",
                    "vin": "VINTEST002",
                    "status": "Ready for approve",
                    "sales_order": "SO-002",
                    "warehouse": "Main Kampala",
                    "order_key": "uuid-002",
                    "action_url": "/installation-orders/approve?id=uuid-002",
                },
            ],
            "has_next_page": False,
        }

        res = self.sync_svc.sync_active_orders(force=True)
        self.assertTrue(res["success"])
        self.assertEqual(res["total_active_seen"], 2)
        self.assertEqual(res["created"], 2)

        order1 = InstallationOrder.objects.get(order_number="PO-TEST-001")
        self.assertEqual(order1.registration_number, "UMA900AA")
        self.assertTrue(order1.is_active_on_itms)
        self.assertEqual(order1.itms_stage, "STAGE_1_INSTALLATION")

        order2 = InstallationOrder.objects.get(order_number="PO-TEST-002")
        self.assertEqual(order2.itms_stage, "STAGE_2_APPROVE")

    def test_cooldown_rate_limit(self):
        """Subsequent sync calls within cooldown window return cached result."""
        self.mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": [],
            "has_next_page": False,
        }
        # First call forces sync
        res1 = self.sync_svc.sync_active_orders(force=True)
        self.assertTrue(res1["success"])
        self.assertFalse(res1["from_cache"])

        # Second call without force should hit cooldown cache
        res2 = self.sync_svc.sync_active_orders(force=False)
        self.assertTrue(res2["success"])
        self.assertTrue(res2["from_cache"])
        self.assertIn("Cooldown active", res2["message"])

    def test_disappeared_orders_detection(self):
        """Orders that were previously active but absent in next sync are marked inactive."""
        # Create an existing active order in local DB
        existing = InstallationOrder.objects.create(
            order_number="PO-OLD-ACTIVE",
            registration_number="UMA999OLD",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
            is_archived=False,
        )

        # Mock ITMS returning a DIFFERENT order on active index (PO-OLD-ACTIVE disappeared!)
        self.mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": [
                {
                    "order_number": "PO-NEW-ACTIVE",
                    "registration_number": "UMA999NEW",
                    "vin": "VINNEW",
                    "status": "Under installation",
                    "sales_order": "SO-NEW",
                    "warehouse": "Main Kampala",
                    "order_key": "uuid-new",
                    "action_url": "/installation-orders/installation?id=uuid-new",
                }
            ],
            "has_next_page": False,
        }

        res = self.sync_svc.sync_active_orders(force=True)
        self.assertTrue(res["success"])
        self.assertEqual(res["disappeared_from_active"], 1)

        existing.refresh_from_db()
        self.assertFalse(existing.is_active_on_itms)
        self.assertTrue(existing.is_archived)
        self.assertEqual(existing.status, InstallationOrder.Status.INSTALLED)
        self.assertEqual(existing.order_status, "Installed")
        self.assertEqual(existing.itms_stage, "ARCHIVED")


class ManualFastPathEntryTests(TestCase):
    def test_manual_override_and_audit_log(self):
        """Verify manual plate typing sets pair to APPROVED, matched_via MANUAL, and creates audit log."""
        order = InstallationOrder.objects.create(
            order_number="PO-UMA835DS-030926",
            registration_number="UMA835DS",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        front = EvidenceImage.objects.create(
            file_hash="hash_front_m",
            vault_file="vault/front_m.jpg",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        rear = EvidenceImage.objects.create(
            file_hash="hash_rear_m",
            vault_file="vault/rear_m.jpg",
            orientation=EvidenceImage.Orientation.REAR,
        )
        pair = VehicleInstallationPair.objects.create(
            registration_number_detected="PAIR-unknown",
            front_image=front,
            rear_image=rear,
            is_complete=True,
        )

        # Simulate operator fast-path entry
        pair.registration_number_detected = "UMA835DS"
        pair.order = order
        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        pair.match_type = VehicleInstallationPair.MatchType.EXACT
        pair.match_score = 100.0
        pair.is_manual_override = True
        pair.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
        pair.manual_plate_override = "UMA835DS"
        pair.save()

        front.detected_plate = "UMA835DS"
        front.save()
        rear.detected_plate = "UMA835DS"
        rear.save()

        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Operator manually typed and confirmed plate 'UMA835DS' (linked to order #PO-UMA835DS-030926).",
        )

        pair.refresh_from_db()
        self.assertEqual(pair.registration_number_detected, "UMA835DS")
        self.assertEqual(pair.verification_status, VehicleInstallationPair.VerificationStatus.APPROVED)
        self.assertTrue(pair.is_manual_override)
        self.assertEqual(pair.matched_via, VehicleInstallationPair.MatchedVia.MANUAL)

        audit = pair.audit_logs.first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.action, SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN)
        self.assertEqual(audit.result, SubmissionAuditLog.ResultStatus.SUCCESS)


class AutomatedSubmissionTests(TestCase):
    def setUp(self):
        self.order = InstallationOrder.objects.create(
            order_number="PO-BATCH-001",
            registration_number="UMA888AA",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        self.front = EvidenceImage.objects.create(
            file_hash="hash_front_b",
            vault_file="vault/front_b.jpg",
            orientation=EvidenceImage.Orientation.FRONT,
        )
        self.rear = EvidenceImage.objects.create(
            file_hash="hash_rear_b",
            vault_file="vault/rear_b.jpg",
            orientation=EvidenceImage.Orientation.REAR,
        )
        self.pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA888AA",
            order=self.order,
            front_image=self.front,
            rear_image=self.rear,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            is_complete=True,
        )

    @patch("core.services.itms_web_client.ITMSWebClient.execute_installation_order_workflow")
    def test_submit_pair_dry_run_safety(self, mock_workflow):
        """Verify submit_pair passes dry_run=True by default and updates status on success."""
        from core.services.submission_worker import submit_pair
        mock_workflow.return_value = {
            "success": True,
            "order_uuid": "uuid-test-01",
            "message": "Step 1-3 Dry Run validated successfully",
            "is_finalized": True,
        }

        outcome = submit_pair(self.pair, backend="web", dry_run=True)
        self.assertTrue(outcome.success)
        mock_workflow.assert_called_once()
        _, kwargs = mock_workflow.call_args
        self.assertTrue(kwargs["dry_run"])

        self.pair.refresh_from_db()
        self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.SUBMITTED)
        self.assertIsNotNone(self.pair.submitted_at)

        audit = self.pair.audit_logs.filter(action=SubmissionAuditLog.Action.SUBMIT).first()
        self.assertIsNotNone(audit)
        self.assertIn("[DRY-RUN]", audit.message)

    @patch("core.services.itms_web_client.ITMSWebClient.execute_installation_order_workflow")
    def test_submit_approved_pairs_batch(self, mock_workflow):
        """Verify submit_approved_pairs processes all approved orders in batch and calls progress callback."""
        from core.services.submission_worker import submit_approved_pairs
        mock_workflow.return_value = {
            "success": True,
            "order_uuid": "uuid-batch",
            "message": "Batch simulated submission",
            "is_finalized": True,
        }

        # Create a second approved pair
        order2 = InstallationOrder.objects.create(
            order_number="PO-BATCH-002",
            registration_number="UMA888BB",
            status=InstallationOrder.Status.PENDING,
            is_active_on_itms=True,
        )
        pair2 = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA888BB",
            order=order2,
            front_image=self.front,
            rear_image=self.rear,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
            is_complete=True,
        )

        progress_calls = []
        def progress_cb(idx, total, p, out):
            progress_calls.append((idx, total, p.registration_number_detected, out.success))

        outcomes = submit_approved_pairs(backend="web", dry_run=True, progress_callback=progress_cb)
        self.assertEqual(len(outcomes), 2)
        self.assertTrue(all(o.success for o in outcomes))
        self.assertEqual(len(progress_calls), 2)
        called_plates = {call[2] for call in progress_calls}
        self.assertEqual(called_plates, {"UMA888AA", "UMA888BB"})


class SyncArchiveTodayTests(TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.sync_svc = OrderSyncService(client=self.mock_client)

    def test_sync_archive_today_date_boundary_termination(self):
        """
        Verifies that sync_archive_today fetches all records for today across pages,
        and halts immediately upon encountering records with a date older than target.
        """
        # Page 1: All 20 orders are from today 30.09.2026
        page1_orders = [
            {
                "order_number": f"PO-PAGE1-{i:02d}-300926",
                "registration_number": f"UMA{100+i}AA",
                "installation_date": "30.09.2026 - 16:30",
                "officer": "ENOCK MATOVU",
                "order_status": "Installed",
            }
            for i in range(1, 21)
        ]

        # Page 2: First 5 are from today 30.09.2026, remaining are from yesterday 29.09.2026
        page2_orders = [
            {
                "order_number": f"PO-PAGE2-{i:02d}-300926",
                "registration_number": f"UMA{200+i}AA",
                "installation_date": "30.09.2026 - 11:15",
                "officer": "ROLLAND MUYIIRA",
                "order_status": "Installed",
            }
            for i in range(1, 6)
        ] + [
            {
                "order_number": f"PO-PAGE2-OLD-{i:02d}-290926",
                "registration_number": f"UMA{300+i}AA",
                "installation_date": "29.09.2026 - 17:45",
                "officer": "EMMY AKENA",
                "order_status": "Installed",
            }
            for i in range(6, 21)
        ]

        def side_effect(page=1, archive=True, search_params=None):
            if page == 1:
                return {"success": True, "orders": page1_orders, "has_next_page": True}
            elif page == 2:
                return {"success": True, "orders": page2_orders, "has_next_page": True}
            else:
                return {"success": True, "orders": [], "has_next_page": False}

        self.mock_client.fetch_installation_orders.side_effect = side_effect
        self.mock_client.sync_orders_to_local_db.return_value = {
            "created": 25,
            "updated": 0,
            "installed_verified": 25,
        }

        from datetime import date
        res = self.sync_svc.sync_archive_today(target_date=date(2026, 9, 30), force=True)

        self.assertTrue(res["success"])
        # Should collect 20 from page 1 + 5 from page 2 = 25 total
        self.assertEqual(res["total_fetched"], 25)
        # Should have stopped at page 2, never calling page 3!
        self.assertEqual(res["pages_fetched"], 2)
        self.mock_client.sync_orders_to_local_db.assert_called_once()
        synced_batch = self.mock_client.sync_orders_to_local_db.call_args[0][0]
        self.assertEqual(len(synced_batch), 25)
        self.assertEqual(res["officers"]["ENOCK MATOVU"], 20)
        self.assertEqual(res["officers"]["ROLLAND MUYIIRA"], 5)


    def test_sync_archive_today_resilient_to_out_of_order_rows(self):
        """
        Verifies that sync_archive_today does NOT abort prematurely when encountering
        a single out-of-order or backdated record from yesterday interspersed in today's rows.
        """
        mixed_orders = [
            {
                "order_number": "PO-PAGE1-01-300926",
                "registration_number": "UMA101AA",
                "installation_date": "30.09.2026 - 16:30",
                "officer": "ENOCK MATOVU",
                "order_status": "Installed",
            },
            {
                "order_number": "PO-PAGE1-OUT-OF-ORDER-290926",
                "registration_number": "UMA999ZZ",
                "installation_date": "29.09.2026 - 17:00",
                "officer": "ROLLAND MUYIIRA",
                "order_status": "Installed",
            },
            {
                "order_number": "PO-PAGE1-02-300926",
                "registration_number": "UMA102AA",
                "installation_date": "30.09.2026 - 16:25",
                "officer": "ENOCK MATOVU",
                "order_status": "Installed",
            },
        ]

        self.mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": mixed_orders,
            "has_next_page": False,
        }
        self.mock_client.sync_orders_to_local_db.return_value = {
            "created": 2,
            "updated": 0,
            "installed_verified": 2,
        }

        from datetime import date
        res = self.sync_svc.sync_archive_today(target_date=date(2026, 9, 30), force=True)

        self.assertTrue(res["success"])
        # Both today's orders (01 and 02) must be collected! Not stopped at out-of-order!
        self.assertEqual(res["total_fetched"], 2)
        synced_batch = self.mock_client.sync_orders_to_local_db.call_args[0][0]
        self.assertEqual(len(synced_batch), 2)
        order_numbers = [o["order_number"] for o in synced_batch]
        self.assertIn("PO-PAGE1-01-300926", order_numbers)
        self.assertIn("PO-PAGE1-02-300926", order_numbers)
        self.assertNotIn("PO-PAGE1-OUT-OF-ORDER-290926", order_numbers)

    def test_sync_shift_scoped_accepts_target_date_suffix(self):
        """Verifies sync_shift_scoped accepts both target_date and target_date_suffix arguments without TypeError."""
        self.mock_client.fetch_installation_orders.return_value = {
            "success": True,
            "orders": [],
            "has_next_page": False,
        }
        self.mock_client.fetch_installation_kits.return_value = {
            "success": True,
            "kits": [],
            "has_next_page": False,
        }
        self.mock_client.sync_orders_to_local_db.return_value = {"created": 0, "updated": 0}
        self.mock_client.sync_kits_to_local_db.return_value = {"created": 0, "updated": 0}

        # Call with target_date_suffix
        res = self.sync_svc.sync_shift_scoped(target_date_suffix="300926", force=True)
        self.assertTrue(res["success"])
        self.assertIn("active", res)
        self.assertIn("archive", res)
        self.assertIn("kits", res)


class ShiftOrderSyncDaemonTests(TestCase):
    def test_daemon_interval_and_rate_limiting(self):
        """Verifies ShiftOrderSyncDaemon singleton, default interval, and rate-limiting cooldown."""
        from core.services.order_sync import ShiftOrderSyncDaemon
        daemon = ShiftOrderSyncDaemon.get_instance(interval_seconds=600)
        status = daemon.get_status()

        self.assertEqual(status["interval_seconds"], 600)
        self.assertEqual(status["interval_minutes"], 10.0)

        # Mock OrderSyncService within daemon
        with patch("core.services.order_sync.OrderSyncService") as MockSvc:
            mock_inst = MagicMock()
            mock_inst.sync_shift_scoped.return_value = {
                "success": True,
                "message": "Shift sync test complete.",
            }
            MockSvc.return_value = mock_inst

            # First pass with force=True
            res1 = daemon.trigger_sync(force=True)
            self.assertTrue(res1["success"])
            self.assertEqual(res1["message"], "Shift sync test complete.")

            # Second pass without force should hit cooldown
            res2 = daemon.trigger_sync(force=False)
            self.assertEqual(res2.get("status"), "cooldown_active")
            self.assertTrue(res2.get("cooldown_remaining_seconds", 0) > 0)

    def test_scheduled_cycle_multi_cadence_and_day_order_trigger(self):
        """
        Verifies:
        1. 3-minute active tick (06:00-22:00) vs 15-minute overnight tick.
        2. 10m orders, 20m archive, and 3h kits cadences.
        3. When new day's orders are detected during the 10m active orders check,
           it immediately triggers kits provisioning, archive check, and shift reconciliation.
        """
        from datetime import datetime
        from django.utils import timezone
        from core.services.order_sync import ShiftOrderSyncDaemon

        daemon = ShiftOrderSyncDaemon(interval_seconds=600)
        tz = timezone.get_current_timezone()
        day_dt = datetime(2026, 10, 20, 10, 0, 0, tzinfo=tz)
        night_dt = datetime(2026, 10, 20, 23, 30, 0, tzinfo=tz)

        self.assertEqual(daemon.get_tick_interval_seconds(day_dt), 180)
        self.assertEqual(daemon.get_tick_interval_seconds(night_dt), 900)

        # Simulate that archive and kits ran recently (5 mins ago), while orders is due (11 mins ago)
        import time
        now = time.time()
        daemon._last_orders_sync_ts = now - 660
        daemon._last_archive_sync_ts = now - 300
        daemon._last_kits_sync_ts = now - 300

        with patch("core.services.order_sync.OrderSyncService") as MockSvc, \
             patch("core.services.kit_provisioning_service.sync_and_provision_warehouse_kits") as mock_prov, \
             patch("core.services.stock_monitoring_service.reconcile_and_update_shift") as mock_rec, \
             patch("core.services.stock_monitoring_service.resolve_transferred_blocked_plates") as mock_resolve:
            mock_inst = MagicMock()
            # Simulate 4 new orders created for the day -> should trigger immediate kits & archive sync!
            mock_inst.sync_active_orders.return_value = {
                "success": True,
                "created": 4,
                "updated": 0,
                "disappeared_to_archive": 0,
                "total_active_seen": 4,
            }
            mock_inst.sync_archive_today.return_value = {"success": True, "total_fetched": 2}
            mock_inst.sync_installation_kits_scoped.return_value = {"success": True, "total_fetched": 20}
            MockSvc.return_value = mock_inst
            mock_prov.return_value = {"success": True}
            mock_rec.return_value = {"summary": {"dispatched_count": 10}}
            mock_resolve.return_value = {"resolved_count": 0, "resolved_plates": []}

            cycle_res = daemon.run_scheduled_cycle(force=False)
            self.assertTrue(cycle_res["success"])
            self.assertIn("active_orders", cycle_res["tasks_executed"])
            self.assertIn("archive_orders", cycle_res["tasks_executed"])
            self.assertIn("installation_kits", cycle_res["tasks_executed"])
            self.assertIn("shift_reconciliation", cycle_res["tasks_executed"])
            self.assertTrue(any("new_day_orders_detected" in ev for ev in cycle_res["triggered_events"]))

    def test_resolve_transferred_blocked_plates(self):
        """
        Verifies that plates recorded in StockBlockedScan ('Not on Stock') are automatically
        cleared when they subsequently appear in InstallationKit as 'New' (transferred to stock).
        """
        from core.models import InstallationKit, StockBlockedScan
        from core.services import stock_monitoring_service

        suffix = "201026"
        stock_monitoring_service.clear_blocked_plates_for_date(suffix)
        stock_monitoring_service.record_blocked_plates(["UMA901AA", "UMA902BB"], target_date_suffix=suffix)
        self.assertEqual(
            set(stock_monitoring_service.get_blocked_plates_for_date(suffix)),
            {"UMA901AA", "UMA902BB"},
        )

        # Warehouse officer transfers UMA901AA into ITMS stock -> synced into InstallationKit
        InstallationKit.objects.create(
            kit_code="IK-UMA901AA",
            registration_number="UMA901AA",
            status="New",
            warehouse="AGM SPIRO",
        )

        res = stock_monitoring_service.resolve_transferred_blocked_plates(
            target_date_suffix=suffix,
            check_itms_live=False,
        )
        self.assertEqual(res["resolved_count"], 1)
        self.assertEqual(res["resolved_plates"], ["UMA901AA"])
        self.assertEqual(res["remaining_plates"], ["UMA902BB"])
        self.assertEqual(
            stock_monitoring_service.get_blocked_plates_for_date(suffix),
            ["UMA902BB"],
        )
        self.assertFalse(
            StockBlockedScan.objects.filter(work_date_suffix=suffix, registration_number="UMA901AA").exists()
        )
        stock_monitoring_service.clear_blocked_plates_for_date(suffix)
