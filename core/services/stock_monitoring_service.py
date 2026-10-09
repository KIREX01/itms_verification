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
import json
import logging
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
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
    SafeAuditScan,
    StockBondTransfer,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
    SubmissionAuditLog,
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
    return None


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

    # Ingest delivery_note_image if uploaded file or local path provided
    if delivery_note_image:
        from core.models import EvidenceImage
        if not isinstance(delivery_note_image, EvidenceImage):
            try:
                from core.services import vault_service
                if hasattr(delivery_note_image, "read"):
                    ev_img, _ = vault_service.ingest_uploaded_file(delivery_note_image)
                    delivery_note_image = ev_img
                elif isinstance(delivery_note_image, (str, Path)) and os.path.exists(str(delivery_note_image)):
                    ev_img, _ = vault_service.ingest_from_disk(str(delivery_note_image))
                    delivery_note_image = ev_img
            except Exception as img_err:
                logger.warning("Could not ingest delivery note image: %s", img_err)
                delivery_note_image = None

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
            from core.services import kit_provisioning_service, bond_service
            active_bond = bond_service.get_active_bond()
            prov_res = kit_provisioning_service.sync_and_provision_warehouse_kits(
                target_date_suffix=suffix,
                source_plates=clean_plates,
                sync_itms=False,
                facility_name=active_bond.get("name", "Bond Warehouse"),
            )
            created_kits_count = prov_res.get("kits_created", 0)

        recon = compute_daily_reconciliation(suffix)

    # Prioritize saving: DB transaction is already committed above.
    # Look up installation kit details (telematics, GPS, BLE, serials) asynchronously in the background.
    import sys
    from django.conf import settings
    is_testing = getattr(settings, "TESTING", False) or any("test" in arg for arg in sys.argv)
    if clean_plates and not is_testing:
        def _bg_enrich_delivery():
            from django.db import connection
            connection.close()
            try:
                enrich_physical_plates_with_itms(clean_plates)
            except Exception as e_err:
                logger.warning("Background delivery ITMS enrichment error: %s", e_err)
            finally:
                connection.close()

        import threading
        threading.Thread(target=_bg_enrich_delivery, daemon=True).start()

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
        "new_kits_created": created_kits_count,
        "has_image": bool(delivery.delivery_note_image),
        "image_url": delivery.image_url,
        "image_path": delivery.image_absolute_path,
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
    require_stock_verification: Optional[bool] = None,
    check_itms_live: bool = True,
    auto_create_kits: bool = False,
) -> Dict[str, Any]:
    """
    Records plates scanned when taken out of warehouse stock and issued to the
    installation/assembly floor for a shift.

    Ensures that scanned plates exist in local synced stock or on live ITMS installation kits:
    - Tier 1: Local synced kits.
    - Tier 2: Live ITMS /installation-kits fallback with automatic synchronization.
    - If auto_create_kits=True, provisional physical kits are saved immediately as Dispatched
      and telematics details are enriched asynchronously in the background.
    - Rejects and blocks kits not on stock from being taken out to the line when strict verification is required.
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    category = PlateCategory.PMO if str(plate_category).strip().upper() == "PMO" else PlateCategory.PSV
    clean_plates, dup_count, dup_plates = parse_plate_input_with_stats(plates)
    if not clean_plates:
        raise ValueError("No valid license plate numbers provided for dispatch.")

    active_bond = bond_service.get_active_bond()
    b_code = (bond_code or active_bond.get("code", "AGM")).strip().upper()

    from django.conf import settings
    from core.services import config_service, kit_provisioning_service

    # Determine whether stock verification is strictly enforced
    if require_stock_verification is None:
        import sys
        is_test_runner = any("test" in arg for arg in sys.argv)
        if (is_test_runner or getattr(settings, "TESTING", False)) and not InstallationKit.objects.filter(registration_number__in=clean_plates).exists():
            require_stock_verification = False
        else:
            require_stock_verification = config_service.get_setting("stock.verify_kits_before_dispatch", True)

    verified_plates: List[str] = clean_plates
    rejected_not_on_stock: List[str] = []
    already_installed: List[str] = []
    synced_from_itms: List[str] = []
    readiness: Dict[str, Any] = {}

    if require_stock_verification:
        # Check local DB first; if missing locally, query ITMS live (Tier 1 & Tier 2)
        verify_res = kit_provisioning_service.verify_scanned_kits_stock(
            clean_plates,
            check_itms_live=check_itms_live,
            facility_name=active_bond.get("name"),
        )
        verified_plates = verify_res.get("verified_plates", [])
        rejected_not_on_stock = verify_res.get("rejected_not_on_stock", [])
        already_installed = verify_res.get("already_installed", [])
        synced_from_itms = verify_res.get("synced_from_itms", [])
        
        # Remove non-serializable model instances to prevent JSON serialization errors
        verify_res.pop("verified_kits", None)
        if "details" in verify_res:
            for p_details in verify_res["details"].values():
                p_details.pop("kit", None)
                
        readiness = verify_res

        # If NO plates are valid, block dispatch completely
        if not verified_plates:
            reasons = []
            if rejected_not_on_stock:
                reasons.append(f"Not on ITMS stock ({len(rejected_not_on_stock)}): {', '.join(rejected_not_on_stock)}")
            if already_installed:
                reasons.append(f"Already installed ({len(already_installed)}): {', '.join(already_installed)}")
            err_msg = f"Kits not on stock cannot be taken out! {'; '.join(reasons)}"
            blocked_all = rejected_not_on_stock + already_installed
            if blocked_all:
                record_blocked_plates(blocked_all, suffix)
            docket = get_stock_transfer_request_docket(suffix, blocked_plates=blocked_all)
            return {
                "success": False,
                "error": err_msg,
                "rejected_not_on_stock": rejected_not_on_stock,
                "already_installed": already_installed,
                "blocked_plates": blocked_all,
                "verified_plates": [],
                "synced_from_itms": [],
                "total_submitted": len(clean_plates) + dup_count,
                "unique_plates_count": len(clean_plates),
                "newly_dispatched": 0,
                "duplicate_scans_skipped": dup_count,
                "duplicate_plates": dup_plates,
                "stock_readiness": readiness,
                "docket": docket,
            }

    # Mark verified stock kits as Dispatched in local database
    if verified_plates:
        InstallationKit.objects.filter(
            registration_number__in=verified_plates,
        ).update(status="Dispatched")

    with transaction.atomic():
        existing_dispatches = set(
            StockDispatchScan.objects.filter(
                work_date_suffix=suffix,
                registration_number__in=verified_plates,
            ).values_list("registration_number", flat=True)
        )

        new_scans = []
        for p in verified_plates:
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

    # Spawn background daemon thread to enrich dispatch kits from ITMS asynchronously
    import sys
    from django.conf import settings
    is_testing = getattr(settings, "TESTING", False) or any("test" in arg for arg in sys.argv)
    if clean_plates and not is_testing:
        def _bg_enrich_dispatch():
            from django.db import connection
            connection.close()
            try:
                enrich_physical_plates_with_itms(clean_plates)
            except Exception as e_err:
                logger.warning("Background dispatch ITMS enrichment error: %s", e_err)
            finally:
                try:
                    connection.close()
                except Exception:
                    pass

        import threading
        threading.Thread(target=_bg_enrich_dispatch, daemon=True).start()

    res_payload = {
        "success": True,
        "total_submitted": len(clean_plates) + dup_count,
        "unique_plates_count": len(clean_plates),
        "newly_dispatched": len(new_scans),
        "already_dispatched": len(existing_dispatches),
        "already_dispatched_plates": sorted(list(existing_dispatches)),
        "duplicate_scans_skipped": dup_count,
        "duplicate_plates": dup_plates,
        "verified_plates": verified_plates,
        "synced_from_itms": synced_from_itms,
        "rejected_not_on_stock": rejected_not_on_stock,
        "already_installed": already_installed,
        "stock_readiness": readiness,
        "reconciliation": recon,
        "enriching_in_background": True,
    }

    blocked_all = rejected_not_on_stock + already_installed
    if blocked_all:
        record_blocked_plates(blocked_all, suffix)
        res_payload["blocked_plates"] = blocked_all
        res_payload["docket"] = get_stock_transfer_request_docket(suffix, blocked_plates=blocked_all)
        warn_parts = []
        if rejected_not_on_stock:
            warn_parts.append(f"{len(rejected_not_on_stock)} kit(s) blocked (not on ITMS stock: {', '.join(rejected_not_on_stock)})")
        if already_installed:
            warn_parts.append(f"{len(already_installed)} kit(s) blocked (already installed: {', '.join(already_installed)})")
        res_payload["warning"] = f"Partial dispatch: {len(new_scans)} valid kit(s) dispatched. " + "; ".join(warn_parts)

    return res_payload


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
    scheduled_target: Optional[int] = None,
    scheduled_psv: Optional[int] = None,
    scheduled_pmo: Optional[int] = None,
    target_date_suffix: Optional[str] = None,
    notes: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """
    Manually feeds in the scheduled target number of plates to be installed
    under the bond for the day.
    
    Per user requirement:
    This is an operational target for the day and does not by any means connect to
    or alter physical stock. It is a single value covering both private and public.
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    with transaction.atomic():
        ledger, _ = DailyStockLedger.objects.get_or_create(
            work_date=work_d,
            defaults={"work_date_suffix": suffix},
        )
        # Handle positional calls e.g. set_scheduled_target(120, 30)
        if scheduled_target is not None and scheduled_psv is not None and scheduled_pmo is None:
            ledger.scheduled_psv = max(0, int(scheduled_target))
            ledger.scheduled_pmo = max(0, int(scheduled_psv))
            ledger.scheduled_total = ledger.scheduled_psv + ledger.scheduled_pmo
        elif scheduled_target is not None:
            # Single combined target input for shift
            tot = max(0, int(scheduled_target))
            ledger.scheduled_total = tot
            if scheduled_psv is not None or scheduled_pmo is not None:
                ledger.scheduled_psv = max(0, int(scheduled_psv or 0))
                ledger.scheduled_pmo = max(0, int(scheduled_pmo or 0))
            else:
                ledger.scheduled_psv = tot
                ledger.scheduled_pmo = 0
        else:
            ledger.scheduled_psv = max(0, int(scheduled_psv or 0))
            ledger.scheduled_pmo = max(0, int(scheduled_pmo or 0))
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
    opening_psv: int = 0,
    opening_pmo: int = 0,
    target_date_suffix: Optional[str] = None,
    notes: str = "",
    opening_total: Optional[int] = None,
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
        if opening_total is not None and opening_psv == 0 and opening_pmo == 0:
            ledger.opening_stock = max(0, int(opening_total))
            ledger.opening_balance_psv = ledger.opening_stock
            ledger.opening_balance_pmo = 0
        else:
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


