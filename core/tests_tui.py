import asyncio
from django.test import TransactionTestCase

from core.models import EvidenceImage, IngestionBatch, InstallationOrder, VehicleInstallationPair
from core.tui.app import ITMSOperatorApp


class TUIAppTests(TransactionTestCase):
    def setUp(self):
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

    def test_tui_pilot_navigation_and_actions(self):
        async def run_pilot():
            app = ITMSOperatorApp()
            async with app.run_test() as pilot:
                # 1. Check initial Queue tab
                tabs = app.query_one("#tabs-content")
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

                # 4. Test Tab Navigation to History
                await pilot.press("2")
                self.assertEqual(tabs.active, "tab-history")

                # Test Cycle Filter in History
                initial_filter = app.current_history_filter
                await pilot.press("f")
                self.assertNotEqual(app.current_history_filter, initial_filter)

                # 5. Test Tab Navigation to Ingestion Batches
                await pilot.press("3")
                self.assertEqual(tabs.active, "tab-batches")
                batches_table = app.query_one("#table-batches")
                self.assertGreaterEqual(batches_table.row_count, 1)

                # 6. Test Web Upload action via 'w'
                await pilot.press("w")

                # 7. Test logging to Activity Log
                log_widget = app.query_one("#activity-log")
                app.log_message("Automated pilot test passed.", level="SUCCESS")

        asyncio.run(run_pilot())
