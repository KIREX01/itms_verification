"""
Views and REST API controllers for the ITMS Operator Web Application.

Provides:
- Web Dashboard view (Single-Page Application for Operators)
- Evidence photo upload & batch ingestion
- Pair Review & Approval REST APIs (queue, detail, approve, swap, edit plate, link order, submit)
- Background AI pipeline execution (YOLO detection + OCR + matcher) & live status polling
- ITMS order synchronization & batch submission
- Shift report export (CSV)
"""
import csv
import io
import json
import logging
import math
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.matcher import order_matcher
from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import auth_service, config_service, submission_worker, vault_service
from core.services.itms_web_client import get_web_client
from core.services.pipeline_runner import runner as pipeline_runner
from core.services.update_service import update_service
from core.version import __version__
from core.vision import normalizer

logger = logging.getLogger(__name__)


# ============================================================================
# Operator Authentication & Account Management Views
# ============================================================================

def login_view(request: HttpRequest) -> HttpResponse:
    """Operator Sign In view."""
    if request.user.is_authenticated:
        return redirect(request.GET.get("next") or "core:dashboard")

    next_url = request.GET.get("next") or request.POST.get("next") or "/"

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "").strip()
        remember = bool(request.POST.get("remember"))

        user, err = auth_service.authenticate_operator(username, password)
        if user is None:
            return render(request, "core/login.html", {
                "error": err,
                "last_username": username,
                "next_url": next_url,
            })

        auth_login(request, user)
        auth_service.save_remembered_session(user, remember=remember)
        return redirect(next_url)

    # If no users exist in the active database yet, prompt first-time onboarding
    if User.objects.count() == 0:
        return redirect("core:signup")

    prefs = auth_service.get_operator_preferences()
    last_username = prefs.get("last_username", "")

    return render(request, "core/login.html", {
        "next_url": next_url,
        "last_username": last_username,
    })


def signup_view(request: HttpRequest) -> HttpResponse:
    """Operator Registration / Onboarding view with optional ITMS account linking."""
    if request.user.is_authenticated:
        return redirect("core:dashboard")

    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip()
        password = request.POST.get("password", "").strip()
        password_confirm = request.POST.get("password_confirm", "").strip()

        connect_itms = bool(request.POST.get("connect_itms"))
        itms_email = request.POST.get("itms_email", "").strip()
        itms_password = request.POST.get("itms_password", "").strip()

        if password != password_confirm:
            return render(request, "core/signup.html", {
                "error": "Passwords do not match.",
                "form_data": request.POST,
            })

        user, err = auth_service.create_operator_account(
            username=username,
            password=password,
            full_name=full_name,
            email=email,
        )
        if user is None:
            return render(request, "core/signup.html", {
                "error": err,
                "form_data": request.POST,
            })

        auth_login(request, user)
        auth_service.save_remembered_session(user, remember=True)

        if connect_itms and itms_email and itms_password:
            ok, itms_msg = auth_service.connect_itms_account(itms_email, itms_password)
            if ok:
                messages.success(request, f"Connected to ITMS WebApp as '{itms_email}'.")
            else:
                messages.warning(request, f"Account created, but ITMS connection note: {itms_msg}")

        return redirect("core:dashboard")

    return render(request, "core/signup.html", {"form_data": {}})


def logout_view(request: HttpRequest) -> HttpResponse:
    """Signs out of operator session and redirects to login."""
    auth_service.clear_remembered_session()
    auth_logout(request)
    return redirect("core:login")


# ============================================================================
# ITMS WebApp Account Connection REST APIs
# ============================================================================

@require_GET
def api_itms_status(request: HttpRequest) -> JsonResponse:
    """Returns current ITMS WebApp connection status and active user identity."""
    status = auth_service.get_itms_status()
    return JsonResponse({"success": True, "status": status})


@csrf_exempt
@require_POST
def api_itms_connect(request: HttpRequest) -> JsonResponse:
    """Connects and authenticates against live ITMS WebApp (stock.itms.ug)."""
    email = request.POST.get("email")
    password = request.POST.get("password")
    base_url = request.POST.get("base_url")

    if not email and request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            email = body.get("email")
            password = body.get("password")
            base_url = body.get("base_url")
        except Exception:
            pass

    if not email or not password:
        return JsonResponse({"success": False, "error": "ITMS email and password are required."}, status=400)

    ok, msg = auth_service.connect_itms_account(email, password, base_url)
    status = auth_service.get_itms_status()
    return JsonResponse({
        "success": ok,
        "message": msg,
        "status": status,
    }, status=200 if ok else 401)


@csrf_exempt
@require_POST
def api_itms_disconnect(request: HttpRequest) -> JsonResponse:
    """Disconnects from ITMS WebApp by clearing local session cookies."""
    try:
        from core.services.itms_web_client import get_web_client
        client = get_web_client()
        client.logout()
    except Exception as exc:
        logger.warning("ITMS disconnect error: %s", exc)

    return JsonResponse({
        "success": True,
        "message": "Disconnected from ITMS WebApp.",
        "status": auth_service.get_itms_status(),
    })


# ============================================================================
# Dashboard HTML View (Protected by @login_required)
# ============================================================================

@login_required(login_url="/login/")
def dashboard_view(request: HttpRequest) -> HttpResponse:
    """
    Renders the primary Operator Console (Single-Page Web Application).
    Provides all tools needed to review evidence, match plates, and submit to ITMS.
    """
    db_info = config_service.get_active_database_info()
    dry_run = config_service.get_setting("submission.dry_run_mode", True)
    itms_status = auth_service.get_itms_status()

    context = {
        "app_title": "ITMS Verification Copilot",
        "current_user": request.user,
        "database_display": db_info.get("display", "SQLite"),
        "database_badge": db_info.get("badge", "SQLite"),
        "dry_run": dry_run,
        "itms_status": itms_status,
        "total_orders": InstallationOrder.objects.count(),
        "total_pairs": VehicleInstallationPair.objects.count(),
        "app_version": __version__,
    }
    return render(request, "core/dashboard.html", context)


# ============================================================================
# Operational Metrics & Statistics API
# ============================================================================

