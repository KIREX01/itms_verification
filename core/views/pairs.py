"""
Pair Review & Approval REST APIs (queue, detail, action, batch submission).
"""
import json
import logging

from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.matcher import order_matcher
from core.models import (
    IngestionBatch,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import config_service, submission_worker
from core.services.itms_web_client import get_web_client
from core.vision import normalizer

logger = logging.getLogger(__name__)

def _clean_media_url(file_path):
    """Safely normalizes file paths to clean /media/ URLs, preventing duplicate prefixes or backslashes."""
    if not file_path:
        return None
    p = str(file_path).replace(chr(92), "/").strip().lstrip("/")
    if p.startswith("media/"):
        return f"/{p}"
    return f"/media/{p}"



@require_GET
def api_pairs_list(request: HttpRequest) -> JsonResponse:
    """Returns filtered list of vehicle installation pairs for the work queue with batch clarity."""
    today = timezone.localdate()

    status_filter = request.GET.get("status", "ALL").upper().strip()
    batch_filter = request.GET.get("batch", "ALL").strip()
    scope = request.GET.get("scope", "").upper().strip()
    search = request.GET.get("search", "").strip()
    try:
        limit = max(1, min(1000, int(request.GET.get("limit", 250))))
    except (ValueError, TypeError):
        limit = 250

    latest_batch = IngestionBatch.objects.order_by("-created_at").first()

    qs = VehicleInstallationPair.objects.all().select_related(
        "order", "front_image", "rear_image", "front_image__batch", "rear_image__batch"
    )

    today_q = (
        Q(created_at__date=today) |
        Q(front_image__batch__created_at__date=today) |
        Q(rear_image__batch__created_at__date=today)
    )

    if scope == "TODAY":
        qs = qs.filter(today_q)
    elif scope == "ACTIVE_BATCH" and latest_batch:
        qs = qs.filter(Q(front_image__batch=latest_batch) | Q(rear_image__batch=latest_batch))
    elif scope == "CARRYOVER":
        qs = qs.exclude(today_q)

    if status_filter == "PENDING":
        qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)
    elif status_filter == "APPROVED":
        qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)
    elif status_filter == "SUBMITTED":
        qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)
    elif status_filter == "ISSUES":
        qs = qs.filter(verification_status__in=[
            VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            VehicleInstallationPair.VerificationStatus.CONFLICT,
            VehicleInstallationPair.VerificationStatus.UNREGISTERED,
            VehicleInstallationPair.VerificationStatus.FAILED,
            VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
        ])

    # Batch scoping
    if batch_filter == "LATEST" and latest_batch:
        qs = qs.filter(Q(front_image__batch=latest_batch) | Q(rear_image__batch=latest_batch))
    elif batch_filter == "CARRYOVER" and latest_batch:
        qs = qs.exclude(Q(front_image__batch=latest_batch) | Q(rear_image__batch=latest_batch))
    elif batch_filter not in ("ALL", ""):
        qs = qs.filter(Q(front_image__batch__batch_id=batch_filter) | Q(rear_image__batch__batch_id=batch_filter))

    if search:
        qs = qs.filter(
            Q(registration_number_detected__icontains=search) |
            Q(order__order_number__icontains=search) |
            Q(order__registration_number__icontains=search) |
            Q(order__vin__icontains=search) |
            Q(front_image__batch__batch_id__icontains=search) |
            Q(rear_image__batch__batch_id__icontains=search)
        )

    pairs_data = []
    for p in qs[:limit]:
        front_url = _clean_media_url(p.front_image.vault_file) if p.front_image and p.front_image.vault_file else None
        rear_url = _clean_media_url(p.rear_image.vault_file) if p.rear_image and p.rear_image.vault_file else None
        front_thumb_url = _clean_media_url(p.front_image.thumbnail_file or p.front_image.vault_file) if p.front_image and p.front_image.vault_file else None
        rear_thumb_url = _clean_media_url(p.rear_image.thumbnail_file or p.rear_image.vault_file) if p.rear_image and p.rear_image.vault_file else None

        batch_obj = (p.front_image.batch if p.front_image and p.front_image.batch else None) or (
            p.rear_image.batch if p.rear_image and p.rear_image.batch else None
        )
        is_today = bool(batch_obj and batch_obj.created_at and timezone.localdate(batch_obj.created_at) == today)
        is_latest = bool(batch_obj and latest_batch and batch_obj.id == latest_batch.id)
        is_carryover = bool(batch_obj and batch_obj.created_at and timezone.localdate(batch_obj.created_at) < today)

        pairs_data.append({
            "id": p.id,
            "registration_number_detected": p.registration_number_detected,
            "verification_status": p.verification_status,
            "match_type": p.match_type,
            "match_score": round(p.match_score, 1) if p.match_score is not None else None,
            "matched_via": p.matched_via,
            "is_complete": p.is_complete,
            "is_manual_override": p.is_manual_override,
            "has_front": bool(p.front_image_id),
            "has_rear": bool(p.rear_image_id),
            "front_url": front_url,
            "rear_url": rear_url,
            "front_thumb_url": front_thumb_url,
            "rear_thumb_url": rear_thumb_url,
            "batch_id": batch_obj.batch_id if batch_obj else "Carryover",
            "batch_label": (batch_obj.source_label or batch_obj.batch_id) if batch_obj else "Carryover Batch",
            "batch_created_at": batch_obj.created_at.strftime("%Y-%m-%d %H:%M") if (batch_obj and batch_obj.created_at) else "",
            "is_today": is_today,
            "is_latest_batch": is_latest,
            "is_carryover": is_carryover,
            "order": {
                "order_number": p.order.order_number,
                "registration_number": p.order.registration_number,
                "vin": p.order.vin,
                "status": p.order.status,
            } if p.order else None,
            "updated_at": p.updated_at.strftime("%H:%M:%S") if p.updated_at else "",
        })

    batches_list = [
        {
            "batch_id": b.batch_id,
            "source_label": b.source_label or b.batch_id,
            "created_at": b.created_at.strftime("%Y-%m-%d %H:%M") if b.created_at else "",
            "is_latest": bool(latest_batch and b.id == latest_batch.id),
            "is_today": bool(b.created_at and b.created_at.date() == today),
        }
        for b in IngestionBatch.objects.order_by("-created_at")[:20]
    ]

    today_approved_count = VehicleInstallationPair.objects.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
    ).filter(today_q).count()
    carryover_approved_count = VehicleInstallationPair.objects.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
    ).exclude(today_q).count()

    return JsonResponse({
        "success": True,
        "count": len(pairs_data),
        "scope": scope or "ALL",
        "pairs": pairs_data,
        "latest_batch_id": latest_batch.batch_id if latest_batch else None,
        "today_approved_count": today_approved_count,
        "carryover_approved_count": carryover_approved_count,
        "batches": batches_list,
    })


