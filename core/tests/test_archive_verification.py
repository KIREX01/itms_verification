from unittest.mock import MagicMock, patch
import pytest
from core.models import InstallationOrder, VehicleInstallationPair, EvidenceImage, IngestionBatch
from core.services.itms_web_client import ITMSWebClient


@pytest.fixture
def test_order_and_pair(db):
    batch = IngestionBatch.objects.create(batch_id="Batch-Test", source_type=IngestionBatch.SourceType.WEB)
    img_f = EvidenceImage.objects.create(
        batch=batch,
        file_hash="hash_front_123456",
        original_source_path="front.jpg",
        vault_file="vault/front.jpg",
        orientation=EvidenceImage.Orientation.FRONT,
    )
    img_r = EvidenceImage.objects.create(
        batch=batch,
        file_hash="hash_rear_123456",
        original_source_path="rear.jpg",
        vault_file="vault/rear.jpg",
        orientation=EvidenceImage.Orientation.REAR,
    )
    order = InstallationOrder.objects.create(
        order_number="PO-UMA999Z-231026",
        registration_number="UMA999Z",
        itms_order_uuid="uuid-999-test",
        status=InstallationOrder.Status.PENDING,
        order_status="Under installation",
    )
    pair = VehicleInstallationPair.objects.create(
        order=order,
        front_image=img_f,
        rear_image=img_r,
        registration_number_detected="UMA999Z",
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        is_complete=True,
    )
    return order, pair


def test_submit_confirmation_step3_session_expired(test_order_and_pair):
    """Verify that a 302 redirect to /site/login is treated as SESSION_EXPIRED, not success."""
    order, pair = test_order_and_pair
    client = ITMSWebClient()

    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.headers = {"Location": "/site/login"}

    with patch.object(client, "_request_with_retry", return_value=mock_resp):
        res = client.submit_confirmation_step3(order_uuid=order.itms_order_uuid, dry_run=False)

    assert not res["success"]
    assert res["code"] == "SESSION_EXPIRED"
    assert "session expired" in res["error"].lower()


def test_submit_confirmation_step3_confirmation_rejected(test_order_and_pair):
    """Verify that a 302 redirect back to /installation-orders/approve is treated as CONFIRMATION_REJECTED."""
    order, pair = test_order_and_pair
    client = ITMSWebClient()

    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.headers = {"Location": f"/installation-orders/approve?id={order.itms_order_uuid}"}

    with patch.object(client, "_request_with_retry", return_value=mock_resp):
        res = client.submit_confirmation_step3(order_uuid=order.itms_order_uuid, dry_run=False)

    assert not res["success"]
    assert res["code"] == "CONFIRMATION_REJECTED"


def test_verify_order_archived_success(test_order_and_pair):
    """Verify that when archive confirms STAGE_ARCHIVED, database records advance to INSTALLED and SUBMITTED."""
    order, pair = test_order_and_pair
    client = ITMSWebClient()

    stage_archived_result = {
        "success": True,
        "stage": "STAGE_ARCHIVED",
        "order_uuid": order.itms_order_uuid,
        "order_number": order.order_number,
    }

    with patch.object(client, "detect_order_stage", return_value=stage_archived_result):
        res = client.verify_order_archived(order_identifier=order.order_number, pair_id=pair.id, max_retries=1)

    assert res["success"]
    assert res["verified"]

    order.refresh_from_db()
    pair.refresh_from_db()
    assert order.status == InstallationOrder.Status.INSTALLED
    assert order.is_archived is True
    assert order.order_status == "Installed"
    assert pair.verification_status == VehicleInstallationPair.VerificationStatus.SUBMITTED


def test_verify_order_archived_failure(test_order_and_pair):
    """Verify that when archive check fails/times out, records are NOT marked as installed."""
    order, pair = test_order_and_pair
    client = ITMSWebClient()

    stage_not_archived = {
        "success": True,
        "stage": "STAGE_3_CONFIRMATION",
        "order_uuid": order.itms_order_uuid,
    }

    with patch.object(client, "detect_order_stage", return_value=stage_not_archived):
        res = client.verify_order_archived(order_identifier=order.order_number, pair_id=pair.id, max_retries=2, retry_delay=0.01)

    assert not res["success"]
    assert not res["verified"]
    assert res["code"] == "ARCHIVE_VERIFICATION_TIMEOUT"

    order.refresh_from_db()
    pair.refresh_from_db()
    # Order must NOT be archived or installed!
    assert order.is_archived is False
    assert order.status != InstallationOrder.Status.INSTALLED
    assert pair.verification_status != VehicleInstallationPair.VerificationStatus.SUBMITTED