@require_GET
def api_stats(request: HttpRequest) -> JsonResponse:
    """Returns real-time operational summary metrics for the header ribbon and dashboard."""
    from django.utils import timezone
    today = timezone.localdate()

    total_orders = InstallationOrder.objects.count()
    pending_orders = InstallationOrder.objects.filter(status=InstallationOrder.Status.PENDING).count()
    total_pairs = VehicleInstallationPair.objects.count()
    pending_review = VehicleInstallationPair.objects.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
    ).count()
    approved = VehicleInstallationPair.objects.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
    ).count()
    submitted = VehicleInstallationPair.objects.filter(
        verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED
    ).count()
    issues = VehicleInstallationPair.objects.filter(
        verification_status__in=[
            VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            VehicleInstallationPair.VerificationStatus.CONFLICT,
            VehicleInstallationPair.VerificationStatus.UNREGISTERED,
            VehicleInstallationPair.VerificationStatus.FAILED,
            VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
        ]
    ).count()
    total_photos = EvidenceImage.objects.count()
    batches_count = IngestionBatch.objects.count()

    today_pairs_qs = VehicleInstallationPair.objects.filter(
        Q(created_at__date=today) |
        Q(front_image__batch__created_at__date=today) |
        Q(rear_image__batch__created_at__date=today)
    )
    shift_stats = {
        "date": today.strftime("%Y-%m-%d"),
        "total_pairs": today_pairs_qs.count(),
        "pending_review": today_pairs_qs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        ).count(),
        "approved": today_pairs_qs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.APPROVED
        ).count(),
        "submitted": today_pairs_qs.filter(
            verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED
        ).count(),
        "issues": today_pairs_qs.filter(
            verification_status__in=[
                VehicleInstallationPair.VerificationStatus.INCOMPLETE,
                VehicleInstallationPair.VerificationStatus.CONFLICT,
                VehicleInstallationPair.VerificationStatus.UNREGISTERED,
                VehicleInstallationPair.VerificationStatus.FAILED,
                VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
            ]
        ).count(),
        "batches_count": IngestionBatch.objects.filter(created_at__date=today).count(),
        "total_photos": EvidenceImage.objects.filter(
            Q(ingested_at__date=today) | Q(batch__created_at__date=today)
        ).count(),
    }

    dry_run = config_service.get_setting("submission.dry_run_mode", True)
    db_info = config_service.get_active_database_info()

    stats_payload = {
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "total_pairs": total_pairs,
        "pending_review": pending_review,
        "approved": approved,
        "submitted": submitted,
        "issues": issues,
        "total_photos": total_photos,
        "batches_count": batches_count,
        "shift": shift_stats,
    }

    return JsonResponse({
        "success": True,
        "stats": stats_payload,
        **stats_payload,
        "dry_run": dry_run,
        "database": db_info.get("name", "db.sqlite3"),
        "database_vendor": db_info.get("vendor", "sqlite"),
    })


# ============================================================================
# Work Queue & Pair Details REST APIs
# ============================================================================

@require_GET
def api_pairs_list(request: HttpRequest) -> JsonResponse:
    """Returns filtered list of vehicle installation pairs for the work queue with batch clarity."""
    from django.utils import timezone
    today = timezone.localdate()

    status_filter = request.GET.get("status", "ALL").upper().strip()
    batch_filter = request.GET.get("batch", "ALL").strip()
    scope = request.GET.get("scope", "").upper().strip()
    search = request.GET.get("search", "").strip()
    limit = int(request.GET.get("limit", 250))

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
        front_url = f"/media/{p.front_image.vault_file}" if p.front_image and p.front_image.vault_file else None
        rear_url = f"/media/{p.rear_image.vault_file}" if p.rear_image and p.rear_image.vault_file else None

        batch_obj = (p.front_image.batch if p.front_image and p.front_image.batch else None) or (
            p.rear_image.batch if p.rear_image and p.rear_image.batch else None
        )
        is_today = bool(batch_obj and batch_obj.created_at and batch_obj.created_at.date() == today)
        is_latest = bool(batch_obj and latest_batch and batch_obj.id == latest_batch.id)
        is_carryover = bool(batch_obj and batch_obj.created_at and batch_obj.created_at.date() < today)

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
            "batch_id": batch_obj.batch_id if batch_obj else "Carryover",
            "batch_label": batch_obj.batch_id if batch_obj else "Carryover Batch",
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
    pair = get_object_or_404(
        VehicleInstallationPair.objects.select_related(
            "order", "front_image", "rear_image", "front_image__batch", "rear_image__batch"
        ),
        id=pair_id,
    )

    latest_batch = IngestionBatch.objects.order_by("-created_at").first()
    batch_obj = (pair.front_image.batch if pair.front_image and pair.front_image.batch else None) or (
        pair.rear_image.batch if pair.rear_image and pair.rear_image.batch else None
    )

    front = None
    if pair.front_image:
        vault_path = str(pair.front_image.vault_file).replace("\\", "/")
        front = {
            "id": str(pair.front_image.id),
            "url": f"/media/{vault_path}",
            "detected_plate": pair.front_image.detected_plate,
            "ocr_confidence": round(pair.front_image.ocr_confidence * 100, 1) if pair.front_image.ocr_confidence is not None else None,
            "detector_confidence": round(pair.front_image.detector_confidence * 100, 1) if pair.front_image.detector_confidence is not None else None,
            "captured_at": pair.front_image.captured_at.strftime("%H:%M:%S") if pair.front_image.captured_at else None,
        }

    rear = None
    if pair.rear_image:
        vault_path = str(pair.rear_image.vault_file).replace("\\", "/")
        rear = {
            "id": str(pair.rear_image.id),
            "url": f"/media/{vault_path}",
            "detected_plate": pair.rear_image.detected_plate,
            "ocr_confidence": round(pair.rear_image.ocr_confidence * 100, 1) if pair.rear_image.ocr_confidence is not None else None,
            "detector_confidence": round(pair.rear_image.detector_confidence * 100, 1) if pair.rear_image.detector_confidence is not None else None,
            "captured_at": pair.rear_image.captured_at.strftime("%H:%M:%S") if pair.rear_image.captured_at else None,
        }

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


# ============================================================================
# Operator Action REST API (Approve, Swap, Edit, Link, Submit)
# ============================================================================