@require_GET
def api_pair_detail(request: HttpRequest, pair_id: int) -> JsonResponse:
    """Returns complete details of a specific pair for high-resolution inspection."""
    pair = VehicleInstallationPair.objects.select_related(
        "order", "front_image", "rear_image", "front_image__batch", "rear_image__batch"
    ).filter(id=pair_id).first()
    if not pair:
        return JsonResponse({"success": False, "error": f"Pair #{pair_id} not found."}, status=404)

    latest_batch = IngestionBatch.objects.order_by("-created_at").first()
    batch_obj = (pair.front_image.batch if pair.front_image and pair.front_image.batch else None) or (
        pair.rear_image.batch if pair.rear_image and pair.rear_image.batch else None
    )

    front = None
    if pair.front_image:
        vault_path = str(pair.front_image.vault_file).replace("\\", "/")
        thumb_path = str(pair.front_image.thumbnail_file or pair.front_image.vault_file).replace("\\", "/")
        preview_path = str(pair.front_image.preview_file or pair.front_image.vault_file).replace("\\", "/")
        front = {
            "id": str(pair.front_image.id),
            "url": _clean_media_url(pair.front_image.vault_file),
            "preview_url": _clean_media_url(pair.front_image.preview_file or pair.front_image.vault_file),
            "thumb_url": _clean_media_url(pair.front_image.thumbnail_file or pair.front_image.vault_file),
            "detected_plate": pair.front_image.detected_plate,
            "ocr_confidence": round(pair.front_image.ocr_confidence * 100, 1) if pair.front_image.ocr_confidence is not None else None,
            "detector_confidence": round(pair.front_image.detector_confidence * 100, 1) if pair.front_image.detector_confidence is not None else None,
            "captured_at": pair.front_image.captured_at.strftime("%H:%M:%S") if pair.front_image.captured_at else None,
        }

    rear = None
    if pair.rear_image:
        vault_path = str(pair.rear_image.vault_file).replace("\\", "/")
        thumb_path = str(pair.rear_image.thumbnail_file or pair.rear_image.vault_file).replace("\\", "/")
        preview_path = str(pair.rear_image.preview_file or pair.rear_image.vault_file).replace("\\", "/")
        rear = {
            "id": str(pair.rear_image.id),
            "url": _clean_media_url(pair.rear_image.vault_file),
            "preview_url": _clean_media_url(pair.rear_image.preview_file or pair.rear_image.vault_file),
            "thumb_url": _clean_media_url(pair.rear_image.thumbnail_file or pair.rear_image.vault_file),
            "detected_plate": pair.rear_image.detected_plate,
            "ocr_confidence": round(pair.rear_image.ocr_confidence * 100, 1) if pair.rear_image.ocr_confidence is not None else None,
            "detector_confidence": round(pair.rear_image.detector_confidence * 100, 1) if pair.rear_image.detector_confidence is not None else None,
            "captured_at": pair.rear_image.captured_at.strftime("%H:%M:%S") if pair.rear_image.captured_at else None,
        }

    time_delta_sec = None
    if pair.front_image and pair.rear_image and pair.front_image.captured_at and pair.rear_image.captured_at:
        try:
            time_delta_sec = abs((pair.rear_image.captured_at - pair.front_image.captured_at).total_seconds())
        except (TypeError, ValueError, AttributeError) as exc:
            logger.debug("Failed calculating time delta for pair %s: %s", pair_id, exc)

    return JsonResponse({
        "success": True,
        "pair": {
            "id": pair.id,
            "registration_number_detected": pair.registration_number_detected,
            "verification_status": pair.verification_status,
            "match_type": pair.match_type,
            "match_score": round(pair.match_score, 1) if pair.match_score is not None else None,
            "matched_via": pair.matched_via,
            "is_complete": pair.is_complete,
            "is_manual_override": pair.is_manual_override,
            "operator_note": pair.operator_note,
            "time_delta_sec": time_delta_sec,
            "front": front,
            "rear": rear,
            "batch_id": batch_obj.batch_id if batch_obj else "Carryover",
            "batch_created_at": batch_obj.created_at.strftime("%Y-%m-%d %H:%M") if (batch_obj and batch_obj.created_at) else "",
            "is_latest_batch": bool(batch_obj and latest_batch and batch_obj.id == latest_batch.id),
            "is_carryover": bool(latest_batch and (not batch_obj or batch_obj.id != latest_batch.id)),
            "order": {
                "id": pair.order.id,
                "order_number": pair.order.order_number,
                "registration_number": pair.order.registration_number,
                "vin": pair.order.vin,
                "warehouse_name": pair.order.warehouse_name,
                "installation_officer": pair.order.installation_officer,
                "status": pair.order.status,
            } if pair.order else None,
        }
    })


