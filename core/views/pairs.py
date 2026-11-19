"""
Pair Review & Approval REST APIs (queue, detail, action, batch submission).
"""
import json
import logging
import time

from django.db.models import Q
from django.http import HttpRequest, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.matcher import order_matcher
from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationKit,
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



_ACTIVE_BATCH_TOKENS = ("ACTIVE", "ACTIVE_BATCH", "LATEST")
_SHIFT_BATCH_TOKENS = ("ALL", "ALL_BATCHES", "SHIFT")


def _resolve_shift_scope(date_param: str, scope: str) -> dict:
    """
    Layer 1 of the queue funnel, shared by api_pairs_list and api_batch_submit so the
    list the operator sees and what batch-submit sends always use the same scope.
    """
    from core.services import date_service

    today = timezone.localdate()
    date_param = (date_param or "").strip()
    scope = (scope or "").upper().strip()
    active_shift = date_service.get_active_shift_info()
    is_all_dates = (
        scope == "ALL"
        or date_param.upper() == "ALL"
        or (not date_param and active_shift.get("mode") == "all")
    )

    target_date = today
    if not is_all_dates:
        parsed = date_service.parse_date_input(date_param) if date_param else None
        target_date = parsed or date_service.get_active_shift_date() or today

    if is_all_dates:
        shift_batches_qs = IngestionBatch.objects.all().order_by("-created_at")
    else:
        shift_batches_qs = IngestionBatch.objects.filter(
            date_service.batches_on_date_q(target_date)
        ).order_by("-created_at")

    return {
        "is_all_dates": is_all_dates,
        "target_date": target_date,
        "shift_batches_qs": shift_batches_qs,
        "active_batch": shift_batches_qs.first(),
    }


def _filter_pairs_to_shift(qs, shift: dict):
    """All pairs of the shift: pairs in any of its batches, plus unbatched pairs created that day."""
    from core.services import date_service

    if shift["is_all_dates"]:
        return qs
    start_dt, end_dt = date_service.kampala_day_bounds(shift["target_date"])
    shift_batches_qs = shift["shift_batches_qs"]
    return qs.filter(
        Q(front_image__batch__in=shift_batches_qs)
        | Q(rear_image__batch__in=shift_batches_qs)
        | (
            Q(front_image__batch__isnull=True)
            & Q(rear_image__batch__isnull=True)
            & Q(created_at__range=(start_dt, end_dt))
        )
    )


def _filter_pairs_to_batch(qs, shift_pairs_qs, shift: dict, batch_param: str, scope: str):
    """Layer 2 of the queue funnel: a specific batch, the shift's active batch, or all shift batches."""
    batch_param = (batch_param or "").strip()
    token = batch_param.upper()
    if batch_param and token not in _ACTIVE_BATCH_TOKENS + _SHIFT_BATCH_TOKENS:
        return qs.filter(Q(front_image__batch__batch_id=batch_param) | Q(rear_image__batch__batch_id=batch_param))
    if token in _ACTIVE_BATCH_TOKENS or (scope or "").upper().strip() == "ACTIVE_BATCH":
        active_batch = shift["active_batch"]
        if not active_batch:
            return qs.none()
        return qs.filter(Q(front_image__batch=active_batch) | Q(rear_image__batch=active_batch))
    return shift_pairs_qs


