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

                # 6. Test Web Upload action via 'w' (mocked dialog to avoid blocking GUI prompt)
                from unittest.mock import patch
                with patch("core.services.file_dialog.prompt_native_photo_selection", return_value=[]), \
                     patch("webbrowser.open", return_value=True):
                    await pilot.press("w")
                    await pilot.pause(0.1)

                # 7. Test logging to Activity Log
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