@csrf_exempt
def api_pair_action(request: HttpRequest, pair_id: int) -> JsonResponse:
    """Executes an operator decision or action on a pair."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    pair = VehicleInstallationPair.objects.select_related(
        "order", "front_image", "rear_image"
    ).filter(id=pair_id).first()
    if not pair:
        return JsonResponse({"success": False, "error": f"Pair #{pair_id} not found."}, status=404)

    action = request.POST.get("action")
    body_data = {}
    if not action and request.content_type == "application/json" and request.body:
        try:
            body_data = json.loads(request.body.decode("utf-8"))
            action = body_data.get("action")
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.debug("JSON decode error in pair action for pair %s: %s", pair_id, exc)

    if not action:
        return JsonResponse({"success": False, "error": "No action specified"}, status=400)

    action = action.lower().strip()
    operator_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    operator_name = operator_user.username if operator_user else (request.headers.get("X-Operator-Username") or "operator")

    if action == "approve":
        # Attempt auto-linking if order is missing (matches TUI parity)
        if pair.is_complete and not pair.order:
            from core.matcher.order_matcher import match_pair_to_order
            match_pair_to_order(pair)
            if not pair.order:
                clean_reg = "".join(c for c in pair.registration_number_detected.upper() if c.isalnum())
                found_order = (
                    InstallationOrder.objects.filter(registration_number__iexact=clean_reg)
                    .exclude(status=InstallationOrder.Status.SUBMITTED)
                    .first()
                )
                if found_order:
                    pair.order = found_order
                    pair.save(update_fields=["order"])

        was_failed = (pair.verification_status == VehicleInstallationPair.VerificationStatus.FAILED)
        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        pair.save(update_fields=["verification_status"])

        if pair.order and pair.order.status == InstallationOrder.Status.FAILED:
            pair.order.status = InstallationOrder.Status.PENDING
            pair.order.save(update_fields=["status"])

        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=operator_user,
            operator_username=operator_name,
            action=SubmissionAuditLog.Action.OPERATOR_APPROVE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Pair approved by operator via Web Console." if not was_failed else "Pair reset from FAILED to APPROVED for retry via Web Console.",
        )
        return JsonResponse({
            "success": True,
            "message": f"Pair {pair.registration_number_detected} approved.",
            "status": pair.verification_status,
        })

    elif action == "swap":
        if not (pair.front_image and pair.rear_image):
            return JsonResponse({"success": False, "error": "Both front and rear photos required to swap."}, status=400)

        pair.front_image, pair.rear_image = pair.rear_image, pair.front_image
        pair.save(update_fields=["front_image", "rear_image"])
        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=operator_user,
            operator_username=operator_name,
            action=SubmissionAuditLog.Action.OPERATOR_SWAP,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Operator swapped front and rear image assignments via Web Console.",
        )
        return JsonResponse({
            "success": True,
            "message": f"Swapped front and rear photos for {pair.registration_number_detected}.",
        })

    elif action in ("edit_plate", "manual_override", "link_order"):
        # Unified Link Order & Plate Correction (parity with TUI QuickPlateTypeModal)
        raw_val = (
            request.POST.get("query") or request.POST.get("plate") or request.POST.get("order_number") or
            body_data.get("query") or body_data.get("plate") or body_data.get("order_number") or ""
        ).strip()
        order_id = request.POST.get("order_id") or body_data.get("order_id")
        new_status = request.POST.get("verification_status") or body_data.get("verification_status")
        operator_note = request.POST.get("operator_note") or body_data.get("operator_note")

        target_order = None
        if order_id:
            try:
                target_order = InstallationOrder.objects.filter(id=order_id).first()
            except (ValueError, TypeError) as exc:
                logger.debug("Invalid order_id '%s': %s", order_id, exc)

        if not target_order and raw_val:
            clean_val = normalizer.canonicalize(raw_val) or raw_val.replace(" ", "").upper()
            target_order = InstallationOrder.objects.filter(
                Q(registration_number__iexact=clean_val) |
                Q(registration_number__iexact=raw_val) |
                Q(order_number__iexact=raw_val) |
                Q(order_number__iexact=clean_val) |
                Q(order_number__icontains=raw_val) |
                Q(order_number__icontains=clean_val) |
                Q(vin__iexact=raw_val)
            ).first()

            if not target_order:
                try:
                    outcome = order_matcher.find_best_match(clean_val)
                    if outcome and outcome.order and outcome.score >= 85:
                        target_order = outcome.order
                except Exception as match_err:
                    logger.warning("Order matcher lookup error: %s", match_err)

            # Live lookup against ITMS server if order not in local DB (TUI Parity)
            if not target_order:
                try:
                    client = get_web_client(user=request.user)
                    if client.session_store.session.is_cookie_valid():
                        fetch_res = client.fetch_order_info(clean_val, download_photos=False)
                        if not fetch_res.get("success") and clean_val != raw_val:
                            fetch_res = client.fetch_order_info(raw_val, download_photos=False)
                        if fetch_res.get("success"):
                            client.sync_order_info_to_local_db(fetch_res, order_uuid=fetch_res.get("order_uuid", ""))
                            target_order = InstallationOrder.objects.filter(
                                order_number=fetch_res.get("order_number")
                            ).first()
                except Exception as itms_err:
                    logger.warning("Live ITMS lookup failed for '%s': %s", raw_val, itms_err)

        canonical_plate = ""
        if target_order:
            canonical_plate = normalizer.canonicalize(target_order.registration_number) or target_order.registration_number
            pair.order = target_order
            is_exact = (normalizer.canonicalize(target_order.registration_number) == normalizer.canonicalize(raw_val or pair.registration_number_detected))
            pair.match_type = VehicleInstallationPair.MatchType.EXACT if is_exact else VehicleInstallationPair.MatchType.FUZZY
            pair.match_score = 100.0 if is_exact else 90.0
            pair.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
        elif raw_val:
            canonical_plate = normalizer.canonicalize(raw_val) or raw_val.strip().upper()
            pair.order = None
            pair.match_type = VehicleInstallationPair.MatchType.NONE
            pair.match_score = None
            pair.matched_via = VehicleInstallationPair.MatchedVia.MANUAL

        if canonical_plate:
            pair.manual_plate_override = canonical_plate
            pair.registration_number_detected = canonical_plate
            pair.is_manual_override = True

            if pair.front_image:
                pair.front_image.detected_plate = canonical_plate
                pair.front_image.save(update_fields=["detected_plate"])
            if pair.rear_image:
                pair.rear_image.detected_plate = canonical_plate
                pair.rear_image.save(update_fields=["detected_plate"])

        if new_status == "NEEDS_REVIEW":
            new_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW

        if new_status and new_status in VehicleInstallationPair.VerificationStatus.values:
            pair.verification_status = new_status
        else:
            pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED

        pair.refresh_completeness()

        if operator_note is not None and str(operator_note).strip():
            pair.operator_note = str(operator_note).strip()
        elif target_order:
            pair.operator_note = f"Linked to Order #{target_order.order_number} ({target_order.registration_number})"
        elif canonical_plate:
            pair.operator_note = f"Manual plate updated: {canonical_plate}"

        pair.save()

        order_desc = f"linked to order #{target_order.order_number}" if target_order else "no matching order; plate updated directly"
        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=operator_user,
            operator_username=operator_name,
            action=SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator Link/Override: plate='{pair.registration_number_detected}' ({order_desc}), status='{pair.verification_status}'.",
        )

        return JsonResponse({
            "success": True,
            "message": f"Pair updated: {pair.registration_number_detected} " + (f"(Linked to #{target_order.order_number})" if target_order else ""),
            "registration_number_detected": pair.registration_number_detected,
            "verification_status": pair.verification_status,
            "operator_note": pair.operator_note,
            "match_type": pair.match_type,
            "match_score": pair.match_score,
            "order_number": pair.order.order_number if pair.order else None,
            "order": {
                "id": pair.order.id,
                "order_number": pair.order.order_number,
                "registration_number": pair.order.registration_number,
                "vin": pair.order.vin,
                "warehouse_name": pair.order.warehouse_name,
                "installation_officer": pair.order.installation_officer,
            } if pair.order else None,
        })

    elif action == "unlink_order":
        prev_order = pair.order
        pair.order = None
        pair.match_type = VehicleInstallationPair.MatchType.NONE
        pair.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
        pair.match_score = 0.0
        pair.is_manual_override = True
        pair.save(update_fields=["order", "match_type", "matched_via", "match_score", "is_manual_override"])

        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=operator_user,
            operator_username=operator_name,
            action=SubmissionAuditLog.Action.OPERATOR_OVERRIDE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator unlinked order #{prev_order.order_number if prev_order else 'N/A'} via Web Console.",
        )
        return JsonResponse({
            "success": True,
            "message": "Order unlinked from pair.",
        })

    elif action == "submit":
        dry_run_param = request.POST.get("dry_run")
        if dry_run_param is not None and str(dry_run_param).strip() != "":
            is_dry_run = str(dry_run_param).strip().lower() in ("true", "1", "yes")
        else:
            is_dry_run = config_service.get_setting("submission.dry_run_mode", True)

        # Attempt auto-linking if order is missing
        if pair.is_complete and not pair.order:
            from core.matcher.order_matcher import match_pair_to_order
            match_pair_to_order(pair)
            if not pair.order:
                clean_reg = "".join(c for c in pair.registration_number_detected.upper() if c.isalnum())
                found_order = (
                    InstallationOrder.objects.filter(registration_number__iexact=clean_reg)
                    .exclude(status=InstallationOrder.Status.SUBMITTED)
                    .first()
                )
                if found_order:
                    pair.order = found_order
                    pair.save(update_fields=["order"])

        if not pair.order:
            return JsonResponse({"success": False, "error": "Cannot submit: pair is not matched to an order."}, status=400)

        # Auto-approve if currently PENDING_REVIEW or FAILED (operator decision to submit)
        if pair.verification_status != VehicleInstallationPair.VerificationStatus.APPROVED:
            pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
            pair.save(update_fields=["verification_status"])

        outcome = submission_worker.submit_pair(pair, backend="web", dry_run=is_dry_run, operator=operator_user)
        return JsonResponse({
            "success": outcome.success,
            "status": pair.verification_status,
            "token": outcome.token,
            "error": outcome.error,
            "dry_run": is_dry_run,
            "message": f"{'[DRY RUN] ' if is_dry_run else ''}Submitted successfully to ITMS." if outcome.success else f"Submission failed: {outcome.error}",
        })

    return JsonResponse({"success": False, "error": f"Unknown action: {action}"}, status=400)


@csrf_exempt
@require_POST
def api_batch_submit(request: HttpRequest) -> JsonResponse:
    """Submits approved pairs to ITMS in a single operation, scoped to shift or batch."""
    today = timezone.localdate()

    dry_run_param = request.POST.get("dry_run")
    if dry_run_param is not None and str(dry_run_param).strip() != "":
        is_dry_run = str(dry_run_param).strip().lower() in ("true", "1", "yes")
    else:
        is_dry_run = config_service.get_setting("submission.dry_run_mode", True)

    scope = request.POST.get("scope", "TODAY").upper().strip()
    latest_batch = IngestionBatch.objects.order_by("-created_at").first()

    qs = VehicleInstallationPair.objects.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        order__isnull=False,
    ).select_related("order", "front_image", "rear_image")

    today_q = (
        Q(created_at__date=today) |
        Q(front_image__batch__created_at__date=today) |
        Q(rear_image__batch__created_at__date=today)
    )

    if scope == "TODAY":
        approved_pairs = list(qs.filter(today_q))
        carryover_cnt = qs.exclude(today_q).count()
        if not approved_pairs:
            if carryover_cnt > 0:
                return JsonResponse({
                    "success": False,
                    "message": f"No APPROVED orders ready in Today's scope. Found {carryover_cnt} in Prior Carryover (switch Date Scope to Prior Carryover or All Work).",
                    "carryover_count": carryover_cnt,
                })
            return JsonResponse({
                "success": False,
                "message": "No approved pairs available for submission in Today's scope.",
            })
    elif scope == "ACTIVE_BATCH" and latest_batch:
        approved_pairs = list(qs.filter(Q(front_image__batch=latest_batch) | Q(rear_image__batch=latest_batch)))
        if not approved_pairs:
            return JsonResponse({
                "success": False,
                "message": f"No APPROVED orders found in Active Batch ({latest_batch.batch_id}).",
            })
    elif scope == "CARRYOVER":
        approved_pairs = list(qs.exclude(today_q))
        if not approved_pairs:
            return JsonResponse({
                "success": False,
                "message": "No APPROVED orders found in Prior Carryover.",
            })
    else:  # ALL
        approved_pairs = list(qs)
        if not approved_pairs:
            return JsonResponse({
                "success": False,
                "message": "No approved pairs available for submission across all scopes.",
            })

    succeeded = 0
    failed = 0
    operator_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    for p in approved_pairs:
        # Check that pair is still APPROVED to prevent concurrent race condition submissions
        refreshed = VehicleInstallationPair.objects.select_related(
            "order", "front_image", "rear_image"
        ).filter(
            id=p.id,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        ).first()
        if not refreshed:
            continue
        outcome = submission_worker.submit_pair(refreshed, backend="web", dry_run=is_dry_run, operator=operator_user)
        if outcome.success:
            succeeded += 1
        else:
            failed += 1

    return JsonResponse({
        "success": True,
        "total": len(approved_pairs),
        "succeeded": succeeded,
        "failed": failed,
        "dry_run": is_dry_run,
        "scope": scope,
        "message": f"{'[DRY RUN] ' if is_dry_run else ''}Batch submission complete ({scope}): {succeeded} succeeded, {failed} failed.",
    })