@csrf_exempt
def api_pair_action(request: HttpRequest, pair_id: int) -> JsonResponse:
    """Executes an operator decision or action on a pair."""
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST required"}, status=405)

    pair = get_object_or_404(VehicleInstallationPair, id=pair_id)

    action = request.POST.get("action")
    body_data = {}
    if not action and request.content_type == "application/json" and request.body:
        try:
            body_data = json.loads(request.body.decode("utf-8"))
            action = body_data.get("action")
        except Exception:
            pass

    if not action:
        return JsonResponse({"success": False, "error": "No action specified"}, status=400)

    action = action.lower().strip()

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
            action=SubmissionAuditLog.Action.OPERATOR_SWAP,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message="Operator swapped front and rear image assignments via Web Console.",
        )
        return JsonResponse({
            "success": True,
            "message": f"Swapped front and rear photos for {pair.registration_number_detected}.",
        })

    elif action in ("edit_plate", "manual_override"):
        raw_plate = request.POST.get("plate") or body_data.get("plate", "")
        new_status = request.POST.get("verification_status") or body_data.get("verification_status")
        operator_note = request.POST.get("operator_note") or body_data.get("operator_note")

        updated_fields = []
        if raw_plate and raw_plate.strip():
            clean_plate = raw_plate.strip().upper()
            canonical = normalizer.canonicalize(clean_plate)
            pair.manual_plate_override = clean_plate
            pair.registration_number_detected = canonical
            pair.is_manual_override = True
            pair.matched_via = VehicleInstallationPair.MatchedVia.MANUAL

            # Re-run fuzzy order matcher with updated plate string if not already locked
            outcome = order_matcher.find_best_match(canonical)
            if outcome and outcome.order:
                pair.order = outcome.order
                pair.match_type = outcome.match_type
                pair.match_score = outcome.score
            updated_fields.extend(["manual_plate_override", "registration_number_detected", "is_manual_override", "matched_via", "order", "match_type", "match_score"])

        if new_status and new_status in VehicleInstallationPair.VerificationStatus.values:
            pair.verification_status = new_status
            updated_fields.append("verification_status")

        if operator_note is not None:
            pair.operator_note = str(operator_note).strip()
            updated_fields.append("operator_note")

        if updated_fields:
            pair.save(update_fields=list(set(updated_fields)))

        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.OPERATOR_OVERRIDE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator override on pair #{pair.id}: plate='{pair.registration_number_detected}', status='{pair.verification_status}', note='{pair.operator_note}'.",
        )
        return JsonResponse({
            "success": True,
            "message": f"Pair {pair.registration_number_detected} updated.",
            "registration_number_detected": pair.registration_number_detected,
            "verification_status": pair.verification_status,
            "operator_note": pair.operator_note,
            "match_type": pair.match_type,
            "match_score": pair.match_score,
            "order_number": pair.order.order_number if pair.order else None,
        })

    elif action == "link_order":
        order_id = request.POST.get("order_id") or body_data.get("order_id")
        order_number = request.POST.get("order_number") or body_data.get("order_number")

        target_order = None
        if order_id:
            target_order = get_object_or_404(InstallationOrder, id=order_id)
        elif order_number:
            target_order = get_object_or_404(InstallationOrder, order_number=order_number)
        else:
            return JsonResponse({"success": False, "error": "order_id or order_number required."}, status=400)

        pair.order = target_order
        is_exact = (normalizer.canonicalize(target_order.registration_number) == pair.registration_number_detected)
        pair.match_type = VehicleInstallationPair.MatchType.EXACT if is_exact else VehicleInstallationPair.MatchType.FUZZY
        pair.match_score = 100.0 if is_exact else 90.0
        pair.matched_via = VehicleInstallationPair.MatchedVia.MANUAL
        pair.is_manual_override = True
        pair.save(update_fields=["order", "match_type", "match_score", "matched_via", "is_manual_override"])

        SubmissionAuditLog.objects.create(
            pair=pair,
            action=SubmissionAuditLog.Action.OPERATOR_OVERRIDE,
            result=SubmissionAuditLog.ResultStatus.SUCCESS,
            message=f"Operator manually linked pair to order #{target_order.order_number} ({target_order.registration_number}) via Web Console.",
        )
        return JsonResponse({
            "success": True,
            "message": f"Linked to order #{target_order.order_number}.",
            "order_number": target_order.order_number,
        })

    elif action == "unlink_order":
        prev_order = pair.order
        pair.order = None
        pair.match_type = VehicleInstallationPair.MatchType.MANUAL
        pair.match_score = 0.0
        pair.is_manual_override = True
        pair.save(update_fields=["order", "match_type", "match_score", "is_manual_override"])

        SubmissionAuditLog.objects.create(
            pair=pair,
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

        outcome = submission_worker.submit_pair(pair, dry_run=is_dry_run)
        return JsonResponse({
            "success": outcome.success,
            "status": pair.verification_status,
            "token": outcome.token,
            "error": outcome.error,
            "dry_run": is_dry_run,
            "message": f"{'[DRY RUN] ' if is_dry_run else ''}Submitted successfully to ITMS." if outcome.success else f"Submission failed: {outcome.error}",
        })

    return JsonResponse({"success": False, "error": f"Unknown action: {action}"}, status=400)


# ============================================================================
# Order Registry Search & Live Synchronization
# ============================================================================

@require_GET
def api_orders_list(request: HttpRequest) -> JsonResponse:
    """Returns list of installation orders for search / manual linking dropdowns."""
    search = request.GET.get("search", "").strip()
    limit = int(request.GET.get("limit", 50))

    qs = InstallationOrder.objects.all()
    if search:
        qs = qs.filter(
            Q(order_number__icontains=search) |
            Q(registration_number__icontains=search) |
            Q(vin__icontains=search)
        )

    orders = [
        {
            "id": o.id,
            "order_number": o.order_number,
            "registration_number": o.registration_number,
            "vin": o.vin,
            "warehouse_name": o.warehouse_name,
            "status": o.status,
        }
        for o in qs[:limit]
    ]
    return JsonResponse({"success": True, "orders": orders})


@csrf_exempt
@require_POST
def api_sync_orders(request: HttpRequest) -> JsonResponse:
    """Triggers background order sync from ITMS WebApp or seeds mock orders."""
    started = pipeline_runner.start_pipeline("sync_orders")
    if not started:
        return JsonResponse({
            "success": False,
            "message": "Another task is already running in background.",
        }, status=409)

    return JsonResponse({
        "success": True,
        "message": "Order synchronization started in background.",
    })


# ============================================================================
# ITMS WebApp Orders & Archive Explorer REST APIs
# ============================================================================

@require_GET
def api_itms_orders_explorer(request: HttpRequest) -> JsonResponse:
    """
    ITMS Orders & Archive Explorer API.
    Supports querying Active Orders (/installation-orders/index) and
    Completed Archive (/installation-orders/archive) from live ITMS or local cache.
    """
    tab = request.GET.get("tab", "active").strip().lower()  # 'active' or 'archive'
    is_archive = (tab == "archive")
    source = request.GET.get("source", "auto").strip().lower()  # 'auto', 'live', or 'local'
    search = request.GET.get("search", "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        limit = min(100, max(5, int(request.GET.get("limit", 20))))
    except (ValueError, TypeError):
        limit = 20

    client = get_web_client()
    session_valid = client.session_store.session.is_cookie_valid()

    should_try_live = (source == "live") or (source == "auto" and session_valid)

    if should_try_live:
        try:
            live_result = client.fetch_installation_orders(
                page=page,
                search_params=search if search else None,
                archive=is_archive,
            )
            if live_result.get("success"):
                raw_orders = live_result.get("orders", [])
                # Auto-sync to local database so records are saved/updated
                try:
                    client.sync_orders_to_local_db(raw_orders)
                except Exception as exc:
                    logger.warning("Auto-sync error during orders explorer: %s", exc)

                # Annotate each order with local verification pair if one exists
                enriched_orders = []
                for o in raw_orders:
                    order_num = o.get("order_number", "")
                    reg_num = o.get("registration_number", "")
                    canonical_reg = normalizer.canonicalize(reg_num) if reg_num else ""
                    pair = None
                    if order_num:
                        pair = VehicleInstallationPair.objects.filter(order__order_number=order_num).first()
                    if not pair and canonical_reg:
                        pair = VehicleInstallationPair.objects.filter(registration_number_detected=canonical_reg).first()

                    o_dict = dict(o)
                    o_dict["matched_pair_id"] = pair.id if pair else None
                    o_dict["verification_status"] = pair.verification_status if pair else None
                    enriched_orders.append(o_dict)

                return JsonResponse({
                    "success": True,
                    "source": "live",
                    "tab": tab,
                    "page": page,
                    "count": len(enriched_orders),
                    "has_next_page": live_result.get("has_next_page", False),
                    "summary": live_result.get("summary") or f"Live Page {page} ({len(enriched_orders)} items)",
                    "orders": enriched_orders,
                    "itms_connected": True,
                })
            elif source == "live":
                return JsonResponse({
                    "success": False,
                    "error": live_result.get("error", "Failed to fetch orders from live ITMS."),
                    "source": "live",
                    "orders": [],
                    "itms_connected": session_valid,
                }, status=502)
        except Exception as exc:
            logger.exception("Error querying live ITMS orders: %s", exc)
            if source == "live":
                return JsonResponse({
                    "success": False,
                    "error": f"Live ITMS connection error: {exc}",
                    "source": "live",
                    "orders": [],
                    "itms_connected": session_valid,
                }, status=502)

    # Fallback to Local Database query
    qs = InstallationOrder.objects.filter(is_archived=is_archive)
    if search:
        qs = qs.filter(
            Q(order_number__icontains=search) |
            Q(registration_number__icontains=search) |
            Q(vin__icontains=search) |
            Q(warehouse_name__icontains=search) |
            Q(installation_officer__icontains=search)
        )

    total_count = qs.count()
    total_pages = max(1, math.ceil(total_count / limit))
    offset = (page - 1) * limit
    paged_qs = qs.order_by("-updated_at", "-created_at")[offset : offset + limit]

    local_orders = []
    for o in paged_qs:
        pair = VehicleInstallationPair.objects.filter(
            Q(order=o) | Q(registration_number_detected=o.registration_number)
        ).first()

        local_orders.append({
            "id": o.id,
            "order_number": o.order_number,
            "registration_number": o.registration_number,
            "vin": o.vin,
            "sales_order": o.sales_order,
            "service_type": o.service_type,
            "warehouse": o.warehouse_name,
            "officer": o.installation_officer,
            "installation_date": o.installation_date,
            "status": o.order_status or o.status,
            "registration_status": o.registration_status,
            "is_archived": o.is_archived,
            "itms_stage": o.itms_stage,
            "matched_pair_id": pair.id if pair else None,
            "verification_status": pair.verification_status if pair else None,
            "has_photos": bool(o.front_photo_url or o.rear_photo_url or o.photos_json),
            "has_tracker": bool(o.gps_tracker_id),
        })

    summary_text = f"Showing {offset + 1} to {min(offset + len(local_orders), total_count)} of {total_count} local records" if total_count > 0 else "0 orders found"

    return JsonResponse({
        "success": True,
        "source": "local",
        "tab": tab,
        "page": page,
        "total_count": total_count,
        "total_pages": total_pages,
        "has_next_page": page < total_pages,
        "has_prev_page": page > 1,
        "summary": summary_text,
        "orders": local_orders,
        "itms_connected": session_valid,
    })


@require_GET
def api_itms_order_detail(request: HttpRequest, order_ident: str) -> JsonResponse:
    """
    Returns full metadata, telematics (GPS tracker, front/rear beacons),
    plate serials, and photo URLs for a specific order.
    Optionally fetches live details and downloads photos from ITMS.
    """
    order_ident = order_ident.strip()
    download = request.GET.get("download") in ("1", "true", "yes")
    force_live = request.GET.get("live") in ("1", "true", "yes")

    # Look up in local database first
    order = InstallationOrder.objects.filter(
        Q(order_number__iexact=order_ident) |
        Q(registration_number__iexact=normalizer.canonicalize(order_ident) or order_ident) |
        Q(itms_order_uuid__iexact=order_ident) |
        Q(vin__iexact=order_ident)
    ).first()

    client = get_web_client()
    session_valid = client.session_store.session.is_cookie_valid()

    # If missing hardware/photos, or force_live requested, and session is valid:
    needs_live = force_live or (order and (not order.gps_tracker_id and not order.front_photo_url)) or not order
    if needs_live and session_valid:
        try:
            live_info = client.fetch_order_info(order_ident, download_photos=download)
            if live_info.get("success"):
                order_num = live_info.get("order_number") or (order.order_number if order else order_ident)
                reg_num = live_info.get("registration_number") or (order.registration_number if order else "")
                front_plate = live_info.get("front_plate", {})
                rear_plate = live_info.get("rear_plate", {})
                gps = live_info.get("gps_tracker", {})
                front_beacon = live_info.get("front_beacon", {})
                rear_beacon = live_info.get("rear_beacon", {})

                defaults = {
                    "registration_number": normalizer.canonicalize(reg_num) or reg_num,
                    "vin": live_info.get("vin") or (order.vin if order else ""),
                    "warehouse_name": live_info.get("warehouse") or (order.warehouse_name if order else ""),
                    "installation_officer": live_info.get("installed_by") or (order.installation_officer if order else ""),
                    "front_plate_serial": front_plate.get("serial", ""),
                    "rear_plate_serial": rear_plate.get("serial", ""),
                    "front_plate_type": front_plate.get("type", ""),
                    "rear_plate_type": rear_plate.get("type", ""),
                    "gps_tracker_id": gps.get("device_id", ""),
                    "front_beacon_id": front_beacon.get("device_id", ""),
                    "rear_beacon_id": rear_beacon.get("device_id", ""),
                    "front_photo_url": live_info.get("front_photo_url", ""),
                    "rear_photo_url": live_info.get("rear_photo_url", ""),
                    "photos_json": live_info.get("photos", []),
                    "details_json": live_info.get("raw_details", {}),
                    "itms_order_uuid": live_info.get("order_uuid", ""),
                    "info_fetched_at": timezone.now(),
                }
                order, _ = InstallationOrder.objects.update_or_create(
                    order_number=order_num,
                    defaults=defaults,
                )
        except Exception as exc:
            logger.warning("Error fetching live order info for %s: %s", order_ident, exc)

    if not order:
        return JsonResponse({"success": False, "error": f"Order '{order_ident}' not found locally or on live ITMS."}, status=404)

    pair = VehicleInstallationPair.objects.filter(
        Q(order=order) | Q(registration_number_detected=order.registration_number)
    ).first()

    pair_data = None
    if pair:
        pair_data = {
            "id": pair.id,
            "verification_status": pair.verification_status,
            "match_score": pair.match_score,
            "match_type": pair.match_type,
            "has_front_photo": bool(pair.front_image),
            "has_rear_photo": bool(pair.rear_image),
        }

    return JsonResponse({
        "success": True,
        "order": {
            "id": order.id,
            "order_number": order.order_number,
            "registration_number": order.registration_number,
            "vin": order.vin,
            "sales_order": order.sales_order,
            "service_type": order.service_type,
            "warehouse_name": order.warehouse_name,
            "installation_officer": order.installation_officer,
            "installation_date": order.installation_date,
            "order_status": order.order_status or order.status,
            "registration_status": order.registration_status,
            "is_archived": order.is_archived,
            "itms_stage": order.itms_stage,
            "itms_order_uuid": order.itms_order_uuid,
            "hardware": {
                "gps_tracker_id": order.gps_tracker_id,
                "front_beacon_id": order.front_beacon_id,
                "rear_beacon_id": order.rear_beacon_id,
                "front_plate_serial": order.front_plate_serial,
                "front_plate_type": order.front_plate_type,
                "rear_plate_serial": order.rear_plate_serial,
                "rear_plate_type": order.rear_plate_type,
            },
            "photos": order.photos_json or (
                ([{"label": "Front Plate", "url": order.front_photo_url, "orientation": "FRONT"}] if order.front_photo_url else []) +
                ([{"label": "Rear Plate", "url": order.rear_photo_url, "orientation": "REAR"}] if order.rear_photo_url else [])
            ),
            "front_photo_url": order.front_photo_url,
            "rear_photo_url": order.rear_photo_url,
            "details_json": order.details_json,
            "matched_pair": pair_data,
        }
    })


@csrf_exempt
@require_POST
def api_itms_sync_now(request: HttpRequest) -> JsonResponse:
    """
    Directly triggers synchronization of active orders and/or archive orders
    from stock.itms.ug into the local database.
    """
    tab = request.POST.get("tab", "active").strip().lower()
    try:
        pages = max(1, min(5, int(request.POST.get("pages", 1))))
    except (ValueError, TypeError):
        pages = 1

    if request.content_type == "application/json" and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            tab = body.get("tab", tab)
            pages = max(1, min(5, int(body.get("pages", pages))))
        except Exception:
            pass

    client = get_web_client()
    if not client.session_store.session.is_cookie_valid():
        return JsonResponse({
            "success": False,
            "error": "ITMS WebApp session is not connected or has expired. Please connect your ITMS account.",
        }, status=401)

    total_created = 0
    total_updated = 0
    total_installed = 0
    total_fetched = 0

    do_active = tab in ("active", "both")
    do_archive = tab in ("archive", "both")

    for p in range(1, pages + 1):
        if do_active:
            res = client.fetch_installation_orders(page=p, archive=False)
            if res.get("success"):
                sync_res = client.sync_orders_to_local_db(res.get("orders", []))
                total_created += sync_res.get("created", 0)
                total_updated += sync_res.get("updated", 0)
                total_installed += sync_res.get("installed_verified", 0)
                total_fetched += sync_res.get("total", 0)

        if do_archive:
            res = client.fetch_archive_orders(page=p)
            if res.get("success"):
                sync_res = client.sync_orders_to_local_db(res.get("orders", []))
                total_created += sync_res.get("created", 0)
                total_updated += sync_res.get("updated", 0)
                total_installed += sync_res.get("installed_verified", 0)
                total_fetched += sync_res.get("total", 0)

    return JsonResponse({
        "success": True,
        "created": total_created,
        "updated": total_updated,
        "installed_verified": total_installed,
        "total_fetched": total_fetched,
        "message": f"Successfully synced {total_fetched} orders ({total_created} new, {total_updated} updated).",
    })


# ============================================================================
# Background Pipeline Execution & Status Polling
# ============================================================================

@csrf_exempt
@require_POST
def api_run_pipeline(request: HttpRequest) -> JsonResponse:
    """Triggers background AI vision detection and pair matching."""
    task_type = request.POST.get("task_type", "full_pipeline").strip()
    batch_id = request.POST.get("batch_id", "").strip() or None
    if task_type not in ("full_pipeline", "vision", "matcher"):
        task_type = "full_pipeline"

    started = pipeline_runner.start_pipeline(task_type, batch_id=batch_id)
    if not started:
        return JsonResponse({
            "success": False,
            "message": "A pipeline task is already currently running.",
        }, status=409)

    return JsonResponse({
        "success": True,
        "message": f"Started {task_type.replace('_', ' ')} in background.",
    })


@require_GET
def api_pipeline_status(request: HttpRequest) -> JsonResponse:
    """Returns current execution progress, stage, and recent logs from background runner."""
    status = pipeline_runner.get_status()
    return JsonResponse(status)


# ============================================================================
# Batch Submissions & Shift Reporting
# ============================================================================

@csrf_exempt
@require_POST
def api_batch_submit(request: HttpRequest) -> JsonResponse:
    """Submits approved pairs to ITMS in a single operation, scoped to shift or batch."""
    from django.utils import timezone
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
    )

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
    for p in approved_pairs:
        outcome = submission_worker.submit_pair(p, dry_run=is_dry_run)
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


@require_GET
def api_export_report(request: HttpRequest) -> HttpResponse:
    """Exports shift verification report as a clean downloadable CSV file."""
    pairs = (
        VehicleInstallationPair.objects.all()
        .select_related("order", "front_image", "rear_image")
        .order_by("-updated_at")
    )

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Pair ID",
        "Detected Plate",
        "Verification Status",
        "Match Type",
        "Match Score (%)",
        "Matched Via",
        "Order Number",
        "Order Plate",
        "VIN",
        "Warehouse",
        "Front Photo",
        "Rear Photo",
        "Manual Override",
        "Submitted At",
        "Updated At",
    ])

    for p in pairs:
        writer.writerow([
            p.id,
            p.registration_number_detected,
            p.verification_status,
            p.match_type,
            f"{p.match_score:.1f}" if p.match_score is not None else "",
            p.matched_via,
            p.order.order_number if p.order else "",
            p.order.registration_number if p.order else "",
            p.order.vin if p.order else "",
            p.order.warehouse_name if p.order else "",
            p.front_image.vault_file if p.front_image else "",
            p.rear_image.vault_file if p.rear_image else "",
            "YES" if p.is_manual_override else "NO",
            p.submitted_at.strftime("%Y-%m-%d %H:%M:%S") if p.submitted_at else "",
            p.updated_at.strftime("%Y-%m-%d %H:%M:%S") if p.updated_at else "",
        ])

    response = HttpResponse(out.getvalue(), content_type="text/csv")
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    response["Content-Disposition"] = f'attachment; filename="itms_shift_report_{date_str}.csv"'
    return response


