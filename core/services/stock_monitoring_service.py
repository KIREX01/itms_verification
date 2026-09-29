"""
Stock Monitoring & Daily Plate Reconciliation Service.

Manages physical warehouse inventory, inbound deliveries, bond transfers (In/Out),
scheduled installation targets, dispatched plates (taken out for installation),
uninstalled returns, and daily shift reconciliation against ITMS active orders
and completed archive.

Differentiates between Public White (PSV) and Private Yellow (PMO) with identical
accounting descriptions and balances:
1. Opening Balance
2. Kits Received
3. Bond Transfer In   (kits transferred from other bonds to our bond)
4. Bond Transfer Out  (kits transferred from our bond to other bonds)
5. Scheduled          (target number of plates to be installed under bond - manually fed in)
6. Kits Installed     (actual plates in orders and archive for the day)
7. Closing Balance    = Opening + Kits Received + Transfer In - Transfer Out - Kits Installed
"""
import csv
import io
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    DailyStockLedger,
    EvidenceImage,
    InstallationKit,
    InstallationOrder,
    PlateCategory,
    StockBondTransfer,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import bond_service
from core.services.plate_lifecycle_service import format_display_plate
from core.services.report_service import (
    extract_order_date_suffix,
    format_date_suffix_readable,
    parse_target_date_suffix,
)
from core.vision import normalizer

logger = logging.getLogger(__name__)


def extract_single_plate(text: Any) -> Optional[str]:
    """
    Extracts and canonicalizes a single license plate from scanner or user text.
    Handles:
    - Pure plate strings: 'UMA711PW', 'uma 711 pw', 'UMA074PX'
    - Kit codes: 'IK-UMA711PW', 'IK:UMA711PW'
    - ITMS order codes: 'PO-UMA711PW-260926', '#PO-UMA711PW-260926'
    - QR URLs: 'https://itms.go.ug/verify?plate=UMA711PW', '...?rn=UMA711PW'
    - JSON payloads: '{"plate":"UMA711PW"}'
    - Pipe/hyphen separated tags: 'UG|UMA711PW|DEL-01'
    """
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None

    # 1. JSON payload check
    m_json = re.search(r'"(?:plate|registration_number|reg_number|rn|kit_code)":\s*"([^"]+)"', raw, re.IGNORECASE)
    if m_json:
        raw = m_json.group(1).strip()

    # 2. URL query parameters
    m_url = re.search(r'[?&](?:plate|rn|reg|kit)=([A-Za-z0-9\s\-_]+)', raw, re.IGNORECASE)
    if m_url:
        raw = m_url.group(1).strip()

    # 3. Strip common prefixes
    for pfx in ("IK-", "IK:", "KIT-", "PLATE-", "UG-"):
        if raw.upper().startswith(pfx):
            raw = raw[len(pfx):].strip()

    # 4. ITMS PO format e.g. PO-UMA711PW-260926
    m_po = re.match(r"^#?PO-([A-Z0-9]+)-\d{6}$", raw, re.IGNORECASE)
    if m_po:
        raw = m_po.group(1).strip()

    # 5. Regex search for standard Ugandan plate (3 letters, 3 digits, 1-2 letters)
    m_std = re.search(r"\b(U[A-Z]{2})\s*(\d{3})\s*([A-Z]{1,2})\b", raw, re.IGNORECASE)
    if m_std:
        return f"{m_std.group(1).upper()}{m_std.group(2)}{m_std.group(3).upper()}"

    # 6. Special plate patterns (UG, UPF, UPS, CD)
    m_spec = re.search(r"\b(UG|UPF|UPS|CD)\s*(\d{3,4})\s*([A-Z]?)\b", raw, re.IGNORECASE)
    if m_spec:
        suf = m_spec.group(3).upper() if m_spec.group(3) else ""
        return f"{m_spec.group(1).upper()}{m_spec.group(2)}{suf}"

    # 7. Fallback to normalizer
    canon = normalizer.canonicalize(raw)
    if canon and len(canon) in (7, 8) and canon.startswith("U"):
        return canon
    return canon if canon else None


def parse_plate_input_with_stats(raw_input: Any) -> Tuple[List[str], int, List[str]]:
    """
    Parses arbitrary plate inputs and returns:
    (unique_plates, duplicate_count, duplicate_plates)

    Guarantees no duplicate plates in unique_plates while preserving first-seen order.
    """
    if not raw_input:
        return [], 0, []

    lines: List[str] = []
    if isinstance(raw_input, (list, tuple, set)):
        for item in raw_input:
            if isinstance(item, str):
                lines.extend(item.splitlines())
            elif isinstance(item, dict):
                p = item.get("plate") or item.get("registration_number") or ""
                if p:
                    lines.append(str(p))
    elif isinstance(raw_input, str):
        lines = raw_input.splitlines()
    else:
        lines = [str(raw_input)]

    results: List[str] = []
    seen: Set[str] = set()
    duplicates: List[str] = []

    for line in lines:
        for chunk in re.split(r"[,;\t|]+", line):
            cleaned = chunk.strip().strip("'\"#[](){}")
            if not cleaned:
                continue

            plate = extract_single_plate(cleaned)
            if not plate:
                continue

            if plate in seen:
                duplicates.append(plate)
            else:
                seen.add(plate)
                results.append(plate)

    return results, len(duplicates), duplicates


def parse_plate_input(raw_input: Any) -> List[str]:
    """
    Parses arbitrary plate inputs (multi-line text, comma-separated, list of strings,
    or barcode scan payloads) and returns canonical plate strings with duplicates removed.
    """
    unique_plates, _, _ = parse_plate_input_with_stats(raw_input)
    return unique_plates


