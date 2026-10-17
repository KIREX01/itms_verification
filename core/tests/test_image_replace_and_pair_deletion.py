import shutil
import tempfile
from pathlib import Path
from PIL import Image

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import vault_service


class ImageReplaceAndPairDeletionTests(TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.media_root = Path(self.temp_dir) / "media"
        self.vault_root = self.media_root / "vault"
        self.media_root.mkdir(parents=True, exist_ok=True)
        self.vault_root.mkdir(parents=True, exist_ok=True)

        self.user = User.objects.create_user(username="testoperator", password="password123")
        self.client = Client()
        self.client.login(username="testoperator", password="password123")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_image_file(self, filename: str = "test.jpg", color="blue") -> Path:
        img_path = Path(self.temp_dir) / filename
        im = Image.new("RGB", (640, 480), color=color)
        im.save(img_path, format="JPEG")
        return img_path

    def _create_dummy_uploaded_file(self, filename: str = "upload.jpg", color="green") -> SimpleUploadedFile:
        p = self._create_dummy_image_file(filename, color=color)
        return SimpleUploadedFile(filename, p.read_bytes(), content_type="image/jpeg")

    def test_soft_delete_pair_purges_files_and_preserves_db(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            batch = vault_service.create_ingestion_batch(
                source_type=IngestionBatch.SourceType.WEB,
                source_label="Delete Test Batch",
                user=self.user,
            )

            f_path = self._create_dummy_image_file("f1.jpg", "red")
            r_path = self._create_dummy_image_file("r1.jpg", "blue")
            f_img, _ = vault_service.ingest_from_disk(f_path, batch=batch, orientation_override="FRONT")
            r_img, _ = vault_service.ingest_from_disk(r_path, batch=batch, orientation_override="REAR")

            order = InstallationOrder.objects.create(
                order_number="ORD-DEL-001",
                registration_number="UBK100D",
                status=InstallationOrder.Status.PENDING,
            )

            pair = VehicleInstallationPair.objects.create(
                registration_number_detected="UBK100D",
                order=order,
                front_image=f_img,
                rear_image=r_img,
                verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
                operator=self.user,
            )
            pair.refresh_completeness()
            pair.save()

            # Physical files must exist prior to deletion
            f_abs = vault_service.resolve_vault_path(f_img.vault_file)
            r_abs = vault_service.resolve_vault_path(r_img.vault_file)
            self.assertTrue(f_abs.exists())
            self.assertTrue(r_abs.exists())

            # Perform delete pair action
            resp = self.client.post(
                f"/api/pairs/{pair.id}/action/",
                {"action": "delete_pair", "purge_files": "true"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("success"))

            # Check pair state in database
            pair.refresh_from_db()
            self.assertTrue(pair.is_deleted)
            self.assertIsNotNone(pair.deleted_at)
            self.assertIsNone(pair.order)

            # Check linked order was released back to PENDING
            order.refresh_from_db()
            self.assertEqual(order.status, InstallationOrder.Status.PENDING)

            # Check physical files were purged from disk
            self.assertFalse(f_abs.exists())
            self.assertFalse(r_abs.exists())

            # Check EvidenceImage rows still exist with status PRUNED
            f_img.refresh_from_db()
            r_img.refresh_from_db()
            self.assertTrue(f_img.is_file_pruned)
            self.assertEqual(f_img.status, EvidenceImage.Status.PRUNED)
            self.assertTrue(r_img.is_file_pruned)
            self.assertEqual(r_img.status, EvidenceImage.Status.PRUNED)

            # Check audit log
            audit = SubmissionAuditLog.objects.filter(
                pair=pair, action=SubmissionAuditLog.Action.PAIR_DELETED
            ).first()
            self.assertIsNotNone(audit)
            self.assertEqual(audit.result, SubmissionAuditLog.ResultStatus.SUCCESS)

            # Ensure pair is excluded from api_pairs_list
            list_resp = self.client.get("/api/pairs/")
            self.assertEqual(list_resp.status_code, 200)
            pair_ids = [p["id"] for p in list_resp.json().get("pairs", [])]
            self.assertNotIn(pair.id, pair_ids)

            # Ensure pair_detail returns 404
            detail_resp = self.client.get(f"/api/pairs/{pair.id}/")
            self.assertEqual(detail_resp.status_code, 404)

            # Ensure excluded from stats
            stats_resp = self.client.get("/api/stats/")
            self.assertEqual(stats_resp.status_code, 200)
            self.assertEqual(stats_resp.json().get("total_pairs"), 0)

    def test_replace_image_via_upload(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            batch = vault_service.create_ingestion_batch(
                source_type=IngestionBatch.SourceType.WEB,
                source_label="Replace Upload Batch",
                user=self.user,
            )

            old_f_path = self._create_dummy_image_file("old_front.jpg", "yellow")
            r_path = self._create_dummy_image_file("rear.jpg", "purple")
            old_f_img, _ = vault_service.ingest_from_disk(old_f_path, batch=batch, orientation_override="FRONT")
            r_img, _ = vault_service.ingest_from_disk(r_path, batch=batch, orientation_override="REAR")

            pair = VehicleInstallationPair.objects.create(
                registration_number_detected="UBK200E",
                front_image=old_f_img,
                rear_image=r_img,
                verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
                operator=self.user,
            )
            pair.refresh_completeness()
            pair.save()

            old_f_abs = vault_service.resolve_vault_path(old_f_img.vault_file)
            self.assertTrue(old_f_abs.exists())

            # Upload replacement front photo
            new_photo = self._create_dummy_uploaded_file("new_front.jpg", "cyan")
            resp = self.client.post(
                f"/api/pairs/{pair.id}/action/",
                {
                    "action": "replace_image",
                    "side": "front",
                    "source": "upload",
                    "photo": new_photo,
                    "purge_old": "true",
                },
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("success"))

            # Refresh pair and verify front_image updated
            pair.refresh_from_db()
            self.assertNotEqual(pair.front_image.id, old_f_img.id)
            self.assertEqual(pair.front_image.orientation, EvidenceImage.Orientation.FRONT)

            # Old image should be unlinked/pruned
            self.assertFalse(old_f_abs.exists())
            old_f_img.refresh_from_db()
            self.assertTrue(old_f_img.is_file_pruned)
            self.assertEqual(old_f_img.status, EvidenceImage.Status.PRUNED)

            # Audit log created
            audit = SubmissionAuditLog.objects.filter(
                pair=pair, action=SubmissionAuditLog.Action.IMAGE_REPLACED
            ).first()
            self.assertIsNotNone(audit)

    def test_replace_image_via_batch_picker(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            batch = vault_service.create_ingestion_batch(
                source_type=IngestionBatch.SourceType.WEB,
                source_label="Batch Picker Batch",
                user=self.user,
            )

            f_path = self._create_dummy_image_file("f2.jpg", "red")
            r_path = self._create_dummy_image_file("r2.jpg", "blue")
            cand_path = self._create_dummy_image_file("candidate_r.jpg", "green")

            f_img, _ = vault_service.ingest_from_disk(f_path, batch=batch, orientation_override="FRONT")
            r_img, _ = vault_service.ingest_from_disk(r_path, batch=batch, orientation_override="REAR")
            cand_img, _ = vault_service.ingest_from_disk(cand_path, batch=batch, orientation_override="REAR")

            pair = VehicleInstallationPair.objects.create(
                registration_number_detected="UBK300F",
                front_image=f_img,
                rear_image=r_img,
                verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
                operator=self.user,
            )
            pair.refresh_completeness()
            pair.save()

            # Replace rear image with candidate without purging old
            resp = self.client.post(
                f"/api/pairs/{pair.id}/action/",
                {
                    "action": "replace_image",
                    "side": "rear",
                    "source": "batch",
                    "image_id": str(cand_img.id),
                    "purge_old": "false",
                },
            )
            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json().get("success"))

            pair.refresh_from_db()
            self.assertEqual(pair.rear_image.id, cand_img.id)

            # Old rear image is NOT pruned
            r_abs = vault_service.resolve_vault_path(r_img.vault_file)
            self.assertTrue(r_abs.exists())
            r_img.refresh_from_db()
            self.assertFalse(r_img.is_file_pruned)

    def test_api_batch_candidates(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            batch = vault_service.create_ingestion_batch(
                source_type=IngestionBatch.SourceType.WEB,
                source_label="Candidates Batch",
                user=self.user,
            )

            f_path = self._create_dummy_image_file("f3.jpg", "orange")
            unassigned_path = self._create_dummy_image_file("unassigned.jpg", "black")

            f_img, _ = vault_service.ingest_from_disk(f_path, batch=batch, orientation_override="FRONT")
            unassigned_img, _ = vault_service.ingest_from_disk(unassigned_path, batch=batch, orientation_override="REAR")

            pair = VehicleInstallationPair.objects.create(
                registration_number_detected="UBK400G",
                front_image=f_img,
                verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                operator=self.user,
            )

            # All candidates
            resp = self.client.get(f"/api/batches/{batch.batch_id}/candidates/")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("count"), 2)

            # Unassigned only
            resp_unassigned = self.client.get(f"/api/batches/{batch.batch_id}/candidates/?unassigned_only=true")
            self.assertEqual(resp_unassigned.status_code, 200)
            data_u = resp_unassigned.json()
            self.assertEqual(data_u.get("count"), 1)
            self.assertEqual(data_u.get("candidates")[0]["id"], str(unassigned_img.id))

    def test_replace_batch_image(self):
        with override_settings(MEDIA_ROOT=self.media_root, VAULT_ROOT=self.vault_root):
            batch = vault_service.create_ingestion_batch(
                source_type=IngestionBatch.SourceType.WEB,
                source_label="Direct Batch Replace",
                user=self.user,
            )

            orig_path = self._create_dummy_image_file("orig.jpg", "gray")
            orig_img, _ = vault_service.ingest_from_disk(orig_path, batch=batch, orientation_override="FRONT")

            pair = VehicleInstallationPair.objects.create(
                registration_number_detected="UBK500H",
                front_image=orig_img,
                verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                operator=self.user,
            )

            orig_abs = vault_service.resolve_vault_path(orig_img.vault_file)
            self.assertTrue(orig_abs.exists())

            new_photo = self._create_dummy_uploaded_file("replacement.jpg", "white")
            resp = self.client.post(
                f"/api/images/{orig_img.id}/replace/",
                {"photo": new_photo, "purge_old": "true"},
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data.get("success"))
            self.assertIn(pair.id, data.get("affected_pair_ids", []))

            # Pair reflects new image
            pair.refresh_from_db()
            self.assertNotEqual(pair.front_image.id, orig_img.id)

            # Old image is purged
            self.assertFalse(orig_abs.exists())