# ============================================================================
# System Configuration & Preferences API
# ============================================================================

@require_GET
def api_settings(request: HttpRequest) -> JsonResponse:
    """Returns current system configuration and safety controls."""
    cfg = config_service.load_config()
    db_info = config_service.get_active_database_info()
    return JsonResponse({
        "success": True,
        "dry_run": cfg.get("submission", {}).get("dry_run_mode", True),
        "submit_step3": cfg.get("submission", {}).get("submit_step3", True),
        "database": db_info.get("display", "SQLite"),
        "active_engine": db_info.get("vendor", "sqlite"),
        "version": __version__,
        "version_tag": f"v{__version__}",
    })


@csrf_exempt
@require_POST
def api_toggle_dry_run(request: HttpRequest) -> JsonResponse:
    """Toggles or sets safe simulation / dry-run mode for ITMS submissions."""
    mode_param = request.POST.get("mode") or request.POST.get("dry_run")
    if mode_param is not None and str(mode_param).strip() != "":
        new_val = str(mode_param).strip().lower() in ("true", "1", "yes", "dry_run", "dry")
    else:
        curr = config_service.get_setting("submission.dry_run_mode", True)
        new_val = not curr

    config_service.set_setting("submission.dry_run_mode", new_val)
    setattr(settings, "ITMS_WEB_DRY_RUN", new_val)
    return JsonResponse({
        "success": True,
        "dry_run": new_val,
        "dry_run_mode": new_val,
        "message": f"Simulation mode {'ENABLED' if new_val else 'DISABLED'}.",
    })