def resolve_date_and_suffix(target: Optional[Any] = None) -> Tuple[date, str]:
    """
    Normalizes a date object, string, or 6-digit suffix into a (date, suffix) tuple.
    Example: '260926' -> (date(2026, 9, 26), '260926')
             date(2026, 9, 26) -> (date(2026, 9, 26), '260926')
    """
    if isinstance(target, date):
        suffix = target.strftime("%d%m%y")
        return target, suffix

    if isinstance(target, datetime):
        d = target.date()
        return d, d.strftime("%d%m%y")

    parsed_suffix = parse_target_date_suffix(target)
    if parsed_suffix and len(parsed_suffix) == 6 and parsed_suffix.isdigit():
        dd = int(parsed_suffix[:2])
        mm = int(parsed_suffix[2:4])
        yy = int(parsed_suffix[4:6])
        try:
            d = date(2000 + yy, mm, dd)
            return d, parsed_suffix
        except ValueError:
            pass

    today = timezone.localdate()
    return today, today.strftime("%d%m%y")


def generate_delivery_note_reference(target_date: Optional[Any] = None) -> str:
    """
    Generates a standardized date-anchored delivery note reference:
    e.g. DN-20260930-01, DN-20260930-02, etc.
    """
    d, _ = resolve_date_and_suffix(target_date)
    prefix = f"DN-{d.strftime('%Y%m%d')}-"
    existing_count = StockDelivery.objects.filter(
        Q(delivery_date=d) | Q(delivery_number__startswith=prefix)
    ).count()
    next_idx = existing_count + 1
    return f"{prefix}{next_idx:02d}"


def record_delivery(
    delivery_number: Optional[str] = None,
    plates: Iterable[str] = (),
    supplier: str = "Factory / Central Depot",
    plate_category: str = PlateCategory.PSV,
    delivery_date: Optional[date] = None,
    target_date_suffix: Optional[str] = None,
    paper_note_reference: str = "",
    delivery_note_image: Optional[Any] = None,
    received_by: Optional[Any] = None,
    operator_name: str = "Operator",
    notes: str = "",
    auto_create_kits: bool = True,
) -> Dict[str, Any]:
    """
    Records an inbound delivery of license plates.
    Increases warehouse physical stock (Kits Received) and optionally auto-creates
    local InstallationKit records marked as 'New' in stock.
    """
    deliv_date, def_suffix = resolve_date_and_suffix(delivery_date or target_date_suffix)
    suffix = target_date_suffix or def_suffix
    category = PlateCategory.PMO if str(plate_category).strip().upper() == "PMO" else PlateCategory.PSV

    clean_plates, dup_count, dup_plates = parse_plate_input_with_stats(plates)
    if not clean_plates:
        raise ValueError("No valid license plate numbers provided for delivery.")

    deliv_no = (delivery_number or "").strip()
    if not deliv_no or deliv_no.upper() == "AUTO":
        deliv_no = generate_delivery_note_reference(deliv_date)

    paper_ref = (paper_note_reference or "").strip()

    with transaction.atomic():
        delivery, created = StockDelivery.objects.get_or_create(
            delivery_number=deliv_no,
            defaults={
                "paper_note_reference": paper_ref,
                "delivery_note_image": delivery_note_image,
                "supplier": supplier or "Factory / Central Depot",
                "plate_category": category,
                "delivery_date": deliv_date,
                "target_date_suffix": suffix,
                "total_plates_count": len(clean_plates),
                "received_by": received_by,
                "operator_name": operator_name or "Operator",
                "notes": notes or "",
            },
        )
        if not created:
            if paper_ref:
                delivery.paper_note_reference = paper_ref
            if delivery_note_image:
                delivery.delivery_note_image = delivery_note_image
            delivery.supplier = supplier or delivery.supplier
            delivery.plate_category = category
            delivery.notes = notes or delivery.notes
            delivery.total_plates_count = len(clean_plates)
            delivery.save()
            delivery.items.all().delete()

        items = [
            StockDeliveryItem(
                delivery=delivery,
                registration_number=p,
                plate_category=category,
                kit_code=f"IK-{p}",
            )
            for p in clean_plates
        ]
        StockDeliveryItem.objects.bulk_create(items)

        created_kits_count = 0
        if auto_create_kits:
            existing_kits = set(
                InstallationKit.objects.filter(registration_number__in=clean_plates)
                .values_list("registration_number", flat=True)
            )
            kits_to_create = []
            for p in clean_plates:
                if p not in existing_kits:
                    kits_to_create.append(
                        InstallationKit(
                            kit_code=f"IK-{p}",
                            registration_number=p,
                            status="New",
                            warehouse="Warehouse Stock",
                            created_date=deliv_date.strftime("%d.%m.%Y"),
                        )
                    )
            if kits_to_create:
                InstallationKit.objects.bulk_create(kits_to_create, ignore_conflicts=True)
                created_kits_count = len(kits_to_create)

        recon = compute_daily_reconciliation(suffix)

    return {
        "success": True,
        "delivery_number": delivery.delivery_number,
        "paper_note_reference": delivery.paper_note_reference or "",
        "plate_category": category,
        "plates_count": len(clean_plates),
        "total_submitted": len(clean_plates) + dup_count,
        "duplicate_scans_skipped": dup_count,
        "duplicate_plates": dup_plates,
        "created_kits_count": created_kits_count,
        "reconciliation": recon,
    }


