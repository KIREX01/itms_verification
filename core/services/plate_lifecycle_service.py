"""
ITMS Plate Lifecycle Service.

Detects, verifies, and classifies license plates across the 3 ITMS lifecycle states:
1. UNALLOCATED_KIT:
   - Kit status is "New" in https://stock.itms.ug/installation-kits.
   - Does NOT appear in active orders or completed archive.
   - Has not been assigned to a vehicle or sales order yet.
   - Badge: [NEW (UNALLOCATED)] (bold white on dark_green).

2. ALLOCATED_ORDER:
   - Order exists in active fitment queue https://stock.itms.ug/installation-orders/index.
   - Order status: "Ready for installation", "Under installation", "Ready for approve".
   - Ready for physical plate fitment and photo pairing.
   - Badge: [ALLOCATED (ACTIVE ORDER)] (bold black on gold1).

3. INSTALLED_ARCHIVE:
   - Completed order verified in https://stock.itms.ug/installation-orders/archive.
   - Order status: "Installed", Reg status: "Active".
   - Photographic evidence already submitted and approved by an officer.
   - Badge: [INSTALLED (ARCHIVE)] (bold white on dark_blue).

4. UNREGISTERED:
   - Plate does not exist in kits, active orders, or archive.
   - Badge: [NOT FOUND IN ITMS] (bold white on red).
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from django.db import models
from django.utils import timezone

from core.models import InstallationKit, InstallationOrder
from core.services.itms_web_client import get_web_client
from core.vision import normalizer

logger = logging.getLogger(__name__)


@dataclass
class PlateLifecycleResult:
    query: str
    canonical_plate: str
    display_plate: str
    lifecycle_state: str  # "UNALLOCATED_KIT" | "ALLOCATED_ORDER" | "INSTALLED_ARCHIVE" | "UNREGISTERED"
    badge_label: str      # "NEW (UNALLOCATED)" | "ALLOCATED" | "INSTALLED" | "NOT IN ITMS"
    badge_style: str      # Rich markup style
    summary_message: str

    is_unallocated: bool = False
    is_allocated: bool = False
    is_installed: bool = False
    is_unregistered: bool = False

    kit: Optional[Any] = None
    order: Optional[Any] = None

    kit_code: str = ""
    order_number: str = ""
    vin: str = ""
    order_status: str = ""
    reg_status: str = ""
    officer: str = ""
    install_date: str = ""
    warehouse: str = ""
    front_plate_serial: str = ""
    rear_plate_serial: str = ""
    front_tracker: str = ""
    rear_tracker: str = ""
    gps_tracker: str = ""
    sim_serial: str = ""
    sim_mac: str = ""
    created_date: str = ""
    created_by_user: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "canonical_plate": self.canonical_plate,
            "display_plate": self.display_plate,
            "lifecycle_state": self.lifecycle_state,
            "badge_label": self.badge_label,
            "badge_style": self.badge_style,
            "summary_message": self.summary_message,
            "is_unallocated": self.is_unallocated,
            "is_allocated": self.is_allocated,
            "is_installed": self.is_installed,
            "is_unregistered": self.is_unregistered,
            "kit_code": self.kit_code,
            "order_number": self.order_number,
            "vin": self.vin,
            "order_status": self.order_status,
            "reg_status": self.reg_status,
            "officer": self.officer,
            "install_date": self.install_date,
            "warehouse": self.warehouse,
            "front_plate_serial": self.front_plate_serial,
            "rear_plate_serial": self.rear_plate_serial,
            "front_tracker": self.front_tracker,
            "rear_tracker": self.rear_tracker,
            "gps_tracker": self.gps_tracker,
            "sim_serial": self.sim_serial,
            "sim_mac": self.sim_mac,
            "created_date": self.created_date,
            "created_by_user": self.created_by_user,
        }


def format_display_plate(raw_plate: str) -> str:
    """Formats Ugandan plate with standard space: e.g. 'UMA 300PW' or 'UBD 123A'."""
    p = (raw_plate or "").strip().upper()
    m = re.match(r"^([A-Z]{3})\s*(\d{3}[A-Z]{1,2})$", p)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return p


def resolve_plate_lifecycle(
    query_str: str,
    query_live_if_missing: bool = True,
) -> PlateLifecycleResult:
    """
    Classifies a plate or kit number into its exact ITMS lifecycle state:
    1. INSTALLED_ARCHIVE: Completed in archive.
    2. ALLOCATED_ORDER: Active order under installation/ready.
    3. UNALLOCATED_KIT: Exists in Installation Kits as 'New' (no order or archive).
    4. UNREGISTERED: Unknown in ITMS.
    """
    raw = (query_str or "").strip()
    if not raw:
        return PlateLifecycleResult(
            query=raw,
            canonical_plate="",
            display_plate="",
            lifecycle_state="UNREGISTERED",
            badge_label="EMPTY QUERY",
            badge_style="dim",
            summary_message="No plate or kit number provided.",
            is_unregistered=True,
        )

    # Normalize plate query
    canonical = normalizer.canonicalize(raw) or raw.replace(" ", "").upper()
    display = format_display_plate(raw)
    unspaced = raw.replace(" ", "").upper()

    # If query is kit code format (e.g. IK-UMA300PW)
    if raw.upper().startswith("IK-"):
        plate_part = raw[3:].strip()
        canonical = normalizer.canonicalize(plate_part) or plate_part.replace(" ", "").upper()
        display = format_display_plate(plate_part)

    # ──────────────────────────────────────────────────────────────────────────
    # Check 1: Completed Installation Archive (INSTALLED)
    # ──────────────────────────────────────────────────────────────────────────
    archived_order = InstallationOrder.objects.filter(
        models.Q(is_archived=True) | models.Q(order_status__iexact="Installed")
    ).filter(
        models.Q(registration_number__iexact=canonical)
        | models.Q(registration_number__iexact=display)
        | models.Q(registration_number__iexact=unspaced)
        | models.Q(order_number__iexact=raw)
    ).first()

    if not archived_order and query_live_if_missing:
        try:
            client = get_web_client()
            res = client.fetch_archive_orders(page=1, search_params=display)
            orders = res.get("orders", [])
            for o in orders:
                o_reg = normalizer.canonicalize(o.get("registration_number", ""))
                if o_reg == canonical or o.get("order_number") == raw:
                    client.sync_orders_to_local_db([o])
                    archived_order = InstallationOrder.objects.filter(
                        order_number=o.get("order_number")
                    ).first()
                    break
        except Exception as exc:
            logger.debug("Error querying live archive for %s: %s", display, exc)

    if archived_order:
        officer = archived_order.installation_officer or "Verified Officer"
        dt = archived_order.installation_date or "Archived"
        return PlateLifecycleResult(
            query=raw,
            canonical_plate=canonical,
            display_plate=display,
            lifecycle_state="INSTALLED_ARCHIVE",
            badge_label="INSTALLED (ARCHIVE)",
            badge_style="bold white on dark_blue",
            summary_message=(
                f"Plate is ALREADY INSTALLED in ITMS Archive! Order #{archived_order.order_number} "
                f"(Officer: {officer}, Date: {dt}). Do not reinstall."
            ),
            is_installed=True,
            order=archived_order,
            order_number=archived_order.order_number,
            vin=archived_order.vin,
            order_status=archived_order.order_status or "Installed",
            reg_status=archived_order.registration_status or "Active",
            officer=officer,
            install_date=dt,
            warehouse=archived_order.warehouse_name,
            front_plate_serial=archived_order.front_plate_serial or archived_order.plate_serial,
            rear_plate_serial=archived_order.rear_plate_serial,
            front_tracker=archived_order.front_beacon_id,
            rear_tracker=archived_order.rear_beacon_id,
            gps_tracker=archived_order.gps_tracker_id or archived_order.tracker_id,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Check 2: Active Installation Orders (ALLOCATED)
    # ──────────────────────────────────────────────────────────────────────────
    active_order = InstallationOrder.objects.filter(
        is_archived=False
    ).exclude(
        order_status__iexact="Installed"
    ).filter(
        models.Q(registration_number__iexact=canonical)
        | models.Q(registration_number__iexact=display)
        | models.Q(registration_number__iexact=unspaced)
        | models.Q(order_number__iexact=raw)
    ).first()

    if not active_order and query_live_if_missing:
        try:
            client = get_web_client()
            res = client.fetch_installation_orders(page=1, search_params=display, archive=False)
            orders = res.get("orders", [])
            for o in orders:
                o_reg = normalizer.canonicalize(o.get("registration_number", ""))
                if o_reg == canonical or o.get("order_number") == raw:
                    client.sync_orders_to_local_db([o])
                    active_order = InstallationOrder.objects.filter(
                        order_number=o.get("order_number")
                    ).first()
                    break
        except Exception as exc:
            logger.debug("Error querying live active orders for %s: %s", display, exc)

    if active_order:
        st = active_order.itms_stage or active_order.order_status or "Ready for installation"
        return PlateLifecycleResult(
            query=raw,
            canonical_plate=canonical,
            display_plate=display,
            lifecycle_state="ALLOCATED_ORDER",
            badge_label="ALLOCATED (ACTIVE ORDER)",
            badge_style="bold black on gold1",
            summary_message=(
                f"Plate has an ACTIVE installation order #{active_order.order_number} ({st}). "
                f"Warehouse: {active_order.warehouse_name or 'AGM Solutions'}. Ready for submission."
            ),
            is_allocated=True,
            order=active_order,
            order_number=active_order.order_number,
            vin=active_order.vin,
            order_status=st,
            reg_status=active_order.registration_status,
            warehouse=active_order.warehouse_name,
            front_plate_serial=active_order.front_plate_serial or active_order.plate_serial,
            rear_plate_serial=active_order.rear_plate_serial,
            front_tracker=active_order.front_beacon_id,
            rear_tracker=active_order.rear_beacon_id,
            gps_tracker=active_order.gps_tracker_id or active_order.tracker_id,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Check 3: Installation Kits (UNALLOCATED if status is 'New')
    # ──────────────────────────────────────────────────────────────────────────
    kit_record = InstallationKit.objects.filter(
        models.Q(registration_number__iexact=display)
        | models.Q(registration_number__iexact=canonical)
        | models.Q(registration_number__iexact=unspaced)
        | models.Q(kit_code__iexact=raw)
        | models.Q(kit_code__iexact=f"IK-{canonical}")
        | models.Q(kit_code__iexact=f"IK-{unspaced}")
    ).first()

    if not kit_record and query_live_if_missing:
        try:
            client = get_web_client()
            search_param = raw if raw.upper().startswith("IK-") else display
            res = client.fetch_installation_kits(page=1, search_params=search_param)
            kits = res.get("kits", [])
            for k in kits:
                k_reg = normalizer.canonicalize(k.get("registration_number", ""))
                k_code = k.get("kit_code", "").upper()
                if k_reg == canonical or k_code == raw.upper() or k_code == f"IK-{unspaced}":
                    client.sync_kits_to_local_db([k])
                    kit_record = InstallationKit.objects.filter(kit_code=k.get("kit_code")).first()
                    break
        except Exception as exc:
            logger.debug("Error querying live installation kits for %s: %s", display, exc)

    if kit_record:
        k_status = (kit_record.status or "New").strip()
        wh = kit_record.warehouse or "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)"

        if k_status.lower() == "new":
            return PlateLifecycleResult(
                query=raw,
                canonical_plate=canonical,
                display_plate=display,
                lifecycle_state="UNALLOCATED_KIT",
                badge_label="NEW (UNALLOCATED)",
                badge_style="bold white on dark_green",
                summary_message=(
                    f"UNALLOCATED PLATE! Kit {kit_record.kit_code} is New in ITMS stock at {wh}. "
                    f"It has not been allocated to an installation order yet."
                ),
                is_unallocated=True,
                kit=kit_record,
                kit_code=kit_record.kit_code,
                warehouse=wh,
                front_plate_serial=kit_record.front_plate,
                rear_plate_serial=kit_record.rear_plate,
                front_tracker=kit_record.front_tracker,
                rear_tracker=kit_record.rear_tracker,
                gps_tracker=kit_record.gps_tracker,
                sim_serial=kit_record.sim_serial,
                sim_mac=kit_record.sim_mac,
                created_date=kit_record.created_date,
                created_by_user=kit_record.created_by_user,
            )
        elif "allocat" in k_status.lower():
            return PlateLifecycleResult(
                query=raw,
                canonical_plate=canonical,
                display_plate=display,
                lifecycle_state="ALLOCATED_ORDER",
                badge_label="ALLOCATED (KIT STOCK)",
                badge_style="bold black on gold1",
                summary_message=f"Kit {kit_record.kit_code} is marked Allocated in ITMS stock.",
                is_allocated=True,
                kit=kit_record,
                kit_code=kit_record.kit_code,
                warehouse=wh,
                front_plate_serial=kit_record.front_plate,
                rear_plate_serial=kit_record.rear_plate,
                gps_tracker=kit_record.gps_tracker,
            )
        elif "install" in k_status.lower():
            return PlateLifecycleResult(
                query=raw,
                canonical_plate=canonical,
                display_plate=display,
                lifecycle_state="INSTALLED_ARCHIVE",
                badge_label="INSTALLED (KIT STOCK)",
                badge_style="bold white on dark_blue",
                summary_message=f"Kit {kit_record.kit_code} is marked Installed in ITMS stock.",
                is_installed=True,
                kit=kit_record,
                kit_code=kit_record.kit_code,
                warehouse=wh,
                front_plate_serial=kit_record.front_plate,
                rear_plate_serial=kit_record.rear_plate,
                gps_tracker=kit_record.gps_tracker,
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Check 4: Not Found Anywhere in ITMS
    # ──────────────────────────────────────────────────────────────────────────
    return PlateLifecycleResult(
        query=raw,
        canonical_plate=canonical,
        display_plate=display,
        lifecycle_state="UNREGISTERED",
        badge_label="NOT FOUND IN ITMS",
        badge_style="bold white on red",
        summary_message=f"Plate '{display}' does not exist in ITMS kits stock, active orders, or archive.",
        is_unregistered=True,
    )


def format_lifecycle_card(result: PlateLifecycleResult) -> str:
    """Formats a rich visual card displaying the plate lifecycle state and hardware inventory."""
    badge = f"[{result.badge_style}]  {result.badge_label}  [/{result.badge_style}]"
    lines = [
        f"[bold white]Plate Lifecycle Status:[/bold white] {badge}  [bold green]{result.display_plate}[/bold green]",
    ]

    if result.is_unallocated:
        lines.extend([
            f" [b]Kit Code:[/b]       [bold white]{result.kit_code}[/bold white]  │  [b]Status:[/b] [green]New in Stock[/green]",
            f" [b]Warehouse:[/b]      {result.warehouse[:36]}",
            f" [b]Hardware Serials:[/b] Front: [cyan]{result.front_plate_serial or '—'}[/cyan]  Rear: [cyan]{result.rear_plate_serial or '—'}[/cyan]  GPS: [cyan]{result.gps_tracker or '—'}[/cyan]",
            f" [yellow]Notice: This number plate has not been allocated to a vehicle/sales order yet.[/yellow]",
        ])
    elif result.is_allocated:
        lines.extend([
            f" [b]Order #:[/b]        [bold white]#{result.order_number}[/bold white]  │  [b]Stage:[/b] [bold yellow]{result.order_status}[/bold yellow]",
            f" [b]Chassis / VIN:[/b]  {result.vin or '—'}  │  [b]Warehouse:[/b] {result.warehouse[:30]}",
            f" [b]Hardware Serials:[/b] Front: [cyan]{result.front_plate_serial or '—'}[/cyan]  Rear: [cyan]{result.rear_plate_serial or '—'}[/cyan]  GPS: [cyan]{result.gps_tracker or '—'}[/cyan]",
            f" [bold green]✓ Order is active and ready for fitment verification and submission.[/bold green]",
        ])
    elif result.is_installed:
        lines.extend([
            f" [b]Order #:[/b]        [bold white]#{result.order_number}[/bold white]  │  [b]Status:[/b] [bold cyan]Installed[/bold cyan]",
            f" [b]Verified By:[/b]    {result.officer or 'Officer'}  │  [b]Date:[/b] {result.install_date or 'Recorded'}",
            f" [b]Chassis / VIN:[/b]  {result.vin or '—'}  │  [b]Warehouse:[/b] {result.warehouse[:30]}",
            f" [bold red]⚠ Plate has already been installed and submitted in ITMS Archive![/bold red]",
        ])
    else:
        lines.extend([
            f" [dim]{result.summary_message}[/dim]",
            f" [dim]Ensure plate number was typed correctly (e.g. UMA 300PW).[/dim]",
        ])

    return "\n".join(lines)
