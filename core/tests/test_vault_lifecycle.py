import io
import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from django.contrib.auth.models import User
from core.models import EvidenceImage, IngestionBatch
from core.services import vault_service


class VaultLifecycleTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.media_root = Path(self.temp_dir) / "media"
        self.vault_root = self.media_root / "vault"
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.vault_root.mkdir(parents=True, exist_ok=True)
        self.client = Client()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_create_batch_and_structured_storage(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            batch = vault_service.create_ingestion_batch(
                source_type=IngestionBatch.SourceType.CLI,
                source_label="Morning Shift",
            )
            self.assertTrue(batch.batch_id.startswith("BATCH-"))
            self.assertEqual(batch.source_label, "Morning Shift")

            # Create sample image on disk
            source_file = Path(self.temp_dir) / "sample1.jpg"
            source_file.write_bytes(b"sample image content 12345")

            img, status = vault_service.ingest_from_disk(source_file, batch=batch)
            self.assertEqual(status, "INGESTED")
            self.assertIsNotNone(img)
            self.assertEqual(img.batch, batch)
            self.assertTrue(img.vault_file.startswith("vault/"))
            # Ensure path includes batch subdirectory
            self.assertIn("batch_", img.vault_file)

            # Ensure file actually exists in vault
            abs_vault_path = self.media_root / img.vault_file
            self.assertTrue(abs_vault_path.is_file())
            self.assertEqual(abs_vault_path.read_bytes(), b"sample image content 12345")

            # Check batch counters
            batch.refresh_from_db()
            self.assertEqual(batch.total_files, 1)
            self.assertEqual(batch.ingested_count, 1)
            self.assertEqual(batch.duplicate_count, 0)

    def test_cryptographic_deduplication(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            batch1 = vault_service.create_ingestion_batch(source_label="Batch 1")
            source_file = Path(self.temp_dir) / "duplicate_test.jpg"
            source_file.write_bytes(b"exact binary payload")

            # First ingest
            img1, status1 = vault_service.ingest_from_disk(source_file, batch=batch1)
            self.assertEqual(status1, "INGESTED")

            # Second ingest in a different batch (e.g. uploaded later in the day)
            batch2 = vault_service.create_ingestion_batch(source_label="Batch 2")
            img2, status2 = vault_service.ingest_from_disk(source_file, batch=batch2)
            self.assertEqual(status2, "DUPLICATE_SKIPPED")
            self.assertEqual(img1.id, img2.id)

            batch2.refresh_from_db()
            self.assertEqual(batch2.duplicate_count, 1)
            self.assertEqual(batch2.ingested_count, 0)

    def test_uploaded_file_ingestion(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            batch = vault_service.create_ingestion_batch(source_type=IngestionBatch.SourceType.WEB)
            uploaded = SimpleUploadedFile("phone_upload.jpg", b"phone image stream", content_type="image/jpeg")

            img, status = vault_service.ingest_uploaded_file(uploaded, batch=batch)
            self.assertEqual(status, "INGESTED")
            self.assertEqual(img.original_source_path, "phone_upload.jpg")
            self.assertTrue((self.media_root / img.vault_file).is_file())

    def test_prune_vault_lifecycle_prunes_old_submitted_and_retains_issues(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root, VAULT_RETENTION_DAYS=7):
            now = timezone.now()
            eight_days_ago = now - timedelta(days=8)
            two_days_ago = now - timedelta(days=2)

            # 1. Old submitted image (> 7 days ago) -> MUST BE PRUNED
            old_file = self.vault_root / "old_submitted.jpg"
            old_file.write_bytes(b"old image to prune")
            img_old_submitted = EvidenceImage.objects.create(
                file_hash="1" * 64,
                original_source_path="old.jpg",
                vault_file="vault/old_submitted.jpg",
                status=EvidenceImage.Status.SUBMITTED,
                submitted_at=eight_days_ago,
            )

            # 2. Recent submitted image (2 days ago) -> MUST BE RETAINED
            recent_file = self.vault_root / "recent_submitted.jpg"
            recent_file.write_bytes(b"recent image")
            img_recent_submitted = EvidenceImage.objects.create(
                file_hash="2" * 64,
                original_source_path="recent.jpg",
                vault_file="vault/recent_submitted.jpg",
                status=EvidenceImage.Status.SUBMITTED,
                submitted_at=two_days_ago,
            )

            # 3. Old FAILED image (10 days ago) -> MUST BE RETAINED INDEFINITELY
            failed_file = self.vault_root / "failed_image.jpg"
            failed_file.write_bytes(b"failed image with issue")
            img_failed = EvidenceImage.objects.create(
                file_hash="3" * 64,
                original_source_path="failed.jpg",
                vault_file="vault/failed_image.jpg",
                status=EvidenceImage.Status.FAILED,
                error_message="OCR unreadable",
                ingested_at=now - timedelta(days=10),
            )

            # 4. Old NEEDS_REVIEW image (10 days ago) -> MUST BE RETAINED INDEFINITELY
            review_file = self.vault_root / "review_image.jpg"
            review_file.write_bytes(b"needs review image")
            img_review = EvidenceImage.objects.create(
                file_hash="4" * 64,
                original_source_path="review.jpg",
                vault_file="vault/review_image.jpg",
                status=EvidenceImage.Status.NEEDS_REVIEW,
                ingested_at=now - timedelta(days=10),
            )

            # Run prune_vault command
            out = io.StringIO()
            call_command("prune_vault", days=7, stdout=out)
            output = out.getvalue()

            # Verify that old submitted file was deleted from disk
            self.assertFalse(old_file.exists())
            # But its database record is preserved and marked PRUNED
            img_old_submitted.refresh_from_db()
            self.assertTrue(img_old_submitted.is_file_pruned)
            self.assertEqual(img_old_submitted.status, EvidenceImage.Status.PRUNED)
            self.assertIsNotNone(img_old_submitted.pruned_at)

            # Verify recent submitted file is still on disk
            self.assertTrue(recent_file.exists())
            img_recent_submitted.refresh_from_db()
            self.assertFalse(img_recent_submitted.is_file_pruned)

            # Verify ISSUE files are strictly protected and still on disk
            self.assertTrue(failed_file.exists())
            self.assertTrue(review_file.exists())
            img_failed.refresh_from_db()
            img_review.refresh_from_db()
            self.assertFalse(img_failed.is_file_pruned)
            self.assertFalse(img_review.is_file_pruned)

            self.assertIn("Successfully pruned/cleaned 1 file(s)", output)
            self.assertIn("Retained 2 unresolved/issue images indefinitely", output)

    def test_prune_vault_dry_run(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            old_file = self.vault_root / "dry_run_test.jpg"
            old_file.write_bytes(b"dry run content")
            img = EvidenceImage.objects.create(
                file_hash="d" * 64,
                original_source_path="dry.jpg",
                vault_file="vault/dry_run_test.jpg",
                status=EvidenceImage.Status.SUBMITTED,
                submitted_at=timezone.now() - timedelta(days=10),
            )

            out = io.StringIO()
            call_command("prune_vault", dry_run=True, days=7, stdout=out)
            self.assertIn("[DRY RUN]", out.getvalue())
            self.assertTrue(old_file.exists())

            img.refresh_from_db()
            self.assertFalse(img.is_file_pruned)

    def test_web_upload_and_api(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            # Authenticate operator
            User.objects.create_user(username="vault_test_op", password="password123")
            self.client.login(username="vault_test_op", password="password123")

            # Test GET /upload/
            resp_get = self.client.get("/upload/")
            self.assertEqual(resp_get.status_code, 200)
            self.assertContains(resp_get, "ITMS Evidence Vault Ingestion")

            # Test POST /api/upload/
            img_file = SimpleUploadedFile("api_cam.jpg", b"api photo bytes", content_type="image/jpeg")
            resp_api = self.client.post("/api/upload/", {
                "photos": [img_file],
                "batch_label": "Mobile Unit Test",
            })
            self.assertEqual(resp_api.status_code, 201)
            data = resp_api.json()
            self.assertTrue(data["success"])
            self.assertEqual(data["ingested_count"], 1)
            batch_id = data["batch_id"]

            # Test GET /api/batches/<batch_id>/
            resp_batch = self.client.get(f"/api/batches/{batch_id}/")
            self.assertEqual(resp_batch.status_code, 200)
            batch_data = resp_batch.json()
            self.assertEqual(batch_data["batch_id"], batch_id)
            self.assertEqual(len(batch_data["images"]), 1)