def record_bond_transfer(
    transfer_type: str,
    plate_category: str = PlateCategory.PSV,
    plates_count: int = 0,
    other_bond_name: str = "Other Bond",
    transfer_number: Optional[str] = None,
    plates: Optional[Iterable[str]] = None,
    target_date_suffix: Optional[str] = None,
    transfer_date: Optional[date] = None,
    operator_name: str = "Operator",
    notes: str = "",
) -> Dict[str, Any]:
    """
    Records a transfer of kits between bonds:
    - TRANSFER_IN: kits transferred into our bond from other bonds (+Transfer In).
    - TRANSFER_OUT: kits transferred from our bond to other bonds (-Transfer Out).
    """
    trf_date, def_suffix = resolve_date_and_suffix(transfer_date or target_date_suffix)
    suffix = target_date_suffix or def_suffix
    category = PlateCategory.PMO if str(plate_category).strip().upper() == "PMO" else PlateCategory.PSV
    t_type = (
        StockBondTransfer.TransferType.TRANSFER_OUT
        if str(transfer_type).strip().upper() in ("TRANSFER_OUT", "OUT", "BOND_TRANSFER_OUT")
        else StockBondTransfer.TransferType.TRANSFER_IN
    )

    clean_plates = parse_plate_input(plates) if plates else []
    count = len(clean_plates) if clean_plates else max(1, int(plates_count))

    trf_no = (transfer_number or "").strip()
    if not trf_no:
        prefix = "TRF-OUT" if t_type == StockBondTransfer.TransferType.TRANSFER_OUT else "TRF-IN"
        trf_no = f"{prefix}-{trf_date.strftime('%Y%m%d')}-{count}-{category}"

    with transaction.atomic():
        transfer, _ = StockBondTransfer.objects.get_or_create(
            transfer_number=trf_no,
            defaults={
                "transfer_type": t_type,
                "plate_category": category,
                "other_bond_name": other_bond_name or "Other Bond",
                "transfer_date": trf_date,
                "target_date_suffix": suffix,
                "plates_count": count,
                "operator_name": operator_name or "Operator",
                "notes": notes or "",
            },
        )
        transfer.transfer_type = t_type
        transfer.plate_category = category
        transfer.plates_count = count
        transfer.other_bond_name = other_bond_name or transfer.other_bond_name
        transfer.notes = notes or transfer.notes
        transfer.save()

        recon = compute_daily_reconciliation(suffix)

    return {
        "success": True,
        "transfer_number": transfer.transfer_number,
        "transfer_type": transfer.transfer_type,
        "plate_category": transfer.plate_category,
        "plates_count": transfer.plates_count,
        "other_bond_name": transfer.other_bond_name,
        "reconciliation": recon,
    }