@require_GET
def api_check_updates(request: HttpRequest) -> JsonResponse:
    """Checks GitHub Releases for updates."""
    force = request.GET.get("force", "false").lower() in ("true", "1", "yes")
    result = update_service.check_for_updates(force=force)
    return JsonResponse(result)


@csrf_exempt
@require_POST
def api_apply_update(request: HttpRequest) -> JsonResponse:
    """Safely applies pending update from GitHub Releases."""
    result = update_service.apply_update()
    status_code = 200 if result.get("success") else 400
    return JsonResponse(result, status=status_code)


# ============================================================================
# Photo Ingestion Views (Existing & Web Dropzone)
# ============================================================================

@login_required(login_url="/login/")
def upload_photos_view(request: HttpRequest) -> HttpResponse:
    """
    Web Upload Interface for operators to drag-and-drop or select photos.
    Supports separate front and rear photo uploads as well as general batches.
    """
    if request.method == "POST":
        front_files = request.FILES.getlist("front_photos")
        rear_files = request.FILES.getlist("rear_photos")
        general_files = request.FILES.getlist("photos")
        batch_label = request.POST.get("batch_label", "").strip() or "Web Upload"

        total_count = len(front_files) + len(rear_files) + len(general_files)
        if total_count == 0:
            if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
                return JsonResponse({"success": False, "error": "No image files selected."}, status=400)
            messages.error(request, "No image files were selected for upload.")
            return render(request, "core/upload.html")

        batch = vault_service.create_ingestion_batch(
            source_type=IngestionBatch.SourceType.WEB,
            source_label=batch_label,
        )

        results = []

        # Process FRONT photos
        for f in front_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="FRONT")
            results.append({
                "name": f.name,
                "status": status,
                "orientation": "FRONT",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        # Process REAR photos
        for f in rear_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="REAR")
            results.append({
                "name": f.name,
                "status": status,
                "orientation": "REAR",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        # Process General photos (detect orientation from filename or path)
        for f in general_files:
            img, status = vault_service.ingest_uploaded_file(f, batch=batch)
            results.append({
                "name": f.name,
                "status": status,
                "orientation": img.orientation if img else "",
                "id": str(img.id) if img else None,
                "vault_file": img.vault_file if img else None,
            })

        batch.refresh_from_db()
        front_count = batch.images.filter(orientation="FRONT").count()
        rear_count = batch.images.filter(orientation="REAR").count()
        is_symmetric = (front_count == rear_count and front_count > 0)
        discrepancy = abs(front_count - rear_count)

        count_warning = ""
        if front_count > 0 and rear_count > 0 and not is_symmetric:
            missing_side = "REAR" if front_count > rear_count else "FRONT"
            count_warning = f"⚠️ Photo count mismatch: {front_count} Front vs {rear_count} Rear ({discrepancy} missing from {missing_side}). Pairs will be incomplete."
            messages.warning(request, count_warning)
        elif front_count > 0 and rear_count > 0 and is_symmetric:
            messages.success(request, f"✓ Symmetric batch validated: {front_count} Front and {rear_count} Rear photos uploaded into {batch.batch_id} (1:1 ratio).")
        elif front_count > 0 and rear_count == 0:
            count_warning = f"⚠️ Incomplete upload: {front_count} Front photos uploaded without any Rear photos."
            messages.warning(request, count_warning)
        elif rear_count > 0 and front_count == 0:
            count_warning = f"⚠️ Incomplete upload: {rear_count} Rear photos uploaded without any Front photos."
            messages.warning(request, count_warning)

        # Automatically execute initial association on newly uploaded batch
        try:
            from core.matcher import association
            association.run_association(batch_id=batch.batch_id)
        except Exception:
            pass

        # AJAX / JSON response
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", ""):
            return JsonResponse({
                "success": True,
                "batch_id": batch.batch_id,
                "batch_label": batch.source_label,
                "total_files": batch.total_files,
                "ingested": batch.ingested_count,
                "duplicates": batch.duplicate_count,
                "failed": batch.failed_count,
                "front_count": front_count,
                "rear_count": rear_count,
                "is_symmetric": is_symmetric,
                "discrepancy": discrepancy,
                "count_warning": count_warning,
                "results": results,
            })

        # Regular HTML response
        context = {
            "batch": batch,
            "front_count": front_count,
            "rear_count": rear_count,
            "is_symmetric": is_symmetric,
            "discrepancy": discrepancy,
            "count_warning": count_warning,
            "results": results,
            "success": True,
        }
        return render(request, "core/upload.html", context)

    recent_batches = IngestionBatch.objects.all().order_by("-created_at")[:10]
    return render(request, "core/upload.html", {"recent_batches": recent_batches})


@csrf_exempt
def api_upload_photos(request: HttpRequest) -> JsonResponse:
    """REST API endpoint for multi-photo upload from curl or third-party tools."""
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed. Use POST."}, status=405)

    front_files = request.FILES.getlist("front_photos")
    rear_files = request.FILES.getlist("rear_photos")
    general_files = request.FILES.getlist("photos")

    if not (front_files or rear_files or general_files):
        return JsonResponse({"error": "No files provided under 'front_photos', 'rear_photos', or 'photos'."}, status=400)

    batch_label = request.POST.get("batch_label", "").strip() or "API Upload"
    batch = vault_service.create_ingestion_batch(
        source_type=IngestionBatch.SourceType.API,
        source_label=batch_label,
    )

    items = []
    for f in front_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="FRONT")
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": "FRONT",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    for f in rear_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch, orientation_override="REAR")
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": "REAR",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    for f in general_files:
        img, status = vault_service.ingest_uploaded_file(f, batch=batch)
        items.append({
            "filename": f.name,
            "status": status,
            "orientation": img.orientation if img else "",
            "id": str(img.id) if img else None,
            "vault_file": img.vault_file if img else None,
        })

    batch.refresh_from_db()
    front_count = batch.images.filter(orientation="FRONT").count()
    rear_count = batch.images.filter(orientation="REAR").count()
    is_symmetric = (front_count == rear_count and front_count > 0)
    discrepancy = abs(front_count - rear_count)

    # Automatic initial association
    try:
        from core.matcher import association
        association.run_association(batch_id=batch.batch_id)
    except Exception:
        pass

    return JsonResponse({
        "success": True,
        "batch_id": batch.batch_id,
        "source_type": batch.source_type,
        "source_label": batch.source_label,
        "total_files": batch.total_files,
        "ingested_count": batch.ingested_count,
        "duplicate_count": batch.duplicate_count,
        "failed_count": batch.failed_count,
        "front_count": front_count,
        "rear_count": rear_count,
        "is_symmetric": is_symmetric,
        "discrepancy": discrepancy,
        "items": items,
    }, status=201)


def api_batch_detail(request: HttpRequest, batch_id: str) -> JsonResponse:
    """Returns batch metadata and list of associated images with pair assignments."""
    batch = get_object_or_404(IngestionBatch, batch_id=batch_id)
    batch_images = list(batch.images.all())
    image_ids = [img.id for img in batch_images]

    # Find pairs associated with any image in this batch
    pairs = VehicleInstallationPair.objects.filter(
        Q(front_image_id__in=image_ids) | Q(rear_image_id__in=image_ids)
    ).select_related("order")

    pair_map = {}
    for p in pairs:
        if p.front_image_id:
            pair_map[p.front_image_id] = (p, "FRONT")
        if p.rear_image_id:
            pair_map[p.rear_image_id] = (p, "REAR")

    images = []
    for img in batch_images:
        p_info = None
        if img.id in pair_map:
            p, role = pair_map[img.id]
            p_info = {
                "pair_id": p.id,
                "detected_plate": p.registration_number_detected,
                "verification_status": p.verification_status,
                "role": role,
                "order_number": p.order.order_number if p.order else None,
                "order_plate": p.order.registration_number if p.order else None,
            }

        vault_path = str(img.vault_file).replace("\\", "/")
        images.append({
            "id": str(img.id),
            "file_name": Path(img.original_source_path or img.vault_file).name,
            "url": f"/media/{vault_path}",
            "vault_file": vault_path,
            "status": img.status,
            "orientation": img.orientation,
            "detected_plate": img.detected_plate,
            "ocr_confidence": round(img.ocr_confidence * 100, 1) if img.ocr_confidence is not None else None,
            "captured_at": img.captured_at.strftime("%Y-%m-%d %H:%M:%S") if img.captured_at else None,
            "file_size_kb": round(img.file_size_bytes / 1024, 1) if img.file_size_bytes else 0,
            "pair": p_info,
        })

    return JsonResponse({
        "success": True,
        "batch_id": batch.batch_id,
        "source_type": batch.source_type,
        "source_label": batch.source_label,
        "created_at": batch.created_at.strftime("%Y-%m-%d %H:%M:%S") if batch.created_at else "",
        "total_files": batch.total_files,
        "ingested_count": batch.ingested_count,
        "duplicate_count": batch.duplicate_count,
        "failed_count": batch.failed_count,
        "images": images,
    })


@require_GET
def api_batches_list(request: HttpRequest) -> JsonResponse:
    """Returns list of photo ingestion batches with shift scope filtering."""
    from django.utils import timezone
    today = timezone.localdate()
    latest_batch = IngestionBatch.objects.order_by("-created_at").first()
    scope = request.GET.get("scope", "TODAY").upper().strip()

    qs = IngestionBatch.objects.all().order_by("-created_at")
    total_batches = qs.count()
    today_batches_cnt = qs.filter(created_at__date=today).count()
    carryover_batches_cnt = qs.exclude(created_at__date=today).count()

    if scope == "TODAY":
        qs = qs.filter(created_at__date=today)
    elif scope == "ACTIVE_BATCH" and latest_batch:
        qs = qs.filter(id=latest_batch.id)
    elif scope == "CARRYOVER":
        qs = qs.exclude(created_at__date=today)

    batches = [
        {
            "id": str(b.id),
            "batch_id": b.batch_id,
            "source_type": b.source_type,
            "source_label": b.source_label,
            "created_at": b.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "created_date": b.created_at.strftime("%Y-%m-%d"),
            "is_today": bool(b.created_at and b.created_at.date() == today),
            "is_latest": bool(latest_batch and b.id == latest_batch.id),
            "total_files": b.total_files,
            "ingested_count": b.ingested_count,
            "duplicate_count": b.duplicate_count,
            "failed_count": b.failed_count,
        }
        for b in qs[:60]
    ]
    return JsonResponse({
        "success": True,
        "batches": batches,
        "scope": scope,
        "latest_batch_id": latest_batch.batch_id if latest_batch else None,
        "total_batches": total_batches,
        "today_batches": today_batches_cnt,
        "carryover_batches": carryover_batches_cnt,
    })


@require_GET
def api_history_list(request: HttpRequest) -> JsonResponse:
    """Returns historical verification events and audit logs for the History tab with date scoping."""
    from django.utils import timezone
    today = timezone.localdate()

    flt = request.GET.get("filter", "ALL").strip().upper()
    date_scope = request.GET.get("date_scope", "TODAY").strip().upper()

    history_items = []

    # 1. Direct SubmissionAuditLog entries
    logs_qs = (
        SubmissionAuditLog.objects.select_related("pair", "pair__order", "pair__front_image", "pair__rear_image")
        .order_by("-timestamp")
    )
    if date_scope == "TODAY":
        logs_qs = logs_qs.filter(timestamp__date=today)

    logs_qs = logs_qs[:150]

    for l in logs_qs:
        pair = l.pair
        plate = pair.registration_number_detected if pair else "N/A"
        order_no = pair.order.order_number if (pair and pair.order) else "N/A"
        order_plate = pair.order.registration_number if (pair and pair.order) else ""

        # Filter check
        if flt == "SUBMITTED" and l.action not in (SubmissionAuditLog.Action.SUBMIT,):
            continue
        if flt in ("FAILED", "ISSUES") and l.result not in (SubmissionAuditLog.ResultStatus.FAILURE, "FAILED"):
            continue

        front_url = f"/media/{str(pair.front_image.vault_file).replace('\\', '/')}" if (pair and pair.front_image) else None
        rear_url = f"/media/{str(pair.rear_image.vault_file).replace('\\', '/')}" if (pair and pair.rear_image) else None

        is_item_today = bool(l.timestamp and l.timestamp.date() == today)

        history_items.append({
            "id": f"log_{l.id}",
            "timestamp": l.timestamp.strftime("%Y-%m-%d %H:%M:%S") if l.timestamp else "",
            "is_today": is_item_today,
            "plate": plate,
            "order_number": order_no,
            "order_plate": order_plate,
            "action": l.action,
            "result": l.result,
            "message": l.message,
            "operator": getattr(l, "operator_username", "Operator") or "Operator",
            "pair_id": pair.id if pair else None,
            "pair_status": pair.verification_status if pair else "N/A",
            "front_url": front_url,
            "rear_url": rear_url,
            "simulated_token": l.simulated_token or "",
        })

    # 2. Historical actions from VehicleInstallationPair records (ensures history displays even before submissions)
    pairs_qs = (
        VehicleInstallationPair.objects.select_related("order", "front_image", "rear_image")
        .order_by("-updated_at")
    )
    if date_scope == "TODAY":
        pairs_qs = pairs_qs.filter(
            Q(submitted_at__date=today) |
            (Q(submitted_at__isnull=True) & Q(updated_at__date=today))
        )

    if flt == "SUBMITTED":
        pairs_qs = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)
    elif flt == "FAILED":
        pairs_qs = pairs_qs.filter(verification_status=VehicleInstallationPair.VerificationStatus.FAILED)
    elif flt == "ISSUES":
        pairs_qs = pairs_qs.filter(verification_status__in=[
            VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            VehicleInstallationPair.VerificationStatus.CONFLICT,
            VehicleInstallationPair.VerificationStatus.UNREGISTERED,
            VehicleInstallationPair.VerificationStatus.FAILED,
        ])

    for p in pairs_qs[:150]:
        sub_time = p.submitted_at or p.updated_at
        is_item_today = bool(sub_time and sub_time.date() == today)
        front_url = f"/media/{str(p.front_image.vault_file).replace('\\', '/')}" if (p.front_image and p.front_image.vault_file) else None
        rear_url = f"/media/{str(p.rear_image.vault_file).replace('\\', '/')}" if (p.rear_image and p.rear_image.vault_file) else None

        action_name = "VERIFY_PAIR"
        if p.verification_status == VehicleInstallationPair.VerificationStatus.SUBMITTED:
            action_name = "ITMS_SUBMISSION"
        elif p.is_manual_override:
            action_name = "MANUAL_OVERRIDE"
        elif p.verification_status == VehicleInstallationPair.VerificationStatus.APPROVED:
            action_name = "PAIR_APPROVAL"

        res_status = "SUCCESS" if p.verification_status in (
            VehicleInstallationPair.VerificationStatus.APPROVED,
            VehicleInstallationPair.VerificationStatus.SUBMITTED
        ) else ("REVIEW" if p.verification_status == VehicleInstallationPair.VerificationStatus.PENDING_REVIEW else "FAILED")

        history_items.append({
            "id": f"pair_{p.id}",
            "timestamp": sub_time.strftime("%Y-%m-%d %H:%M:%S") if sub_time else "",
            "is_today": is_item_today,
            "plate": p.registration_number_detected or "NO_PLATE",
            "order_number": p.order.order_number if p.order else "Unlinked",
            "order_plate": p.order.registration_number if p.order else "",
            "action": action_name,
            "result": res_status,
            "message": f"Status: {p.verification_status} | Match: {p.match_type or 'None'} ({p.match_score or 0}%) | Strategy: {p.matched_via or 'Vision'}",
            "operator": "Operator",
            "pair_id": p.id,
            "pair_status": p.verification_status,
            "front_url": front_url,
            "rear_url": rear_url,
            "simulated_token": getattr(p, "submission_receipt_token", "") or "",
        })

    # Sort combined history items by timestamp descending
    history_items.sort(key=lambda x: str(x.get("timestamp") or ""), reverse=True)

    return JsonResponse({
        "success": True,
        "filter": flt,
        "date_scope": date_scope,
        "total": len(history_items),
        "items": history_items[:150],
    })