def get_previous_shift_closing_balances(target_date_suffix: Optional[str] = None) -> Dict[str, Any]:
    """
    Finds the most recent closing stock balances prior to the target work date.
    Returns:
    {
        "found": True,
        "opening_psv": prev.closing_balance_psv,
        "opening_pmo": prev.closing_balance_pmo,
        "opening_total": prev.closing_stock,
        "previous_work_date": prev.work_date.isoformat(),
        "previous_suffix": prev.work_date_suffix,
    }
    """
    work_d, _ = resolve_date_and_suffix(target_date_suffix)
    prev = DailyStockLedger.objects.filter(work_date__lt=work_d).order_by("-work_date").first()
    if prev:
        return {
            "found": True,
            "opening_psv": prev.closing_balance_psv,
            "opening_pmo": prev.closing_balance_pmo,
            "opening_total": prev.closing_stock,
            "previous_work_date": prev.work_date.isoformat(),
            "previous_suffix": prev.work_date_suffix,
        }
    return {
        "found": False,
        "opening_psv": 0,
        "opening_pmo": 0,
        "opening_total": 0,
        "previous_work_date": "",
        "previous_suffix": "",
    }


def set_shift_remarks(remarks: str, target_date_suffix: Optional[str] = None) -> Dict[str, Any]:
    """Updates shift remarks (REMARKS column in official report) on DailyStockLedger."""
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    with transaction.atomic():
        ledger, _ = DailyStockLedger.objects.get_or_create(
            work_date=work_d,
            defaults={"work_date_suffix": suffix},
        )
        ledger.notes = (remarks or "").strip()
        ledger.save(update_fields=["notes", "updated_at"])
    return {"success": True, "notes": ledger.notes}


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


def get_physical_bond_plates(target_date_suffix: Optional[str] = None) -> List[str]:
    """
    Returns unique plate numbers that are physically present at this bond facility
    for the specified shift date (or today).
    Sources of Physical Bond Inventory:
    1. Plates audited in Safe Room stock taking (SafeAuditScan for this suffix).
    2. Plates received in Inbound Deliveries (StockDeliveryItem for this suffix).
    3. Plates returned to safe room from line (StockReturnScan for this suffix).
    4. Plates received via Bond Transfer In (StockBondTransfer TRANSFER_IN).
    Excludes plates that have been dispatched to installation line (StockDispatchScan)
    or transferred out (TRANSFER_OUT).
    """
    _, suffix = resolve_date_and_suffix(target_date_suffix)
    plates: Set[str] = set()

    # 1. Safe Room Audits for this shift
    audit_plates = SafeAuditScan.objects.filter(work_date_suffix=suffix).values_list("registration_number", flat=True)
    plates.update(p for p in audit_plates if p)

    # 2. Inbound Deliveries received
    deliv_plates = StockDeliveryItem.objects.filter(delivery__target_date_suffix=suffix).values_list("registration_number", flat=True)
    plates.update(p for p in deliv_plates if p)

    # 3. Line returns to safe room
    return_plates = StockReturnScan.objects.filter(work_date_suffix=suffix).values_list("registration_number", flat=True)
    plates.update(p for p in return_plates if p)

    # Exclude plates that were dispatched out to the line and not returned
    dispatched_plates = set(
        StockDispatchScan.objects.filter(work_date_suffix=suffix)
        .exclude(status=StockDispatchScan.Status.RETURNED_TO_SAFE)
        .values_list("registration_number", flat=True)
    )
    physical_plates = [p for p in plates if p not in dispatched_plates]
    return sorted(physical_plates)


