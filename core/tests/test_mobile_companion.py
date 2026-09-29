"""
Automated unit tests for ITMS Wireless Mobile Companion, network discovery,
heartbeat telemetry, and On-Conveyor / Off-Conveyor photo ingestion.
"""
import io
import json
import pytest
from PIL import Image, ImageDraw

from django.test import Client
from django.urls import reverse

from core.models import EvidenceImage, IngestionBatch, VehicleInstallationPair
from core.services import network_service, vault_service
from core.services.device_session_service import session_manager
from core.services.photo_quality_service import assess_photo_quality


def _create_test_image_bytes(color=(255, 0, 0), text="TEST") -> bytes:
    img = Image.new("RGB", (200, 150), color=color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _create_sharp_test_image_bytes() -> bytes:
    """Creates a high-contrast pattern image with sharp edges for focus testing."""
    img = Image.new("RGB", (200, 150), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    for x in range(0, 200, 10):
        draw.line([(x, 0), (x, 150)], fill=(0, 0, 0), width=2)
    for y in range(0, 150, 10):
        draw.line([(0, y), (200, y)], fill=(0, 0, 0), width=2)
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.mark.django_db
class TestMobileCompanion:

    def setup_method(self):
        self.client = Client()
        session_manager._sessions.clear()

    def test_network_service_discovery(self):
        """Verifies local network interface discovery and mobile URL construction."""
        ips = network_service.get_local_ipv4_addresses()
        assert len(ips) > 0
        assert "ip" in ips[0]
        assert "type" in ips[0]

        info = network_service.get_mobile_connection_info(port=8080)
        assert info["success"] is True
        assert info["port"] == 8080
        assert ":8080/mobile/" in info["primary_url"]
        assert len(info["candidate_urls"]) > 0

    def test_api_mobile_ping(self):
        """Tests the low-latency heartbeat / ping endpoint."""
        url = reverse("core:api_mobile_ping")
        resp = self.client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("pong") is True
        assert data.get("status") == "online"
        assert "server_time" in data

    def test_api_network_info(self):
        """Tests the network info endpoint for QR rendering."""
        url = reverse("core:api_network_info")
        resp = self.client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "primary_url" in data
        assert "candidate_urls" in data

    def test_api_mobile_status(self):
        """Tests the active mobile session status endpoint."""
        url = reverse("core:api_mobile_status")
        resp = self.client.get(url)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("success") is True
        assert "batch_id" in data
        assert "total_photos" in data

    def test_mobile_companion_view_renders(self):
        """Verifies the mobile companion HTML view loads cleanly."""
        url = reverse("core:mobile_companion")
        resp = self.client.get(url)
        assert resp.status_code == 200
        assert "ITMS MOBILE CAPTURE" in resp.content.decode("utf-8")
        assert "btn-mode-conveyor" in resp.content.decode("utf-8")
        assert "btn-mode-uturn" in resp.content.decode("utf-8")

    def test_on_conveyor_guided_pairing_workflow(self):
        """
        Tests the 1-by-1 On-Conveyor workflow:
        Step 1: Front photo uploaded with bike_client_id.
        Step 2: Rear photo uploaded with same bike_client_id.
        Result: Complete VehicleInstallationPair created with 1 Front + 1 Rear.
        """
        bike_id = "BIKE-TEST-CONVEYOR-001"
        upload_url = reverse("core:api_mobile_upload")

        # Step 1: Upload Front Photo
        front_bytes = _create_test_image_bytes(color=(10, 50, 200))
        front_file = io.BytesIO(front_bytes)
        front_file.name = "front_test_01.jpg"

        resp1 = self.client.post(upload_url, {
            "photo": front_file,
            "mode": "conveyor",
            "orientation": "FRONT",
            "bike_client_id": bike_id,
            "sequence_number": "1",
        })
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["success"] is True
        assert data1["orientation"] == "FRONT"
        assert data1["is_paired"] is False  # Awaiting Rear

        pair1 = VehicleInstallationPair.objects.get(id=data1["pair_id"])
        assert pair1.front_image is not None
        assert pair1.rear_image is None
        assert pair1.is_complete is False
        assert pair1.matched_via == VehicleInstallationPair.MatchedVia.MOBILE_CONVEYOR

        # Step 2: Upload Rear Photo for same bike
        rear_bytes = _create_test_image_bytes(color=(10, 200, 50))
        rear_file = io.BytesIO(rear_bytes)
        rear_file.name = "rear_test_01.jpg"

        resp2 = self.client.post(upload_url, {
            "photo": rear_file,
            "mode": "conveyor",
            "orientation": "REAR",
            "bike_client_id": bike_id,
            "sequence_number": "1",
        })
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["success"] is True
        assert data2["orientation"] == "REAR"
        assert data2["is_paired"] is True  # Pair now complete!

        pair2 = VehicleInstallationPair.objects.get(id=data2["pair_id"])
        assert pair2.front_image is not None
        assert pair2.rear_image is not None
        assert pair2.is_complete is True
        assert pair2.verification_status == VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        assert "1F + 1R Complete" in pair2.operator_note

    def test_off_conveyor_uturn_upload_workflow(self):
        """Tests uploading photos in U-Turn mode into a unified mobile batch."""
        upload_url = reverse("core:api_mobile_upload")

        # Upload 2 rear photos sequentially
        for i in range(1, 3):
            b = _create_test_image_bytes(color=(100 + i * 20, 20, 20))
            f = io.BytesIO(b)
            f.name = f"uturn_rear_{i}.jpg"
            resp = self.client.post(upload_url, {
                "photo": f,
                "mode": "uturn",
                "orientation": "REAR",
                "sequence_number": str(i),
            })
            assert resp.status_code == 200
            assert resp.json()["success"] is True

        batch = vault_service.get_or_create_mobile_batch()
        assert batch.images.filter(orientation="REAR").count() >= 2

    def test_offline_retransmission_deduplication(self):
        """
        Verifies that when a mobile client resends an already-synced photo
        due to temporary network drops, the server recognizes the SHA-256
        hash and safely marks it as DUPLICATE_SKIPPED without crashing.
        """
        upload_url = reverse("core:api_mobile_upload")
        img_bytes = _create_test_image_bytes(color=(80, 80, 80))

        # First transmission
        f1 = io.BytesIO(img_bytes)
        f1.name = "test_dup.jpg"
        resp1 = self.client.post(upload_url, {
            "photo": f1,
            "mode": "conveyor",
            "orientation": "FRONT",
            "bike_client_id": "BIKE-DUP-01",
        })
        assert resp1.status_code == 200
        assert resp1.json()["status"] == "INGESTED"

        # Re-transmission of identical image
        f2 = io.BytesIO(img_bytes)
        f2.name = "test_dup.jpg"
        resp2 = self.client.post(upload_url, {
            "photo": f2,
            "mode": "conveyor",
            "orientation": "FRONT",
            "bike_client_id": "BIKE-DUP-01",
        })
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "DUPLICATE_SKIPPED"

    def test_device_session_limit_and_disconnect(self):
        """
        Verifies that maximum active smartphone companion connections are enforced:
        - 2 devices are allowed simultaneously.
        - A 3rd device receives HTTP 429 DEVICE_LIMIT_EXCEEDED.
        - Once a connected device disconnects, a new device can connect.
        """
        # Reset session manager state
        session_manager._sessions.clear()
        ping_url = reverse("core:api_mobile_ping")
        disconnect_url = reverse("core:api_mobile_disconnect")
        status_url = reverse("core:api_mobile_status")

        # 1. Device 1 connects
        resp1 = self.client.get(f"{ping_url}?device_id=dev-01&device_name=Samsung+A54&mode=conveyor")
        assert resp1.status_code == 200
        data1 = resp1.json()
        assert data1["pong"] is True
        assert data1["allowed"] is True
        assert data1["active_count"] == 1

        # 2. Device 2 connects
        resp2 = self.client.get(f"{ping_url}?device_id=dev-02&device_name=iPhone+13&mode=uturn")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["pong"] is True
        assert data2["allowed"] is True
        assert data2["active_count"] == 2

        # 3. Device 3 attempts connection -> Exceeds max (2) -> HTTP 429
        resp3 = self.client.get(f"{ping_url}?device_id=dev-03&device_name=Redmi+Note&mode=conveyor")
        assert resp3.status_code == 429
        data3 = resp3.json()
        assert data3["pong"] is False
        assert data3["allowed"] is False
        assert data3["error"] == "DEVICE_LIMIT_EXCEEDED"

        # Check status endpoint reflects 2 active devices
        status_resp = self.client.get(status_url)
        assert status_resp.status_code == 200
        status_data = status_resp.json()
        assert status_data["active_devices_count"] == 2
        assert len(status_data["active_devices"]) == 2

        # 4. Device 1 disconnects
        disc_resp = self.client.post(disconnect_url, {"device_id": "dev-01"})
        assert disc_resp.status_code == 200
        assert disc_resp.json()["success"] is True

        # 5. Device 3 can now connect successfully
        resp3_retry = self.client.get(f"{ping_url}?device_id=dev-03&device_name=Redmi+Note&mode=conveyor")
        assert resp3_retry.status_code == 200
        assert resp3_retry.json()["allowed"] is True
        assert resp3_retry.json()["active_count"] == 2

    def test_photo_quality_assessment_service(self):
        """
        Validates Laplacian blur/sharpness detection and exposure analysis:
        - Sharp grid pattern is identified as Sharp / Acceptable.
        - Flat uniform image triggers blur / defocus warning.
        - Pure black or bright white triggers underexposed/overexposed warnings.
        """
        # Test empty input
        res_empty = assess_photo_quality(b"")
        assert res_empty["is_valid"] is False
        assert res_empty["has_issues"] is True

        # Test flat blurry image (zero edges)
        flat_bytes = _create_test_image_bytes(color=(128, 128, 128))
        res_flat = assess_photo_quality(flat_bytes)
        assert res_flat["is_valid"] is True
        assert res_flat["is_blurry"] is True
        assert res_flat["quality_grade"] == "CRITICAL"
        assert len(res_flat["correction_tips"]) > 0

        # Test underexposed dark image
        dark_bytes = _create_test_image_bytes(color=(10, 10, 10))
        res_dark = assess_photo_quality(dark_bytes)
        assert res_dark["is_underexposed"] is True

        # Test overexposed bright image
        bright_bytes = _create_test_image_bytes(color=(245, 245, 245))
        res_bright = assess_photo_quality(bright_bytes)
        assert res_bright["is_overexposed"] is True

        # Test sharp high-contrast pattern image
        sharp_bytes = _create_sharp_test_image_bytes()
        res_sharp = assess_photo_quality(sharp_bytes)
        assert res_sharp["is_valid"] is True
        assert res_sharp["is_blurry"] is False
        assert res_sharp["sharpness_score"] > 60.0
        assert res_sharp["quality_grade"] in ("ACCEPTABLE", "EXCELLENT")

    def test_mobile_upload_includes_quality_report(self):
        """
        Tests that api_mobile_upload evaluates photo quality and returns
        the structured quality_report to the mobile phone.
        """
        upload_url = reverse("core:api_mobile_upload")
        sharp_bytes = _create_sharp_test_image_bytes()
        f = io.BytesIO(sharp_bytes)
        f.name = "sharp_bike_front.jpg"

        resp = self.client.post(upload_url, {
            "photo": f,
            "mode": "conveyor",
            "orientation": "FRONT",
            "bike_client_id": "BIKE-QUAL-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert "quality_report" in data
        assert data["quality_report"]["is_blurry"] is False
        assert data["quality_report"]["sharpness_score"] > 60.0

    def test_batch_capacity_limit_and_sequential_rollover(self):
        """
        Verifies that mobile photo ingestion is limited to 200 photos per batch
        (100 motorcycles: 100 Front + 100 Rear) and automatically creates a new
        sequential batch (Batch #2, Batch #3) once capacity is reached.
        """
        # Clean today's mobile batches
        from django.utils import timezone
        today = timezone.localdate()
        IngestionBatch.objects.filter(source_type=IngestionBatch.SourceType.MOBILE, created_at__date=today).delete()

        # Batch 1 creation
        batch1 = vault_service.get_or_create_mobile_batch()
        assert batch1.source_type == IngestionBatch.SourceType.MOBILE
        assert "#1" in batch1.source_label
        assert batch1.batch_id.startswith("BATCH-")

        # Simulate batch 1 reaching 200 photos (100 bikes)
        batch1.ingested_count = 200
        batch1.save(update_fields=["ingested_count"])

        # Requesting a mobile batch now should rollover to Batch #2
        batch2 = vault_service.get_or_create_mobile_batch()
        assert batch2.id != batch1.id
        assert batch2.batch_id != batch1.batch_id
        assert "#2" in batch2.source_label
        assert batch2.ingested_count == 0

    def test_pair_affinity_preserves_batch_across_capacity_boundary(self):
        """
        Guarantees that a motorcycle's Front and Rear photos are NEVER split across
        different batches, even if photo 199 is Front and photo 200 is Rear.
        """
        from django.utils import timezone
        today = timezone.localdate()
        IngestionBatch.objects.filter(source_type=IngestionBatch.SourceType.MOBILE, created_at__date=today).delete()

        upload_url = reverse("core:api_mobile_upload")
        bike_id = "BIKE-BOUNDARY-TEST"

        # 1. Ingest Front photo into batch 1
        front_bytes = _create_test_image_bytes(color=(30, 60, 90))
        f1 = io.BytesIO(front_bytes)
        f1.name = "front_boundary.jpg"
        resp1 = self.client.post(upload_url, {
            "photo": f1,
            "mode": "conveyor",
            "orientation": "FRONT",
            "bike_client_id": bike_id,
        })
        assert resp1.status_code == 200
        batch1_id = resp1.json()["batch_id"]

        # Simulate batch 1 hitting 200 photos after Front photo was added
        b1 = IngestionBatch.objects.get(batch_id=batch1_id)
        b1.ingested_count = 200
        b1.save(update_fields=["ingested_count"])

        # 2. Ingest Rear photo for the same motorcycle
        rear_bytes = _create_test_image_bytes(color=(90, 60, 30))
        f2 = io.BytesIO(rear_bytes)
        f2.name = "rear_boundary.jpg"
        resp2 = self.client.post(upload_url, {
            "photo": f2,
            "mode": "conveyor",
            "orientation": "REAR",
            "bike_client_id": bike_id,
        })
        assert resp2.status_code == 200
        data2 = resp2.json()

        # Both photos MUST be in the exact same batch despite the batch reaching 200 photos!
        assert data2["batch_id"] == batch1_id
        assert data2["is_paired"] is True

        pair = VehicleInstallationPair.objects.get(id=data2["pair_id"])
        assert pair.front_image.batch.id == pair.rear_image.batch.id

    def test_api_mobile_new_batch(self):
        """Tests the explicit new-batch creation endpoint."""
        url = reverse("core:api_mobile_new_batch")
        resp = self.client.post(url, {"source_label": "Line B Shift"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["batch_max_photos"] == 200
        assert "Line B Shift" in data["batch_label"]

    def test_api_mobile_status_and_ping_batch_metrics(self):
        """Verifies that api_mobile_status and api_mobile_ping return accurate batch capacity metrics."""
        status_url = reverse("core:api_mobile_status")
        resp = self.client.get(status_url)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["batch_max_photos"] == 200
        assert data["batch_max_pairs"] == 100
        assert "batch_remaining_photos" in data
        assert "batch_is_full" in data

        ping_url = reverse("core:api_mobile_ping")
        ping_resp = self.client.get(f"{ping_url}?device_id=test-ping-device")
        assert ping_resp.status_code == 200
        ping_data = ping_resp.json()
        assert ping_data["batch_limit"] == 200
        assert "batch_photos" in ping_data