@require_GET
def api_pairs_list(request: HttpRequest) -> JsonResponse:
    """
    Returns filtered list of vehicle installation pairs for the work queue.
    Strictly follows 3-tier funnel:
    Layer 1: Shift Date (batches created on that day)
    Layer 2: Batch (Active Batch, All Shift Batches, or specific Batch ID)
    Layer 3: Status (ALL, REVIEW, ISSUES, DONE)
    """
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    from zoneinfo import ZoneInfo
    from core.services import date_service

    tz_eat = ZoneInfo("Africa/Kampala")

    status_filter = request.GET.get("status", "ALL").upper().strip()
    batch_filter = request.GET.get("batch", "").strip()
    scope = request.GET.get("scope", "").upper().strip()
    date_param = (
        request.GET.get("date")
        or request.GET.get("date_suffix")
        or request.GET.get("suffix")
        or ""
    ).strip()
    search = request.GET.get("search", "").strip()
    try:
        limit = max(1, min(5000, int(request.GET.get("limit", 2500))))
    except (ValueError, TypeError):
        limit = 2500

    # Layer 1: Date Filter (shared with api_batch_submit)
    shift = _resolve_shift_scope(date_param, scope)
    is_all_dates = shift["is_all_dates"]
    target_date = shift["target_date"]
    shift_batches_qs = shift["shift_batches_qs"]
    active_batch = shift["active_batch"]

    ymd_str = target_date.strftime("%Y%m%d")
    suffix_str = date_service.format_date_suffix(target_date)
    shift_display = "All Dates" if is_all_dates else f"{date_service.format_readable_date(target_date)} [{suffix_str}]"

    qs = VehicleInstallationPair.objects.filter(is_deleted=False).select_related(
        "order", "front_image", "rear_image", "front_image__batch", "rear_image__batch"
    ).order_by("-created_at", "-id")

    envelope = getattr(request, "envelope", None)
    if envelope and envelope.is_valid and envelope.facility_code:
        qs = qs.filter(
            Q(facility_code=envelope.facility_code)
            | Q(facility_code="")
            | Q(facility_code__isnull=True)
        )

    # Base shift pairs across ALL batches for the selected shift date
    shift_all_batches_qs = _filter_pairs_to_shift(qs, shift)

    # Shift-wide approved count across ALL batches ready for submission
    shift_approved_count = shift_all_batches_qs.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
    ).count()

    # Layer 2: Batch Filter
    qs = _filter_pairs_to_batch(qs, shift_all_batches_qs, shift, batch_filter, scope)

    # Pre-filter counts for Status Badges (within the selected batch/date scope)
    counts_qs = qs
    total_in_batch = counts_qs.count()
    review_in_batch = counts_qs.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
        order__isnull=False,
    ).count()
    approved_in_batch = counts_qs.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
    ).count()
    submitted_in_batch = counts_qs.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED,
    ).count()
    done_in_batch = approved_in_batch + submitted_in_batch
    issues_in_batch = counts_qs.filter(
        Q(verification_status__in=[
            VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            VehicleInstallationPair.VerificationStatus.CONFLICT,
            VehicleInstallationPair.VerificationStatus.UNREGISTERED,
            VehicleInstallationPair.VerificationStatus.FAILED,
            VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
        ]) | Q(order__isnull=True)
    ).count()

    # Layer 3: Status Filter (ALL, REVIEW, ISSUES, DONE)
    if status_filter in ("PENDING", "REVIEW"):
        qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW, order__isnull=False)
    elif status_filter == "APPROVED":
        qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)
    elif status_filter == "SUBMITTED":
        qs = qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)
    elif status_filter == "DONE":
        qs = qs.filter(verification_status__in=[
            VehicleInstallationPair.VerificationStatus.APPROVED,
            VehicleInstallationPair.VerificationStatus.SUBMITTED,
        ])
    elif status_filter in ("ISSUES", "CONFLICT"):
        qs = qs.filter(
            Q(verification_status__in=[
                VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                VehicleInstallationPair.VerificationStatus.CONFLICT,
                VehicleInstallationPair.VerificationStatus.UNREGISTERED,
                VehicleInstallationPair.VerificationStatus.FAILED,
                VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
            ]) | Q(order__isnull=True)
        )

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
    page_pairs = list(qs[:limit])
    # Open Quality Inspector flags per pair (direct pair flags + flags on the linked order).
    from collections import Counter
    from core.models import QualityFlag
    _pair_ids = [pp.id for pp in page_pairs]
    _order_ids = [pp.order_id for pp in page_pairs if pp.order_id]
    _open = QualityFlag.objects.filter(status=QualityFlag.Status.OPEN)
    _flags_by_pair = Counter(_open.filter(pair_id__in=_pair_ids).values_list("pair_id", flat=True))
    _flags_by_order = Counter(_open.filter(order_id__in=_order_ids).values_list("order_id", flat=True))
    for p in page_pairs:
        front_url = _clean_media_url(p.front_image.vault_file) if p.front_image and p.front_image.vault_file else None
        rear_url = _clean_media_url(p.rear_image.vault_file) if p.rear_image and p.rear_image.vault_file else None
        front_thumb_url = _clean_media_url(p.front_image.thumbnail_file or p.front_image.vault_file) if p.front_image and p.front_image.vault_file else None
        rear_thumb_url = _clean_media_url(p.rear_image.thumbnail_file or p.rear_image.vault_file) if p.rear_image and p.rear_image.vault_file else None

        batch_obj = (p.front_image.batch if p.front_image and p.front_image.batch else None) or (
            p.rear_image.batch if p.rear_image and p.rear_image.batch else None
        )
        batch_dt = timezone.localtime(batch_obj.created_at, tz_eat).date() if (batch_obj and batch_obj.created_at) else None
        is_today = bool(
            (batch_dt and batch_dt == target_date)
            or (batch_obj and batch_obj.batch_id.startswith(f"BATCH-{ymd_str}-"))
        )
        is_latest = bool(batch_obj and active_batch and batch_obj.id == active_batch.id)
        is_carryover = bool(batch_dt and batch_dt < target_date and not is_today)

        op_obj = p.operator or (batch_obj.operator if batch_obj else None)
        op_name = (op_obj.get_full_name().strip() or op_obj.username) if op_obj else ""
        op_email = (p.account_email or (op_obj.email if op_obj else "") or (batch_obj.account_email if batch_obj else "")).strip()

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
            "operator": op_name,
            "operator_email": op_email,
            "batch_id": batch_obj.batch_id if batch_obj else "Carryover",
            "batch_label": (batch_obj.source_label or batch_obj.batch_id) if batch_obj else "Carryover Batch",
            "batch_created_at": timezone.localtime(batch_obj.created_at, tz_eat).strftime("%Y-%m-%d %H:%M") if (batch_obj and batch_obj.created_at) else "",
            "is_today": is_today,
            "is_latest_batch": is_latest,
            "is_carryover": is_carryover,
            "order": {
                "order_number": p.order.order_number,
                "registration_number": p.order.registration_number,
                "service_type": p.order.service_type or "First Time Registration",
                "vin": p.order.vin or "—",
                "old_registration_number": p.order.old_registration_number or "—",
                "status": p.order.status,
                "front_plate_serial": p.order.front_plate_serial or "—",
                "rear_plate_serial": p.order.rear_plate_serial or "—",
                "gps_tracker_id": p.order.gps_tracker_id or "—",
                "front_beacon_id": p.order.front_beacon_id or "—",
                "rear_beacon_id": p.order.rear_beacon_id or "—",
            } if p.order else None,
            "hardware": {
                "service_type": (p.order.service_type if p.order else "") or "First Time Registration",
                "vin": (p.order.vin if p.order else "") or "—",
                "old_registration_number": (p.order.old_registration_number if p.order else "") or "—",
                "registration_number": (p.order.registration_number if p.order else p.registration_number_detected) or "—",
                "front_plate": (p.order.front_plate_serial if p.order else "") or "—",
                "rear_plate": (p.order.rear_plate_serial if p.order else "") or "—",
                "gps_tracker": (p.order.gps_tracker_id if p.order else "") or "—",
                "front_beacon": (p.order.front_beacon_id if p.order else "") or "—",
                "rear_beacon": (p.order.rear_beacon_id if p.order else "") or "—",
            },
            "updated_at": timezone.localtime(p.updated_at, tz_eat).strftime("%H:%M:%S") if p.updated_at else "",
            "open_flags": _flags_by_pair.get(p.id, 0) + (_flags_by_order.get(p.order_id, 0) if p.order_id else 0),
        })

    dropdown_batches_qs = shift_batches_qs[:50]
    batches_list = [
        {
            "batch_id": b.batch_id,
            "source_label": b.source_label or b.batch_id,
            "created_at": timezone.localtime(b.created_at, tz_eat).strftime("%Y-%m-%d %H:%M") if b.created_at else "",
            "is_active": bool(active_batch and b.id == active_batch.id),
        }
        for b in dropdown_batches_qs
    ]

    return JsonResponse({
        "success": True,
        "count": len(pairs_data),
        "scope": "ALL" if is_all_dates else (scope or "SHIFT"),
        "shift_date": "ALL" if is_all_dates else target_date.strftime("%Y-%m-%d"),
        "shift_suffix": "ALL" if is_all_dates else suffix_str,
        "shift_display": shift_display,
        "pairs": pairs_data,
        "active_batch_id": active_batch.batch_id if active_batch else None,
        "active_batch_label": (active_batch.source_label or active_batch.batch_id) if active_batch else None,
        "selected_batch": batch_filter or ("ACTIVE" if active_batch else "ALL"),
        "batches": batches_list,
        "shift_approved_count": shift_approved_count,
        "counts": {
            "all": total_in_batch,
            "review": review_in_batch,
            "approved": approved_in_batch,
            "shift_approved": shift_approved_count,
            "submitted": submitted_in_batch,
            "done": done_in_batch,
            "issues": issues_in_batch,
        },
    })


