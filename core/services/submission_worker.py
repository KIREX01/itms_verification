"""
Automated submission worker.

Takes an APPROVED VehicleInstallationPair, drives it through the
simulated ITMS 4-step workflow, and writes a SubmissionAuditLog row for
every step (success or failure). On any failure, the pair is marked
FAILED and a FALLBACK audit entry is written -- automation never blocks
the operator from taking over manually.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

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
    dry_run: bool = True


def _log(
    pair: VehicleInstallationPair,
    action: str,
    result: str,
    message: str,
    token: str = "",
    operator: Optional[Any] = None,
):
    op_user = operator if (operator and getattr(operator, "is_authenticated", False)) else None
    op_name = getattr(op_user, "username", str(operator)) if operator else "System"
    return SubmissionAuditLog.objects.create(
        pair=pair,
        action=action,
        result=result,
        message=message,
        simulated_token=token,
        operator=op_user,
        operator_username=op_name,
    )


def submit_pair(
    pair: VehicleInstallationPair,
    backend: Optional[str] = None,
    dry_run: Optional[bool] = None,
    submit_step3: Optional[bool] = None,
    log_callback: Optional[Any] = None,
    operator: Optional[Any] = None,
) -> SubmissionOutcome:
    """Drive a single pair through the ITMS workflow (mock, live API, or live_web) with full audit logging."""
    from core.services import config_service, vault_service
    backend_mode = (backend or getattr(settings, "ITMS_SUBMISSION_BACKEND", "web")).lower()
    
    if dry_run is not None:
        is_dry_run = bool(dry_run)
    else:
        is_dry_run = config_service.get_setting("submission.dry_run_mode", getattr(settings, "ITMS_WEB_DRY_RUN", True))

    if submit_step3 is not None:
        is_submit_step3 = bool(submit_step3)
    else:
        is_submit_step3 = config_service.get_setting("submission.submit_step3", getattr(settings, "ITMS_SUBMIT_STEP3", True))

    if not pair.order:
        _log(pair, SubmissionAuditLog.Action.ORDER_LOOKUP, SubmissionAuditLog.ResultStatus.FAILURE,
             f"[{backend_mode.upper()}] No matched InstallationOrder on this pair; cannot submit.", operator=operator)
        pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        pair.save(update_fields=["verification_status"])
        return SubmissionOutcome(pair_id=pair.id, success=False, error="No matched order", backend=backend_mode, dry_run=is_dry_run)

    if not (pair.front_image and pair.rear_image):
        _log(pair, SubmissionAuditLog.Action.VALIDATE, SubmissionAuditLog.ResultStatus.FAILURE,
             f"[{backend_mode.upper()}] Pair is missing front or rear evidence; cannot submit.", operator=operator)
        pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        pair.save(update_fields=["verification_status"])
        return SubmissionOutcome(pair_id=pair.id, success=False, error="Incomplete evidence", backend=backend_mode, dry_run=is_dry_run)

    order = pair.order

    # Cross-Account Safety Check: ensure session matches order account ownership
    if backend_mode in ("live_web", "web", "live"):
        from core.services.itms_web_client import get_web_client
        web_client = get_web_client()

        active_session_email = (web_client.session_store.session.user_email or "").strip().lower()
        order_acc = (order.account_email or "").strip().lower()
        if active_session_email and order_acc and active_session_email != order_acc:
            err_msg = (
                f"Cross-account submission blocked: Order #{order.order_number} is linked to "
                f"'{order.account_email}', but active ITMS session is '{active_session_email}'."
            )
            _log(pair, SubmissionAuditLog.Action.SUBMIT, SubmissionAuditLog.ResultStatus.FAILURE, err_msg, operator=operator)
            pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
            pair.save(update_fields=["verification_status"])
            return SubmissionOutcome(pair_id=pair.id, success=False, error=err_msg, backend=backend_mode, dry_run=is_dry_run)

    # Phase 4.1: Pre-Submission SHA-256 Tamper & Integrity Verification
    front_path = vault_service.resolve_vault_path(pair.front_image.vault_file)
    rear_path = vault_service.resolve_vault_path(pair.rear_image.vault_file)

    if front_path.is_file() and rear_path.is_file():
        front_hash = vault_service.hash_file_path(front_path)
        rear_hash = vault_service.hash_file_path(rear_path)
        if front_hash != pair.front_image.file_hash or rear_hash != pair.rear_image.file_hash:
            err_msg = (
                f"Tamper detection failed! Photo SHA-256 mismatch: "
                f"Front ({front_hash[:8]} vs {pair.front_image.file_hash[:8]}), "
                f"Rear ({rear_hash[:8]} vs {pair.rear_image.file_hash[:8]})."
            )
            _log(pair, SubmissionAuditLog.Action.VALIDATE, SubmissionAuditLog.ResultStatus.FAILURE, err_msg, operator=operator)
            pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
            pair.save(update_fields=["verification_status"])
            return SubmissionOutcome(pair_id=pair.id, success=False, error=err_msg, backend=backend_mode, dry_run=is_dry_run)
    elif not getattr(settings, "TESTING", False) and not is_dry_run and backend_mode != "mock":
        err_msg = f"Evidence file missing on disk: Front={front_path.exists()}, Rear={rear_path.exists()}"
        _log(pair, SubmissionAuditLog.Action.VALIDATE, SubmissionAuditLog.ResultStatus.FAILURE, err_msg, operator=operator)
        pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
        pair.save(update_fields=["verification_status"])
        return SubmissionOutcome(pair_id=pair.id, success=False, error=err_msg, backend=backend_mode, dry_run=is_dry_run)

    # Live Web Wizard Backend (Yii2 WebApp stock.itms.ug)
    if backend_mode in ("live_web", "web", "live"):
        from core.services.itms_web_client import get_web_client
        web_client = get_web_client()

        res = web_client.execute_installation_order_workflow(
            order_identifier=order.order_number,
            front_photo_path=pair.front_image.vault_file,
            rear_photo_path=pair.rear_image.vault_file,
            pair_id=pair.id,
            dry_run=is_dry_run,
            submit_step3=is_submit_step3,
            log_callback=log_callback,
        )
        if not res.get("success"):
            err_msg = res.get("error", "Web workflow failed")
            err_lower = str(err_msg).lower()
            is_net_err = any(k in err_lower for k in ("connection", "timeout", "10054", "offline", "unreachable", "getaddrinfo", "host", "reset by peer"))
            if is_net_err:
                outbox_enabled = bool(config_service.get_setting("outbox.enabled", True))
                if outbox_enabled:
                    pair.verification_status = VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX
                    pair.save(update_fields=["verification_status"])
                    _log(pair, SubmissionAuditLog.Action.OUTBOX_QUEUE, SubmissionAuditLog.ResultStatus.INFO,
                         f"Network dropped during upload. Queued into Offline Outbox for automatic synchronization: {err_msg}", operator=operator)
                    if log_callback:
                        try:
                            log_callback(f"📦 [bold magenta][OFFLINE OUTBOX][/bold magenta] Queued {pair.registration_number_detected} for auto-sync.")
                        except Exception:
                            pass
                    return SubmissionOutcome(pair_id=pair.id, success=False, error=f"Queued to Offline Outbox: {err_msg}", backend="live_web", dry_run=is_dry_run)
                else:
                    _log(pair, SubmissionAuditLog.Action.SUBMIT, SubmissionAuditLog.ResultStatus.FAILURE, f"[WEB NETWORK DROP] {err_msg}", operator=operator)
                    _log(pair, SubmissionAuditLog.Action.FALLBACK, SubmissionAuditLog.ResultStatus.INFO,
                         "Network dropped during upload. Vehicle pair preserved in APPROVED status for retry once connection is restored.", operator=operator)
            else:
                _log(pair, SubmissionAuditLog.Action.SUBMIT, SubmissionAuditLog.ResultStatus.FAILURE, f"[WEB] {err_msg}", operator=operator)
                _log(pair, SubmissionAuditLog.Action.FALLBACK, SubmissionAuditLog.ResultStatus.INFO,
                     "Automation halted. Manual operator fallback required for this order.", operator=operator)
                pair.verification_status = VehicleInstallationPair.VerificationStatus.FAILED
                pair.save(update_fields=["verification_status"])
                order.status = InstallationOrder.Status.FAILED
                order.save(update_fields=["status"])
            return SubmissionOutcome(pair_id=pair.id, success=False, error=err_msg, backend="live_web", dry_run=is_dry_run)

        token = res.get("redirect_url", res.get("order_uuid", ""))
        dry_run_tag = " [DRY-RUN]" if is_dry_run else ""
        _log(pair, SubmissionAuditLog.Action.SUBMIT, SubmissionAuditLog.ResultStatus.SUCCESS,
             f"[WEB]{dry_run_tag} Successfully completed ITMS installation wizard ({res.get('message', '')})", token=token, operator=operator)

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

        return SubmissionOutcome(pair_id=pair.id, success=True, token=token, backend="live_web", dry_run=is_dry_run)

    itms_service = itms_client.default_client if backend_mode == "live" else itms_mock

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
        return SubmissionOutcome(pair_id=pair.id, success=False, error=str(exc), backend=backend_mode, dry_run=is_dry_run)

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

    return SubmissionOutcome(pair_id=pair.id, success=True, token=step.token or "", backend=backend_mode, dry_run=is_dry_run)


def submit_approved_pairs(
    auto_approve: bool = False,
    retry_failed: bool = False,
    backend: Optional[str] = None,
    dry_run: Optional[bool] = None,
    submit_step3: Optional[bool] = None,
    progress_callback: Optional[Any] = None,
    log_callback: Optional[Any] = None,
    operator: Optional[Any] = None,
) -> List[SubmissionOutcome]:
    """
    Submits every pair currently APPROVED. If auto_approve=True, also
    promotes high-confidence PENDING_REVIEW pairs (complete + EXACT/FUZZY
    match) to APPROVED first, for unattended batch runs.
    If retry_failed=True, resets complete FAILED pairs to APPROVED.
    """
    if retry_failed:
        failed_candidates = list(VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.FAILED,
            is_complete=True,
            order__isnull=False,
        ).select_related("order"))
        if failed_candidates:
            op_user = operator if (operator and getattr(operator, "is_authenticated", False)) else None
            op_name = getattr(op_user, "username", str(operator)) if operator else "System"
            logs_to_create = []
            orders_to_update = set()
            now = timezone.now()
            for pair in failed_candidates:
                pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
                if pair.order and pair.order.status != InstallationOrder.Status.PENDING:
                    pair.order.status = InstallationOrder.Status.PENDING
                    orders_to_update.add(pair.order)
                logs_to_create.append(SubmissionAuditLog(
                    pair=pair,
                    action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
                    result=SubmissionAuditLog.ResultStatus.INFO,
                    message="Reset from FAILED to APPROVED for retry submission.",
                    operator=op_user,
                    operator_username=op_name,
                    timestamp=now,
                ))
            with transaction.atomic():
                VehicleInstallationPair.objects.bulk_update(failed_candidates, ["verification_status"], batch_size=200)
                if orders_to_update:
                    InstallationOrder.objects.bulk_update(list(orders_to_update), ["status"], batch_size=200)
                SubmissionAuditLog.objects.bulk_create(logs_to_create, batch_size=200)

    if auto_approve:
        candidates = list(VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
            is_complete=True,
            match_type__in=[VehicleInstallationPair.MatchType.EXACT, VehicleInstallationPair.MatchType.FUZZY],
            order__isnull=False,
        ))
        if candidates:
            op_user = operator if (operator and getattr(operator, "is_authenticated", False)) else None
            op_name = getattr(op_user, "username", str(operator)) if operator else "System"
            logs_to_create = []
            now = timezone.now()
            for pair in candidates:
                pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
                logs_to_create.append(SubmissionAuditLog(
                    pair=pair,
                    action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
                    result=SubmissionAuditLog.ResultStatus.INFO,
                    message="Auto-approved by --auto-approve batch run (high-confidence match).",
                    operator=op_user,
                    operator_username=op_name,
                    timestamp=now,
                ))
            with transaction.atomic():
                VehicleInstallationPair.objects.bulk_update(candidates, ["verification_status"], batch_size=200)
                SubmissionAuditLog.objects.bulk_create(logs_to_create, batch_size=200)

    outcomes = []
    approved_qs = list(
        VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
        ).select_related("order", "front_image", "rear_image")
    )
    total_count = len(approved_qs)

    for idx, pair in enumerate(approved_qs, 1):
        outcome = submit_pair(
            pair,
            backend=backend,
            dry_run=dry_run,
            submit_step3=submit_step3,
            log_callback=log_callback,
            operator=operator,
        )
        outcomes.append(outcome)
        if progress_callback:
            try:
                progress_callback(idx, total_count, pair, outcome)
            except Exception:
                pass

    return outcomes


def drain_offline_outbox(
    backend: Optional[str] = None,
    dry_run: Optional[bool] = None,
    submit_step3: Optional[bool] = None,
    progress_callback: Optional[Any] = None,
    log_callback: Optional[Any] = None,
    operator: Optional[Any] = None,
) -> List[SubmissionOutcome]:
    """
    Submits every pair currently in OFFLINE_OUTBOX status when network connection is restored.
    Seamlessly resumes the queue without re-verification.
    """
    outbox_qs = list(
        VehicleInstallationPair.objects.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
            order__isnull=False,
            is_complete=True,
        ).select_related("order", "front_image", "rear_image")
    )
    if not outbox_qs:
        return []

    outcomes = []
    total = len(outbox_qs)
    for idx, pair in enumerate(outbox_qs, 1):
        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        pair.save(update_fields=["verification_status"])
        _log(
            pair,
            SubmissionAuditLog.Action.OUTBOX_DRAIN,
            SubmissionAuditLog.ResultStatus.INFO,
            f"Draining Offline Outbox [{idx}/{total}] for {pair.registration_number_detected}: Auto-sync initiated.",
            operator=operator,
        )
        if log_callback:
            try:
                log_callback(f"⚡ [bold cyan][OUTBOX AUTO-SYNC][/bold cyan] [{idx}/{total}] Submitting {pair.registration_number_detected}...")
            except Exception:
                pass

        outcome = submit_pair(
            pair,
            backend=backend,
            dry_run=dry_run,
            submit_step3=submit_step3,
            log_callback=log_callback,
            operator=operator,
        )
        outcomes.append(outcome)
        if progress_callback:
            try:
                progress_callback(idx, total, pair, outcome)
            except Exception:
                pass

    return outcomes

