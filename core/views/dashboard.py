"""
Operator Web Dashboard, metrics, stats, totals, and audit history.
"""
import logging

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import auth_service, config_service
from core.version import __version__

logger = logging.getLogger(__name__)


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
        "user_settings": config_service.get_user_settings(),
        "developer_settings": config_service.get_developer_settings(),
        "developer_mode": config_service.is_developer_mode(),
        "active_bond": config_service.get_active_bond(),
        "available_bonds": config_service.get_available_bonds(),
    }
    return render(request, "core/dashboard.html", context)


@require_GET
def api_stats(request: HttpRequest) -> JsonResponse:
    """Returns real-time operational summary metrics for the header ribbon and dashboard."""
    today = timezone.localdate()

    total_orders = InstallationOrder.objects.count()
    pending_orders = InstallationOrder.objects.filter(status=InstallationOrder.Status.PENDING).count()
    issue_filter = Q(
        verification_status__in=[
            VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            VehicleInstallationPair.VerificationStatus.CONFLICT,
            VehicleInstallationPair.VerificationStatus.UNREGISTERED,
            VehicleInstallationPair.VerificationStatus.FAILED,
            VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
        ]
    )
    pair_counts = VehicleInstallationPair.objects.aggregate(
        total=Count("id"),
        pending_review=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)),
        approved=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)),
        submitted=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)),
        issues=Count("id", filter=issue_filter),
    )
    total_pairs = pair_counts["total"]
    pending_review = pair_counts["pending_review"]
    approved = pair_counts["approved"]
    submitted = pair_counts["submitted"]
    issues = pair_counts["issues"]
    total_photos = EvidenceImage.objects.count()
    batches_count = IngestionBatch.objects.count()

    today_pairs_qs = VehicleInstallationPair.objects.filter(
        Q(created_at__date=today) |
        Q(front_image__batch__created_at__date=today) |
        Q(rear_image__batch__created_at__date=today)
    )
    today_counts = today_pairs_qs.aggregate(
        total=Count("id"),
        pending_review=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)),
        approved=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)),
        submitted=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)),
        issues=Count("id", filter=issue_filter),
    )
    shift_stats = {
        "date": today.strftime("%Y-%m-%d"),
        "total_pairs": today_counts["total"],
        "pending_review": today_counts["pending_review"],
        "approved": today_counts["approved"],
        "submitted": today_counts["submitted"],
        "issues": today_counts["issues"],
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


@require_GET
def api_reports_totals(request: HttpRequest) -> JsonResponse:
    """
    Returns comprehensive system and shift totals across photos, batches,
    pairs, orders, kits, hardware components, and date-driven category totals.
    """
    from core.services import report_service
    scope = request.GET.get("scope", "ALL").strip().upper()
    if scope not in ("ALL", "TODAY"):
        scope = "ALL"

    target_date = request.GET.get("date") or request.GET.get("date_suffix") or request.GET.get("suffix") or None

    try:
        totals = report_service.get_system_totals(scope=scope, target_date=target_date)
        return JsonResponse({"success": True, "totals": totals})
    except Exception as exc:
        logger.error("api_reports_totals error: %s", exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@require_GET
def api_history_list(request: HttpRequest) -> JsonResponse:
    """Returns historical verification events and audit logs for the History tab with date scoping."""
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

    if flt == "SUBMITTED":
        logs_qs = logs_qs.filter(action=SubmissionAuditLog.Action.SUBMIT)
    elif flt in ("FAILED", "ISSUES"):
        logs_qs = logs_qs.filter(Q(result=SubmissionAuditLog.ResultStatus.FAILURE) | Q(result="FAILED"))

    for l in logs_qs[:150]:
        pair = l.pair
        plate = pair.registration_number_detected if pair else "N/A"
        order_no = pair.order.order_number if (pair and pair.order) else "N/A"
        order_plate = pair.order.registration_number if (pair and pair.order) else ""

        front_file = str(pair.front_image.vault_file).replace("\\", "/") if (pair and pair.front_image) else None
        front_url = f"/media/{front_file}" if front_file else None
        rear_file = str(pair.rear_image.vault_file).replace("\\", "/") if (pair and pair.rear_image) else None
        rear_url = f"/media/{rear_file}" if rear_file else None

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
        front_file = str(p.front_image.vault_file).replace("\\", "/") if (p.front_image and p.front_image.vault_file) else None
        front_url = f"/media/{front_file}" if front_file else None
        rear_file = str(p.rear_image.vault_file).replace("\\", "/") if (p.rear_image and p.rear_image.vault_file) else None
        rear_url = f"/media/{rear_file}" if rear_file else None

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


