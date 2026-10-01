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


def extract_order_date_suffix(order_number: str) -> Optional[str]:
    """
    Extracts the 6-digit DDMMYY date suffix from an order number string.
    Example: 'PO-UMA667PU-260926' -> '260926'
             '#PO-UMA667PU-260926' -> '260926'
    """
    if not order_number:
        return None
    cleaned = order_number.strip().lstrip("#")
    m = re.search(r"-(\d{6})$", cleaned)
    return m.group(1) if m else None


def format_date_suffix_readable(date_suffix: str) -> str:
    """
    Converts 6-digit DDMMYY string (e.g. '260926') into human-readable '26.09.2026'.
    """
    if not date_suffix or len(date_suffix) != 6 or not date_suffix.isdigit():
        return date_suffix or ""
    dd = date_suffix[:2]
    mm = date_suffix[2:4]
    yy = date_suffix[4:6]
    return f"{dd}.{mm}.20{yy}"


def parse_target_date_suffix(date_input: Optional[str]) -> Optional[str]:
    """
    Normalizes any date representation (e.g. '260926', '26.09.2026', '2026-09-26')
    to 6-digit DDMMYY suffix format ('260926').
    """
    if not date_input or date_input.strip().upper() in ("ALL", "NONE", ""):
        return None
    val = date_input.strip().upper()
    if val == "TODAY":
        today = timezone.localdate()
        return today.strftime("%d%m%y")
    # If 6-digit already
    if len(val) == 6 and val.isdigit():
        return val
    # If DD.MM.YYYY or DD/MM/YYYY or DD-MM-YYYY
    m1 = re.match(r"^(\d{2})[./-](\d{2})[./-](?:20)?(\d{2})$", val)
    if m1:
        return f"{m1.group(1)}{m1.group(2)}{m1.group(3)}"
    # If YYYY-MM-DD
    m2 = re.match(r"^(?:20)?(\d{2})[./-](\d{2})[./-](\d{2})$", val)
    if m2:
        return f"{m2.group(3)}{m2.group(2)}{m2.group(1)}"
    return val


