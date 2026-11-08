"""
Kit Unallocation & Misallocation Monitoring Service.

Maintains strict ground-truth tracking for warehouse kit movements:
1. Physical Dispatches as Ground Truth:
   Kits physically scanned or pasted by safe room operators represent the physical ground truth.
2. Unallocated Kits Detection:
   Identifies kits physically dispatched to the assembly line that:
   - Were never returned to the safe room.
   - Have never appeared in ITMS orders or archives for that shift.
   Differentiates between kits with local camera evidence (Pending System Sync)
   and pure floor discrepancies (Unresolved Discrepancies).
3. Misallocated Kits by MVR:
   Detects clerical and operational errors made by MVR officers when creating/allocating orders:
   - Hardware Serial Mismatch: Order serials (Front/Rear plate, GPS, Beacons) differing from InstallationKit stock.
   - Category Mismatch: PSV White plate assigned to PMO Yellow service or vice-versa.
   - Warehouse Mismatch: Kit dispatched in AGM Bond but allocated under a foreign facility in ITMS.
   - Duplicate Allocation: Same kit assigned to multiple active orders.
4. Safe Auto-Ingestion:
   Ensures auto-registration of orders/archives into dispatches marks provenance with
   AUTO_ORDER_DISPATCH_NOTE so it NEVER collides with or distorts unallocated tracking.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    DailyStockLedger,
    EvidenceImage,
    InstallationKit,
    InstallationOrder,
    PlateCategory,
    StockBlockedScan,
    StockDispatchScan,
    StockReturnScan,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from core.services import bond_service
from core.services.date_service import (
    format_date_suffix_readable,
    get_active_shift_suffix,
    resolve_date_and_suffix,
)
from core.services.plate_lifecycle_service import format_display_plate
from core.vision import normalizer

logger = logging.getLogger(__name__)

AUTO_ORDER_DISPATCH_NOTE = "[AUTO_REGISTERED_FROM_ORDER]"


class KitUnallocationService:
    """
    Dedicated domain service for tracking unallocated, misallocated, and spiked kits.
    """

    def __init__(self, default_facility: str = "AGM"):
        self.default_facility = default_facility.strip().upper()

    # -------------------------------------------------------------------------
    # 1. Unallocated Kits Detection (Physical Dispatches vs ITMS)
    # -------------------------------------------------------------------------

    def get_unallocated_kits(
        self,
        target_date_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculates kits physically dispatched to the line that have NOT appeared in ITMS.
        Source of Truth: Physical StockDispatchScans (excluding AUTO_ORDER_DISPATCH_NOTE).
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        active_bond = bond_service.get_active_bond()
        active_bond_code = (active_bond.get("code") or self.default_facility).strip().upper()

        # 1. Physical Dispatches (Ground Truth)
        dispatches_qs = StockDispatchScan.objects.filter(
            Q(work_date_suffix=suffix) | Q(work_date=work_d)
        ).filter(
            Q(bond_code__iexact=active_bond_code) | Q(bond_code="") | Q(bond_code__isnull=True)
        )

        # 2. Returns to safe room
        returns_qs = StockReturnScan.objects.filter(
            Q(work_date_suffix=suffix) | Q(work_date=work_d)
        )
        returned_plates_set = {
            normalizer.canonicalize(p)
            for p in returns_qs.values_list("registration_number", flat=True)
            if p
        }

        # 3. Scoped ITMS orders & archives for the shift
        order_filter = Q(order_number__endswith=suffix) | Q(order_number__icontains=suffix)
        if target_date_suffix:
            order_filter |= Q(order_number__icontains=str(target_date_suffix).strip())
        orders_qs = list(InstallationOrder.objects.filter(order_filter))
        scoped_orders: List[InstallationOrder] = []
        cross_bond_orders: List[InstallationOrder] = []

        for o in orders_qs:
            if bond_service.classify_order_bond_scope(o, active_code=active_bond_code) == "ACTIVE_BOND":
                scoped_orders.append(o)
            else:
                cross_bond_orders.append(o)

        scoped_order_by_plate: Dict[str, InstallationOrder] = {}
        for o in scoped_orders:
            c = normalizer.canonicalize(o.registration_number)
            if c:
                scoped_order_by_plate[c] = o

        # Dispatches map
        dispatched_plates_map: Dict[str, StockDispatchScan] = {}
        physical_dispatched_set: Set[str] = set()

        for s in dispatches_qs:
            c = normalizer.canonicalize(s.registration_number)
            if not c:
                continue
            dispatched_plates_map[c] = s
            is_auto = bool((s.notes or "").strip() == AUTO_ORDER_DISPATCH_NOTE)
            if not is_auto:
                physical_dispatched_set.add(c)

        # Classify each physical dispatch
        reconciled_installed: List[str] = []
        on_line_active: List[str] = []
        returned_to_safe: List[str] = []
        pending_system_sync: List[str] = []
        unresolved_discrepancy: List[str] = []

        # Local evidence check for pending system sync
        candidate_plates_no_order = [
            c for c in physical_dispatched_set
            if c not in returned_plates_set and c not in scoped_order_by_plate
        ]

        # Check local pairs/evidence images
        plates_with_evidence: Set[str] = set()
        if candidate_plates_no_order:
            q_ev = Q()
            q_pair = Q()
            for c_plate in candidate_plates_no_order:
                disp = format_display_plate(c_plate)
                q_ev |= Q(detected_plate__iexact=c_plate) | Q(detected_plate__iexact=disp) | Q(order_guided_plate__iexact=c_plate)
                q_pair |= Q(registration_number_detected__iexact=c_plate) | Q(registration_number_detected__iexact=disp) | Q(manual_plate_override__iexact=c_plate)
            plates_with_evidence = set(
                normalizer.canonicalize(p)
                for p in EvidenceImage.objects.filter(q_ev).values_list("detected_plate", flat=True)
                if p
            )
            # Also check VehicleInstallationPair
            pair_plates = set(
                normalizer.canonicalize(p)
                for p in VehicleInstallationPair.objects.filter(q_pair).values_list("registration_number_detected", flat=True)
                if p
            )
            plates_with_evidence.update(pair_plates)

        for c_plate in sorted(physical_dispatched_set):
            scan = dispatched_plates_map.get(c_plate)
            orig_plate = scan.registration_number if scan else c_plate

            if c_plate in returned_plates_set:
                returned_to_safe.append(orig_plate)
            elif c_plate in scoped_order_by_plate:
                order = scoped_order_by_plate[c_plate]
                if order.is_archived or (order.order_status or "").strip().lower() == "installed":
                    reconciled_installed.append(orig_plate)
                else:
                    on_line_active.append(orig_plate)
            elif c_plate in plates_with_evidence:
                pending_system_sync.append(orig_plate)
            else:
                unresolved_discrepancy.append(orig_plate)

        # Total unallocated = pending_system_sync + unresolved_discrepancy
        all_unallocated_plates = sorted(pending_system_sync + unresolved_discrepancy)

        return {
            "work_date_suffix": suffix,
            "warehouse_code": active_bond_code,
            "total_physical_dispatched": len(physical_dispatched_set),
            "reconciled_installed_count": len(reconciled_installed),
            "reconciled_installed_plates": reconciled_installed,
            "on_line_active_count": len(on_line_active),
            "on_line_active_plates": on_line_active,
            "returned_to_safe_count": len(returned_to_safe),
            "returned_to_safe_plates": returned_to_safe,
            "pending_sync_count": len(pending_system_sync),
            "pending_sync_plates": pending_system_sync,
            "unresolved_discrepancy_count": len(unresolved_discrepancy),
            "unresolved_discrepancy_plates": unresolved_discrepancy,
            "unallocated_count": len(all_unallocated_plates),
            "unallocated_plates": all_unallocated_plates,
        }

    # -------------------------------------------------------------------------
    # 2. Misallocated Kits by MVR Detection
    # -------------------------------------------------------------------------

    def get_misallocated_kits(
        self,
        target_date_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Monitors orders created/updated by MVR officers against physical stock & dispatches.
        Detects:
        - Category mismatches (PSV plate vs PMO order, or vice-versa)
        - Hardware serial discrepancies (order plate/GPS/beacon serial vs InstallationKit stock)
        - Warehouse mismatches (order assigned to another warehouse despite local dispatch)
        - Duplicate order allocations
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        active_bond = bond_service.get_active_bond()
        active_bond_code = (active_bond.get("code") or self.default_facility).strip().upper()

        order_filter = Q(order_number__endswith=suffix) | Q(order_number__icontains=suffix)
        if target_date_suffix:
            order_filter |= Q(order_number__icontains=str(target_date_suffix).strip())
        orders = list(InstallationOrder.objects.filter(order_filter))
        if not orders:
            return {
                "work_date_suffix": suffix,
                "misallocated_count": 0,
                "misallocated_items": [],
                "category_mismatches": [],
                "serial_discrepancies": [],
                "warehouse_mismatches": [],
                "duplicate_allocations": [],
            }

        # Build plate to kit cache map
        order_plates: List[str] = [o.registration_number for o in orders if o.registration_number]
        q_kit = Q()
        for p in order_plates:
            c = normalizer.canonicalize(p)
            disp = format_display_plate(c)
            q_kit |= Q(registration_number__iexact=c) | Q(registration_number__iexact=disp)

        kit_cache: Dict[str, InstallationKit] = {}
        for k in InstallationKit.objects.filter(q_kit):
            c = normalizer.canonicalize(k.registration_number)
            if c:
                kit_cache[c] = k

        # Check local dispatches
        dispatches_map: Dict[str, StockDispatchScan] = {}
        for d in StockDispatchScan.objects.filter(Q(work_date_suffix=suffix) | Q(work_date=work_d)):
            c = normalizer.canonicalize(d.registration_number)
            if c:
                dispatches_map[c] = d

        category_mismatches: List[Dict[str, Any]] = []
        serial_discrepancies: List[Dict[str, Any]] = []
        warehouse_mismatches: List[Dict[str, Any]] = []
        duplicate_allocations: List[Dict[str, Any]] = []

        seen_plates_in_orders: Dict[str, List[str]] = {}

        for o in orders:
            p = o.registration_number or ""
            c_p = normalizer.canonicalize(p)
            if not c_p:
                continue

            # Check duplicate order allocations
            if c_p in seen_plates_in_orders:
                seen_plates_in_orders[c_p].append(o.order_number)
            else:
                seen_plates_in_orders[c_p] = [o.order_number]

            kit = kit_cache.get(c_p)
            dispatch = dispatches_map.get(c_p)

            # 1. Warehouse Mismatch Check
            bond_scope = bond_service.classify_order_bond_scope(o, active_code=active_bond_code)
            if bond_scope != "ACTIVE_BOND" and dispatch is not None:
                warehouse_mismatches.append({
                    "plate": p,
                    "order_number": o.order_number,
                    "order_warehouse": o.warehouse_name or "Unknown",
                    "dispatched_bond": dispatch.bond_code or active_bond_code,
                    "reason": f"Dispatched under {active_bond_code} but MVR assigned order to {o.warehouse_name}",
                })

            # 2. Category Mismatch Check
            if dispatch:
                is_pmo_order = "PMO" in (o.service_type or "").upper() or "YELLOW" in (o.front_plate_type or "").upper()
                is_pmo_dispatch = dispatch.plate_category == PlateCategory.PMO
                if is_pmo_order != is_pmo_dispatch:
                    category_mismatches.append({
                        "plate": p,
                        "order_number": o.order_number,
                        "dispatched_category": dispatch.plate_category,
                        "order_service": o.service_type or "Unknown",
                        "reason": f"Dispatched as {dispatch.plate_category} but ordered as {'PMO' if is_pmo_order else 'PSV'}",
                    })

            # 3. Hardware Serial Discrepancies
            if kit:
                mismatches: List[str] = []
                # Check plate serials
                if o.front_plate_serial and kit.front_plate and o.front_plate_serial.strip().upper() != kit.front_plate.strip().upper():
                    mismatches.append(f"Front Plate: Order({o.front_plate_serial}) vs Kit({kit.front_plate})")
                if o.rear_plate_serial and kit.rear_plate and o.rear_plate_serial.strip().upper() != kit.rear_plate.strip().upper():
                    mismatches.append(f"Rear Plate: Order({o.rear_plate_serial}) vs Kit({kit.rear_plate})")
                # Check GPS
                if o.gps_tracker_id and kit.gps_tracker and o.gps_tracker_id.strip().upper() != kit.gps_tracker.strip().upper():
                    mismatches.append(f"GPS: Order({o.gps_tracker_id}) vs Kit({kit.gps_tracker})")
                # Check Beacons
                if o.front_beacon_id and kit.front_tracker and o.front_beacon_id.strip().upper() != kit.front_tracker.strip().upper():
                    mismatches.append(f"Front BLE: Order({o.front_beacon_id}) vs Kit({kit.front_tracker})")
                if o.rear_beacon_id and kit.rear_tracker and o.rear_beacon_id.strip().upper() != kit.rear_tracker.strip().upper():
                    mismatches.append(f"Rear BLE: Order({o.rear_beacon_id}) vs Kit({kit.rear_tracker})")

                if mismatches:
                    serial_discrepancies.append({
                        "plate": p,
                        "order_number": o.order_number,
                        "kit_code": kit.kit_code or "---",
                        "discrepancies": mismatches,
                        "reason": "; ".join(mismatches),
                    })

        for c_p, ord_nums in seen_plates_in_orders.items():
            if len(ord_nums) > 1:
                duplicate_allocations.append({
                    "plate": format_display_plate(c_p),
                    "orders": ord_nums,
                    "reason": f"Assigned to {len(ord_nums)} separate ITMS orders simultaneously",
                })

        all_misallocated = category_mismatches + serial_discrepancies + warehouse_mismatches + duplicate_allocations

        return {
            "work_date_suffix": suffix,
            "misallocated_count": len(all_misallocated),
            "misallocated_items": all_misallocated,
            "category_mismatches": category_mismatches,
            "serial_discrepancies": serial_discrepancies,
            "warehouse_mismatches": warehouse_mismatches,
            "duplicate_allocations": duplicate_allocations,
        }

    # -------------------------------------------------------------------------
    # 3. Spiked / Undispatched Kits Detection
    # -------------------------------------------------------------------------

    def get_spiked_undispatched_kits(
        self,
        target_date_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Identifies orders appearing in ITMS (installed or active pending) whose plates
        were NEVER physically scanned at safe room dispatch.
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        active_bond = bond_service.get_active_bond()
        active_bond_code = (active_bond.get("code") or self.default_facility).strip().upper()

        order_filter = Q(order_number__endswith=suffix) | Q(order_number__icontains=suffix)
        if target_date_suffix:
            order_filter |= Q(order_number__icontains=str(target_date_suffix).strip())
        orders_qs = list(InstallationOrder.objects.filter(order_filter))
        scoped_orders: List[InstallationOrder] = [
            o for o in orders_qs
            if bond_service.classify_order_bond_scope(o, active_code=active_bond_code) == "ACTIVE_BOND"
            and o.registration_number
        ]

        dispatches_qs = StockDispatchScan.objects.filter(
            Q(work_date_suffix=suffix) | Q(work_date=work_d)
        )
        physical_dispatches = set()
        for d in dispatches_qs:
            is_auto = bool((d.notes or "").strip() == AUTO_ORDER_DISPATCH_NOTE)
            if not is_auto:
                c = normalizer.canonicalize(d.registration_number)
                if c:
                    physical_dispatches.add(c)

        spiked_installed: List[str] = []
        spiked_pending: List[str] = []

        for o in scoped_orders:
            c = normalizer.canonicalize(o.registration_number)
            if not c:
                continue
            if c not in physical_dispatches:
                orig = o.registration_number
                if o.is_archived or (o.order_status or "").strip().lower() == "installed":
                    spiked_installed.append(orig)
                else:
                    spiked_pending.append(orig)

        spiked_installed.sort()
        spiked_pending.sort()

        return {
            "work_date_suffix": suffix,
            "total_spiked_count": len(spiked_installed) + len(spiked_pending),
            "spiked_installed_count": len(spiked_installed),
            "spiked_installed_plates": spiked_installed,
            "spiked_pending_count": len(spiked_pending),
            "spiked_pending_plates": spiked_pending,
            "all_spiked_plates": spiked_installed + spiked_pending,
        }

    # -------------------------------------------------------------------------
    # 4. Safe Auto-Ingestion of Orders & Archives
    # -------------------------------------------------------------------------

    def auto_ingest_shift_orders_to_dispatch(
        self,
        target_date_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Auto-registers unscanned orders/archives into StockDispatchScan with strict provenance
        notes (AUTO_ORDER_DISPATCH_NOTE) so daily installed totals are correct,
        while ensuring unallocated floor discrepancy tracking is preserved.
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        active_bond = bond_service.get_active_bond()
        active_bond_code = (active_bond.get("code") or self.default_facility).strip().upper()

        order_filter = Q(order_number__endswith=suffix) | Q(order_number__icontains=suffix)
        if target_date_suffix:
            order_filter |= Q(order_number__icontains=str(target_date_suffix).strip())
        orders_qs = list(InstallationOrder.objects.filter(order_filter))
        scoped_orders: List[InstallationOrder] = [
            o for o in orders_qs
            if bond_service.classify_order_bond_scope(o, active_code=active_bond_code) == "ACTIVE_BOND"
            and o.registration_number
        ]

        if not scoped_orders:
            return {
                "work_date_suffix": suffix,
                "auto_registered_plates": [],
                "auto_registered_count": 0,
            }

        existing_dispatches = set(
            normalizer.canonicalize(p)
            for p in StockDispatchScan.objects.filter(
                Q(work_date_suffix=suffix) | Q(work_date=work_d)
            ).values_list("registration_number", flat=True)
            if p
        )
        existing_returns = set(
            normalizer.canonicalize(p)
            for p in StockReturnScan.objects.filter(
                Q(work_date_suffix=suffix) | Q(work_date=work_d)
            ).values_list("registration_number", flat=True)
            if p
        )

        scans_to_create: List[StockDispatchScan] = []
        auto_registered_plates: List[str] = []
        seen_canon: Set[str] = set()

        for order in scoped_orders:
            c_plate = normalizer.canonicalize(order.registration_number)
            if not c_plate or c_plate in seen_canon:
                continue
            seen_canon.add(c_plate)
            if c_plate in existing_dispatches or c_plate in existing_returns:
                continue

            is_pmo = "PMO" in c_plate.upper() or "YELLOW" in (order.front_plate_type or "").upper()
            cat = PlateCategory.PMO if is_pmo else PlateCategory.PSV
            is_inst = bool(order.is_archived or (order.order_status or "").strip().lower() == "installed")
            scan_status = (
                StockDispatchScan.Status.RECONCILED_INSTALLED
                if is_inst
                else StockDispatchScan.Status.ON_LINE_ACTIVE
            )
            op_name = (order.installation_officer or "").strip() or "Auto-Registered (Order Sync)"

            scans_to_create.append(
                StockDispatchScan(
                    registration_number=c_plate,
                    plate_category=cat,
                    work_date=work_d,
                    work_date_suffix=suffix,
                    bond_code=active_bond_code,
                    operator_name=op_name,
                    status=scan_status,
                    notes=AUTO_ORDER_DISPATCH_NOTE,
                )
            )
            auto_registered_plates.append(c_plate)

        if scans_to_create:
            try:
                StockDispatchScan.objects.bulk_create(scans_to_create, ignore_conflicts=True)
                # Clear from blocked plates if previously blocked
                StockBlockedScan.objects.filter(
                    Q(work_date_suffix=suffix) | Q(work_date=work_d),
                    registration_number__in=auto_registered_plates,
                ).delete()
                logger.info(
                    "Auto-registered %d unscanned order/archive kit(s) into StockDispatchScan for shift %s: %s",
                    len(auto_registered_plates),
                    suffix,
                    ", ".join(auto_registered_plates[:20]),
                )
            except Exception as exc:
                logger.warning("Error auto-registering dispatched kits from orders for %s: %s", suffix, exc)

        return {
            "work_date_suffix": suffix,
            "auto_registered_plates": auto_registered_plates,
            "auto_registered_count": len(auto_registered_plates),
        }

    # -------------------------------------------------------------------------
    # 5. MVR Discrepancy Docket Generation
    # -------------------------------------------------------------------------

    def generate_mvr_docket(
        self,
        target_date_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Produces the formal, actionable MVR Allocation Discrepancy Docket.
        Returns:
        - raw_plates: Newline-separated list of plates for bulk search in ITMS.
        - formatted_docket: Complete text docket for MVR officer and team lead.
        - breakdown of unallocated, misallocated, and pending sync kits.
        """
        unalloc = self.get_unallocated_kits(target_date_suffix)
        misalloc = self.get_misallocated_kits(target_date_suffix)
        spiked = self.get_spiked_undispatched_kits(target_date_suffix)

        suffix = unalloc["work_date_suffix"]
        active_code = unalloc["warehouse_code"]
        active_bond = bond_service.get_active_bond()
        warehouse_name = active_bond.get("name", "AGM Bonded Warehouse")
        formatted_date = format_date_suffix_readable(suffix)
        now_str = timezone.now().strftime("%Y-%m-%d %H:%M:%S")

        unallocated_plates = unalloc["unallocated_plates"]
        raw_plates = "\n".join(unallocated_plates)

        plates_block = ""
        if unallocated_plates:
            plates_block = "\n".join(f"  {idx:2d}. {p}" for idx, p in enumerate(unallocated_plates, start=1))
        else:
            plates_block = "  [None - No unallocated discrepancies for this shift]"

        # Misallocation details
        misalloc_lines: List[str] = []
        if misalloc["misallocated_items"]:
            misalloc_lines.append("")
            misalloc_lines.append(f"MVR ALLOCATION MISMATCHES ({misalloc['misallocated_count']} Item(s)):")
            for item in misalloc["misallocated_items"]:
                misalloc_lines.append(f"  ⚠️ Plate {item.get('plate')}: {item.get('reason')}")

        docket_lines = [
            "==================================================",
            f"{warehouse_name.upper()} — MVR ALLOCATION EXCEPTION DOCKET",
            f"Shift Date: {formatted_date} (Suffix: {suffix})",
            f"Operating Facility: {warehouse_name} (Code: {active_code})",
            f"Generated At: {now_str}",
            "==================================================",
            "PHYSICAL DISPATCH & ITMS SUMMARY:",
            f"  • Physical Dispatched to Line:     {unalloc['total_physical_dispatched']:>4}",
            f"  • Reconciled Installed in ITMS:    {unalloc['reconciled_installed_count']:>4}",
            f"  • Returned to Safe Room:           {unalloc['returned_to_safe_count']:>4}",
            f"  • On-Line Active in ITMS:          {unalloc['on_line_active_count']:>4}",
            f"  • Pending System Sync (Photos):    {unalloc['pending_sync_count']:>4}",
            f"  • UNALLOCATED FLOOR DISCREPANCY:   {unalloc['unresolved_discrepancy_count']:>4}",
            "--------------------------------------------------",
            f"  • MVR Misallocations Detected:     {misalloc['misallocated_count']:>4}",
            f"  • Spiked (Undispatched) in ITMS:   {spiked['total_spiked_count']:>4}",
            "==================================================",
            "ACTION REQUIRED FOR MVR ALLOCATION TEAM:",
            f"The following {len(unallocated_plates)} kit(s) were physically issued to the line,",
            "are NOT returned to safe room, and have NO matching ITMS order or archive:",
            "",
            plates_block,
        ]
        if misalloc_lines:
            docket_lines.extend(misalloc_lines)

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
            "work_date_suffix": suffix,
            "formatted_date": formatted_date,
            "warehouse_name": warehouse_name,
            "active_bond_code": active_code,
            "unallocated_plates": unallocated_plates,
            "count": len(unallocated_plates),
            "pending_sync_count": unalloc["pending_sync_count"],
            "pending_sync_plates": unalloc["pending_sync_plates"],
            "unresolved_discrepancy_count": unalloc["unresolved_discrepancy_count"],
            "unresolved_discrepancy_plates": unalloc["unresolved_discrepancy_plates"],
            "misallocated_count": misalloc["misallocated_count"],
            "misallocated_items": misalloc["misallocated_items"],
            "spiked_count": spiked["total_spiked_count"],
            "spiked_plates": spiked["all_spiked_plates"],
            "raw_plates": raw_plates,
            "formatted_docket": formatted_docket,
        }


# Global singleton instance for clean consumption
kit_unallocation_service = KitUnallocationService()
