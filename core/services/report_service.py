"""
Comprehensive System & Shift Totals Report Service.

Aggregates real-time verification metrics, inventory counts, installation kit allocations,
hardware serial totals, and shift statistics for both the TUI Reports Pane (Tab 7)
and Web Dashboard Reports Hub.
"""
import logging
from datetime import date
from typing import Any, Dict, List, Optional

import re
from datetime import date
from typing import Any, Dict, List, Optional

from django.db import models
from django.db.models import Count, Q
from django.utils import timezone

from core.models import (
    EvidenceImage,
    IngestionBatch,
    InstallationKit,
    InstallationOrder,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services.plate_lifecycle_service import format_display_plate
from core.vision import normalizer

logger = logging.getLogger(__name__)


from core.services import date_service
from core.services.date_service import (
    extract_order_date_suffix,
    format_date_suffix,
    format_date_suffix_readable,
    format_iso_date,
    format_itms_timestamp,
    format_readable_date,
    get_active_shift_date,
    get_active_shift_info,
    get_active_shift_suffix,
    get_available_shift_dates,
    parse_date_input,
    parse_target_date_suffix,
    resolve_date_and_suffix,
)

get_available_order_dates = get_available_shift_dates


def get_date_driven_plate_totals(target_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Computes date-driven plate and order verification totals for a specific work date:
    
    1. INSTALLED / IN ARCHIVE:
       Orders completed and archived for this work date.
    
    2. PENDING TO BE INSTALLED:
       Orders active (under installation / ready for installation) for this work date.
    
    3. UNALLOCATED PLATES:
       Plates physically dispatched to the line for the work shift that appear neither in
       active ITMS orders nor in ITMS archive, determined strictly via daily shift elimination.
    """
    available_dates = get_available_order_dates()
    parsed_suffix = parse_target_date_suffix(target_date)

    is_all = (target_date or "").strip().upper() == "ALL"

    if is_all:
        selected_suffix = "ALL"
        formatted_date = "All Dates"
        orders_qs = InstallationOrder.objects.all()
    elif parsed_suffix:
        selected_suffix = parsed_suffix
        formatted_date = format_date_suffix_readable(selected_suffix)
        orders_qs = InstallationOrder.objects.filter(order_number__endswith=selected_suffix)
    else:
        # Default to active operating shift date configured in Settings
        active_info = date_service.get_active_shift_info()
        selected_suffix = active_info["suffix"]
        formatted_date = active_info["formatted_date"]
        orders_qs = InstallationOrder.objects.filter(order_number__endswith=selected_suffix)

    # 1. Category 1: Installed / In Archive
    installed_qs = orders_qs.filter(Q(is_archived=True) | Q(order_status__iexact="Installed"))
    installed_count = installed_qs.count()

    officer_counts: Dict[str, int] = {}
    installed_items: List[Dict[str, Any]] = []
    for o in installed_qs[:100]:
        off = o.installation_officer or "Verified Officer"
        officer_counts[off] = officer_counts.get(off, 0) + 1
        installed_items.append({
            "plate": o.registration_number,
            "display_plate": format_display_plate(o.registration_number),
            "order_number": o.order_number,
            "status": o.order_status or "Installed",
            "officer": off,
            "install_date": o.installation_date or "Archived",
            "warehouse": o.warehouse_name or "—",
            "vin": o.vin or "—",
        })

    # 2. Category 2: Pending to be Installed (Active Orders)
    pending_qs = orders_qs.filter(is_archived=False).exclude(order_status__iexact="Installed")
    pending_count = pending_qs.count()

    stage_counts: Dict[str, int] = {}
    pending_items: List[Dict[str, Any]] = []
    for o in pending_qs[:100]:
        st = o.itms_stage or o.order_status or "Ready for installation"
        stage_counts[st] = stage_counts.get(st, 0) + 1
        pending_items.append({
            "plate": o.registration_number,
            "display_plate": format_display_plate(o.registration_number),
            "order_number": o.order_number,
            "stage": st,
            "warehouse": o.warehouse_name or "—",
            "vin": o.vin or "—",
        })

    total_orders_for_date = installed_count + pending_count
    installed_pct = (
        f"{round((installed_count / total_orders_for_date * 100), 1)}%"
        if total_orders_for_date > 0
        else "0%"
    )
    pending_pct = (
        f"{round((pending_count / total_orders_for_date * 100), 1)}%"
        if total_orders_for_date > 0
        else "0%"
    )

    # 3. Category 3: Unallocated Plates
    # Plates physically scanned out for the shift that have no active ITMS order and are not in archive

    unallocated_items: List[Dict[str, Any]] = []
    seen_unallocated_plates = set()

    # Source A: Dispatched Plates from Warehouse (Taken Out for Shift via Physical Scans)
    # Ground truth: Physical plates counted/scanned out to installation line for this shift.
    # Elimination: Dispatched plates − Archived Orders − Active Pending Orders − Returned to Safe = Unallocated Discrepancy.
    dispatched_unallocated_count = 0
    candidate_unalloc_records = []
    candidate_plates_to_lookup = set()
    stock_recon = {}

    try:
        from core.services import stock_monitoring_service
        from core.models import StockDispatchScan
        if is_all:
            discrepancy_scans = list(
                StockDispatchScan.objects.filter(
                    status=StockDispatchScan.Status.UNRESOLVED_DISCREPANCY
                ).order_by("-work_date", "registration_number")
            )
            for scan in discrepancy_scans:
                c_u = normalizer.canonicalize(scan.registration_number)
                if c_u and c_u not in seen_unallocated_plates:
                    seen_unallocated_plates.add(c_u)
                    candidate_plates_to_lookup.add(c_u)
                    candidate_unalloc_records.append({
                        "plate": c_u,
                        "source_id": f"Shift Out ({scan.work_date_suffix or 'ALL'})",
                        "date": scan.work_date.strftime("%d.%m.%Y") if scan.work_date else formatted_date,
                    })
        else:
            stock_recon = stock_monitoring_service.compute_daily_reconciliation(selected_suffix)
            for u_plate in stock_recon.get("unallocated_plates", []):
                c_u = normalizer.canonicalize(u_plate)
                if c_u and c_u not in seen_unallocated_plates:
                    seen_unallocated_plates.add(c_u)
                    candidate_plates_to_lookup.add(c_u)
                    candidate_unalloc_records.append({
                        "plate": c_u,
                        "source_id": f"Shift Out ({selected_suffix})",
                        "date": formatted_date,
                    })
    except Exception as stock_err:
        logger.warning("Error calculating stock reconciliation in report_service: %s", stock_err)
        stock_recon = {}

    # Target-fetch ONLY matching InstallationKit records for candidate unallocated plates
    kit_by_reg: Dict[str, InstallationKit] = {}
    kit_by_code: Dict[str, InstallationKit] = {}
    if candidate_plates_to_lookup:
        target_codes = [f"IK-{p}" for p in candidate_plates_to_lookup]
        matching_kits = InstallationKit.objects.filter(
            Q(registration_number__in=candidate_plates_to_lookup) | Q(kit_code__in=target_codes)
        )
        for k in matching_kits:
            c = normalizer.canonicalize(k.registration_number)
            if c:
                kit_by_reg[c] = k
            if k.kit_code:
                clean_code = k.kit_code.strip().upper()
                kit_by_code[clean_code] = k
                if not clean_code.startswith("IK-"):
                    kit_by_code[f"IK-{clean_code}"] = k

    for item in candidate_unalloc_records:
        c_u = item["plate"]
        dispatched_unallocated_count += 1
        matching_kit = kit_by_reg.get(c_u) or kit_by_code.get(f"IK-{c_u}") or kit_by_code.get(c_u)
        wh_name = matching_kit.warehouse if (matching_kit and matching_kit.warehouse) else "Line Floor (AGM)"
        unallocated_items.append({
            "plate": c_u,
            "display_plate": format_display_plate(c_u),
            "source": "DISPATCH_UNALLOCATED",
            "source_type_label": "Floor Dispatched (Unallocated)",
            "source_id": item["source_id"],
            "reason": "Dispatched to line; no active ITMS order & not in archive",
            "status": "Taken Out to Line",
            "warehouse": wh_name,
            "date": item["date"],
            "serials": {
                "front_plate": matching_kit.front_plate if matching_kit else "—",
                "rear_plate": matching_kit.rear_plate if matching_kit else "—",
                "gps": matching_kit.gps_tracker if matching_kit else "—",
            },
        })

    # Source B (Stock Installation Kits 'New' in warehouse safe-room) is intentionally excluded from
    # floor unallocated plates. Safe-room stock belongs exclusively in warehouse inventory stats,
    # NOT in the floor unallocated discrepancy audit (which strictly tracks kits physically taken out to the line).
    failed_linking_count = 0
    stock_kits_count = 0


    # 4. Category 4: Spiked / Undispatched Plates (In ITMS orders without safe room dispatch scan)
    spiked_installed_list = (stock_recon.get("spiked_installed_plates") or stock_recon.get("spiked_installed") or []) if stock_recon else []
    spiked_pending_list = (stock_recon.get("spiked_pending_plates") or stock_recon.get("spiked_pending") or []) if stock_recon else []
    spiked_total_count = stock_recon.get("spiked_undispatched_count", len(spiked_installed_list) + len(spiked_pending_list)) if stock_recon else 0

    spiked_items: List[Dict[str, Any]] = []
    for p in spiked_installed_list[:50]:
        spiked_items.append({
            "plate": p,
            "display_plate": format_display_plate(p),
            "status": "In ITMS Archive (Installed)",
            "reason": "Kit verified/installed in ITMS without safe room dispatch scan",
            "date": formatted_date,
        })
    for p in spiked_pending_list[:50]:
        spiked_items.append({
            "plate": p,
            "display_plate": format_display_plate(p),
            "status": "In Active Pending Queue",
            "reason": "Kit active in pending queue without safe room dispatch scan",
            "date": formatted_date,
        })

    return {
        "selected_date_suffix": selected_suffix,
        "selected_date_formatted": formatted_date,
        "is_all_dates": is_all,
        "available_dates": available_dates,
        "total_orders_for_date": total_orders_for_date,
        "installed_archive": {
            "count": installed_count,
            "percentage": installed_pct,
            "officers": officer_counts,
            "items": installed_items,
        },
        "pending_orders": {
            "count": pending_count,
            "percentage": pending_pct,
            "stages": stage_counts,
            "items": pending_items,
        },
        "unallocated_plates": {
            "total_count": len(seen_unallocated_plates),
            "failed_linking_count": failed_linking_count,
            "stock_kits_count": stock_kits_count,
            "dispatched_unallocated_count": dispatched_unallocated_count,
            "items": unallocated_items,
        },
        "spiked_undispatched": {
            "total_count": spiked_total_count,
            "installed_count": len(spiked_installed_list),
            "pending_count": len(spiked_pending_list),
            "items": spiked_items,
        },
        "stock_reconciliation": stock_recon,
    }


def get_system_totals(scope: str = "ALL", target_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Computes system-wide totals across photos, batches, pairs, orders,
    and installation kits, plus date-driven verification category breakdowns.

    Args:
        scope: "ALL" for all-time totals, or "TODAY" for today's shift totals.
        target_date: Optional DDMMYY suffix (e.g. '260926') or 'ALL' for date-driven breakdown.

    Returns:
        Structured dictionary of counts, breakdowns, percentages, warehouse stats,
        and date_driven categories.
    """
    today = timezone.localdate()

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Evidence Photos Totals
    # ──────────────────────────────────────────────────────────────────────────
    images_qs = EvidenceImage.objects.all()
    today_images_qs = images_qs.filter(ingested_at__date=today)

    total_photos = images_qs.count()
    front_photos = images_qs.filter(orientation=EvidenceImage.Orientation.FRONT).count()
    rear_photos = images_qs.filter(orientation=EvidenceImage.Orientation.REAR).count()
    unknown_photos = images_qs.filter(orientation=EvidenceImage.Orientation.UNKNOWN).count()
    today_photos = today_images_qs.count()

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Ingestion Batches Totals
    # ──────────────────────────────────────────────────────────────────────────
    batches_qs = IngestionBatch.objects.all()
    today_batches_qs = batches_qs.filter(created_at__date=today)

    total_batches = batches_qs.count()
    today_batches = today_batches_qs.count()

    # ──────────────────────────────────────────────────────────────────────────
    # 3. Vehicle Pairs & Verification Breakdown
    # ──────────────────────────────────────────────────────────────────────────
    pairs_qs = VehicleInstallationPair.objects.all()
    today_pairs_qs = pairs_qs.filter(
        Q(created_at__date=today) |
        Q(front_image__ingested_at__date=today) |
        Q(rear_image__ingested_at__date=today)
    )

    total_pairs = pairs_qs.count()
    today_pairs = today_pairs_qs.count()

    # High-performance conditional aggregation (1 query across 1000s of pairs)
    all_status_agg = pairs_qs.aggregate(
        pending_review=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)),
        approved=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)),
        submitted=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)),
        failed=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.FAILED)),
        incomplete=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE)),
        conflict=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT)),
        unregistered=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.UNREGISTERED)),
        offline_outbox=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX)),
        manual_overrides=Count("id", filter=Q(is_manual_override=True)),
    )
    today_status_agg = today_pairs_qs.aggregate(
        pending_review=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.PENDING_REVIEW)),
        approved=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.APPROVED)),
        submitted=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.SUBMITTED)),
        failed=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.FAILED)),
        incomplete=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.INCOMPLETE)),
        conflict=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.CONFLICT)),
        unregistered=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.UNREGISTERED)),
        offline_outbox=Count("id", filter=Q(verification_status=VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX)),
        manual_overrides=Count("id", filter=Q(is_manual_override=True)),
    )

    approved_cnt = all_status_agg["approved"] or 0
    submitted_cnt = all_status_agg["submitted"] or 0
    failed_cnt = all_status_agg["failed"] or 0
    pending_cnt = all_status_agg["pending_review"] or 0
    incomplete_cnt = all_status_agg["incomplete"] or 0
    conflict_cnt = all_status_agg["conflict"] or 0
    outbox_cnt = all_status_agg["offline_outbox"] or 0
    manual_overrides_count = all_status_agg["manual_overrides"] or 0
    today_manual_overrides = today_status_agg["manual_overrides"] or 0

    sub_attempted = submitted_cnt + failed_cnt
    submission_success_rate = round((submitted_cnt / sub_attempted * 100), 1) if sub_attempted > 0 else 100.0

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # 4. ITMS Orders Totals (Active vs Archive)
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    orders_qs = InstallationOrder.objects.all()
    total_orders = orders_qs.count()
    active_orders = orders_qs.filter(is_archived=False).count()
    archive_orders = orders_qs.filter(is_archived=True).count()
    orders_with_photos = orders_qs.filter(has_front_photo=True, has_rear_photo=True).count()

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # 5. ITMS Installation Kits & Plate Lifecycle Totals
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    kits_qs = InstallationKit.objects.all()
    total_kits = kits_qs.count()
    new_kits = kits_qs.filter(status__iexact="New").count()
    allocated_kits = kits_qs.filter(status__icontains="Allocat").count()
    installed_kits = kits_qs.filter(status__icontains="Install").count()
    other_kits = max(0, total_kits - (new_kits + allocated_kits + installed_kits))

    # Kits breakdown by warehouse (Single aggregated query avoiding loop N+1s)
    warehouse_groups = (
        kits_qs.values("warehouse")
        .annotate(
            total=Count("id"),
            new=Count("id", filter=Q(status__iexact="New")),
            allocated=Count("id", filter=Q(status__icontains="Allocat")),
            installed=Count("id", filter=Q(status__icontains="Install")),
        )
        .order_by("-total")
    )
    warehouse_stats = [
        {
            "warehouse": g.get("warehouse") or "Unassigned Warehouse",
            "total": g.get("total", 0),
            "new": g.get("new", 0),
            "allocated": g.get("allocated", 0),
            "installed": g.get("installed", 0),
        }
        for g in warehouse_groups
    ]

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # 6. Hardware & Component Inventory Totals
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    kits_with_front_plate = kits_qs.exclude(front_plate="").exclude(front_plate__isnull=True).count()
    kits_with_rear_plate = kits_qs.exclude(rear_plate="").exclude(rear_plate__isnull=True).count()
    kits_with_gps = kits_qs.exclude(gps_tracker="").exclude(gps_tracker__isnull=True).count()
    kits_with_ble = kits_qs.filter(
        Q(front_tracker__gt="") | Q(rear_tracker__gt="")
    ).count()
    kits_with_sim = kits_qs.exclude(sim_serial="").exclude(sim_serial__isnull=True).count()

    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # 7. Vehicle Categories & Plate Classes Breakdown (Aggregated queries)
    # â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    audit_agg = SubmissionAuditLog.objects.aggregate(
        psv=Count("id", filter=Q(message__icontains="Category: PSV")),
        pmo=Count("id", filter=Q(message__icontains="Category: PMO")),
        conflicts=Count("id", filter=Q(message__icontains="COLOR_CONFLICT")),
    )
    psv_count = audit_agg.get("psv") or 0
    pmo_count = audit_agg.get("pmo") or 0
    color_conflicts = audit_agg.get("conflicts") or 0

    return {
        "generated_at": timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
        "today_date": str(today),
        "scope": scope,
        "photos": {
            "total": total_photos,
            "front": front_photos,
            "rear": rear_photos,
            "unknown": unknown_photos,
            "today": today_photos,
            "symmetry_ratio": f"{round((front_photos / rear_photos * 100), 1)}%" if rear_photos > 0 else "N/A",
        },
        "batches": {
            "total": total_batches,
            "today": today_batches,
            "avg_photos_per_batch": round(total_photos / total_batches, 1) if total_batches > 0 else 0,
        },
        "pairs": {
            "total": total_pairs,
            "today": today_pairs,
            "approved": approved_cnt,
            "submitted": submitted_cnt,
            "failed": failed_cnt,
            "pending_review": pending_cnt,
            "incomplete": incomplete_cnt,
            "conflict": conflict_cnt,
            "offline_outbox": outbox_cnt,
            "manual_overrides": manual_overrides_count,
            "today_approved": today_status_agg["approved"] or 0,
            "today_submitted": today_status_agg["submitted"] or 0,
            "today_failed": today_status_agg["failed"] or 0,
            "submission_success_rate": f"{submission_success_rate}%",
        },
        "orders": {
            "total": total_orders,
            "active": active_orders,
            "archive": archive_orders,
            "with_photos": orders_with_photos,
        },
        "kits": {
            "total": total_kits,
            "new_unallocated": new_kits,
            "allocated": allocated_kits,
            "installed": installed_kits,
            "other": other_kits,
            "unallocated_pct": f"{round((new_kits / total_kits * 100), 1)}%" if total_kits > 0 else "0%",
            "warehouses": warehouse_stats,
        },
        "hardware": {
            "front_plates": kits_with_front_plate,
            "rear_plates": kits_with_rear_plate,
            "gps_trackers": kits_with_gps,
            "ble_trackers": kits_with_ble,
            "sim_cards": kits_with_sim,
        },
        "categories": {
            "psv_commercial": psv_count,
            "pmo_private": pmo_count,
            "color_conflicts": color_conflicts,
        },
        "date_driven": get_date_driven_plate_totals(target_date),
    }