def record_dispatch_scans(
    plates: Iterable[str],
    plate_category: str = PlateCategory.PSV,
    target_date_suffix: Optional[str] = None,
    bond_code: Optional[str] = None,
    operator_name: str = "Operator",
    dispatched_by: Optional[Any] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Records plates scanned when taken out of warehouse stock and issued to the
    installation/assembly floor for a shift.
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    category = PlateCategory.PMO if str(plate_category).strip().upper() == "PMO" else PlateCategory.PSV
    clean_plates, dup_count, dup_plates = parse_plate_input_with_stats(plates)
    if not clean_plates:
        raise ValueError("No valid license plate numbers provided for dispatch.")

    active_bond = bond_service.get_active_bond()
    b_code = (bond_code or active_bond.get("code", "AGM")).strip().upper()

    with transaction.atomic():
        existing_dispatches = set(
            StockDispatchScan.objects.filter(
                work_date_suffix=suffix,
                registration_number__in=clean_plates,
            ).values_list("registration_number", flat=True)
        )

        new_scans = []
        for p in clean_plates:
            if p not in existing_dispatches:
                new_scans.append(
                    StockDispatchScan(
                        registration_number=p,
                        plate_category=category,
                        work_date=work_d,
                        work_date_suffix=suffix,
                        bond_code=b_code,
                        dispatched_by=dispatched_by,
                        operator_name=operator_name or "Operator",
                        status=StockDispatchScan.Status.ON_LINE_ACTIVE,
                        notes=notes or "",
                    )
                )

        if new_scans:
            StockDispatchScan.objects.bulk_create(new_scans)

        recon = compute_daily_reconciliation(suffix)

    return {
        "success": True,
        "total_submitted": len(clean_plates) + dup_count,
        "unique_plates_count": len(clean_plates),
        "newly_dispatched": len(new_scans),
        "already_dispatched": len(existing_dispatches),
        "already_dispatched_plates": sorted(list(existing_dispatches)),
        "duplicate_scans_skipped": dup_count,
        "duplicate_plates": dup_plates,
        "reconciliation": recon,
    }


def record_return_scans(
    plates: Iterable[str],
    plate_category: str = PlateCategory.PSV,
    target_date_suffix: Optional[str] = None,
    reason: str = StockReturnScan.Reason.BIKE_NO_SHOW,
    operator_name: str = "Operator",
    returned_by: Optional[Any] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Records plates returned to stock uninstalled (bike no-show, defect, cancellation).
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    category = PlateCategory.PMO if str(plate_category).strip().upper() == "PMO" else PlateCategory.PSV
    clean_plates, dup_count, dup_plates = parse_plate_input_with_stats(plates)
    if not clean_plates:
        raise ValueError("No valid license plate numbers provided for return.")

    valid_reason = reason if reason in StockReturnScan.Reason.values else StockReturnScan.Reason.BIKE_NO_SHOW

    with transaction.atomic():
        existing_returns = set(
            StockReturnScan.objects.filter(
                work_date_suffix=suffix,
                registration_number__in=clean_plates,
            ).values_list("registration_number", flat=True)
        )

        new_returns = []
        for p in clean_plates:
            if p not in existing_returns:
                new_returns.append(
                    StockReturnScan(
                        registration_number=p,
                        plate_category=category,
                        work_date=work_d,
                        work_date_suffix=suffix,
                        returned_by=returned_by,
                        operator_name=operator_name or "Operator",
                        reason=valid_reason,
                        notes=notes or "",
                    )
                )

        if new_returns:
            StockReturnScan.objects.bulk_create(new_returns)

        # Update matching dispatch scans for this shift to RETURNED_TO_SAFE
        StockDispatchScan.objects.filter(
            work_date_suffix=suffix,
            registration_number__in=clean_plates,
        ).update(status=StockDispatchScan.Status.RETURNED_TO_SAFE)

        recon = compute_daily_reconciliation(suffix)

    return {
        "success": True,
        "total_submitted": len(clean_plates) + dup_count,
        "unique_plates_count": len(clean_plates),
        "newly_returned": len(new_returns),
        "already_returned": len(existing_returns),
        "already_returned_plates": sorted(list(existing_returns)),
        "duplicate_scans_skipped": dup_count,
        "duplicate_plates": dup_plates,
        "reconciliation": recon,
    }


def set_scheduled_target(
    scheduled_psv: int,
    scheduled_pmo: int = 0,
    target_date_suffix: Optional[str] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Manually feeds in the scheduled target number of plates to be installed
    under the bond for the day (PSV White and PMO Yellow).
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    with transaction.atomic():
        ledger, _ = DailyStockLedger.objects.get_or_create(
            work_date=work_d,
            defaults={"work_date_suffix": suffix},
        )
        ledger.scheduled_psv = max(0, int(scheduled_psv))
        ledger.scheduled_pmo = max(0, int(scheduled_pmo))
        ledger.scheduled_total = ledger.scheduled_psv + ledger.scheduled_pmo
        if notes:
            ledger.notes = notes
        ledger.save()

        recon = compute_daily_reconciliation(suffix)

    return {
        "success": True,
        "scheduled_psv": ledger.scheduled_psv,
        "scheduled_pmo": ledger.scheduled_pmo,
        "scheduled_total": ledger.scheduled_total,
        "reconciliation": recon,
    }


def set_opening_balances(
    opening_psv: int,
    opening_pmo: int = 0,
    target_date_suffix: Optional[str] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Manually sets or adjusts the Opening Balance for PSV (White) and PMO (Yellow).
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    with transaction.atomic():
        ledger, _ = DailyStockLedger.objects.get_or_create(
            work_date=work_d,
            defaults={"work_date_suffix": suffix},
        )
        ledger.opening_balance_psv = max(0, int(opening_psv))
        ledger.opening_balance_pmo = max(0, int(opening_pmo))
        ledger.opening_stock = ledger.opening_balance_psv + ledger.opening_balance_pmo
        if notes:
            ledger.notes = notes
        ledger.save()

        recon = compute_daily_reconciliation(suffix)

    return {
        "success": True,
        "opening_balance_psv": ledger.opening_balance_psv,
        "opening_balance_pmo": ledger.opening_balance_pmo,
        "opening_stock": ledger.opening_stock,
        "reconciliation": recon,
    }


def set_physical_count(
    physical_count: int,
    target_date_suffix: Optional[str] = None,
    notes: str = "",
) -> Dict[str, Any]:
    """
    Manually sets the physical count audit for the safe room/storage box and updates variance.
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    with transaction.atomic():
        ledger, _ = DailyStockLedger.objects.get_or_create(
            work_date=work_d,
            defaults={"work_date_suffix": suffix},
        )
        ledger.physical_count = max(0, int(physical_count))
        ledger.variance = ledger.physical_count - ledger.closing_stock
        if notes:
            ledger.notes = notes
        ledger.save()

        recon = compute_daily_reconciliation(suffix)

    return {
        "success": True,
        "physical_count": ledger.physical_count,
        "variance": ledger.variance,
        "closing_stock": ledger.closing_stock,
        "reconciliation": recon,
    }


def record_stock_taking_audit(
    scanned_plates: Iterable[str],
    target_date_suffix: Optional[str] = None,
    operator_name: str = "Operator",
    notes: str = "",
) -> Dict[str, Any]:
    """
    Performs full safe room physical stock-taking / retaking by scanning all physical
    plates currently inside the safe room or storage box.

    Reconciles scanned plates against the calculated closing stock balance:
    - Physical Scanned Count
    - Book Closing Stock
    - Variance = Physical Scanned Count - Book Closing Stock
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    clean_plates, dup_count, dup_plates = parse_plate_input_with_stats(scanned_plates)

    recon = compute_daily_reconciliation(suffix)
    book_closing = recon.get("report_table", {}).get("rows", [])
    closing_row = next((r for r in book_closing if r["metric"] == "Closing Balance"), {})
    book_closing_total = closing_row.get("total", 0)
    book_closing_psv = closing_row.get("psv", 0)
    book_closing_pmo = closing_row.get("pmo", 0)

    # Classify scanned plates into PSV vs PMO
    pmo_plates_set = {
        normalizer.canonicalize(p)
        for p in StockDispatchScan.objects.filter(plate_category=PlateCategory.PMO).values_list("registration_number", flat=True)
    }
    pmo_plates_set.update(
        normalizer.canonicalize(p)
        for p in StockDeliveryItem.objects.filter(plate_category=PlateCategory.PMO).values_list("registration_number", flat=True)
    )

    scanned_psv_count = 0
    scanned_pmo_count = 0
    clean_plates_canonical = set()
    for p in clean_plates:
        c = normalizer.canonicalize(p)
        clean_plates_canonical.add(c)
        if c in pmo_plates_set:
            scanned_pmo_count += 1
        else:
            scanned_psv_count += 1

    total_scanned = len(clean_plates)
    total_variance = total_scanned - book_closing_total
    variance_psv = scanned_psv_count - book_closing_psv
    variance_pmo = scanned_pmo_count - book_closing_pmo

    with transaction.atomic():
        ledger, _ = DailyStockLedger.objects.get_or_create(
            work_date=work_d,
            defaults={"work_date_suffix": suffix},
        )
        ledger.physical_count = total_scanned
        ledger.variance = total_variance
        if notes:
            ledger.notes = notes
        ledger.save()

        updated_recon = compute_daily_reconciliation(suffix)

    return {
        "success": True,
        "work_date": work_d.isoformat(),
        "work_date_suffix": suffix,
        "physical_count": total_scanned,
        "total_scanned": total_scanned,
        "physical_psv": scanned_psv_count,
        "physical_pmo": scanned_pmo_count,
        "book_closing_stock": book_closing_total,
        "book_closing_total": book_closing_total,
        "book_closing_psv": book_closing_psv,
        "book_closing_pmo": book_closing_pmo,
        "variance": total_variance,
        "variance_psv": variance_psv,
        "variance_pmo": variance_pmo,
        "total_submitted": len(clean_plates) + dup_count,
        "duplicate_scans_skipped": dup_count,
        "duplicate_plates": dup_plates,
        "scanned_plates": clean_plates,
        "reconciliation": updated_recon,
    }


def compute_daily_reconciliation(target_date_suffix: Optional[str] = None) -> Dict[str, Any]:
    """
    Computes comprehensive daily plate and stock reconciliation report for target date,
    strictly following the user's required metrics:
    - Opening Balance
    - Kits Received
    - Bond Transfer In
    - Bond Transfer Out
    - Scheduled (Target)
    - Kits Installed (Actual)
    - Closing Balance = Opening + Received + Transfer In - Transfer Out - Installed
    Differentiated between Public White (PSV) and Private Yellow (PMO) + Combined Total.
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)

    ledger, created = DailyStockLedger.objects.get_or_create(
        work_date=work_d,
        defaults={
            "work_date_suffix": suffix,
            "opening_stock": 0,
            "opening_balance_psv": 0,
            "opening_balance_pmo": 0,
        },
    )

    # Auto-carry over previous shift's closing balance if newly created & 0
    if created and ledger.opening_balance_psv == 0 and ledger.opening_balance_pmo == 0:
        prev_ledger = (
            DailyStockLedger.objects.filter(work_date__lt=work_d)
            .order_by("-work_date")
            .first()
        )
        if prev_ledger:
            ledger.opening_balance_psv = max(0, prev_ledger.closing_balance_psv)
            ledger.opening_balance_pmo = max(0, prev_ledger.closing_balance_pmo)
            ledger.opening_stock = ledger.opening_balance_psv + ledger.opening_balance_pmo
            ledger.save(update_fields=["opening_balance_psv", "opening_balance_pmo", "opening_stock"])

    # 1. Inbound Deliveries (Kits Received)
    deliveries_qs = StockDelivery.objects.filter(
        Q(target_date_suffix=suffix) | Q(delivery_date=work_d)
    )
    kits_received_psv = 0
    kits_received_pmo = 0
    for itm in StockDeliveryItem.objects.filter(delivery__in=deliveries_qs):
        if itm.plate_category == PlateCategory.PMO:
            kits_received_pmo += 1
        else:
            kits_received_psv += 1
    total_kits_received = kits_received_psv + kits_received_pmo

    # 2. Bond Transfers (Transfer In / Transfer Out)
    transfers_qs = StockBondTransfer.objects.filter(
        Q(target_date_suffix=suffix) | Q(transfer_date=work_d)
    )
    transfer_in_psv = sum(
        t.plates_count
        for t in transfers_qs
        if t.transfer_type == StockBondTransfer.TransferType.TRANSFER_IN
        and t.plate_category == PlateCategory.PSV
    )
    transfer_in_pmo = sum(
        t.plates_count
        for t in transfers_qs
        if t.transfer_type == StockBondTransfer.TransferType.TRANSFER_IN
        and t.plate_category == PlateCategory.PMO
    )
    total_transfer_in = transfer_in_psv + transfer_in_pmo

    transfer_out_psv = sum(
        t.plates_count
        for t in transfers_qs
        if t.transfer_type == StockBondTransfer.TransferType.TRANSFER_OUT
        and t.plate_category == PlateCategory.PSV
    )
    transfer_out_pmo = sum(
        t.plates_count
        for t in transfers_qs
        if t.transfer_type == StockBondTransfer.TransferType.TRANSFER_OUT
        and t.plate_category == PlateCategory.PMO
    )
    total_transfer_out = transfer_out_psv + transfer_out_pmo

    active_bond = bond_service.get_active_bond()
    active_bond_code = active_bond.get("code", "AGM")
    active_bond_name = active_bond.get("name", "AGM Bonded Warehouse")

    if not ledger.warehouse_name or ledger.warehouse_name in ("Bond Warehouse", "AGM Bonded Warehouse", "Warehouse Stock / Bond", "Warehouse Stock"):
        ledger.warehouse_name = active_bond_name

    # 3. Kits Installed (Orders & Archive for this day)
    orders_qs = list(InstallationOrder.objects.filter(order_number__endswith=suffix))
    scoped_orders: List[InstallationOrder] = []
    cross_bond_orders: List[InstallationOrder] = []

    for o in orders_qs:
        if bond_service.classify_order_bond_scope(o, active_code=active_bond_code) == "ACTIVE_BOND":
            scoped_orders.append(o)
        else:
            cross_bond_orders.append(o)

    installed_orders_qs = [
        o for o in scoped_orders
        if o.is_archived or (o.order_status or "").strip().lower() == "installed"
    ]

    # Classify installed orders into PSV vs PMO
    # Check dispatches, inbound delivery items, and verification audit logs for PMO yellow
    pmo_plates_set = {
        normalizer.canonicalize(p)
        for p in StockDispatchScan.objects.filter(plate_category=PlateCategory.PMO).values_list("registration_number", flat=True)
    }
    pmo_plates_set.update(
        normalizer.canonicalize(p)
        for p in StockDeliveryItem.objects.filter(plate_category=PlateCategory.PMO).values_list("registration_number", flat=True)
    )
    for l in SubmissionAuditLog.objects.filter(
        pair__order__in=installed_orders_qs, message__icontains="Category: PMO"
    ):
        if l.pair and l.pair.order:
            pmo_plates_set.add(normalizer.canonicalize(l.pair.order.registration_number))

    installed_psv_count = 0
    installed_pmo_count = 0
    for o in installed_orders_qs:
        c_p = normalizer.canonicalize(o.registration_number)
        if c_p in pmo_plates_set:
            installed_pmo_count += 1
        else:
            installed_psv_count += 1

    total_installed = installed_psv_count + installed_pmo_count

    # 4. Closing Balance calculations:
    # Closing = Opening + Received + Transfer In - Transfer Out - Installed
    closing_psv = (
        ledger.opening_balance_psv
        + kits_received_psv
        + transfer_in_psv
        - transfer_out_psv
        - installed_psv_count
    )
    closing_pmo = (
        ledger.opening_balance_pmo
        + kits_received_pmo
        + transfer_in_pmo
        - transfer_out_pmo
        - installed_pmo_count
    )
    total_closing = closing_psv + closing_pmo

    # 5. Scheduled variance: Actual Installed - Target Scheduled
    scheduled_total = ledger.scheduled_psv + ledger.scheduled_pmo
    variance_psv = installed_psv_count - ledger.scheduled_psv
    variance_pmo = installed_pmo_count - ledger.scheduled_pmo
    variance_total = total_installed - scheduled_total

    # 6. Floor Dispatched & Returns Reconciliation (Graduated Discrepancy Scale)
    dispatches_qs = StockDispatchScan.objects.filter(
        Q(work_date_suffix=suffix) | Q(work_date=work_d)
    ).filter(
        Q(bond_code__iexact=active_bond_code) | Q(bond_code="") | Q(bond_code__isnull=True)
    )
    dispatched_plates_map = {
        normalizer.canonicalize(s.registration_number): s
        for s in dispatches_qs
        if s.registration_number
    }
    dispatched_count = len(dispatched_plates_map)

    returns_qs = StockReturnScan.objects.filter(
        Q(work_date_suffix=suffix) | Q(work_date=work_d)
    )
    returned_plates_map = {
        normalizer.canonicalize(s.registration_number): s
        for s in returns_qs
        if s.registration_number
    }
    returned_count = len(returned_plates_map)
    net_dispatched = max(0, dispatched_count - returned_count)

    scoped_order_by_plate = {
        normalizer.canonicalize(o.registration_number): o
        for o in scoped_orders
        if o.registration_number
    }

    # Query local companion verification pairs / evidence photos to identify physical fitments
    evidence_plates_set: Set[str] = {
        normalizer.canonicalize(p)
        for p in VehicleInstallationPair.objects.exclude(registration_number_detected="").values_list(
            "registration_number_detected", flat=True
        )
        if p
    }
    evidence_plates_set.update({
        normalizer.canonicalize(p)
        for p in EvidenceImage.objects.exclude(detected_plate="").values_list("detected_plate", flat=True)
        if p
    })

    reconciled_installed: List[str] = []
    returned_to_safe: List[str] = []
    on_line_active: List[str] = []
    pending_system_sync: List[str] = []
    unresolved_discrepancy: List[str] = []
    scans_to_update: List[StockDispatchScan] = []

    for c_plate, scan in dispatched_plates_map.items():
        orig_plate = scan.registration_number
        if c_plate in returned_plates_map:
            returned_to_safe.append(orig_plate)
            if scan.status != StockDispatchScan.Status.RETURNED_TO_SAFE:
                scan.status = StockDispatchScan.Status.RETURNED_TO_SAFE
                scans_to_update.append(scan)
            continue

        matched_order = scoped_order_by_plate.get(c_plate)
        if matched_order:
            if matched_order.is_archived or (matched_order.order_status or "").strip().lower() == "installed":
                reconciled_installed.append(orig_plate)
                if scan.status != StockDispatchScan.Status.RECONCILED_INSTALLED:
                    scan.status = StockDispatchScan.Status.RECONCILED_INSTALLED
                    scans_to_update.append(scan)
            else:
                on_line_active.append(orig_plate)
                if scan.status != StockDispatchScan.Status.ON_LINE_ACTIVE:
                    scan.status = StockDispatchScan.Status.ON_LINE_ACTIVE
                    scans_to_update.append(scan)
        else:
            if c_plate in evidence_plates_set:
                pending_system_sync.append(orig_plate)
                if scan.status != StockDispatchScan.Status.PENDING_SYSTEM_SYNC:
                    scan.status = StockDispatchScan.Status.PENDING_SYSTEM_SYNC
                    scans_to_update.append(scan)
            else:
                unresolved_discrepancy.append(orig_plate)
                if scan.status != StockDispatchScan.Status.UNRESOLVED_DISCREPANCY:
                    scan.status = StockDispatchScan.Status.UNRESOLVED_DISCREPANCY
                    scans_to_update.append(scan)

    if scans_to_update:
        StockDispatchScan.objects.bulk_update(scans_to_update, ["status"])

    itms_pending_count = len([
        o for o in scoped_orders
        if not o.is_archived and (o.order_status or "").strip().lower() != "installed"
    ])

    # 7. Update DailyStockLedger with calculated snapshot
    ledger.opening_stock = ledger.opening_balance_psv + ledger.opening_balance_pmo
    ledger.kits_received_psv = kits_received_psv
    ledger.kits_received_pmo = kits_received_pmo
    ledger.delivered_count = total_kits_received

    ledger.bond_transfer_in_psv = transfer_in_psv
    ledger.bond_transfer_in_pmo = transfer_in_pmo
    ledger.bond_transfer_in_total = total_transfer_in

    ledger.bond_transfer_out_psv = transfer_out_psv
    ledger.bond_transfer_out_pmo = transfer_out_pmo
    ledger.bond_transfer_out_total = total_transfer_out

    ledger.scheduled_total = scheduled_total
    ledger.kits_installed_psv = installed_psv_count
    ledger.kits_installed_pmo = installed_pmo_count
    ledger.installed_count = total_installed

    ledger.closing_balance_psv = closing_psv
    ledger.closing_balance_pmo = closing_pmo
    ledger.closing_stock = total_closing

    ledger.dispatched_count = dispatched_count
    ledger.returned_count = returned_count
    ledger.pending_count = itms_pending_count
    ledger.unallocated_count = len(unresolved_discrepancy)

    if ledger.physical_count is not None:
        ledger.variance = ledger.physical_count - total_closing
    else:
        ledger.variance = 0

    ledger.last_reconciled_at = timezone.now()
    ledger.save()

    formatted_date = format_date_suffix_readable(suffix)

    return {
        "work_date": work_d.isoformat(),
        "work_date_suffix": suffix,
        "formatted_date": formatted_date,
        "warehouse_name": ledger.warehouse_name,
        "active_bond_code": active_bond_code,
        # Structured report columns
        "report_table": {
            "columns": [
                "Description",
                "Public White (PSV)",
                "Private Yellow (PMO)",
                "Total Combined (Bond)",
            ],
            "rows": [
                {
                    "metric": "Opening Balance",
                    "psv": ledger.opening_balance_psv,
                    "pmo": ledger.opening_balance_pmo,
                    "total": ledger.opening_stock,
                    "note": "Physical count at start of day",
                },
                {
                    "metric": "Kits Received",
                    "psv": kits_received_psv,
                    "pmo": kits_received_pmo,
                    "total": total_kits_received,
                    "note": "Shipments received from supplier",
                },
                {
                    "metric": "Bond Transfer In",
                    "psv": transfer_in_psv,
                    "pmo": transfer_in_pmo,
                    "total": total_transfer_in,
                    "note": "Kits transferred in from other bonds",
                },
                {
                    "metric": "Bond Transfer Out",
                    "psv": transfer_out_psv,
                    "pmo": transfer_out_pmo,
                    "total": total_transfer_out,
                    "note": "Kits transferred out to other bonds",
                },
                {
                    "metric": "Scheduled (Target)",
                    "psv": ledger.scheduled_psv,
                    "pmo": ledger.scheduled_pmo,
                    "total": scheduled_total,
                    "note": "Target installation under bond (manually entered)",
                },
                {
                    "metric": "Kits Installed (Actual)",
                    "psv": installed_psv_count,
                    "pmo": installed_pmo_count,
                    "total": total_installed,
                    "note": "Installed & verified in orders / archive",
                },
                {
                    "metric": "Closing Balance",
                    "psv": closing_psv,
                    "pmo": closing_pmo,
                    "total": total_closing,
                    "note": "Opening + Received + Transfer In - Transfer Out - Installed",
                },
                {
                    "metric": "Scheduled Target Variance",
                    "psv": variance_psv,
                    "pmo": variance_pmo,
                    "total": variance_total,
                    "note": "Actual Installed vs Scheduled Target",
                },
            ],
        },
        # Floor Operations & Graduated Discrepancy Audit
        "floor_operations": {
            "dispatched_count": dispatched_count,
            "returned_count": returned_count,
            "net_dispatched": net_dispatched,
            "itms_pending_count": itms_pending_count,
            "cross_bond_count": len(cross_bond_orders),
            "unallocated_discrepancy": len(unresolved_discrepancy),
            "unallocated_plates": unresolved_discrepancy,
            "graduated_scale": {
                "reconciled_installed": {
                    "count": len(reconciled_installed),
                    "plates": reconciled_installed,
                    "label": "Reconciled Installed (Verified in Order/Archive)",
                    "badge": "success",
                },
                "returned_to_safe": {
                    "count": len(returned_to_safe),
                    "plates": returned_to_safe,
                    "label": "Returned to Safe Room",
                    "badge": "info",
                },
                "on_line_active": {
                    "count": len(on_line_active),
                    "plates": on_line_active,
                    "label": "On Line / In Progress (Dispatched to Bay)",
                    "badge": "warning",
                },
                "pending_system_sync": {
                    "count": len(pending_system_sync),
                    "plates": pending_system_sync,
                    "label": "Pending System Sync (Local Evidence Captured)",
                    "badge": "secondary",
                },
                "unresolved_discrepancy": {
                    "count": len(unresolved_discrepancy),
                    "plates": unresolved_discrepancy,
                    "label": "Unresolved Discrepancy (Requires MVR Investigation)",
                    "badge": "danger",
                },
            },
        },
        "is_closed": ledger.is_closed,
        "last_reconciled_at": ledger.last_reconciled_at.strftime("%Y-%m-%d %H:%M:%S") if ledger.last_reconciled_at else "",
        "unallocated_plates": unresolved_discrepancy,
    }


def export_stock_reconciliation_csv(target_date_suffix: Optional[str] = None) -> str:
    """Generates the clean CSV export strictly adhering to the user's report format."""
    recon = compute_daily_reconciliation(target_date_suffix)
    suffix = recon["work_date_suffix"]
    formatted_date = recon["formatted_date"]

    out = io.StringIO()
    writer = csv.writer(out)

    writer.writerow(["ITMS BOND PHYSICAL STOCK REPORT"])
    writer.writerow(["Work Date", formatted_date, f"Suffix: {suffix}"])
    writer.writerow(["Warehouse / Bond", recon.get("warehouse_name", "Bond Warehouse")])
    writer.writerow(["Generated At", timezone.now().strftime("%Y-%m-%d %H:%M:%S")])
    writer.writerow([])

    table = recon["report_table"]
    writer.writerow(table["columns"] + ["Formula / Description Note"])

    for row in table["rows"]:
        writer.writerow([
            row["metric"],
            row["psv"],
            row["pmo"],
            row["total"],
            row["note"],
        ])

    writer.writerow([])
    writer.writerow(["FLOOR OPERATIONS & DISCREPANCY AUDIT"])
    floor = recon["floor_operations"]
    writer.writerow(["Plates Dispatched to Floor", floor["dispatched_count"]])
    writer.writerow(["Plates Returned Uninstalled", floor["returned_count"]])
    writer.writerow(["Net Plates on Installation Floor", floor["net_dispatched"]])
    writer.writerow(["Active in Orders Queue (Pending)", floor["itms_pending_count"]])
    writer.writerow(["UNALLOCATED DISCREPANCY", floor["unallocated_discrepancy"]])
    writer.writerow([])

    writer.writerow(["DISPATCHED PLATES DETAIL AUDIT"])
    writer.writerow(["#", "Plate Number", "Category", "Dispatched At", "Operator", "Status", "Notes"])

    dispatches = StockDispatchScan.objects.filter(work_date_suffix=suffix).order_by("dispatched_at")
    for idx, d in enumerate(dispatches, start=1):
        writer.writerow([
            idx,
            d.registration_number,
            d.plate_category,
            d.dispatched_at.strftime("%Y-%m-%d %H:%M:%S") if d.dispatched_at else "",
            d.operator_name,
            d.status,
            d.notes or "—",
        ])

    return out.getvalue()


def get_delivery_notes_for_date(target_date_suffix: Optional[Any] = None) -> List[Dict[str, Any]]:
    """
    Returns all stored delivery notes for a specific date or suffix, with itemized
    plate counts, paper note references, timestamps, and plate lists.
    """
    deliv_date, suffix = resolve_date_and_suffix(target_date_suffix)
    deliveries_qs = (
        StockDelivery.objects.filter(
            Q(target_date_suffix=suffix) | Q(delivery_date=deliv_date)
        )
        .prefetch_related("items")
        .order_by("-created_at")
    )

    results: List[Dict[str, Any]] = []
    for d in deliveries_qs:
        plates = list(d.items.values_list("registration_number", flat=True))
        results.append({
            "id": d.id,
            "delivery_number": d.delivery_number,
            "paper_note_reference": d.paper_note_reference or "",
            "supplier": d.supplier,
            "plate_category": d.plate_category,
            "delivery_date": d.delivery_date.isoformat() if d.delivery_date else "",
            "target_date_suffix": d.target_date_suffix,
            "total_plates_count": d.total_plates_count or len(plates),
            "operator_name": d.operator_name,
            "notes": d.notes or "",
            "created_at": d.created_at.strftime("%Y-%m-%d %H:%M:%S") if d.created_at else "",
            "has_image": bool(d.delivery_note_image),
            "image_url": d.delivery_note_image.url if d.delivery_note_image else None,
            "plates": plates,
        })

    return results


def get_mvr_unallocated_docket(target_date_suffix: Optional[Any] = None) -> Dict[str, Any]:
    """
    Generates the actionable MVR Allocation Discrepancy Docket for a specific shift date.
    Returns:
    - raw_plates: newline-separated plate strings for MVR officer to paste into ITMS search.
    - formatted_docket: formal text docket for team leaders with AGM Bond header and shift metrics.
    - unallocated_plates: array of unallocated plates.
    - count: total unallocated count.
    """
    recon = compute_daily_reconciliation(target_date_suffix)
    suffix = recon["work_date_suffix"]
    formatted_date = recon["formatted_date"]
    warehouse = recon.get("warehouse_name", "AGM Bonded Warehouse")
    floor = recon.get("floor_operations", {})

    unallocated_plates = floor.get("unallocated_plates", [])
    raw_plates = "\n".join(unallocated_plates)

    graduated_scale = floor.get("graduated_scale", {})
    reconciled_installed = graduated_scale.get("reconciled_installed", {}).get("count", 0)
    returned_to_safe = graduated_scale.get("returned_to_safe", {}).get("count", 0)
    on_line_active = graduated_scale.get("on_line_active", {}).get("count", 0)
    pending_sync_data = graduated_scale.get("pending_system_sync", {})
    pending_sync_count = pending_sync_data.get("count", 0)
    pending_sync_plates = pending_sync_data.get("plates", [])
    cross_bond_count = floor.get("cross_bond_count", 0)
    active_code = recon.get("active_bond_code", "AGM")

    now_str = timezone.now().strftime("%Y-%m-%d %H:%M:%S")
    count = len(unallocated_plates)

    plates_block = ""
    if unallocated_plates:
        plates_block = "\n".join(f"  {idx:2d}. {p}" for idx, p in enumerate(unallocated_plates, start=1))
    else:
        plates_block = "  [None - No unresolved discrepancies for this shift]"

    pending_block_lines = []
    if pending_sync_plates:
        pending_block_lines.append("")
        pending_block_lines.append(f"PENDING SYSTEM SYNC NOTICE ({pending_sync_count} Plate(s) with Local Camera Evidence):")
        pending_block_lines.append("These plates were physically fitted and companion photos were ingested,")
        pending_block_lines.append("but the order has not yet been synced or created in ITMS:")
        for idx, p in enumerate(pending_sync_plates, start=1):
            pending_block_lines.append(f"  • {p}")

    docket_lines = [
        "==================================================",
        f"{warehouse.upper()} — MVR ALLOCATION EXCEPTION DOCKET",
        f"Shift Date: {formatted_date} (Suffix: {suffix})",
        f"Operating Facility: {warehouse} (Code: {active_code})",
        f"Generated At: {now_str}",
        "==================================================",
        "SHIFT PHYSICAL DISPATCH & ITMS SUMMARY:",
        f"  • Morning Dispatched to Line:     {floor.get('dispatched_count', 0):>4}",
        f"  • Evening Returned to Safe Room:  {floor.get('returned_count', 0):>4}",
        f"  • Net Physically Fitted on Line:  {floor.get('net_dispatched', 0):>4}",
        f"  • ITMS Active Orders Queue:       {floor.get('itms_pending_count', 0):>4}",
        f"  • Cross-Bond Orders Excluded:     {cross_bond_count:>4}",
        "--------------------------------------------------",
        "GRADUATED DISCREPANCY AUDIT SCALE:",
        f"  🟢 1. Reconciled Installed:       {reconciled_installed:>4}",
        f"  🟢 2. Returned to Safe Room:      {returned_to_safe:>4}",
        f"  🟡 3. On-Line Active in ITMS:     {on_line_active:>4}",
        f"  🟠 4. Pending System Sync:        {pending_sync_count:>4}",
        f"  🔴 5. UNRESOLVED DISCREPANCY:     {count:>4}",
        "==================================================",
        "ACTION REQUIRED FOR MVR OFFICER:",
        f"The following {count} plate(s) were physically issued to the line,",
        "are NOT returned to safe room, and have NO matching ITMS order or archive.",
        "Please search and allocate these kits immediately in ITMS:",
        "",
        plates_block,
    ]
    if pending_block_lines:
        docket_lines.extend(pending_block_lines)

    docket_lines.extend([
        "",
        "==================================================",
        "ITMS Verification & Daily Stock Ledger Audit — v1.0.5",
        "==================================================",
    ])
    formatted_docket = "\n".join(docket_lines)

    return {
        "success": True,
        "work_date": recon["work_date"],
        "work_date_suffix": suffix,
        "formatted_date": formatted_date,
        "warehouse_name": warehouse,
        "active_bond_code": active_code,
        "unallocated_plates": unallocated_plates,
        "count": count,
        "pending_sync_count": pending_sync_count,
        "pending_sync_plates": pending_sync_plates,
        "raw_plates": raw_plates,
        "formatted_docket": formatted_docket,
        "floor_operations": floor,
    }

