import os
import asyncio
from django.test import TransactionTestCase

from django.contrib.auth.models import User
from core.models import EvidenceImage, IngestionBatch, InstallationOrder, VehicleInstallationPair
from core.services import auth_service
from core.tui.app import ITMSOperatorApp


class TUIAppTests(TransactionTestCase):
    def setUp(self):
        VehicleInstallationPair.objects.all().delete()
        EvidenceImage.objects.all().delete()
        InstallationOrder.objects.all().delete()
        IngestionBatch.objects.all().delete()
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        self._temp_session_file = Path(tempfile.mktemp(suffix=".json"))
        self._session_patcher = patch("core.services.auth_service.get_session_file_path", return_value=self._temp_session_file)
        self._session_patcher.start()

        self.user = User.objects.create_user(username="testoperator", password="password123")
        auth_service.save_remembered_session(self.user, remember=True)

        self.order = InstallationOrder.objects.create(
            order_number="ORD-TUI-01",
            registration_number="UMA145PD",
            plate_serial="PLT-999",
            tracker_id="TRK-888",
        )
        self.front_img = EvidenceImage.objects.create(
            file_hash="1" * 64,
            original_source_path="front.jpg",
            vault_file="vault/front.jpg",
            detected_plate="UMA145PD",
            orientation="FRONT",
            ocr_confidence=0.92,
            status=EvidenceImage.Status.MATCHED,
        )
        self.rear_img = EvidenceImage.objects.create(
            file_hash="2" * 64,
            original_source_path="rear.jpg",
            vault_file="vault/rear.jpg",
            detected_plate="UMA145PD",
            orientation="REAR",
            ocr_confidence=0.88,
            status=EvidenceImage.Status.MATCHED,
        )
        self.pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA145PD",
            order=self.order,
            front_image=self.front_img,
            rear_image=self.rear_img,
            is_complete=True,
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        )
        self.batch = IngestionBatch.objects.create(
            batch_id="BATCH-20260907-000000-tui01",
            source_type="CLI",
            source_label="TUI Test Batch",
            total_files=2,
            ingested_count=2,
        )

    def tearDown(self):
        auth_service.clear_remembered_session()
        try:
            self._session_patcher.stop()
        except Exception:
            pass
        if hasattr(self, "_temp_session_file") and self._temp_session_file.is_file():
            try:
                self._temp_session_file.unlink()
            except OSError:
                pass

    def test_tui_pilot_navigation_and_actions(self):
        async def run_pilot():
            app = ITMSOperatorApp()
            async with app.run_test() as pilot:
                # 1. Check initial Dashboard tab and navigate to Queue via '4'
                tabs = app.query_one("#tabs-content")
                self.assertEqual(tabs.active, "tab-dashboard")
                await pilot.press("4")
                self.assertEqual(tabs.active, "tab-queue")

                # Check queue table has rows
                queue_table = app.query_one("#table-queue")
                self.assertGreaterEqual(queue_table.row_count, 1)

                # 2. Test Approve action via key
                await pilot.press("a")
                self.pair.refresh_from_db()
                self.assertEqual(self.pair.verification_status, VehicleInstallationPair.VerificationStatus.APPROVED)

                # 3. Test Swap action via key
                await pilot.press("s")
                self.pair.refresh_from_db()
                self.assertEqual(self.pair.front_image, self.rear_img)
                self.assertEqual(self.pair.rear_image, self.front_img)

                # 4. Test Tab Navigation to History via '5'
                await pilot.press("5")
                self.assertEqual(tabs.active, "tab-history")

                # Test Cycle Filter in History
                initial_filter = app.current_history_filter
                await pilot.press("f")
                self.assertNotEqual(app.current_history_filter, initial_filter)

                # 5. Test Tab Navigation to Ingestion Batches via '3'
                await pilot.press("3")
                self.assertEqual(tabs.active, "tab-batches")
                batches_table = app.query_one("#table-batches")
                self.assertGreaterEqual(batches_table.row_count, 1)

                # 6. Test Tab Navigation to Stock via '6'
                await pilot.press("6")
                self.assertEqual(tabs.active, "tab-stock")
                stock_panel = app.query_one("#stock-pane")
                self.assertIsNotNone(stock_panel)
                inspector_stock = app.query_one("#inspector-stock")
                self.assertIsNotNone(inspector_stock)

                # 7. Test Tab Navigation to Reports via '7'
                await pilot.press("7")
                self.assertEqual(tabs.active, "tab-reports")

                # 8. Test Tab Navigation to Settings via '8'
                await pilot.press("8")
                self.assertEqual(tabs.active, "tab-settings")

                # 9. Test Hotkey 'k' to jump straight back to Stock
                await pilot.press("k")
                self.assertEqual(tabs.active, "tab-stock")

                # 10. Test Web Upload action via 'w' (mocked dialog to avoid blocking GUI prompt)
                from unittest.mock import patch
                with patch("core.services.file_dialog.prompt_native_photo_selection", return_value=[]), \
                     patch("webbrowser.open", return_value=True):
                    await pilot.press("w")
                    await pilot.pause(0.1)

                # 11. Test logging to Activity Log
                log_widget = app.query_one("#activity-log")
                app.log_message("Automated pilot test passed.", level="SUCCESS")

        asyncio.run(run_pilot())

    def test_action_native_ingest_executes_cleanly(self):
        """Verifies action_native_ingest completes without NameError and logs completion."""
        from unittest.mock import patch, MagicMock
        import tempfile
        from PIL import Image

        temp_dir = tempfile.mkdtemp()
        front_p = os.path.join(temp_dir, "test_front.jpg")
        rear_p = os.path.join(temp_dir, "test_rear.jpg")
        Image.new("RGB", (100, 100), color="blue").save(front_p)
        Image.new("RGB", (100, 100), color="green").save(rear_p)

        sample_items = [
            {"path": front_p, "orientation": "FRONT"},
            {"path": rear_p, "orientation": "REAR"},
        ]

        app = ITMSOperatorApp()
        app.log_message = MagicMock()
        app.notify = MagicMock()
        app.reload_data = MagicMock()
        app.call_from_thread = lambda fn, *args, **kwargs: fn(*args, **kwargs)

        with patch("core.services.file_dialog.prompt_native_photo_selection", return_value=sample_items):
            from core.tui.actions import OperatorActionsMixin
            unwrapped_fn = getattr(OperatorActionsMixin.action_native_ingest, "__wrapped__", OperatorActionsMixin.action_native_ingest)
            unwrapped_fn(app)

            app.reload_data.assert_called_once()
            logged_messages = [call[0][0] for call in app.log_message.call_args_list]
            self.assertTrue(any("complete: 2 ingested (1 Front, 1 Rear)" in m for m in logged_messages))

    def test_settings_pane_developer_mode_scroll(self):
        """Verifies that enabling developer mode expands virtual height and enables scrolling."""
        async def run_pilot():
            app = ITMSOperatorApp()
            async with app.run_test() as pilot:
                tabs = app.query_one("#tabs-content")
                await pilot.press("8")
                self.assertEqual(tabs.active, "tab-settings")

                scroll_body = app.query_one("#settings-scroll-body")
                dev_container = app.query_one("#developer-settings-container")
                dev_switch = app.query_one("#switch-dev-mode")

                # Set dev mode to False
                dev_switch.value = False
                await pilot.pause()
                off_max_scroll = scroll_body.max_scroll_y

                # Enable Developer Mode
                dev_switch.value = True
                await pilot.pause()
                on_max_scroll = scroll_body.max_scroll_y
                self.assertGreater(on_max_scroll, off_max_scroll)
                self.assertNotIn("hidden", dev_container.classes)

                # Test keyboard scrolling downwards
                scroll_body.focus()
                await pilot.pause()
                initial_y = scroll_body.scroll_y
                await pilot.press("pagedown")
                await pilot.pause()
                self.assertGreater(scroll_body.scroll_y, initial_y)

                # Test scrolling to bottom (End)
                await pilot.press("end")
                await pilot.pause()
                self.assertEqual(scroll_body.scroll_y, on_max_scroll)

                # Test scrolling to top (Home)
                await pilot.press("home")
                await pilot.pause()
                self.assertEqual(scroll_body.scroll_y, 0)

        asyncio.run(run_pilot())

    def test_daily_date_scope_and_filtering(self):
        """Verifies that the date scope filter isolates today's work from prior day carryover."""
        from datetime import timedelta
        from django.utils import timezone
        today = timezone.now()
        yesterday = today - timedelta(days=1)

        # Create yesterday batch and pair
        prior_batch = IngestionBatch.objects.create(
            batch_id="BATCH-PRIOR-TEST",
            created_at=yesterday,
            total_files=2,
            ingested_count=2,
        )
        prior_front = EvidenceImage.objects.create(
            batch=prior_batch,
            file_hash="pfront" + "0" * 58,
            original_source_path="prior_front.jpg",
            vault_file="vault/prior_front.jpg",
            detected_plate="UMA999PR",
            orientation="FRONT",
            ocr_confidence=0.9,
            ingested_at=yesterday,
        )
        prior_pair = VehicleInstallationPair.objects.create(
            registration_number_detected="UMA999PR",
            front_image=prior_front,
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
            created_at=yesterday,
        )
        # Update created_at directly in DB since auto_now_add might override
        VehicleInstallationPair.objects.filter(id=prior_pair.id).update(created_at=yesterday)

        async def run_pilot():
            app = ITMSOperatorApp()
            async with app.run_test() as pilot:
                # 1. Queue Tab - defaults to TODAY
                await pilot.press("4")
                tabs = app.query_one("#tabs-content")
                self.assertEqual(tabs.active, "tab-queue")
                self.assertEqual(app.current_queue_scope, "TODAY")

                queue_table = app.query_one("#table-queue")
                # Today pair exists (self.pair from setUp), prior_pair is excluded
                self.assertEqual(queue_table.row_count, 1)

                # 2. Cycle scope to ACTIVE_BATCH via 'D'
                await pilot.press("d")
                self.assertEqual(app.current_queue_scope, "ACTIVE_BATCH")

                # 3. Cycle scope to CARRYOVER via 'D'
                await pilot.press("d")
                self.assertEqual(app.current_queue_scope, "CARRYOVER")
                self.assertEqual(queue_table.row_count, 1)  # only prior_pair

                # 4. Cycle scope to ALL via 'D'
                await pilot.press("d")
                self.assertEqual(app.current_queue_scope, "ALL")
                self.assertEqual(queue_table.row_count, 2)  # today + prior

                # 5. Cycle back to TODAY via 'D'
                await pilot.press("d")
                self.assertEqual(app.current_queue_scope, "TODAY")
                self.assertEqual(queue_table.row_count, 1)

                # 6. Check Dashboard header includes Shift (Today)
                await pilot.press("1")
                self.assertEqual(tabs.active, "tab-dashboard")
                banner = app.query_one("#dashboard-header-banner")
                banner_text = str(getattr(banner, "content", banner.render()))
                self.assertIn("Shift (Today", banner_text)

                # 7. Check Batches Tab Scope
                await pilot.press("3")
                self.assertEqual(tabs.active, "tab-batches")
                self.assertEqual(app.current_batches_scope, "TODAY")
                await pilot.press("d")
                self.assertEqual(app.current_batches_scope, "ACTIVE_BATCH")

                # 8. Check History Tab Scope
                await pilot.press("5")
                self.assertEqual(tabs.active, "tab-history")
                self.assertEqual(app.current_history_date_scope, "TODAY")
                await pilot.press("d")
                self.assertEqual(app.current_history_date_scope, "ALL")

        asyncio.run(run_pilot())

    def test_date_select_modal_behavior(self):
        """Verifies DateSelectModal shortcuts and manual date suffix submission."""
        from unittest.mock import MagicMock
        from core.tui.dialogs import DateSelectModal

        sample_dates = [
            {"suffix": "260929", "formatted_date": "29.09.2026", "order_count": 42},
            {"suffix": "260928", "formatted_date": "28.09.2026", "order_count": 35},
        ]
        modal = DateSelectModal(current_suffix="260929", available_dates=sample_dates)
        modal.dismiss = MagicMock()

        # 1. Test typing 6-digit suffix manually
        mock_input = MagicMock()
        mock_input.value = "260928"
        modal.query_one = MagicMock(return_value=mock_input)
        modal._handle_manual_submit()
        modal.dismiss.assert_called_with("260928")

        # 2. Test ALL keyword
        mock_input.value = "ALL"
        modal._handle_manual_submit()
        modal.dismiss.assert_called_with("ALL")

    def test_stock_manager_modal_stocktake_audit_tab(self):
        """Verifies rapid USB scanner handling and audit saving in StockManagerModal Tab 7."""
        from unittest.mock import MagicMock
        from core.tui.dialogs import StockManagerModal

        modal = StockManagerModal(target_date_suffix="260929")
        modal.notify = MagicMock()

        # Test rapid scanning single plate adds to bulk text with duplicate prevention
        mock_area = MagicMock()
        mock_area.text = "UMA100PW"
        mock_single = MagicMock()
        mock_single.value = "UMA101PW"
        mock_badge = MagicMock()

        def mock_query(selector, expected_type=None):
            if "bulk" in selector:
                return mock_area
            elif "single" in selector:
                return mock_single
            elif "staged" in selector or "summary" in selector:
                return mock_badge
            return MagicMock()

        modal.query_one = mock_query

        # Simulate USB scanner Enter event
        event = MagicMock()
        event.input.id = "input-stocktake-single"
        event.input.value = "UMA101PW"
        event.value = "UMA101PW"

        modal.on_input_submitted(event)
        self.assertIn("UMA101PW", mock_area.text)

        # Test duplicate scan ignored
        modal.on_input_submitted(event)
        self.assertTrue(modal.notify.called)

    def test_stock_pane_instant_scan_and_inspector(self):
        """Verifies instant barcode scan verification and InspectorPane formatting in StockPane."""
        from unittest.mock import MagicMock, patch
        from core.models import InstallationKit, StockDispatchScan
        from core.tui.stock_pane import StockPane
        from core.tui.inspectors import InspectorPane

        # Create test stock kit
        kit = InstallationKit.objects.create(
            kit_code="IK-TEST-001",
            registration_number="UMA 888TK",
            status="New",
            warehouse="Warehouse Stock",
            gps_tracker="GPS-888-TK",
            front_tracker="BLE-F-888",
            rear_tracker="BLE-R-888",
            front_plate="FP-888",
            rear_plate="RP-888",
        )

        pane = StockPane(target_date_suffix="260929")
        pane.notify = MagicMock()
        pane._app = MagicMock()

        # Test InspectorPane.show_stock_kit_details
        inspector = InspectorPane()
        inspector.show_stock_kit_details(plate="UMA 888TK", kit=kit)
        content = str(inspector.render())
        self.assertIn("UMA 888TK", content)
        self.assertIn("IK-TEST-001", content)
        self.assertIn("GPS-888-TK", content)
        self.assertIn("BLE-F-888", content)

        # Test InspectorPane with non-stock error
        inspector.show_stock_kit_details(plate="UZZ 999ZZ", error_message="Not found on stock")
        err_content = str(inspector.render())
        self.assertIn("NOT ON STOCK", err_content)
        self.assertIn("Not found on stock", err_content)

        # Test instant scan valid plate
        mock_input = MagicMock()
        mock_input.value = "UMA 888TK"
        mock_feedback = MagicMock()
        mock_table = MagicMock()
        mock_category = MagicMock()
        mock_category.value = "PSV"

        def mock_query(selector, expected_type=None):
            if "feedback" in selector:
                return mock_feedback
            elif "table" in selector:
                return mock_table
            elif "category" in selector:
                return mock_category
            elif "inspector" in selector:
                return inspector
            return MagicMock()

        pane.query_one = mock_query

        with patch("core.services.kit_provisioning_service.verify_scanned_kit_stock", return_value={"is_valid": True, "kit": kit, "source": "local_db"}):
            pane._handle_instant_dispatch_scan("UMA 888TK", mock_input)

        # Verify dispatch record created
        dispatch = StockDispatchScan.objects.filter(registration_number="UMA888TK").first()
        self.assertIsNotNone(dispatch)
        self.assertEqual(dispatch.plate_category, "PSV")
        self.assertEqual(mock_input.value, "")  # Input cleared for next scan

        # Test instant scan of plate not on stock (e.g. UMA027QK) is strictly set aside
        from core.services.kit_provisioning_service import KitVerificationResult
        tuple_result = KitVerificationResult(False, None, "Kit UMA027QK not found on ITMS installation kits (Not on stock).")
        mock_input_unalloc = MagicMock()
        mock_input_unalloc.value = "UMA027QK"
        with patch("core.services.kit_provisioning_service.verify_scanned_kit_stock", return_value=tuple_result):
            pane._handle_instant_dispatch_scan("UMA027QK", mock_input_unalloc)

        # Verify plate is NOT dispatched, is set aside, and added to blocked list for Stock Officer
        self.assertFalse(StockDispatchScan.objects.filter(registration_number="UMA027QK").exists())
        self.assertIn("UMA027QK", pane._last_blocked_dispatch)

        # Test instant scan of ALREADY_INSTALLED plate is strictly blocked (double-fitting protection)
        already_installed_result = KitVerificationResult(False, None, "Plate UMA999ZZ is already installed in ITMS Archive!", status="ALREADY_INSTALLED")
        mock_input_installed = MagicMock()
        mock_input_installed.value = "UMA999ZZ"
        with patch("core.services.kit_provisioning_service.verify_scanned_kit_stock", return_value=already_installed_result):
            pane._handle_instant_dispatch_scan("UMA999ZZ", mock_input_installed)

        # Verify NO dispatch created for already installed plate
        self.assertFalse(StockDispatchScan.objects.filter(registration_number="UMA999ZZ").exists())

        # Test KitVerificationResult dual behavior (tuple unpacking + dict .get)
        k_res = KitVerificationResult(False, None, "Kit UMA027QK not found on ITMS installation kits (Not on stock).")
        unpacked_val, unpacked_kit, unpacked_err = k_res
        self.assertFalse(unpacked_val)
        self.assertIsNone(unpacked_kit)
        self.assertEqual(unpacked_err, "Kit UMA027QK not found on ITMS installation kits (Not on stock).")
        self.assertFalse(k_res.get("is_valid"))
        self.assertEqual(k_res.get("error"), "Kit UMA027QK not found on ITMS installation kits (Not on stock).")