def get_available_order_dates() -> List[Dict[str, Any]]:
    """
    Scans all InstallationOrders in the local database and returns distinct work dates
    ordered chronologically newest first.
    """
    suffix_counts: Dict[str, int] = {}
    for num in InstallationOrder.objects.values_list("order_number", flat=True):
        s = extract_order_date_suffix(num)
        if s:
            suffix_counts[s] = suffix_counts.get(s, 0) + 1

    today_suffix = timezone.localdate().strftime("%d%m%y")
    if today_suffix not in suffix_counts:
        suffix_counts[today_suffix] = 0

    def _sort_key(s: str):
        if len(s) == 6 and s.isdigit():
            dd, mm, yy = int(s[:2]), int(s[2:4]), int(s[4:6])
            return (yy, mm, dd)
        return (0, 0, 0)

    results = []
    for s in sorted(suffix_counts.keys(), key=_sort_key, reverse=True):
        formatted = format_date_suffix_readable(s)
        cnt = suffix_counts[s]
        results.append({
            "suffix": s,
            "formatted_date": formatted,
            "label": f"{formatted} ({s})",
            "order_count": cnt,
            "is_today": (s == today_suffix),
        })

    return results


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
        # Default to latest date with orders if none selected
        latest_with_orders = next((d["suffix"] for d in available_dates if d["order_count"] > 0), None)
        selected_suffix = latest_with_orders or timezone.localdate().strftime("%d%m%y")
        formatted_date = format_date_suffix_readable(selected_suffix)
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
    # Plates attempted to link and failed, plus new kits in stock
    all_order_plates = set(
        normalizer.canonicalize(p)
        for p in InstallationOrder.objects.values_list("registration_number", flat=True)
        if p
    )

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

    failed_linking_count = 0
    stock_kits_count = 0


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

    status_counts = {
        s: pairs_qs.filter(verification_status=s).count()
        for s in [
            VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
            VehicleInstallationPair.VerificationStatus.APPROVED,
            VehicleInstallationPair.VerificationStatus.SUBMITTED,
            VehicleInstallationPair.VerificationStatus.FAILED,
            VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            VehicleInstallationPair.VerificationStatus.CONFLICT,
            VehicleInstallationPair.VerificationStatus.UNREGISTERED,
            VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
        ]
    }

    today_status_counts = {
        s: today_pairs_qs.filter(verification_status=s).count()
        for s in [
            VehicleInstallationPair.VerificationStatus.PENDING_REVIEW,
            VehicleInstallationPair.VerificationStatus.APPROVED,
            VehicleInstallationPair.VerificationStatus.SUBMITTED,
            VehicleInstallationPair.VerificationStatus.FAILED,
            VehicleInstallationPair.VerificationStatus.INCOMPLETE,
            VehicleInstallationPair.VerificationStatus.CONFLICT,
            VehicleInstallationPair.VerificationStatus.UNREGISTERED,
            VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX,
        ]
    }

    manual_overrides_count = pairs_qs.filter(is_manual_override=True).count()
    today_manual_overrides = today_pairs_qs.filter(is_manual_override=True).count()

    approved_cnt = status_counts[VehicleInstallationPair.VerificationStatus.APPROVED]
    submitted_cnt = status_counts[VehicleInstallationPair.VerificationStatus.SUBMITTED]
    failed_cnt = status_counts[VehicleInstallationPair.VerificationStatus.FAILED]
    pending_cnt = status_counts[VehicleInstallationPair.VerificationStatus.PENDING_REVIEW]
    incomplete_cnt = status_counts[VehicleInstallationPair.VerificationStatus.INCOMPLETE]
    conflict_cnt = status_counts[VehicleInstallationPair.VerificationStatus.CONFLICT]
    outbox_cnt = status_counts[VehicleInstallationPair.VerificationStatus.OFFLINE_OUTBOX]

    sub_attempted = submitted_cnt + failed_cnt
    submission_success_rate = round((submitted_cnt / sub_attempted * 100), 1) if sub_attempted > 0 else 100.0

    # ──────────────────────────────────────────────────────────────────────────
    # 4. ITMS Orders Totals (Active vs Archive)
    # ──────────────────────────────────────────────────────────────────────────
    orders_qs = InstallationOrder.objects.all()
    total_orders = orders_qs.count()
    active_orders = orders_qs.filter(is_archived=False).count()
    archive_orders = orders_qs.filter(is_archived=True).count()
    orders_with_photos = orders_qs.filter(has_front_photo=True, has_rear_photo=True).count()

    # ──────────────────────────────────────────────────────────────────────────
    # 5. ITMS Installation Kits & Plate Lifecycle Totals
    # ──────────────────────────────────────────────────────────────────────────
    kits_qs = InstallationKit.objects.all()
    total_kits = kits_qs.count()
    new_kits = kits_qs.filter(status__iexact="New").count()
    allocated_kits = kits_qs.filter(status__icontains="Allocat").count()
    installed_kits = kits_qs.filter(status__icontains="Install").count()
    other_kits = max(0, total_kits - (new_kits + allocated_kits + installed_kits))

    # Kits breakdown by warehouse
    warehouse_groups = (
        kits_qs.values("warehouse")
        .annotate(total=Count("id"))
        .order_by("-total")
    )
    warehouse_stats: List[Dict[str, Any]] = []
    for g in warehouse_groups:
        wh_name = g.get("warehouse") or "Unassigned Warehouse"
        wh_total = g.get("total", 0)
        wh_new = kits_qs.filter(warehouse=wh_name, status__iexact="New").count()
        wh_alloc = kits_qs.filter(warehouse=wh_name, status__icontains="Allocat").count()
        wh_inst = kits_qs.filter(warehouse=wh_name, status__icontains="Install").count()
        warehouse_stats.append({
            "warehouse": wh_name,
            "total": wh_total,
            "new": wh_new,
            "allocated": wh_alloc,
            "installed": wh_inst,
        })

    # ──────────────────────────────────────────────────────────────────────────
    # 6. Hardware & Component Inventory Totals
    # ──────────────────────────────────────────────────────────────────────────
    kits_with_front_plate = kits_qs.exclude(front_plate="").exclude(front_plate__isnull=True).count()
    kits_with_rear_plate = kits_qs.exclude(rear_plate="").exclude(rear_plate__isnull=True).count()
    kits_with_gps = kits_qs.exclude(gps_tracker="").exclude(gps_tracker__isnull=True).count()
    kits_with_ble = kits_qs.filter(
        Q(front_tracker__gt="") | Q(rear_tracker__gt="")
    ).count()
    kits_with_sim = kits_qs.exclude(sim_serial="").exclude(sim_serial__isnull=True).count()

    # ──────────────────────────────────────────────────────────────────────────
    # 7. Vehicle Categories & Plate Classes Breakdown
    # ──────────────────────────────────────────────────────────────────────────
    psv_count = 0
    pmo_count = 0
    color_conflicts = 0
    for l in SubmissionAuditLog.objects.filter(message__icontains="Category:"):
        if "Category: PSV" in l.message:
            psv_count += 1
        elif "Category: PMO" in l.message:
            pmo_count += 1
        elif "COLOR_CONFLICT" in l.message:
            color_conflicts += 1

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
            "today_approved": today_status_counts[VehicleInstallationPair.VerificationStatus.APPROVED],
            "today_submitted": today_status_counts[VehicleInstallationPair.VerificationStatus.SUBMITTED],
            "today_failed": today_status_counts[VehicleInstallationPair.VerificationStatus.FAILED],
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

