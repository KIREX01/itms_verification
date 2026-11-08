"""
Stock Checking & Daily Ledger Service.

Encapsulates all inventory validation, stock checks, and daily ledger accounting
(Opening + Receipts + Transfer In - Transfer Out - Installed = Closing) as a dedicated,
cohesive domain service. Centralizes previously scattered ledger calculation,
override tracking, and inventory availability checking.
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
from core.services.date_service import (
    format_date_suffix_readable,
    get_active_shift_suffix,
    resolve_date_and_suffix,
)
from core.services.plate_lifecycle_service import format_display_plate
from core.vision import normalizer

logger = logging.getLogger(__name__)


class StockCheckingService:
    """
    Centralized service for warehouse stock checking and daily ledger calculations.
    """

    def __init__(self, default_facility: str = "AGM"):
        self.default_facility = default_facility.strip().upper()

    # -------------------------------------------------------------------------
    # 1. Stock Availability & Inventory Validation
    # -------------------------------------------------------------------------

    def validate_plates_stock_availability(
        self,
        plates: Iterable[str],
        category: Optional[str] = None,
        target_date_suffix: Optional[str] = None,
        warehouse_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validates a list of plate numbers or barcodes against warehouse inventory.
        Identifies:
        - valid_in_stock: Plates present in InstallationKit with status 'New' or verified in warehouse.
        - not_on_stock: Plates referenced or scanned that do NOT exist in active stock.
        - already_dispatched: Plates already marked dispatched for this shift.
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        active_bond = bond_service.get_active_bond()
        active_code = (warehouse_code or active_bond.get("code") or self.default_facility).strip().upper()

        clean_plates: List[str] = []
        canonical_map: Dict[str, str] = {}
        for p in plates:
            if not p:
                continue
            orig = str(p).strip().upper()
            c_plate = normalizer.canonicalize(orig)
            if c_plate and len(c_plate) >= 4:
                clean_plates.append(orig)
                canonical_map[c_plate] = orig

        if not clean_plates:
            return {
                "valid": True,
                "total_evaluated": 0,
                "in_stock_count": 0,
                "not_on_stock_count": 0,
                "in_stock_plates": [],
                "not_on_stock_plates": [],
                "already_dispatched_plates": [],
            }

        # Check existing dispatches for shift
        existing_dispatches = set(
            normalizer.canonicalize(p)
            for p in StockDispatchScan.objects.filter(
                Q(work_date_suffix=suffix) | Q(work_date=work_d)
            ).values_list("registration_number", flat=True)
            if p
        )

        # Check InstallationKit records
        q_filter = Q()
        for c_plate in canonical_map.keys():
            disp = format_display_plate(c_plate)
            q_filter |= Q(registration_number__iexact=c_plate) | Q(registration_number__iexact=disp)

        kit_records = list(InstallationKit.objects.filter(q_filter))
        kit_map: Dict[str, InstallationKit] = {}
        for k in kit_records:
            c = normalizer.canonicalize(k.registration_number)
            if c:
                kit_map[c] = k

        in_stock_plates: List[str] = []
        not_on_stock_plates: List[str] = []
        already_dispatched_plates: List[str] = []

        for c_plate, orig_plate in canonical_map.items():
            if c_plate in existing_dispatches:
                already_dispatched_plates.append(orig_plate)

            if c_plate in kit_map:
                in_stock_plates.append(orig_plate)
            else:
                not_on_stock_plates.append(orig_plate)

        return {
            "valid": len(not_on_stock_plates) == 0,
            "total_evaluated": len(canonical_map),
            "in_stock_count": len(in_stock_plates),
            "not_on_stock_count": len(not_on_stock_plates),
            "already_dispatched_count": len(already_dispatched_plates),
            "in_stock_plates": in_stock_plates,
            "not_on_stock_plates": not_on_stock_plates,
            "already_dispatched_plates": already_dispatched_plates,
            "work_date_suffix": suffix,
            "warehouse_code": active_code,
        }

    # -------------------------------------------------------------------------
    # 2. Daily Stock Ledger Retrieval & Computation
    # -------------------------------------------------------------------------

    def get_or_create_daily_ledger(
        self,
        target_date_suffix: Optional[str] = None,
    ) -> Tuple[DailyStockLedger, bool]:
        """
        Retrieves or provisions the DailyStockLedger record for the given shift date suffix.
        Carries forward previous day closing balance if opening balance is not yet set.
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        active_bond = bond_service.get_active_bond()
        active_bond_name = active_bond.get("name", "AGM Bonded Warehouse")

        ledger, created = DailyStockLedger.objects.get_or_create(
            work_date=work_d,
            defaults={
                "work_date_suffix": suffix,
                "warehouse_name": active_bond_name,
                "opening_stock": 0,
                "opening_balance_psv": 0,
                "opening_balance_pmo": 0,
            },
        )

        if not ledger.warehouse_name:
            ledger.warehouse_name = active_bond_name

        # Auto-carry forward previous day closing balance if not overridden
        prev_ledger = (
            DailyStockLedger.objects.filter(work_date__lt=work_d)
            .order_by("-work_date")
            .first()
        )
        if prev_ledger and not getattr(ledger, "override_opening", False) and ledger.opening_stock == 0:
            if prev_ledger.closing_balance_psv > 0 or prev_ledger.closing_balance_pmo > 0:
                ledger.opening_balance_psv = max(0, prev_ledger.closing_balance_psv)
                ledger.opening_balance_pmo = max(0, prev_ledger.closing_balance_pmo)
                ledger.opening_stock = ledger.opening_balance_psv + ledger.opening_balance_pmo
                ledger.save(update_fields=["opening_balance_psv", "opening_balance_pmo", "opening_stock", "warehouse_name"])

        return ledger, created

    def calculate_shift_balances(
        self,
        target_date_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Calculates all 9 rows of the daily stock ledger:
        1. Opening Balance (PSV, PMO, Total)
        2. Kits Received (Inbound Deliveries)
        3. Bond Transfer In
        4. Bond Transfer Out
        5. Scheduled (Target)
        6. Kits Installed (Orders + Archive)
        7. Closing Balance = Opening + Received + Transfer In - Transfer Out - Installed
        8. Physical Count (from SafeAuditScan)
        9. Variance = Physical Count - Closing Balance
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        ledger, _ = self.get_or_create_daily_ledger(suffix)

        active_bond = bond_service.get_active_bond()
        active_bond_code = active_bond.get("code", "AGM")
        active_bond_name = active_bond.get("name", "AGM Bonded Warehouse")

        # 1. Kits Received (Inbound Deliveries)
        if ledger.override_received:
            kits_received_psv = ledger.kits_received_psv
            kits_received_pmo = ledger.kits_received_pmo
            total_kits_received = kits_received_psv + kits_received_pmo
        else:
            deliveries_qs = StockDelivery.objects.filter(
                Q(target_date_suffix=suffix) | Q(delivery_date=work_d)
            )
            kits_received_psv = 0
            kits_received_pmo = 0
            for d in deliveries_qs:
                items_qs = d.items.all()
                if items_qs.exists():
                    for itm in items_qs:
                        if itm.plate_category == PlateCategory.PMO:
                            kits_received_pmo += 1
                        else:
                            kits_received_psv += 1
                elif d.total_plates_count > 0:
                    if d.plate_category == PlateCategory.PMO:
                        kits_received_pmo += d.total_plates_count
                    else:
                        kits_received_psv += d.total_plates_count
            total_kits_received = kits_received_psv + kits_received_pmo

        # 2. Bond Transfers (Transfer In / Transfer Out)
        if ledger.override_transfers:
            transfer_in_psv = ledger.bond_transfer_in_psv
            transfer_in_pmo = ledger.bond_transfer_in_pmo
            total_transfer_in = transfer_in_psv + transfer_in_pmo
            transfer_out_psv = ledger.bond_transfer_out_psv
            transfer_out_pmo = ledger.bond_transfer_out_pmo
            total_transfer_out = transfer_out_psv + transfer_out_pmo
        else:
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

        # 3. Kits Installed (Orders & Archive for this day)
        orders_qs = list(InstallationOrder.objects.filter(order_number__endswith=suffix))
        scoped_orders: List[InstallationOrder] = []
        cross_bond_orders: List[InstallationOrder] = []

        for o in orders_qs:
            if bond_service.classify_order_bond_scope(o, active_code=active_bond_code) == "ACTIVE_BOND":
                scoped_orders.append(o)
            else:
                cross_bond_orders.append(o)

        installed_orders_qs = scoped_orders

        if ledger.override_installed:
            installed_psv_count = ledger.kits_installed_psv
            installed_pmo_count = ledger.kits_installed_pmo
            total_installed = installed_psv_count + installed_pmo_count
        else:
            # Classify installed orders into PSV vs PMO
            pmo_plates_set = {
                normalizer.canonicalize(p)
                for p in StockDispatchScan.objects.filter(plate_category=PlateCategory.PMO).values_list("registration_number", flat=True)
            }
            pmo_plates_set.update(
                normalizer.canonicalize(p)
                for p in StockDeliveryItem.objects.filter(plate_category=PlateCategory.PMO).values_list("registration_number", flat=True)
            )
            pmo_audit_plates = (
                SubmissionAuditLog.objects.filter(
                    message__icontains="Category: PMO",
                    pair__order__order_number__endswith=suffix,
                )
                .values_list("pair__order__registration_number", flat=True)
                .distinct()
            )
            for p in pmo_audit_plates:
                if p:
                    pmo_plates_set.add(normalizer.canonicalize(p))

            installed_psv_count = 0
            installed_pmo_count = 0
            for o in installed_orders_qs:
                c_p = normalizer.canonicalize(o.registration_number)
                if c_p in pmo_plates_set:
                    installed_pmo_count += 1
                else:
                    installed_psv_count += 1

            total_installed = installed_psv_count + installed_pmo_count

        # 4. Closing Balance Calculation
        if ledger.override_closing:
            closing_psv = ledger.closing_balance_psv
            closing_pmo = ledger.closing_balance_pmo
            total_closing = closing_psv + closing_pmo
        else:
            closing_psv = (
                ledger.opening_balance_psv
                + kits_received_psv
                - installed_psv_count
                - transfer_out_psv
                + transfer_in_psv
            )
            closing_pmo = (
                ledger.opening_balance_pmo
                + kits_received_pmo
                - installed_pmo_count
                - transfer_out_pmo
                + transfer_in_pmo
            )
            total_closing = closing_psv + closing_pmo

        # 5. Scheduled Target & KPIs
        scheduled_total = ledger.scheduled_total if ledger.scheduled_total > 0 else (ledger.scheduled_psv + ledger.scheduled_pmo)
        daily_perf_total = round((total_installed / scheduled_total * 100), 1) if scheduled_total > 0 else 0.0
        daily_perf_psv = round((installed_psv_count / ledger.scheduled_psv * 100), 1) if ledger.scheduled_psv > 0 else 0.0
        daily_perf_pmo = round((installed_pmo_count / ledger.scheduled_pmo * 100), 1) if ledger.scheduled_pmo > 0 else 0.0

        backlog_total = max(0, scheduled_total - total_installed)
        backlog_psv = max(0, ledger.scheduled_psv - installed_psv_count) if ledger.scheduled_psv > 0 else 0
        backlog_pmo = max(0, ledger.scheduled_pmo - installed_pmo_count) if ledger.scheduled_pmo > 0 else 0

        # Physical Audit Scans
        audit_scans_count = SafeAuditScan.objects.filter(work_date_suffix=suffix).count()
        if audit_scans_count > 0:
            ledger.physical_count = audit_scans_count

        if ledger.physical_count is not None:
            ledger.variance = ledger.physical_count - total_closing
        else:
            ledger.variance = 0

        # Update ledger snapshot
        ledger.opening_stock = ledger.opening_balance_psv + ledger.opening_balance_pmo
        if not ledger.override_received:
            ledger.kits_received_psv = kits_received_psv
            ledger.kits_received_pmo = kits_received_pmo
            ledger.delivered_count = total_kits_received

        if not ledger.override_transfers:
            ledger.bond_transfer_in_psv = transfer_in_psv
            ledger.bond_transfer_in_pmo = transfer_in_pmo
            ledger.bond_transfer_in_total = total_transfer_in
            ledger.bond_transfer_out_psv = transfer_out_psv
            ledger.bond_transfer_out_pmo = transfer_out_pmo
            ledger.bond_transfer_out_total = total_transfer_out

        ledger.scheduled_total = scheduled_total
        if not ledger.override_installed:
            ledger.kits_installed_psv = installed_psv_count
            ledger.kits_installed_pmo = installed_pmo_count
            ledger.installed_count = total_installed

        if not ledger.override_closing:
            ledger.closing_balance_psv = closing_psv
            ledger.closing_balance_pmo = closing_pmo
            ledger.closing_stock = total_closing

        ledger.last_reconciled_at = timezone.now()
        ledger.save()

        # Build Structured 9-Row Report Table
        report_rows = [
            {
                "row_num": 1,
                "metric": "1. Opening Balance",
                "pmo": ledger.opening_balance_pmo,
                "psv": ledger.opening_balance_psv,
                "total": ledger.opening_stock,
                "description": "Starting physical warehouse balance before daily shifts commence.",
            },
            {
                "row_num": 2,
                "metric": "2. Kits Received",
                "pmo": ledger.kits_received_pmo,
                "psv": ledger.kits_received_psv,
                "total": ledger.delivered_count,
                "description": "Inbound kits delivered to bond warehouse for this shift.",
            },
            {
                "row_num": 3,
                "metric": "3. Bond Transfer In",
                "pmo": ledger.bond_transfer_in_pmo,
                "psv": ledger.bond_transfer_in_psv,
                "total": ledger.bond_transfer_in_total,
                "description": "Kits transferred from other bonds into our bond.",
            },
            {
                "row_num": 4,
                "metric": "4. Bond Transfer Out",
                "pmo": ledger.bond_transfer_out_pmo,
                "psv": ledger.bond_transfer_out_psv,
                "total": ledger.bond_transfer_out_total,
                "description": "Kits transferred from our bond out to other bond facilities.",
            },
            {
                "row_num": 5,
                "metric": "5. Scheduled (Target)",
                "pmo": ledger.scheduled_pmo,
                "psv": ledger.scheduled_psv,
                "total": scheduled_total,
                "description": "Daily planned/targeted installation quota.",
            },
            {
                "row_num": 6,
                "metric": "6. Kits Installed (Actual)",
                "pmo": ledger.kits_installed_pmo,
                "psv": ledger.kits_installed_psv,
                "total": ledger.installed_count,
                "description": "Actual installations registered in ITMS orders & archive for this shift.",
            },
            {
                "row_num": 7,
                "metric": "7. Closing Balance",
                "pmo": ledger.closing_balance_pmo,
                "psv": ledger.closing_balance_psv,
                "total": ledger.closing_stock,
                "description": "Calculated ending balance = Opening + Received + Transfer In - Transfer Out - Installed.",
            },
            {
                "row_num": 8,
                "metric": "8. Physical Count",
                "pmo": "---",
                "psv": "---",
                "total": ledger.physical_count if ledger.physical_count is not None else 0,
                "description": "Actual physical safe room audit count.",
            },
            {
                "row_num": 9,
                "metric": "9. Variance",
                "pmo": "---",
                "psv": "---",
                "total": ledger.variance if ledger.variance is not None else 0,
                "description": "Physical Count minus Closing Balance (0 indicates perfect reconciliation).",
            },
        ]

        return {
            "work_date": work_d.isoformat(),
            "work_date_suffix": suffix,
            "formatted_date": format_date_suffix_readable(suffix),
            "warehouse_name": ledger.warehouse_name,
            "active_bond_code": active_bond_code,
            "ledger": {
                "id": ledger.id,
                "opening_stock": ledger.opening_stock,
                "opening_balance_psv": ledger.opening_balance_psv,
                "opening_balance_pmo": ledger.opening_balance_pmo,
                "delivered_count": ledger.delivered_count,
                "kits_received_psv": ledger.kits_received_psv,
                "kits_received_pmo": ledger.kits_received_pmo,
                "bond_transfer_in_total": ledger.bond_transfer_in_total,
                "bond_transfer_in_psv": ledger.bond_transfer_in_psv,
                "bond_transfer_in_pmo": ledger.bond_transfer_in_pmo,
                "bond_transfer_out_total": ledger.bond_transfer_out_total,
                "bond_transfer_out_psv": ledger.bond_transfer_out_psv,
                "bond_transfer_out_pmo": ledger.bond_transfer_out_pmo,
                "scheduled_total": scheduled_total,
                "scheduled_psv": ledger.scheduled_psv,
                "scheduled_pmo": ledger.scheduled_pmo,
                "installed_count": ledger.installed_count,
                "kits_installed_psv": ledger.kits_installed_psv,
                "kits_installed_pmo": ledger.kits_installed_pmo,
                "closing_stock": ledger.closing_stock,
                "closing_balance_psv": ledger.closing_balance_psv,
                "closing_balance_pmo": ledger.closing_balance_pmo,
                "physical_count": ledger.physical_count,
                "variance": ledger.variance,
                "override_installed": ledger.override_installed,
                "override_received": ledger.override_received,
                "override_transfers": ledger.override_transfers,
                "override_closing": ledger.override_closing,
                "notes": ledger.notes or "",
            },
            "performance": {
                "daily_perf_total": daily_perf_total,
                "daily_perf_psv": daily_perf_psv,
                "daily_perf_pmo": daily_perf_pmo,
                "backlog_total": backlog_total,
                "backlog_psv": backlog_psv,
                "backlog_pmo": backlog_pmo,
            },
            "report_table": {
                "headers": ["Stock Movement Category", "PMO Yellow", "PSV White", "Total Combined", "Notes & Explanations"],
                "rows": report_rows,
            },
        }

    # -------------------------------------------------------------------------
    # 3. Direct Manual Entry & Overrides
    # -------------------------------------------------------------------------

    def update_ledger_entry(
        self,
        target_date_suffix: Optional[str] = None,
        storage_bond_name: Optional[str] = None,
        opening_balance_pmo: Optional[int] = None,
        opening_balance_psv: Optional[int] = None,
        opening_stock: Optional[int] = None,
        kits_received_pmo: Optional[int] = None,
        kits_received_psv: Optional[int] = None,
        scheduled_pmo: Optional[int] = None,
        scheduled_psv: Optional[int] = None,
        scheduled_total: Optional[int] = None,
        kits_installed_pmo: Optional[int] = None,
        kits_installed_psv: Optional[int] = None,
        bond_transfer_in_pmo: Optional[int] = None,
        bond_transfer_in_psv: Optional[int] = None,
        bond_transfer_out_pmo: Optional[int] = None,
        bond_transfer_out_psv: Optional[int] = None,
        closing_balance_pmo: Optional[int] = None,
        closing_balance_psv: Optional[int] = None,
        closing_stock: Optional[int] = None,
        physical_count: Optional[int] = None,
        notes: Optional[str] = None,
        operator_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Updates the daily stock ledger with manual values or overrides.
        Allows setting opening, targets, closing overrides, physical count, and remarks atomically.
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        ledger, _ = self.get_or_create_daily_ledger(suffix)

        if storage_bond_name:
            ledger.warehouse_name = storage_bond_name.strip()

        # Opening balance
        if opening_balance_psv is not None:
            ledger.opening_balance_psv = max(0, opening_balance_psv)
        if opening_balance_pmo is not None:
            ledger.opening_balance_pmo = max(0, opening_balance_pmo)
        if opening_stock is not None:
            ledger.opening_stock = max(0, opening_stock)
        else:
            ledger.opening_stock = ledger.opening_balance_psv + ledger.opening_balance_pmo

        # Kits received override
        if kits_received_psv is not None or kits_received_pmo is not None:
            ledger.override_received = True
            if kits_received_psv is not None:
                ledger.kits_received_psv = max(0, kits_received_psv)
            if kits_received_pmo is not None:
                ledger.kits_received_pmo = max(0, kits_received_pmo)
            ledger.delivered_count = ledger.kits_received_psv + ledger.kits_received_pmo

        # Scheduled target
        if scheduled_psv is not None:
            ledger.scheduled_psv = max(0, scheduled_psv)
        if scheduled_pmo is not None:
            ledger.scheduled_pmo = max(0, scheduled_pmo)
        if scheduled_total is not None:
            ledger.scheduled_total = max(0, scheduled_total)
        elif scheduled_psv is not None or scheduled_pmo is not None:
            ledger.scheduled_total = ledger.scheduled_psv + ledger.scheduled_pmo

        # Installed override
        if kits_installed_psv is not None or kits_installed_pmo is not None:
            ledger.override_installed = True
            if kits_installed_psv is not None:
                ledger.kits_installed_psv = max(0, kits_installed_psv)
            if kits_installed_pmo is not None:
                ledger.kits_installed_pmo = max(0, kits_installed_pmo)
            ledger.installed_count = ledger.kits_installed_psv + ledger.kits_installed_pmo

        # Transfers override
        if any(v is not None for v in [bond_transfer_in_psv, bond_transfer_in_pmo, bond_transfer_out_psv, bond_transfer_out_pmo]):
            ledger.override_transfers = True
            if bond_transfer_in_psv is not None:
                ledger.bond_transfer_in_psv = max(0, bond_transfer_in_psv)
            if bond_transfer_in_pmo is not None:
                ledger.bond_transfer_in_pmo = max(0, bond_transfer_in_pmo)
            ledger.bond_transfer_in_total = ledger.bond_transfer_in_psv + ledger.bond_transfer_in_pmo

            if bond_transfer_out_psv is not None:
                ledger.bond_transfer_out_psv = max(0, bond_transfer_out_psv)
            if bond_transfer_out_pmo is not None:
                ledger.bond_transfer_out_pmo = max(0, bond_transfer_out_pmo)
            ledger.bond_transfer_out_total = ledger.bond_transfer_out_psv + ledger.bond_transfer_out_pmo

        # Closing override
        if closing_balance_psv is not None or closing_balance_pmo is not None or closing_stock is not None:
            ledger.override_closing = True
            if closing_balance_psv is not None:
                ledger.closing_balance_psv = closing_balance_psv
            if closing_balance_pmo is not None:
                ledger.closing_balance_pmo = closing_balance_pmo
            if closing_stock is not None:
                ledger.closing_stock = closing_stock
            else:
                ledger.closing_stock = ledger.closing_balance_psv + ledger.closing_balance_pmo

        if physical_count is not None:
            ledger.physical_count = max(0, physical_count)

        if notes is not None:
            ledger.notes = notes.strip()

        ledger.last_reconciled_at = timezone.now()
        ledger.save()

        # Recalculate balances
        return self.calculate_shift_balances(suffix)

    def auto_carry_previous_closing(
        self,
        target_date_suffix: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Carries forward closing balances from the immediately preceding shift
        to populate the opening balance of the active shift.
        """
        work_d, suffix = resolve_date_and_suffix(target_date_suffix)
        prev_ledger = (
            DailyStockLedger.objects.filter(work_date__lt=work_d)
            .order_by("-work_date")
            .first()
        )
        if not prev_ledger:
            return {
                "success": False,
                "error": f"No previous shift ledger found before {work_d}.",
                "work_date_suffix": suffix,
            }

        prev_closing_psv = max(0, prev_ledger.closing_balance_psv)
        prev_closing_pmo = max(0, prev_ledger.closing_balance_pmo)
        prev_closing_total = prev_closing_psv + prev_closing_pmo

        return self.update_ledger_entry(
            target_date_suffix=suffix,
            opening_balance_psv=prev_closing_psv,
            opening_balance_pmo=prev_closing_pmo,
            opening_stock=prev_closing_total,
        )


# Global singleton instance for clean consumption
stock_checking_service = StockCheckingService()