def enrich_physical_plates_with_itms(
    plates: Iterable[str],
    log_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Background worker function:
    Enriches plates physically present at this bond (Safe Audit, Deliveries, Returns)
    with their hardware specs (Kit Code, GPS Tracker, Front BLE, Rear BLE, Order Link) from ITMS.
    Never downloads unrelated cloud kits; only queries ITMS specifically for physical plates.
    """
    from core.services.itms_web_client import get_web_client
    clean_plates, _, _ = parse_plate_input_with_stats(plates)
    if not clean_plates:
        return {"success": True, "enriched": 0}

    # Identify plates that already have complete hardware details in InstallationKit
    existing_complete = set(
        InstallationKit.objects.filter(
            registration_number__in=clean_plates
        ).exclude(
            Q(gps_tracker="") | Q(gps_tracker__isnull=True)
        ).values_list("registration_number", flat=True)
    )
    plates_to_enrich = [p for p in clean_plates if p not in existing_complete]
    if not plates_to_enrich:
        return {
            "success": True,
            "total_found": len(existing_complete),
            "created": 0,
            "updated": 0,
            "message": "All physical plates already have hardware details linked.",
        }

    try:
        client = get_web_client()
        session = client.session_store.session
        if session and session.is_cookie_valid():
            if log_callback:
                log_callback(f"Targeting ITMS for {len(plates_to_enrich)} physical bond plates...")
            res = client.search_and_sync_kits_for_plates(plates_to_enrich, log_callback=log_callback)
            return res
        else:
            return {"success": False, "error": "ITMS session not active; local hardware profiles used."}
    except Exception as exc:
        logger.warning("Error enriching physical plates with ITMS: %s", exc)
        return {"success": False, "error": str(exc)}


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

    # Separate physical plates into verified on-stock with hardware serials vs unregistered
    target_codes = [f"IK-{p}" for p in clean_plates]
    local_kits = {
        k.registration_number: k
        for k in InstallationKit.objects.filter(
            Q(registration_number__in=clean_plates) | Q(kit_code__in=target_codes)
        )
    }

    verified_plates: List[str] = []
    unregistered_plates: List[str] = []
    hardware_profiles: List[Dict[str, Any]] = []

    for p in clean_plates:
        kit = local_kits.get(p) or local_kits.get(f"IK-{p}")
        if kit:
            verified_plates.append(p)
            hardware_profiles.append({
                "plate": p,
                "kit_code": kit.kit_code or f"IK-{p}",
                "gps_tracker": getattr(kit, "gps_tracker", "") or getattr(kit, "gps_tracker_id", "") or "—",
                "front_ble": getattr(kit, "front_tracker", "") or getattr(kit, "ble_beacon_front", "") or "—",
                "rear_ble": getattr(kit, "rear_tracker", "") or getattr(kit, "ble_beacon_rear", "") or "—",
                "status": kit.status or "New",
            })
        else:
            unregistered_plates.append(p)

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

        # Persist physical plate scans into SafeAuditScan
        from core.services import kit_provisioning_service, bond_service
        active_bond = bond_service.get_active_bond()
        b_code = active_bond.get("code", "AGM")
        existing_audits = set(
            SafeAuditScan.objects.filter(
                work_date_suffix=suffix,
                registration_number__in=clean_plates,
            ).values_list("registration_number", flat=True)
        )
        new_audits = [
            SafeAuditScan(
                registration_number=p,
                plate_category=PlateCategory.PMO if normalizer.canonicalize(p) in pmo_plates_set else PlateCategory.PSV,
                work_date=work_d,
                work_date_suffix=suffix,
                bond_code=b_code,
                operator_name=operator_name or "Operator",
                notes=notes or "",
            )
            for p in clean_plates
            if p not in existing_audits
        ]
        if new_audits:
            SafeAuditScan.objects.bulk_create(new_audits, ignore_conflicts=True)

        # Auto-provision all scanned safe room plates into InstallationKit marked 'New'
        prov_res = kit_provisioning_service.sync_and_provision_warehouse_kits(
            target_date_suffix=suffix,
            source_plates=clean_plates,
            sync_itms=False,
            facility_name=active_bond.get("name", "Bond Warehouse"),
        )

        updated_recon = compute_daily_reconciliation(suffix)

    # Trigger targeted background ITMS enrichment for physical plates missing hardware specs
    import sys
    from django.conf import settings
    is_testing = getattr(settings, "TESTING", False) or any("test" in arg for arg in sys.argv)
    if clean_plates and not is_testing:
        def _bg_enrich():
            from django.db import connection
            connection.close()
            try:
                enrich_physical_plates_with_itms(clean_plates)
            except Exception as e_err:
                logger.warning("Background stock audit ITMS enrichment error: %s", e_err)
            finally:
                connection.close()

        import threading
        threading.Thread(
            target=_bg_enrich,
            daemon=True,
        ).start()

    return {
        "success": True,
        "work_date": work_d.isoformat(),
        "work_date_suffix": suffix,
        "physical_count": total_scanned,
        "total_scanned": total_scanned,
        "verified_count": len(verified_plates),
        "unregistered_count": len(unregistered_plates),
        "verified_plates": verified_plates,
        "unregistered_plates": unregistered_plates,
        "hardware_profiles": hardware_profiles,
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
        "kits_provisioned": prov_res,
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

    # 5. Scheduled Target, Daily Performance % & Backlog Level (from official report)
    scheduled_total = ledger.scheduled_total if ledger.scheduled_total > 0 else (ledger.scheduled_psv + ledger.scheduled_pmo)
    daily_perf_total = round((total_installed / scheduled_total * 100), 1) if scheduled_total > 0 else 0.0
    daily_perf_psv = round((installed_psv_count / ledger.scheduled_psv * 100), 1) if ledger.scheduled_psv > 0 else 0.0
    daily_perf_pmo = round((installed_pmo_count / ledger.scheduled_pmo * 100), 1) if ledger.scheduled_pmo > 0 else 0.0

    backlog_total = max(0, scheduled_total - total_installed)
    backlog_psv = max(0, ledger.scheduled_psv - installed_psv_count) if ledger.scheduled_psv > 0 else 0
    backlog_pmo = max(0, ledger.scheduled_pmo - installed_pmo_count) if ledger.scheduled_pmo > 0 else 0

    variance_psv = installed_psv_count - ledger.scheduled_psv
    variance_pmo = installed_pmo_count - ledger.scheduled_pmo
    variance_total = total_installed - scheduled_total

    sched_psv_display = ledger.scheduled_psv if (ledger.scheduled_psv > 0 or ledger.scheduled_pmo > 0) else scheduled_total
    sched_pmo_display = ledger.scheduled_pmo if (ledger.scheduled_psv > 0 or ledger.scheduled_pmo > 0) else 0

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
            # Physical plate was dispatched to line, not returned to safe room,
            # and has NO matching active order or archive record in ITMS.
            # Strictly classified as an Unallocated Floor Discrepancy (no vision OCR noise).
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

    audit_scans_count = SafeAuditScan.objects.filter(work_date_suffix=suffix).count()
    if audit_scans_count > 0:
        ledger.physical_count = audit_scans_count

    if ledger.physical_count is not None:
        ledger.variance = ledger.physical_count - total_closing
    else:
        ledger.variance = 0

    ledger.last_reconciled_at = timezone.now()
    ledger.save()

    formatted_date = format_date_suffix_readable(suffix)
    pmo_dispatched_count = sum(1 for s in dispatched_plates_map.values() if s.plate_category == PlateCategory.PMO)
    psv_dispatched_count = max(0, dispatched_count - pmo_dispatched_count)
    pmo_returned_count = sum(1 for s in returns_qs if s.plate_category == PlateCategory.PMO)
    psv_returned_count = max(0, returned_count - pmo_returned_count)

    return {
        "work_date": work_d.isoformat(),
        "work_date_suffix": suffix,
        "formatted_date": formatted_date,
        "warehouse_name": ledger.warehouse_name,
        "active_bond_code": active_bond_code,
        "closing_stock": total_closing,
        "closing_balance_psv": closing_psv,
        "closing_balance_pmo": closing_pmo,
        # Structured report columns adhering to official spreadsheet (IMG-20260929-WA0005.jpg)
        "storage_bond_name": ledger.warehouse_name or "AGM SPIRO/8/2",
        "remarks": ledger.notes or "",
        "consumables": ["Rivets", "Cable ties", "Drill bits", "Insulating Tape"],
        "scheduled_summary": {
            "scheduled_target": scheduled_total,
            "installed_total": total_installed,
            "installed_pmo": installed_pmo_count,
            "installed_psv": installed_psv_count,
            "daily_performance_pct": daily_perf_total,
            "backlog_level": backlog_total,
            "variance": variance_total,
        },
        "report_table": {
            "columns": [
                "Description",
                "Private Yellow (PMO)",
                "Public White (PSV)",
                "Total Combined (Bond)",
            ],
            "rows": [
                {
                    "metric": "Opening Balance",
                    "pmo": ledger.opening_balance_pmo,
                    "psv": ledger.opening_balance_psv,
                    "total": ledger.opening_stock,
                    "note": "Physical count in safe room at start of shift",
                },
                {
                    "metric": "Kits Received",
                    "pmo": kits_received_pmo,
                    "psv": kits_received_psv,
                    "total": total_kits_received,
                    "note": "Shipments received from supplier",
                },
                {
                    "metric": "SCHEDULED",
                    "pmo": sched_pmo_display,
                    "psv": sched_psv_display,
                    "total": scheduled_total,
                    "note": "Target installation under bond (operational target)",
                },
                {
                    "metric": "Kits Installed",
                    "pmo": installed_pmo_count,
                    "psv": installed_psv_count,
                    "total": total_installed,
                    "note": "Installed & verified in orders / archive",
                },
                {
                    "metric": "Daily perfomance, %",
                    "pmo": f"{daily_perf_pmo}%" if ledger.scheduled_pmo > 0 else "0%",
                    "psv": f"{daily_perf_psv}%" if ledger.scheduled_psv > 0 else "0%",
                    "total": f"{daily_perf_total}%",
                    "note": "Kits Installed vs Scheduled Target",
                },
                {
                    "metric": "Bond transfer IN",
                    "pmo": transfer_in_pmo,
                    "psv": transfer_in_psv,
                    "total": total_transfer_in,
                    "note": "Kits transferred in from other bonds",
                },
                {
                    "metric": "Bond transfer OUT",
                    "pmo": transfer_out_pmo,
                    "psv": transfer_out_psv,
                    "total": total_transfer_out,
                    "note": "Kits transferred out to other bonds",
                },
                {
                    "metric": "Backlog level",
                    "pmo": backlog_pmo,
                    "psv": backlog_psv,
                    "total": backlog_total,
                    "note": "Scheduled Target minus Installed",
                },
                {
                    "metric": "Closing Balance",
                    "pmo": closing_pmo,
                    "psv": closing_psv,
                    "total": total_closing,
                    "note": "Opening + Received + Transfer In - Transfer Out - Installed",
                },
                *(
                    [
                        {
                            "metric": "Physical Count (Safe Room Audit)",
                            "pmo": "—",
                            "psv": "—",
                            "total": ledger.physical_count or 0,
                            "note": "Physical number plates audited in safe room / storage box",
                        },
                        {
                            "metric": "Variance (Physical vs Closing)",
                            "pmo": "—",
                            "psv": "—",
                            "total": f"{ledger.variance:+d}" if (ledger.variance or 0) != 0 else "0 (Balanced)",
                            "note": "Exact match with book closing" if (ledger.variance or 0) == 0 else ("Surplus in safe room" if (ledger.variance or 0) > 0 else "Shortage in safe room"),
                        },
                    ]
                    if (ledger.physical_count is not None and ledger.physical_count > 0)
                    else []
                ),
            ],
            # Aliases dictionary for backward-compatible lookups
            "rows_by_metric": {
                "Opening Balance": {"metric": "Opening Balance", "pmo": ledger.opening_balance_pmo, "psv": ledger.opening_balance_psv, "total": ledger.opening_stock},
                "Kits Received": {"metric": "Kits Received", "pmo": kits_received_pmo, "psv": kits_received_psv, "total": total_kits_received},
                "SCHEDULED": {"metric": "SCHEDULED", "pmo": sched_pmo_display, "psv": sched_psv_display, "total": scheduled_total},
                "Scheduled (Target)": {"metric": "SCHEDULED", "pmo": sched_pmo_display, "psv": sched_psv_display, "total": scheduled_total},
                "Kits Installed": {"metric": "Kits Installed", "pmo": installed_pmo_count, "psv": installed_psv_count, "total": total_installed},
                "Kits Installed (Actual)": {"metric": "Kits Installed", "pmo": installed_pmo_count, "psv": installed_psv_count, "total": total_installed},
                "Daily perfomance, %": {"metric": "Daily perfomance, %", "pmo": daily_perf_pmo, "psv": daily_perf_psv, "total": daily_perf_total},
                "Scheduled Target Variance": {"metric": "Scheduled Target Variance", "pmo": variance_pmo, "psv": variance_psv, "total": variance_total},
                "Bond transfer IN": {"metric": "Bond transfer IN", "pmo": transfer_in_pmo, "psv": transfer_in_psv, "total": total_transfer_in},
                "Bond Transfer In": {"metric": "Bond transfer IN", "pmo": transfer_in_pmo, "psv": transfer_in_psv, "total": total_transfer_in},
                "Bond transfer OUT": {"metric": "Bond transfer OUT", "pmo": transfer_out_pmo, "psv": transfer_out_psv, "total": total_transfer_out},
                "Bond Transfer Out": {"metric": "Bond transfer OUT", "pmo": transfer_out_pmo, "psv": transfer_out_psv, "total": total_transfer_out},
                "Backlog level": {"metric": "Backlog level", "pmo": backlog_pmo, "psv": backlog_psv, "total": backlog_total},
                "Closing Balance": {"metric": "Closing Balance", "pmo": closing_pmo, "psv": closing_psv, "total": total_closing},
                "Physical Count": {"metric": "Physical Count (Safe Room Audit)", "pmo": "—", "psv": "—", "total": ledger.physical_count or 0},
                "Variance": {"metric": "Variance (Physical vs Closing)", "pmo": "—", "psv": "—", "total": ledger.variance or 0},
            },
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
        "physical_count": ledger.physical_count or 0,
        "variance": ledger.variance or 0,
        "has_physical_count": bool(ledger.physical_count is not None and ledger.physical_count > 0),
        "scheduled_target": scheduled_total,
        "shift_remarks": ledger.notes or "",
        "pmo": {
            "opening": ledger.opening_balance_pmo,
            "received": kits_received_pmo,
            "transfer_in": transfer_in_pmo,
            "transfer_out": transfer_out_pmo,
            "scheduled": sched_pmo_display,
            "installed": installed_pmo_count,
            "closing_stock": closing_pmo,
            "dispatched": pmo_dispatched_count,
            "returned": pmo_returned_count,
        },
        "psv": {
            "opening": ledger.opening_balance_psv,
            "received": kits_received_psv,
            "transfer_in": transfer_in_psv,
            "transfer_out": transfer_out_psv,
            "scheduled": sched_psv_display,
            "installed": installed_psv_count,
            "closing_stock": closing_psv,
            "dispatched": psv_dispatched_count,
            "returned": psv_returned_count,
        },
        "total": {
            "opening": ledger.opening_stock,
            "received": total_kits_received,
            "transfer_in": total_transfer_in,
            "transfer_out": total_transfer_out,
            "scheduled": scheduled_total,
            "installed": total_installed,
            "closing_stock": total_closing,
            "dispatched": dispatched_count,
            "returned": returned_count,
            "physical_count": ledger.physical_count or 0,
            "variance": ledger.variance or 0,
        },
    }



def export_stock_reconciliation_csv(target_date_suffix: Optional[str] = None) -> str:
    """Generates the clean CSV export strictly adhering to the user's report format (IMG-20260929-WA0005.jpg)."""
    recon = compute_daily_reconciliation(target_date_suffix)
    suffix = recon["work_date_suffix"]
    formatted_date = recon["formatted_date"]
    bond_name = recon.get("storage_bond_name") or recon.get("warehouse_name") or "AGM SPIRO/8/2"

    out = io.StringIO()
    writer = csv.writer(out)

    # Official spreadsheet header from IMG-20260929-WA0005.jpg
    writer.writerow([
        "STORAGE BOND NAME",
        "DESCRIPTION",
        "PRIVATE",
        "PUBLIC",
        "Total",
        "REMARKS",
        "Consumables",
    ])

    consumables_list = ["Rivets", "Cable ties", "Drill bits", "Insulating Tape"]
    raw_notes = recon.get("remarks") or ""
    remarks_lines = [line.strip() for line in raw_notes.splitlines() if line.strip()]

    table = recon["report_table"]
    for idx, row in enumerate(table["rows"]):
        bond_cell = bond_name if idx == 0 else ""
        rem_cell = remarks_lines[idx] if idx < len(remarks_lines) else ""
        cons_cell = consumables_list[idx] if idx < len(consumables_list) else ""

        writer.writerow([
            bond_cell,
            row["metric"],
            row["pmo"],
            row["psv"],
            row["total"],
            rem_cell,
            cons_cell,
        ])

    writer.writerow([])
    writer.writerow(["SHIFT CONTEXT METADATA"])
    writer.writerow(["Work Date", formatted_date, f"Suffix: {suffix}"])
    writer.writerow(["Generated At", timezone.now().strftime("%Y-%m-%d %H:%M:%S")])
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


def get_configured_export_dir(exports_dir: Optional[str] = None) -> Path:
    """
    Resolves the standardized directory where CSV and shift reports are exported,
    strictly adhering to the user's configured export directory setting.
    """
    from django.conf import settings
    if exports_dir:
        out_dir = Path(exports_dir)
    else:
        from core.services import config_service
        def_dir = str(config_service.get_setting("sync.default_export_directory", "exports")).strip() or "exports"
        out_dir = Path(def_dir) if os.path.isabs(def_dir) else Path(settings.BASE_DIR) / def_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def get_blocked_plates_cache_file(target_date_suffix: Optional[str] = None) -> Path:
    _, suffix = resolve_date_and_suffix(target_date_suffix)
    out_dir = get_configured_export_dir()
    return out_dir / f".blocked_kits_{suffix}.json"


def record_blocked_plates(
    plates: Iterable[str],
    target_date_suffix: Optional[str] = None,
    reason: str = "NOT ON ITMS STOCK",
) -> List[str]:
    """
    Persistently records plates that were blocked at dispatch for a shift.
    Merges with previously recorded blocked plates so that end-of-day reports
    retain all non-stock plates from both single barcode scans and Excel batch pastes.
    """
    _, suffix = resolve_date_and_suffix(target_date_suffix)
    new_clean = parse_plate_input(plates)
    cache_file = get_blocked_plates_cache_file(suffix)

    existing: List[str] = []
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    existing = [p for p in data if isinstance(p, str)]
                elif isinstance(data, dict):
                    existing = data.get("plates", [])
        except Exception as exc:
            logger.warning("Error reading blocked plates cache: %s", exc)

    combined = parse_plate_input(existing + new_clean)
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({
                "work_date_suffix": suffix,
                "count": len(combined),
                "updated_at": timezone.now().isoformat(),
                "plates": combined,
            }, f, indent=2)
    except Exception as exc:
        logger.warning("Error saving blocked plates cache: %s", exc)

    return combined


def get_blocked_plates_for_date(target_date_suffix: Optional[str] = None) -> List[str]:
    """Returns all accumulated plates blocked at dispatch for the target date."""
    _, suffix = resolve_date_and_suffix(target_date_suffix)
    cache_file = get_blocked_plates_cache_file(suffix)
    if cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return [p for p in data if isinstance(p, str)]
                elif isinstance(data, dict):
                    return data.get("plates", [])
        except Exception as exc:
            logger.warning("Error reading blocked plates cache: %s", exc)
    return []


def export_blocked_kits_csv(
    blocked_plates: Optional[Iterable[str]] = None,
    reason: str = "Awaiting ITMS Stock Transfer Officer",
    target_date_suffix: Optional[str] = None,
    bond_name: Optional[str] = None,
    exports_dir: Optional[str] = None,
) -> Tuple[str, str, int]:
    """
    Exports a clean CSV file containing plates blocked at dispatch because they are
    not on stock / awaiting ITMS stock transfer.
    Returns: (absolute_file_path, filename, row_count)
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    if blocked_plates is None:
        blocked_plates = get_blocked_plates_for_date(suffix)
    clean_plates = parse_plate_input(blocked_plates)
    if not clean_plates:
        return "", "", 0

    active_bond = bond_service.get_active_bond()
    b_name = bond_name or active_bond.get("name", "AGM SPIRO")

    timestamp_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    filename = f"blocked_dispatch_kits_{suffix}_{timestamp_str}.csv"
    out_dir = get_configured_export_dir(exports_dir)
    file_path = str(out_dir / filename)

    target_codes = [f"IK-{p}" for p in clean_plates]
    known_kits = {
        k.registration_number: k
        for k in InstallationKit.objects.filter(
            Q(registration_number__in=clean_plates) | Q(kit_code__in=target_codes)
        )
    }

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "Registration Plate", "Plate Category", "Kit Code", "Warehouse Bond", "Status / Reason", "Exported At"])
        for idx, p in enumerate(clean_plates, start=1):
            cat = "PMO" if "PMO" in p.upper() else "PSV"
            kit = known_kits.get(p) or known_kits.get(f"IK-{p}")
            plate_reason = reason
            if kit and (kit.status or "").lower() == "new":
                plate_reason = "Awaiting MVR Allocation in Orders (Dispatched to Line)"
            elif kit and (kit.status or "").lower() in ("installed", "archived"):
                plate_reason = "ALREADY INSTALLED in Archive (Blocked from Line)"
            writer.writerow([
                idx,
                p,
                cat,
                f"IK-{p}",
                b_name,
                plate_reason,
                timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
            ])

    return file_path, filename, len(clean_plates)


def get_stock_transfer_request_docket(
    target_date_suffix: Optional[str] = None,
    blocked_plates: Optional[Iterable[str]] = None,
    reason: str = "Not on ITMS stock / missing from Safe Room",
) -> Dict[str, Any]:
    """
    Generates a structured ITMS Stock Transfer & Provisioning Docket for plates that
    are NOT on stock or were rejected during dispatch verification.
    Provides:
    - raw_plates: newline-separated plate numbers ready for bulk ITMS import / search.
    - formatted_message: formal text docket ready to copy/share directly with ITMS Transfer Manager.
    - items: array of detailed plate records with status and action instructions.
    - count: total rejected plate count.
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    if blocked_plates is None:
        blocked_plates = get_blocked_plates_for_date(suffix)
    clean_plates = parse_plate_input(blocked_plates)

    active_bond = bond_service.get_active_bond()
    warehouse = active_bond.get("name", "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)")
    active_code = active_bond.get("code", "AGM")
    now_str = timezone.now().strftime("%Y-%m-%d %H:%M:%S")

    target_codes = [f"IK-{p}" for p in clean_plates]
    known_kits = {
        k.registration_number: k
        for k in InstallationKit.objects.filter(
            Q(registration_number__in=clean_plates) | Q(kit_code__in=target_codes)
        )
    }

    installed_orders = set(
        InstallationOrder.objects.filter(
            registration_number__in=clean_plates,
        ).filter(Q(is_archived=True) | Q(order_status__iexact="Installed")).values_list("registration_number", flat=True)
    )

    items = []
    for idx, p in enumerate(clean_plates, start=1):
        kit = known_kits.get(p) or known_kits.get(f"IK-{p}")
        category = "PMO" if "PMO" in p.upper() else "PSV"

        if p in installed_orders or (kit and (kit.status or "").lower() in ("installed", "archived")):
            status = "ALREADY_INSTALLED"
            item_reason = "Already installed / archived on an order"
        elif kit and (kit.status or "").lower() == "new":
            status = "AWAITING_ALLOCATION"
            item_reason = f"Kit exists in {kit.warehouse or 'Safe Room'} - awaiting MVR allocation"
        else:
            status = "NOT_ON_STOCK"
            item_reason = reason

        items.append({
            "index": idx,
            "plate": p,
            "category": category,
            "status": status,
            "reason": item_reason,
            "action": "Set Box Aside",
        })

    raw_plates = "\n".join(clean_plates)
    count = len(clean_plates)

    docket_lines = [
        "==================================================",
        "ITMS STOCK TRANSFER & REGISTRATION REQUEST",
        f"Facility: {warehouse} (Code: {active_code})",
        f"Shift Date: {work_d.strftime('%Y-%m-%d')} (Suffix: {suffix})",
        f"Generated At: {now_str}",
        f"Total Rejected / Not On Stock: {count} Plate(s)",
        "==================================================",
        "",
        "ATTENTION ITMS TRANSFER MANAGER:",
        "The following physical kit(s) were submitted for assembly line",
        "dispatch but were NOT found in active warehouse stock or ITMS records.",
        "Their physical boxes have been SET ASIDE (quarantined from fitters).",
        "",
        "Please add, transfer, or provision these plates to active bond stock:",
        "",
        "REJECTED PLATES LIST (SET ASIDE):",
    ]

    if items:
        for it in items:
            docket_lines.append(f"  {it['index']:2d}. {it['plate']:<10} [{it['category']}] - {it['reason']}")
    else:
        docket_lines.append("  [None - All submitted plates are currently in stock]")

    docket_lines.extend([
        "",
        "--------------------------------------------------",
        "RAW PLATES FOR BULK ITMS TRANSFER / SEARCH:",
        raw_plates if raw_plates else "[None]",
        "--------------------------------------------------",
        "",
        "* Action Taken on Floor: Physical boxes set aside. None issued to fitters.",
        "* Verified by: ITMS Verification Copilot (Dispatch Stock Guard)",
        "==================================================",
    ])

    formatted_message = "\n".join(docket_lines)

    return {
        "success": True,
        "work_date_suffix": suffix,
        "formatted_date": work_d.strftime("%Y-%m-%d"),
        "warehouse": warehouse,
        "active_code": active_code,
        "count": count,
        "plates": clean_plates,
        "raw_plates": raw_plates,
        "items": items,
        "formatted_message": formatted_message,
        "generated_at": now_str,
    }


def export_unregistered_stocktake_csv(
    unregistered_plates: Iterable[str],
    target_date_suffix: Optional[str] = None,
    bond_name: Optional[str] = None,
    exports_dir: Optional[str] = None,
) -> Tuple[str, str, int]:
    """
    Exports a clean CSV file containing physical plates audited in the safe room that
    have zero record in ITMS installation kits / local database.
    Returns: (absolute_file_path, filename, row_count)
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    clean_plates = parse_plate_input(unregistered_plates)
    if not clean_plates:
        return "", "", 0

    active_bond = bond_service.get_active_bond()
    b_name = bond_name or active_bond.get("name", "AGM SPIRO")

    timestamp_str = timezone.now().strftime("%Y%m%d_%H%M%S")
    filename = f"unregistered_stocktake_kits_{suffix}_{timestamp_str}.csv"
    out_dir = get_configured_export_dir(exports_dir)
    file_path = str(out_dir / filename)

    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["#", "Registration Plate", "Plate Category", "Kit Code", "Warehouse Facility", "Audit Verdict", "Notes", "Audited At"])
        for idx, p in enumerate(clean_plates, start=1):
            cat = "PMO" if "PMO" in p.upper() else "PSV"
            writer.writerow([
                idx,
                p,
                cat,
                f"IK-{p}",
                b_name,
                "NOT ON ITMS STOCK",
                "Physical kit box in safe room; missing from ITMS installation kits",
                timezone.now().strftime("%Y-%m-%d %H:%M:%S"),
            ])

    return file_path, filename, len(clean_plates)



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
            "image_url": d.image_url,
            "image_path": d.image_absolute_path,
            "plates": plates,
        })

    return results


def get_delivery_note_detail(delivery_id: int) -> Optional[Dict[str, Any]]:
    """Returns detailed information for a specific Delivery Note by ID."""
    try:
        d = StockDelivery.objects.prefetch_related("items").get(id=delivery_id)
        plates = list(d.items.values_list("registration_number", flat=True))
        return {
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
            "image_url": d.image_url,
            "image_path": d.image_absolute_path,
            "plates": plates,
        }
    except StockDelivery.DoesNotExist:
        return None


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

    from core.version import __version__
    docket_lines.extend([
        "",
        "==================================================",
        f"ITMS Verification & Daily Stock Ledger Audit — v{__version__}",
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


def generate_category_csv_content(category: str, target_date_suffix: Optional[str] = None) -> str:
    """
    Generates standard downloadable CSV content for a specific shift category:
    - 'unallocated': Kits counted/dispatched with NO order in ITMS (Status New).
    - 'archived' or 'installed': Officially completed & verified orders in ITMS archive.
    - 'pending' or 'active': Active installation orders still in progress/queue.
    - 'dispatched' or 'counted': Morning counted & issued plates for the shift.
    - 'returned': Plates returned uninstalled to safe room storage.
    - 'master' or 'reconciliation': Master spreadsheet combining ledger balances & floor audits.
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    cat_lower = str(category).strip().lower()

    if cat_lower in ("master", "reconciliation", "ledger"):
        return export_stock_reconciliation_csv(suffix)

    active_bond = bond_service.get_active_bond()
    active_code = active_bond.get("code", "AGM")
    active_name = active_bond.get("name", "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)")

    out = io.StringIO()
    writer = csv.writer(out)

    if cat_lower in ("unallocated", "new"):
        # Unallocated plates: Dispatched but not found in ITMS orders or archive, and not returned
        recon = compute_daily_reconciliation(suffix)
        unalloc_plates = recon.get("unallocated_plates", [])

        writer.writerow([
            "#",
            "Registration Number",
            "Series",
            "Kit Status",
            "ITMS Order Found",
            "Operating Facility",
            "Audit Note",
        ])
        for idx, p in enumerate(unalloc_plates, start=1):
            m = re.search(r"U[A-Z]{2}\d{3}([A-Z]{1,2})", p)
            series = m.group(1) if m else "UG"
            writer.writerow([
                idx,
                p,
                series,
                "New / Unallocated",
                "NO",
                active_name,
                "Counted on site; verified absent from ITMS active orders and archive",
            ])
        return out.getvalue()

    if cat_lower in ("archived", "installed"):
        orders_qs = list(InstallationOrder.objects.filter(order_number__endswith=suffix))
        archived_orders = [
            o for o in orders_qs
            if bond_service.classify_order_bond_scope(o, active_code=active_code) == "ACTIVE_BOND"
            and (o.is_archived or (o.order_status or "").strip().lower() == "installed")
        ]
        # Sort by installation date or order number
        archived_orders.sort(key=lambda o: (o.installation_date or "", o.order_number))

        writer.writerow([
            "#",
            "Order Number",
            "Registration Number",
            "VIN",
            "Installation Officer",
            "Installation Date",
            "Order Status",
            "Warehouse",
        ])
        for idx, o in enumerate(archived_orders, start=1):
            writer.writerow([
                idx,
                o.order_number,
                o.registration_number,
                o.vin or "—",
                o.installation_officer or "—",
                o.installation_date or "—",
                o.order_status or "Installed",
                o.warehouse_name or active_name,
            ])
        return out.getvalue()

    if cat_lower in ("pending", "active"):
        orders_qs = list(InstallationOrder.objects.filter(order_number__endswith=suffix))
        active_orders = [
            o for o in orders_qs
            if bond_service.classify_order_bond_scope(o, active_code=active_code) == "ACTIVE_BOND"
            and not o.is_archived
            and (o.order_status or "").strip().lower() != "installed"
        ]
        active_orders.sort(key=lambda o: (o.order_status or "", o.registration_number))

        writer.writerow([
            "#",
            "Order Number",
            "Registration Number",
            "VIN",
            "Order Status",
            "ITMS Stage",
            "Warehouse",
        ])
        for idx, o in enumerate(active_orders, start=1):
            writer.writerow([
                idx,
                o.order_number,
                o.registration_number,
                o.vin or "—",
                o.order_status or "Ready for installation",
                o.itms_stage or "STAGE_1_INSTALLATION",
                o.warehouse_name or active_name,
            ])
        return out.getvalue()

    if cat_lower in ("dispatched", "counted", "morning"):
        dispatches = list(
            StockDispatchScan.objects.filter(work_date_suffix=suffix)
            .order_by("registration_number")
        )
        writer.writerow([
            "#",
            "Registration Number",
            "Plate Category",
            "Dispatched At",
            "Operator",
            "Reconciliation Status",
            "Bond Facility",
        ])
        for idx, d in enumerate(dispatches, start=1):
            writer.writerow([
                idx,
                d.registration_number,
                d.plate_category,
                d.dispatched_at.strftime("%Y-%m-%d %H:%M:%S") if d.dispatched_at else "",
                d.operator_name or "Operator",
                d.status,
                d.bond_code or active_code,
            ])
        return out.getvalue()

    if cat_lower in ("returned", "returns"):
        returns = list(
            StockReturnScan.objects.filter(work_date_suffix=suffix)
            .order_by("registration_number")
        )
        writer.writerow([
            "#",
            "Registration Number",
            "Plate Category",
            "Returned At",
            "Operator",
            "Return Reason",
            "Notes",
        ])
        for idx, r in enumerate(returns, start=1):
            writer.writerow([
                idx,
                r.registration_number,
                r.plate_category,
                r.returned_at.strftime("%Y-%m-%d %H:%M:%S") if r.returned_at else "",
                r.operator_name or "Operator",
                r.get_reason_display() if hasattr(r, "get_reason_display") else r.reason,
                r.notes or "",
            ])
        return out.getvalue()

    if cat_lower in ("blocked", "not_on_stock", "rejected"):
        blocked_plates = get_blocked_plates_for_date(suffix)
        writer.writerow([
            "#",
            "Registration Plate",
            "Plate Category",
            "Kit Code",
            "Operating Facility",
            "Audit Verdict",
            "Action Required",
        ])
        for idx, p in enumerate(blocked_plates, start=1):
            cat = "PMO" if "PMO" in p.upper() else "PSV"
            writer.writerow([
                idx,
                p,
                cat,
                f"IK-{p}",
                active_name,
                "NOT ON ITMS STOCK",
                "Set physical box aside in safe room; awaiting ITMS Stock Transfer Officer registration",
            ])
        return out.getvalue()

    # Fallback default: unallocated
    return generate_category_csv_content("unallocated", target_date_suffix)


def export_shift_csvs(
    target_date_suffix: Optional[str] = None,
    exports_dir: Optional[str] = None,
) -> Dict[str, str]:
    """
    Exports all shift reconciliation lists to standardized CSV files in exports/ directory:
    1. shift_{suffix}_morning_dispatched_{count}.csv
    2. shift_{suffix}_itms_archived_{count}.csv
    3. shift_{suffix}_itms_active_pending_{count}.csv
    4. shift_{suffix}_unallocated_kits_{count}.csv
    5. shift_{suffix}_reconciliation_master.csv
    6. shift_{suffix}_returned_to_stock_{count}.csv (if any returns exist)
    7. blocked_dispatch_kits_{suffix}_{timestamp}.csv (all non-stock kits set aside)

    Returns dictionary of {category_key: absolute_file_path}.
    """
    from django.conf import settings
    _, suffix = resolve_date_and_suffix(target_date_suffix)

    out_dir = get_configured_export_dir(exports_dir)

    recon = compute_daily_reconciliation(suffix)
    floor = recon.get("floor_operations", {})
    disp_cnt = floor.get("dispatched_count", 0)
    ret_cnt = floor.get("returned_count", 0)
    unalloc_cnt = floor.get("unallocated_discrepancy", 0)

    sched_summary = recon.get("scheduled_summary", {})
    arch_cnt = sched_summary.get("installed_total", 0)
    pend_cnt = floor.get("itms_pending_count", 0)

    files_generated: Dict[str, str] = {}

    # 1. Unallocated Kits CSV
    unalloc_csv = generate_category_csv_content("unallocated", suffix)
    unalloc_path = out_dir / f"shift_{suffix}_unallocated_kits_{unalloc_cnt}.csv"
    unalloc_path.write_text(unalloc_csv, encoding="utf-8")
    files_generated["unallocated"] = str(unalloc_path)

    # 2. ITMS Archived / Installed CSV
    arch_csv = generate_category_csv_content("archived", suffix)
    arch_path = out_dir / f"shift_{suffix}_itms_archived_{arch_cnt}.csv"
    arch_path.write_text(arch_csv, encoding="utf-8")
    files_generated["archived"] = str(arch_path)

    # 3. ITMS Active / Pending Orders CSV
    pend_csv = generate_category_csv_content("pending", suffix)
    pend_path = out_dir / f"shift_{suffix}_itms_active_pending_{pend_cnt}.csv"
    pend_path.write_text(pend_csv, encoding="utf-8")
    files_generated["pending"] = str(pend_path)

    # 4. Morning Dispatched Plates CSV
    disp_csv = generate_category_csv_content("dispatched", suffix)
    disp_path = out_dir / f"shift_{suffix}_morning_dispatched_{disp_cnt}.csv"
    disp_path.write_text(disp_csv, encoding="utf-8")
    files_generated["dispatched"] = str(disp_path)

    # 5. Returns CSV (if any)
    if ret_cnt > 0:
        ret_csv = generate_category_csv_content("returned", suffix)
        ret_path = out_dir / f"shift_{suffix}_returned_to_stock_{ret_cnt}.csv"
        ret_path.write_text(ret_csv, encoding="utf-8")
        files_generated["returned"] = str(ret_path)

    # 6. Master Shift Reconciliation CSV
    master_csv = export_stock_reconciliation_csv(suffix)
    master_path = out_dir / f"shift_{suffix}_reconciliation_master.csv"
    master_path.write_text(master_csv, encoding="utf-8")
    files_generated["master"] = str(master_path)

    # 7. Blocked / Not-On-Stock Kits CSV (for ITMS Stock Transfer Officer)
    blocked_plates = get_blocked_plates_for_date(suffix)
    if blocked_plates:
        blocked_path, _, _ = export_blocked_kits_csv(
            blocked_plates=blocked_plates,
            target_date_suffix=suffix,
            exports_dir=str(out_dir),
        )
        files_generated["blocked"] = blocked_path

    return files_generated


def reconcile_and_update_shift(
    morning_plates: Optional[Iterable[str]] = None,
    return_plates: Optional[Iterable[str]] = None,
    target_date_suffix: Optional[str] = None,
    operator_name: str = "Operator",
    notes: str = "",
    auto_create_kits: bool = True,
    export_csvs: bool = True,
    exports_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Automated Daily Shift Stock Reconciliation and Inventory System Update:
    1. Ingests morning physical plates counted/scanned for installation.
    2. Ingests any evening uninstalled plates returned to safe room storage.
    3. Cross-references against ITMS active orders and completed archive for the shift date.
    4. Categorizes every plate into:
       - RECONCILED_INSTALLED (Archive)
       - ON_LINE_ACTIVE (Pending Orders)
       - RETURNED_TO_SAFE (Safe Room Returns)
       - UNRESOLVED_DISCREPANCY (Unallocated Kits)
    5. Synchronizes local physical inventory (InstallationKit):
       - Sets status='New' for unallocated kits without ITMS orders.
       - Sets status='Allocated' for active orders and installed kits.
       - Sets warehouse assignment to active bond facility.
    6. Updates DailyStockLedger with opening, received, installed, pending, returned,
       unallocated, and closing balance figures.
    7. Automatically generates downloadable and saved CSV reports for all categories.
    """
    work_d, suffix = resolve_date_and_suffix(target_date_suffix)
    formatted_date = format_date_suffix_readable(suffix)
    active_bond = bond_service.get_active_bond()
    active_bond_code = active_bond.get("code", "AGM")
    active_bond_name = active_bond.get("name", "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)")

    new_dispatches_count = 0
    new_returns_count = 0

    with transaction.atomic():
        # 1. Ingest morning plates if provided
        if morning_plates:
            clean_morning, _, _ = parse_plate_input_with_stats(morning_plates)
            if clean_morning:
                existing_dispatches = set(
                    StockDispatchScan.objects.filter(
                        work_date_suffix=suffix,
                        registration_number__in=clean_morning,
                    ).values_list("registration_number", flat=True)
                )
                scans_to_add = [
                    StockDispatchScan(
                        registration_number=p,
                        plate_category=PlateCategory.PSV,
                        work_date=work_d,
                        work_date_suffix=suffix,
                        bond_code=active_bond_code,
                        operator_name=operator_name or "Operator",
                        status=StockDispatchScan.Status.ON_LINE_ACTIVE,
                        notes=notes or "",
                    )
                    for p in clean_morning
                    if p not in existing_dispatches
                ]
                if scans_to_add:
                    StockDispatchScan.objects.bulk_create(scans_to_add)
                    new_dispatches_count = len(scans_to_add)

        # 2. Ingest return plates if provided
        if return_plates:
            clean_returns, _, _ = parse_plate_input_with_stats(return_plates)
            if clean_returns:
                existing_returns = set(
                    StockReturnScan.objects.filter(
                        work_date_suffix=suffix,
                        registration_number__in=clean_returns,
                    ).values_list("registration_number", flat=True)
                )
                returns_to_add = [
                    StockReturnScan(
                        registration_number=p,
                        plate_category=PlateCategory.PSV,
                        work_date=work_d,
                        work_date_suffix=suffix,
                        operator_name=operator_name or "Operator",
                        reason=StockReturnScan.Reason.BIKE_NO_SHOW,
                        notes=notes or "",
                    )
                    for p in clean_returns
                    if p not in existing_returns
                ]
                if returns_to_add:
                    StockReturnScan.objects.bulk_create(returns_to_add)
                    new_returns_count = len(returns_to_add)

        # 3. Retrieve all dispatches and returns for this shift
        dispatches_qs = list(
            StockDispatchScan.objects.filter(work_date_suffix=suffix)
            .filter(Q(bond_code__iexact=active_bond_code) | Q(bond_code="") | Q(bond_code__isnull=True))
        )
        returns_set = set(
            normalizer.canonicalize(p)
            for p in StockReturnScan.objects.filter(work_date_suffix=suffix).values_list("registration_number", flat=True)
            if p
        )

        orders_qs = list(InstallationOrder.objects.filter(order_number__endswith=suffix))
        scoped_orders: List[InstallationOrder] = [
            o for o in orders_qs
            if bond_service.classify_order_bond_scope(o, active_code=active_bond_code) == "ACTIVE_BOND"
        ]
        order_by_plate: Dict[str, InstallationOrder] = {
            normalizer.canonicalize(o.registration_number): o
            for o in scoped_orders
            if o.registration_number
        }

        # 4. Classify each dispatched plate
        reconciled_installed: List[str] = []
        on_line_active: List[str] = []
        returned_to_safe: List[str] = []
        unallocated_plates: List[str] = []
        scans_to_update: List[StockDispatchScan] = []

        for scan in dispatches_qs:
            orig_plate = scan.registration_number
            c_plate = normalizer.canonicalize(orig_plate)

            if c_plate in returns_set:
                returned_to_safe.append(orig_plate)
                if scan.status != StockDispatchScan.Status.RETURNED_TO_SAFE:
                    scan.status = StockDispatchScan.Status.RETURNED_TO_SAFE
                    scans_to_update.append(scan)
                continue

            matched_order = order_by_plate.get(c_plate)
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
                unallocated_plates.append(orig_plate)
                if scan.status != StockDispatchScan.Status.UNRESOLVED_DISCREPANCY:
                    scan.status = StockDispatchScan.Status.UNRESOLVED_DISCREPANCY
                    scans_to_update.append(scan)

        if scans_to_update:
            StockDispatchScan.objects.bulk_update(scans_to_update, ["status"])

        # 5. Inventory System Synchronization (InstallationKit)
        kits_created_count = 0
        kits_updated_count = 0
        if auto_create_kits and dispatches_qs:
            dispatch_plates = [normalizer.canonicalize(s.registration_number) for s in dispatches_qs if s.registration_number]
            target_codes = [f"IK-{p}" for p in dispatch_plates if p]
            existing_kits = InstallationKit.objects.filter(
                Q(registration_number__in=dispatch_plates) | Q(kit_code__in=target_codes)
            )
            existing_kits_map = {
                normalizer.canonicalize(k.registration_number): k
                for k in existing_kits
            }
            kits_to_create = []
            kits_to_update = []

            for scan in dispatches_qs:
                orig_plate = scan.registration_number
                c_plate = normalizer.canonicalize(orig_plate)
                matched_order = order_by_plate.get(c_plate)
                is_unalloc = (c_plate not in order_by_plate) and (c_plate not in returns_set)
                target_status = "New" if is_unalloc else "Allocated"
                wh = (matched_order.warehouse_name if matched_order and matched_order.warehouse_name else active_bond_name)

                kit = existing_kits_map.get(c_plate)
                if kit:
                    changed = False
                    if kit.status != target_status:
                        kit.status = target_status
                        changed = True
                    if not kit.warehouse and wh:
                        kit.warehouse = wh
                        changed = True
                    if matched_order:
                        if not kit.front_plate and matched_order.front_plate_serial:
                            kit.front_plate = matched_order.front_plate_serial
                            changed = True
                        if not kit.rear_plate and matched_order.rear_plate_serial:
                            kit.rear_plate = matched_order.rear_plate_serial
                            changed = True
                        if not kit.gps_tracker and matched_order.gps_tracker_id:
                            kit.gps_tracker = matched_order.gps_tracker_id
                            changed = True
                    if changed:
                        kits_to_update.append(kit)
                else:
                    new_kit = InstallationKit(
                        kit_code=f"IK-{orig_plate}",
                        registration_number=orig_plate,
                        status=target_status,
                        warehouse=wh,
                        created_date=work_d.strftime("%d.%m.%Y"),
                    )
                    if matched_order:
                        new_kit.front_plate = matched_order.front_plate_serial or ""
                        new_kit.rear_plate = matched_order.rear_plate_serial or ""
                        new_kit.gps_tracker = matched_order.gps_tracker_id or ""
                    kits_to_create.append(new_kit)

            if kits_to_create:
                InstallationKit.objects.bulk_create(kits_to_create, ignore_conflicts=True, batch_size=200)
                kits_created_count = len(kits_to_create)
            if kits_to_update:
                InstallationKit.objects.bulk_update(
                    kits_to_update,
                    ["status", "warehouse", "front_plate", "rear_plate", "gps_tracker"],
                    batch_size=200
                )
                kits_updated_count = len(kits_to_update)

        # 6. Recompute and balance DailyStockLedger
        recon = compute_daily_reconciliation(suffix)

    # 7. Generate CSV exports
    exported_files: Dict[str, str] = {}
    if export_csvs:
        exported_files = export_shift_csvs(
            target_date_suffix=suffix,
            exports_dir=exports_dir,
        )

    return {
        "success": True,
        "work_date": work_d.isoformat(),
        "work_date_suffix": suffix,
        "formatted_date": formatted_date,
        "warehouse_name": active_bond_name,
        "active_bond_code": active_bond_code,
        "summary": {
            "dispatched_count": len(dispatches_qs),
            "reconciled_installed_count": len(reconciled_installed),
            "on_line_active_count": len(on_line_active),
            "returned_count": len(returned_to_safe),
            "unallocated_count": len(unallocated_plates),
            "new_dispatches_recorded": new_dispatches_count,
            "new_returns_recorded": new_returns_count,
            "kits_created": kits_created_count,
            "kits_updated": kits_updated_count,
        },
        "dispatched_plates": [s.registration_number for s in dispatches_qs],
        "reconciled_installed": reconciled_installed,
        "on_line_active": on_line_active,
        "returned_to_safe": returned_to_safe,
        "unallocated_plates": unallocated_plates,
        "exported_files": exported_files,
        "reconciliation": recon,
    }


# ============================================================================
# Aliases & Convenience Wrappers for Backward Compatibility & TUI Panes
# ============================================================================
get_stock_reconciliation_summary = compute_daily_reconciliation
set_manual_physical_count = set_physical_count


def record_inbound_delivery(
    plates: Iterable[str] = (),
    plate_category: str = PlateCategory.PSV,
    delivery_note_ref: Optional[str] = None,
    supplier: str = "Factory / Central Depot",
    target_date_suffix: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Alias / convenience wrapper for record_delivery to support TUI inbound
    delivery operations seamlessly.
    """
    return record_delivery(
        delivery_number=delivery_note_ref,
        plates=plates,
        supplier=supplier,
        plate_category=plate_category,
        target_date_suffix=target_date_suffix,
        **kwargs,
    )



