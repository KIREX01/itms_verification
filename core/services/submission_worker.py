"""
Automated submission worker.

Takes an APPROVED VehicleInstallationPair, drives it through the
simulated ITMS 4-step workflow, and writes a SubmissionAuditLog row for
every step (success or failure). On any failure, the pair is marked
FAILED and a FALLBACK audit entry is written -- automation never blocks
the operator from taking over manually.
"""
from dataclasses import dataclass
from typing import List

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.models import EvidenceImage, InstallationOrder, SubmissionAuditLog, VehicleInstallationPair
from core.services import itms_client, itms_mock


@dataclass
class SubmissionOutcome:
    pair_id: int
    success: bool
    token: str = ""
    error: str = ""
    backend: str = "mock"


def _log(pair: VehicleInstallationPair, action: str, result: str, message: str, token: str = ""):
    SubmissionAuditLog.objects.create(
        pair=pair, action=action, result=result, message=message, simulated_token=token,
    )


def submit_pair(pair: VehicleInstallationPair, backend: Optional[str] = None) -> SubmissionOutcome:
    """Drive a single pair through the ITMS workflow (mock or live) with full audit logging."""
    backend_mode = (backend or getattr(settings, "ITMS_SUBMISSION_BACKEND", "mock")).lower()
    itms_service = itms_client.default_client if backend_mode == "live" else itms_mock

    if not pair.order:
        _log(pair, SubmissionAuditLog.Action.ORDER_LOOKUP, SubmissionAuditLog.ResultStatus.FAILURE,
             f"[{backend_mode.upper()}] No matched InstallationOrder on this pair; cannot submit.")
        pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        pair.save(update_fields=["verification_status"])
        return SubmissionOutcome(pair_id=pair.id, success=False, error="No matched order", backend=backend_mode)

    if not (pair.front_image and pair.rear_image):
        _log(pair, SubmissionAuditLog.Action.VALIDATE, SubmissionAuditLog.ResultStatus.FAILURE,
             f"[{backend_mode.upper()}] Pair is missing front or rear evidence; cannot submit.")
        pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        pair.save(update_fields=["verification_status"])
        return SubmissionOutcome(pair_id=pair.id, success=False, error="Incomplete evidence", backend=backend_mode)

    order = pair.order
    try:
        step = itms_service.lookup_order(order.order_number)
        _log(pair, SubmissionAuditLog.Action.ORDER_LOOKUP, SubmissionAuditLog.ResultStatus.SUCCESS, f"[{backend_mode.upper()}] {step.message}")

        step = itms_service.verify_serial(order.order_number, order.plate_serial, order.tracker_id)
        _log(pair, SubmissionAuditLog.Action.SERIAL_VERIFY, SubmissionAuditLog.ResultStatus.SUCCESS, f"[{backend_mode.upper()}] {step.message}")

        step = itms_service.upload_front_photo(order.order_number, pair.front_image.vault_file)
        _log(pair, SubmissionAuditLog.Action.UPLOAD_FRONT, SubmissionAuditLog.ResultStatus.SUCCESS, f"[{backend_mode.upper()}] {step.message}")

        step = itms_service.upload_rear_photo(order.order_number, pair.rear_image.vault_file)
        _log(pair, SubmissionAuditLog.Action.UPLOAD_REAR, SubmissionAuditLog.ResultStatus.SUCCESS, f"[{backend_mode.upper()}] {step.message}")

        step = itms_service.validate_and_finalize(order.order_number)
        _log(pair, SubmissionAuditLog.Action.SUBMIT, SubmissionAuditLog.ResultStatus.SUCCESS, f"[{backend_mode.upper()}] {step.message}", token=step.token or "")

    except itms_mock.ITMSError as exc:
        _log(pair, SubmissionAuditLog.Action.SUBMIT, SubmissionAuditLog.ResultStatus.FAILURE, f"[{backend_mode.upper()}] {exc}")
        _log(pair, SubmissionAuditLog.Action.FALLBACK, SubmissionAuditLog.ResultStatus.INFO,
             "Automation halted. Manual operator fallback required for this order.")
        pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        pair.save(update_fields=["verification_status"])
        order.status = InstallationOrder.Status.FAILED
        order.save(update_fields=["status"])
        return SubmissionOutcome(pair_id=pair.id, success=False, error=str(exc), backend=backend_mode)
        _log(pair, SubmissionAuditLog.Action.FALLBACK, SubmissionAuditLog.ResultStatus.INFO,
             "Automation halted. Manual operator fallback required for this order.")
        pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        pair.save(update_fields=["verification_status"])
        order.status = InstallationOrder.Status.FAILED
        order.save(update_fields=["status"])
        return SubmissionOutcome(pair_id=pair.id, success=False, error=str(exc))

    now = timezone.now()
    with transaction.atomic():
        pair.verification_status = VehicleInstallationPair.VerificationStatus.SUBMITTED
        pair.submitted_at = now
        pair.save(update_fields=["verification_status", "submitted_at"])

        order.status = InstallationOrder.Status.SUBMITTED
        order.save(update_fields=["status"])

        for image in (pair.front_image, pair.rear_image):
            if image:
                image.status = EvidenceImage.Status.SUBMITTED
                image.submitted_at = now
                image.save(update_fields=["status", "submitted_at"])

    return SubmissionOutcome(pair_id=pair.id, success=True, token=step.token or "")


def submit_approved_pairs(auto_approve: bool = False, backend: Optional[str] = None) -> List[SubmissionOutcome]:
    """
    Submits every pair currently APPROVED. If auto_approve=True, also
    promotes high-confidence PENDING_REVIEW pairs (complete + EXACT/FUZZY
    match) to APPROVED first, for unattended batch runs.
    """
    if auto_approve:
        candidates = VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
            is_complete=True,
            match_type__in=[VehicleInstallationPair.MatchType.EXACT, VehicleInstallationPair.MatchType.FUZZY],
            order__isnull=False,
        )
        for pair in candidates:
            pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
            pair.save(update_fields=["verification_status"])
            _log(pair, SubmissionAuditLog.Action.OPERATOR_APPROVE, SubmissionAuditLog.ResultStatus.INFO,
                 "Auto-approved by --auto-approve batch run (high-confidence match).")

    outcomes = []
    approved_qs = VehicleInstallationPair.objects.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
    )
    for pair in approved_qs:
        outcomes.append(submit_pair(pair, backend=backend))
    return outcomes