@require_GET
def api_pair_detail(request: HttpRequest, pair_id: int) -> JsonResponse:
    """Returns complete details of a specific pair for high-resolution inspection."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    pair = VehicleInstallationPair.objects.select_related(
        "order", "front_image", "rear_image", "front_image__batch", "rear_image__batch"
    ).filter(id=pair_id, is_deleted=False).first()
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

    order_obj = pair.order
    kit = None
    reg_to_check = (order_obj.registration_number if order_obj else pair.registration_number_detected) or ""
    if reg_to_check:
        clean_reg = normalizer.canonicalize(reg_to_check) or reg_to_check.upper().replace(" ", "")
        kit = InstallationKit.objects.filter(
            Q(registration_number__iexact=reg_to_check) |
            Q(registration_number__iexact=clean_reg) |
            Q(kit_code__icontains=clean_reg)
        ).first()

    front_plate_val = (order_obj.front_plate_serial if order_obj and order_obj.front_plate_serial else (kit.front_plate if kit else "")) or "—"
    rear_plate_val = (order_obj.rear_plate_serial if order_obj and order_obj.rear_plate_serial else (kit.rear_plate if kit else "")) or "—"
    gps_tracker_val = (order_obj.gps_tracker_id if order_obj and order_obj.gps_tracker_id else (kit.gps_tracker if kit else "")) or "—"
    front_beacon_val = (order_obj.front_beacon_id if order_obj and order_obj.front_beacon_id else (kit.front_tracker or getattr(kit, "front_tracker_mac", "") if kit else "")) or "—"
    rear_beacon_val = (order_obj.rear_beacon_id if order_obj and order_obj.rear_beacon_id else (kit.rear_tracker or getattr(kit, "rear_tracker_mac", "") if kit else "")) or "—"

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
                "id": order_obj.id,
                "order_number": order_obj.order_number,
                "registration_number": order_obj.registration_number,
                "service_type": order_obj.service_type or "First Time Registration",
                "vin": order_obj.vin or "—",
                "old_registration_number": order_obj.old_registration_number or "—",
                "sales_order": order_obj.sales_order or "—",
                "warehouse_name": order_obj.warehouse_name or "Uganda Hub",
                "installation_officer": order_obj.installation_officer or "Station Officer",
                "status": order_obj.status,
                "front_plate_serial": front_plate_val,
                "rear_plate_serial": rear_plate_val,
                "gps_tracker_id": gps_tracker_val,
                "front_beacon_id": front_beacon_val,
                "rear_beacon_id": rear_beacon_val,
            } if order_obj else None,
            "hardware": {
                "service_type": (order_obj.service_type if order_obj else "") or "First Time Registration",
                "vin": (order_obj.vin if order_obj else "") or "—",
                "old_registration_number": (order_obj.old_registration_number if order_obj else "") or "—",
                "registration_number": (order_obj.registration_number if order_obj else pair.registration_number_detected) or "—",
                "front_plate": front_plate_val,
                "rear_plate": rear_plate_val,
                "gps_tracker": gps_tracker_val,
                "front_beacon": front_beacon_val,
                "rear_beacon": rear_beacon_val,
            },
            "kit": {
                "id": kit.id,
                "kit_code": kit.kit_code,
                "front_plate": kit.front_plate or "—",
                "rear_plate": kit.rear_plate or "—",
                "gps_tracker": kit.gps_tracker or "—",
                "front_tracker": kit.front_tracker or getattr(kit, "front_tracker_mac", "") or "—",
                "rear_tracker": kit.rear_tracker or getattr(kit, "rear_tracker_mac", "") or "—",
                "warehouse": kit.warehouse or "—",
                "status": kit.status or "New",
            } if kit else None,
        }
    })


def api_pair_action(request: HttpRequest, pair_id: int) -> JsonResponse:
    """Executes an operator decision or action on a pair."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    pair = VehicleInstallationPair.objects.select_related(
        "order", "front_image", "rear_image"
    ).filter(id=pair_id, is_deleted=False).first()
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
    operator_name = (operator_user.get_full_name().strip() or operator_user.username) if operator_user else (request.headers.get("X-Operator-Username") or "operator")
    operator_email = operator_user.email.strip() if (operator_user and getattr(operator_user, "email", None)) else ""

    if action == "approve":
        # Attempt auto-linking if order is missing (matches TUI parity)
        if pair.is_complete and not pair.order:
            clean_reg = normalizer.canonicalize(pair.registration_number_detected) or "".join(c for c in pair.registration_number_detected.upper() if c.isalnum())
            found_order = (
                InstallationOrder.objects.filter(
                    is_archived=False,
                ).exclude(
                    order_status__in=["Installed", "Closed", "Archived"]
                ).filter(
                    Q(registration_number__iexact=clean_reg) |
                    Q(registration_number__iexact=pair.registration_number_detected)
                ).first()
            )
            # If not in local DB, attempt quick live lookup from ITMS
            if not found_order and clean_reg:
                try:
                    client = get_web_client(user=request.user)
                    if client.session_store.session.is_cookie_valid():
                        fetch_res = client.fetch_order_info(clean_reg, download_photos=False)
                        if fetch_res.get("success"):
                            client.sync_order_info_to_local_db(fetch_res, order_uuid=fetch_res.get("order_uuid", ""))
                            found_order = InstallationOrder.objects.filter(
                                order_number=fetch_res.get("order_number")
                            ).first()
                except Exception as live_err:
                    logger.debug("Live ITMS lookup in approve action skipped: %s", live_err)

            if found_order:
                # Ensure not already submitted on another pair
                already_submitted = VehicleInstallationPair.objects.filter(
                    is_deleted=False,
                    order=found_order,
                    verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED,
                ).exists()
                if not already_submitted:
                    pair.order = found_order
                    found_order.status = InstallationOrder.Status.MATCHED
                    found_order.is_active_on_itms = True
                    found_order.is_archived = False
                    found_order.save(update_fields=["status", "is_active_on_itms", "is_archived"])
                    pair.match_type = VehicleInstallationPair.MatchType.EXACT
                    pair.match_score = 100.0
                    pair.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
                    pair.operator_note = f"Linked to Order #{found_order.order_number} ({found_order.registration_number})"
                    pair.save(update_fields=["order", "match_type", "match_score", "matched_via", "operator_note"])

        if not pair.order:
            pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
            pair.save(update_fields=["verification_status"])
            return JsonResponse({
                "success": False,
                "error": f"Cannot approve {pair.registration_number_detected}: No pending installation order found. Order may be closed, installed, or not yet created on ITMS. Pair set to CONFLICT.",
                "code": "NO_PENDING_ORDER",
                "status": pair.verification_status,
            }, status=400)

        # Safeguard: Verify order is not already claimed by another active pair
        other_pair = (
            VehicleInstallationPair.objects.filter(
                is_deleted=False,
                order=pair.order,
            ).exclude(id=pair.id).first()
        )
        if other_pair:
            # If the other pair was already submitted, this order cannot be reused
            if other_pair.verification_status == VehicleInstallationPair.VerificationStatus.SUBMITTED:
                claimed_order = pair.order
                pair.order = None
                pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
                pair.operator_note = f"Order Conflict: Order #{claimed_order.order_number} was already submitted to ITMS on Pair #{other_pair.id}."
                pair.save(update_fields=["order", "verification_status", "operator_note"])
                return JsonResponse({
                    "success": False,
                    "error": f"Cannot approve: Order #{claimed_order.order_number} was already submitted to ITMS on Pair #{other_pair.id}. Pair unlinked and set to CONFLICT.",
                    "code": "ORDER_ALREADY_ASSIGNED",
                    "status": pair.verification_status,
                }, status=400)
            else:
                # Reassign from stale unsubmitted pair
                other_pair.order = None
                if other_pair.verification_status == VehicleInstallationPair.VerificationStatus.APPROVED:
                    other_pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
                other_pair.save(update_fields=["order", "verification_status"])

        was_failed = (pair.verification_status == VehicleInstallationPair.VerificationStatus.FAILED)
        pair.verification_status = VehicleInstallationPair.VerificationStatus.APPROVED
        update_cols = ["verification_status"]
        if operator_user:
            pair.operator = operator_user
            update_cols.append("operator")
            if operator_email and not pair.account_email:
                pair.account_email = operator_email
                update_cols.append("account_email")
        pair.save(update_fields=update_cols)

        if pair.order and pair.order.status == InstallationOrder.Status.FAILED:
            pair.order.status = InstallationOrder.Status.PENDING
            pair.order.save(update_fields=["status"])

        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=operator_user,
            operator_username=operator_name,
            operator_email=operator_email,
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
        update_cols = ["front_image", "rear_image"]
        if operator_user:
            pair.operator = operator_user
            update_cols.append("operator")
            if operator_email and not pair.account_email:
                pair.account_email = operator_email
                update_cols.append("account_email")
        pair.save(update_fields=update_cols)
        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=operator_user,
            operator_username=operator_name,
            operator_email=operator_email,
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
        if not target_order and raw_val:
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

        # Validate target order status and handle 1:1 pair assignments
        if target_order:
            # 1. Prevent linking if order is truly finalized in ITMS Archive
            if target_order.is_archived or target_order.status == InstallationOrder.Status.INSTALLED or (target_order.order_status or "").lower() in ["installed", "archived", "closed"]:
                return JsonResponse({
                    "success": False,
                    "error": f"Order #{target_order.order_number} ({target_order.registration_number}) is already closed/installed in the ITMS archive.",
                    "code": "ORDER_ALREADY_CLOSED",
                }, status=400)

            # 2. Check if already claimed by another active pair (1:1 invariant)
            other_pair = (
                VehicleInstallationPair.objects.filter(
                    is_deleted=False,
                    order=target_order,
                ).exclude(id=pair.id).first()
            )
            if other_pair:
                if other_pair.verification_status == VehicleInstallationPair.VerificationStatus.SUBMITTED:
                    return JsonResponse({
                        "success": False,
                        "error": f"Order #{target_order.order_number} ({target_order.registration_number}) was already submitted to ITMS on Pair #{other_pair.id}.",
                        "code": "ORDER_ALREADY_ASSIGNED",
                    }, status=400)
                else:
                    # Safely unbind from previous unsubmitted pair and reassign to this pair
                    other_pair.order = None
                    if other_pair.verification_status == VehicleInstallationPair.VerificationStatus.APPROVED:
                        other_pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
                    other_pair.save(update_fields=["order", "verification_status"])

        # Sibling Series Collision Guard (e.g. UMA496QV vs UMA496QW)
        confirm_sibling = str(request.POST.get("confirm_sibling") or body_data.get("confirm_sibling") or "").strip().lower() in ("1", "true", "yes")
        from core.services.sibling_detector import detect_sibling_collision
        cand_plate_to_check = (target_order.registration_number if target_order else raw_val) or pair.registration_number_detected
        collision = detect_sibling_collision(
            cand_plate_to_check,
            account_email=pair.account_email,
            exclude_pair_id=pair.id,
            exclude_order_id=target_order.id if target_order else None,
        )
        # If the operator explicitly picked an order_id from the UI, or confirm_sibling=True,
        # we consider this deliberate operator intent and do not block the linking action.
        if collision["has_sibling_collision"] and not confirm_sibling and not order_id:
            return JsonResponse({
                "success": False,
                "requires_confirmation": True,
                "confirmation_type": "SIBLING_PLATE_COLLISION",
                "error": collision["warning"],
                "target_plate": collision["target_plate"],
                "numeric_digits": collision["numeric_digits"],
                "sibling_plates": collision["sibling_plates"],
                "sibling_orders": collision["sibling_orders"],
                "order_id": target_order.id if target_order else None,
                "order_number": target_order.order_number if target_order else "",
                "code": "SIBLING_PLATE_COLLISION",
            }, status=200)

        if target_order:
            # 3. Activate target order status
            target_order.status = InstallationOrder.Status.MATCHED
            target_order.is_active_on_itms = True
            target_order.is_archived = False
            target_order.save(update_fields=["status", "is_active_on_itms", "is_archived"])

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

        pair.refresh_completeness()
        if target_order:
            # Promoting directly to APPROVED on complete manual order link saves operator review friction;
            # if missing rear or front image, set to PENDING_REVIEW instead of CONFLICT
            pair.verification_status = (
                VehicleInstallationPair.VerificationStatus.APPROVED
                if pair.is_complete
                else VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
            )
        else:
            pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT

        sibling_tag = f" [SIBLING_OVERRIDE_CONFIRMED: {', '.join(collision['sibling_plates'])}]" if collision.get("has_sibling_collision") and (confirm_sibling or order_id) else ""
        if operator_note is not None and str(operator_note).strip():
            pair.operator_note = str(operator_note).strip() + sibling_tag
        elif target_order:
            pair.operator_note = f"Linked to Order #{target_order.order_number} ({target_order.registration_number}){sibling_tag}"
        elif canonical_plate:
            pair.operator_note = f"Manual plate updated: {canonical_plate}{sibling_tag}"

        if operator_user:
            pair.operator = operator_user
            if operator_email and not pair.account_email:
                pair.account_email = operator_email
        pair.save()

        order_desc = f"linked to order #{target_order.order_number}" if target_order else "no matching order; plate updated directly"
        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=operator_user,
            operator_username=operator_name,
            operator_email=operator_email,
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
        pair.verification_status = VehicleInstallationPair.VerificationStatus.CONFLICT
        pair.operator_note = (
            f"Order Conflict: Order #{prev_order.order_number if prev_order else 'N/A'} was unlinked by operator."
        )
        update_cols = ["order", "match_type", "matched_via", "match_score", "is_manual_override", "verification_status", "operator_note"]
        if operator_user:
            pair.operator = operator_user
            update_cols.append("operator")
            if operator_email and not pair.account_email:
                pair.account_email = operator_email
                update_cols.append("account_email")
        pair.save(update_fields=update_cols)

        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=operator_user,
            operator_username=operator_name,
            operator_email=operator_email,
            action=SubmissionAuditLog.Action.OPERATOR_OVERRIDE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator unlinked order #{prev_order.order_number if prev_order else 'N/A'} via Web Console. Pair transitioned to CONFLICT.",
        )
        return JsonResponse({
            "success": True,
            "message": "Order unlinked from pair. Status transitioned to CONFLICT.",
            "verification_status": pair.verification_status,
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
                from core.matcher.order_matcher import get_pending_orders_queryset
                found_order = (
                    get_pending_orders_queryset()
                    .filter(registration_number__iexact=clean_reg)
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
        update_cols = ["verification_status"]
        if operator_user:
            pair.operator = operator_user
            update_cols.append("operator")
            if operator_email and not pair.account_email:
                pair.account_email = operator_email
                update_cols.append("account_email")
        pair.save(update_fields=update_cols)

        # Concurrency safety: check if another submission is actively executing
        from core.services.submission_lock import is_submission_active
        is_locked, lock_info = is_submission_active()
        if is_locked and lock_info.get("pair_id") != pair.id:
            active_op = lock_info.get("operator", "another operator")
            active_plate = lock_info.get("registration_number") or lock_info.get("order_number", "")
            return JsonResponse({
                "success": False,
                "code": "SUBMISSION_IN_PROGRESS",
                "error": f"Another submission is currently in progress ({active_plate} by {active_op}). Please wait for it to complete.",
                "lock_info": lock_info,
            }, status=409)

        outcome = submission_worker.submit_pair(pair, backend="web", dry_run=is_dry_run, operator=operator_user)
        return JsonResponse({
            "success": outcome.success,
            "status": pair.verification_status,
            "token": outcome.token,
            "error": outcome.error,
            "dry_run": is_dry_run,
            "message": f"{'[DRY RUN] ' if is_dry_run else ''}Submitted successfully to ITMS." if outcome.success else f"Submission failed: {outcome.error}",
        })

    elif action == "delete_pair":
        purge_param = request.POST.get("purge_files") if request.POST.get("purge_files") is not None else body_data.get("purge_files", True)
        if isinstance(purge_param, str):
            purge_files = purge_param.lower().strip() in ("true", "1", "yes")
        else:
            purge_files = bool(purge_param)

        res = pair.soft_delete(operator=request.user, purge_files=purge_files)
        return JsonResponse({
            "success": True,
            "message": f"Pair {pair.registration_number_detected} deleted and files {'purged' if purge_files else 'retained'}.",
            "purged_files": res.get("purged_files", []),
        })

    elif action == "replace_image":
        side = (request.POST.get("side") or body_data.get("side") or "").lower().strip()
        source = (request.POST.get("source") or body_data.get("source") or "upload").lower().strip()
        purge_param = request.POST.get("purge_old") if request.POST.get("purge_old") is not None else body_data.get("purge_old", True)
        if isinstance(purge_param, str):
            purge_old = purge_param.lower().strip() in ("true", "1", "yes")
        else:
            purge_old = bool(purge_param)

        if side not in ("front", "rear"):
            return JsonResponse({"success": False, "error": "Invalid side. Must be 'front' or 'rear'."}, status=400)

        new_image = None
        from core.services import vault_service

        if source == "upload":
            photo_file = request.FILES.get("photo")
            if not photo_file:
                return JsonResponse({"success": False, "error": "No photo file provided for upload."}, status=400)

            target_batch = (pair.front_image.batch if pair.front_image and pair.front_image.batch else None) or (
                pair.rear_image.batch if pair.rear_image and pair.rear_image.batch else None
            )
            if not target_batch:
                target_batch = IngestionBatch.objects.order_by("-created_at").first()
            if not target_batch:
                target_batch = vault_service.create_ingestion_batch(
                    source_type=IngestionBatch.SourceType.WEB,
                    source_label=f"Replace Photo Batch ({side.title()})",
                    user=request.user,
                )

            new_image, _status = vault_service.ingest_uploaded_file(
                uploaded_file=photo_file,
                batch=target_batch,
                orientation_override=side.upper(),
            )
            if not new_image:
                return JsonResponse({"success": False, "error": "Failed to ingest uploaded replacement photo into vault."}, status=500)

            vault_service.process_single_image_vision(new_image)

        elif source == "batch":
            image_id = request.POST.get("image_id") or body_data.get("image_id")
            if not image_id:
                return JsonResponse({"success": False, "error": "image_id is required when source is 'batch'."}, status=400)

            new_image = EvidenceImage.objects.filter(id=image_id).first()
            if not new_image:
                return JsonResponse({"success": False, "error": f"Candidate image #{image_id} not found."}, status=404)
        else:
            return JsonResponse({"success": False, "error": f"Invalid source '{source}'. Must be 'upload' or 'batch'."}, status=400)

        pair.replace_image(side=side, new_image=new_image, operator=request.user, purge_old=purge_old)

        return JsonResponse({
            "success": True,
            "message": f"{side.title()} photo replaced successfully.",
            "pair": {
                "id": pair.id,
                "registration_number_detected": pair.registration_number_detected,
                "verification_status": pair.verification_status,
                "is_complete": pair.is_complete,
                "side_replaced": side,
                "new_image_id": str(new_image.id),
                "new_image_url": _clean_media_url(new_image.vault_file),
                "new_image_thumb": _clean_media_url(new_image.thumbnail_file or new_image.vault_file),
                "detected_plate": new_image.detected_plate,
                "ocr_confidence": round(new_image.ocr_confidence * 100, 1) if new_image.ocr_confidence else None,
            }
        })

    return JsonResponse({"success": False, "error": f"Unknown action: {action}"}, status=400)


@require_POST
def api_batch_submit(request: HttpRequest) -> JsonResponse:
    """Submits approved pairs to ITMS in a single operation, scoped to shift or batch."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    dry_run_param = request.POST.get("dry_run")
    if dry_run_param is not None and str(dry_run_param).strip() != "":
        is_dry_run = str(dry_run_param).strip().lower() in ("true", "1", "yes")
    else:
        is_dry_run = config_service.get_setting("submission.dry_run_mode", True)

    scope = request.POST.get("scope", "TODAY").upper().strip()
    date_param = (
        request.POST.get("date")
        or request.GET.get("date")
        or ""
    ).strip()

    qs = VehicleInstallationPair.objects.filter(
        is_deleted=False,
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        order__isnull=False,
        order__status=InstallationOrder.Status.PENDING,
        order__is_archived=False,
        order__is_active_on_itms=True,
    ).exclude(
        order__order_status__in=["Installed", "Ready for approve", "Closed", "Archived"]
    ).exclude(
        order__itms_stage__in=["STAGE_2_APPROVE", "STAGE_3_CONFIRMATION", "ARCHIVED"]
    ).select_related("order", "front_image", "rear_image")

    # Same facility scoping as the queue list (api_pairs_list)
    envelope = getattr(request, "envelope", None)
    if envelope and envelope.is_valid and envelope.facility_code:
        qs = qs.filter(
            Q(facility_code=envelope.facility_code)
            | Q(facility_code="")
            | Q(facility_code__isnull=True)
        )

    # Same date -> batch funnel as the queue list, so submit sends what the operator sees
    shift = _resolve_shift_scope(date_param, scope)
    batch_param = (request.POST.get("batch") or request.POST.get("batch_id") or "").strip()
    shift_pairs_qs = _filter_pairs_to_shift(qs, shift)
    approved_pairs = list(_filter_pairs_to_batch(qs, shift_pairs_qs, shift, batch_param, scope))

    if not approved_pairs:
        return JsonResponse({
            "success": False,
            "message": f"No approved pairs ready for submission in the selected scope.",
        })

    # Concurrency safety: check if another submission is in progress before starting batch
    from core.services.submission_lock import is_submission_active
    is_locked, lock_info = is_submission_active()
    if is_locked:
        active_op = lock_info.get("operator", "another operator")
        active_plate = lock_info.get("registration_number") or lock_info.get("order_number", "")
        return JsonResponse({
            "success": False,
            "code": "SUBMISSION_IN_PROGRESS",
            "message": f"Another submission is currently in progress ({active_plate} by {active_op}). Please wait for it to complete.",
            "lock_info": lock_info,
        }, status=409)

    # Pacing and detailed result tracking
    pacing_seconds = float(config_service.get_setting("submission.batch_pacing_seconds", 2.0))
    succeeded = 0
    failed = 0
    outbox_queued = 0
    failures = []
    operator_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None

    for idx, p in enumerate(approved_pairs, 1):
        if idx > 1 and pacing_seconds > 0:
            time.sleep(pacing_seconds)

        # Check that pair is still APPROVED to prevent concurrent race condition submissions
        refreshed = VehicleInstallationPair.objects.select_related(
            "order", "front_image", "rear_image"
        ).filter(
            id=p.id,
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED,
        ).first()
        if not refreshed:
            continue

        outcome = submission_worker.submit_pair(
            refreshed,
            backend="web",
            dry_run=is_dry_run,
            operator=operator_user,
            blocking_lock=True,
            lock_timeout=25.0,
        )
        if outcome.success:
            succeeded += 1
        else:
            failed += 1
            is_outbox = "Offline Outbox" in (outcome.error or "")
            if is_outbox:
                outbox_queued += 1
            failures.append({
                "pair_id": p.id,
                "plate": p.registration_number_detected,
                "order_number": p.order.order_number if p.order else "",
                "error": outcome.error,
                "queued_outbox": is_outbox,
            })

    return JsonResponse({
        "success": True,
        "total": len(approved_pairs),
        "succeeded": succeeded,
        "failed": failed,
        "outbox_queued": outbox_queued,
        "failures": failures,
        "dry_run": is_dry_run,
        "scope": scope,
        "message": f"{'[DRY RUN] ' if is_dry_run else ''}Batch submission complete ({scope}): {succeeded} succeeded, {failed} failed (Outbox: {outbox_queued}).",
    })


@require_POST
def api_pair_create(request: HttpRequest) -> JsonResponse:
    """Creates a new VehicleInstallationPair on the fly from an unassigned EvidenceImage."""
    if not (request.user and getattr(request.user, "is_authenticated", False)):
        return JsonResponse({"success": False, "error": "Authentication required.", "code": "AUTHENTICATION_REQUIRED"}, status=401)

    image_id = request.POST.get("image_id")
    if not image_id:
        return JsonResponse({"success": False, "error": "image_id is required."}, status=400)

    image = EvidenceImage.objects.filter(id=image_id).select_related("batch").first()
    if not image:
        return JsonResponse({"success": False, "error": f"Image #{image_id} not found."}, status=404)

    # Check if already belongs to an active pair
    existing_pair = VehicleInstallationPair.objects.filter(
        is_deleted=False
    ).filter(
        Q(front_image=image) | Q(rear_image=image)
    ).first()
    if existing_pair:
        return JsonResponse({
            "success": True,
            "created": False,
            "pair_id": existing_pair.id,
            "detected_plate": existing_pair.registration_number_detected,
            "message": f"Photo already belongs to Pair #{existing_pair.id}.",
        })

    orient = (image.orientation or "FRONT").upper()
    front_img = image if orient == "FRONT" else None
    rear_img = image if orient == "REAR" else None
    plate_detected = image.detected_plate or "UNREAD"

    envelope = getattr(request, "envelope", None)
    facility = envelope.facility_code if (envelope and envelope.is_valid) else (getattr(image.batch, "facility_code", "AGM") or "AGM")

    new_pair = VehicleInstallationPair.objects.create(
        registration_number_detected=plate_detected,
        front_image=front_img,
        rear_image=rear_img,
        verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT,
        facility_code=facility,
        operator=request.user,
        account_email=request.user.email if getattr(request.user, "email", None) else "",
        operator_note="Pair created manually from unassigned photo in batch.",
        matched_via=VehicleInstallationPair.MatchedVia.MANUAL,
    )
    new_pair.refresh_completeness()
    new_pair.save()

    return JsonResponse({
        "success": True,
        "created": True,
        "pair_id": new_pair.id,
        "detected_plate": new_pair.registration_number_detected,
        "message": f"Created Pair #{new_pair.id} from photo.",
    })



