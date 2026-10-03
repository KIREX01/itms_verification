"""
Installation Kit Stock Synchronization & Morning Provisioning Service.

Ensures that all license plates received in deliveries, scanned during safe room
stock-taking / retaking audits, or created in ITMS are pre-provisioned and marked
as 'New' in warehouse physical stock before morning dispatch scanning begins.
"""
import logging
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.models import (
    InstallationKit,
    InstallationOrder,
    StockDelivery,
    StockDeliveryItem,
    StockDispatchScan,
    StockReturnScan,
)
from core.services import bond_service
from core.services.itms_web_client import get_web_client
from core.services.plate_lifecycle_service import format_display_plate
from core.vision import normalizer

logger = logging.getLogger(__name__)


def sync_and_provision_warehouse_kits(
    target_date_suffix: Optional[str] = None,
    source_plates: Optional[Iterable[str]] = None,
    sync_itms: bool = True,
    facility_name: Optional[str] = None,
    max_pages: int = 35,
    log_callback: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Synchronizes, provisions, and marks installation kits as 'New' in warehouse stock.

    Collects plates globally (unconstrained by shift date) from:
    1. Inbound deliveries (StockDeliveryItem).
    2. Physical safe room stock-taking / retaking scans.
    3. Central ITMS web portal (/installation-kits) supporting full multi-page crawl (20+ pages)
       and targeted search for candidate plates.
    4. Optional explicit source_plates.

    For every plate:
    - If it already has an active order (allocated) or is archived (installed),
      preserves its respective lifecycle status.
    - Otherwise, guarantees it exists in InstallationKit with status='New'
      and warehouse set to the operating facility (e.g. AGM SPIRO).
    """
    start_time = time.time()
    active_bond = bond_service.get_active_bond()
    wh_name = facility_name or active_bond.get("name", "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)")

    candidate_plates: Set[str] = set()

    # 1. Gather plates from all inbound deliveries
    delivery_plates = set(
        StockDeliveryItem.objects.exclude(registration_number="").values_list("registration_number", flat=True)
    )
    for p in delivery_plates:
        c = normalizer.canonicalize(p)
        if c:
            candidate_plates.add(c)

    # 2. Gather plates from safe room returns / retaking scans
    return_plates = set(
        StockReturnScan.objects.exclude(registration_number="").values_list("registration_number", flat=True)
    )
    for p in return_plates:
        c = normalizer.canonicalize(p)
        if c:
            candidate_plates.add(c)

    # 3. Gather plates from explicit source if provided
    if source_plates:
        for p in source_plates:
            c = normalizer.canonicalize(p)
            if c:
                candidate_plates.add(c)

    # 4. ITMS Web Client Online Sync for Installation Kits (multi-page crawl + targeted search)
    itms_synced_count = 0
    itms_pages_crawled = 0
    itms_status = "Skipped"
    itms_error = None
    if sync_itms:
        try:
            client = get_web_client()
            session = client.session_store.session
            if session and session.is_cookie_valid():
                # A. Multi-page crawling first across all 20+ pages on ITMS
                if log_callback:
                    log_callback(f"Starting ITMS installation kits multi-page crawl (up to {max_pages} pages)...")
                crawl_res = client.fetch_and_sync_all_kits(
                    max_pages=max_pages,
                    delay=0.12,
                    log_callback=log_callback,
                    allow_local_fallback=False,
                )
                if crawl_res.get("success"):
                    fetched = crawl_res.get("total_fetched", 0)
                    itms_synced_count += fetched
                    itms_pages_crawled = crawl_res.get("pages_crawled", 0)
                    itms_status = f"✓ Fetched {itms_synced_count} kits across {itms_pages_crawled} pages"
                    for k in InstallationKit.objects.exclude(registration_number="").values_list("registration_number", flat=True):
                        c = normalizer.canonicalize(k)
                        if c:
                            candidate_plates.add(c)
                else:
                    itms_error = crawl_res.get("error", "Multi-page crawl failed")
                    itms_status = f"⚠️ ITMS Crawl Notice: {itms_error}"
                    if log_callback:
                        log_callback(f"⚠️ ITMS Crawl Notice: {itms_error}")

                # B. Targeted plate search for any delivery/audit candidate plates not yet in DB
                existing_known_plates = set(
                    InstallationKit.objects.exclude(registration_number="").values_list("registration_number", flat=True)
                )
                missing_candidates = [p for p in candidate_plates if p not in existing_known_plates]
                if missing_candidates:
                    if log_callback:
                        log_callback(f"Targeting {len(missing_candidates[:60])} missing plates in ITMS...")
                    search_res = client.search_and_sync_kits_for_plates(missing_candidates[:60], log_callback=log_callback)
                    itms_synced_count += search_res.get("kits_found", 0)
            else:
                itms_status = "⚠️ ITMS session expired / not signed in (used local records)"
                itms_error = "Session expired or not signed in"
                if log_callback:
                    log_callback("⚠️ ITMS session is not active. Sign in via Tab 2 to fetch new kits from cloud.")
        except Exception as itms_err:
            logger.warning("ITMS web kit sync encountered error: %s", itms_err)
            itms_error = str(itms_err)
            itms_status = f"⚠️ ITMS Error: {itms_err}"
            if log_callback:
                log_callback(f"⚠️ ITMS kit sync error: {itms_err}")

    if not candidate_plates:
        # Fallback to existing InstallationKit records in database
        candidate_plates = set(
            InstallationKit.objects.exclude(registration_number="").values_list("registration_number", flat=True)
        )

    # Pre-fetch existing orders to identify already allocated/installed plates using light values_list
    orders_vals = InstallationOrder.objects.exclude(registration_number="").values_list(
        "registration_number", "is_archived", "order_status"
    )
    installed_plates = set()
    active_pending_plates = set()

    for reg, is_arch, ord_st in orders_vals:
        c = normalizer.canonicalize(reg)
        if not c:
            continue
        if is_arch or (ord_st or "").strip().lower() == "installed":
            installed_plates.add(c)
        else:
            active_pending_plates.add(c)

    # Fetch ONLY candidate InstallationKit records in-memory (prevents loading entire DB table into RAM)
    target_codes = [f"IK-{p}" for p in candidate_plates]
    kits_qs = InstallationKit.objects.filter(
        Q(registration_number__in=candidate_plates) | Q(kit_code__in=target_codes)
    )
    existing_kits_map: Dict[str, InstallationKit] = {}
    for k in kits_qs:
        c = normalizer.canonicalize(k.registration_number)
        if c:
            existing_kits_map[c] = k
        if k.kit_code:
            existing_kits_map[k.kit_code.strip().upper()] = k

    kits_to_create: List[InstallationKit] = []
    kits_to_update: List[InstallationKit] = []
    new_kits_ready: Set[str] = set()

    today_str = timezone.localdate().strftime("%d.%m.%Y")

    for c_plate in candidate_plates:
        # Determine target status
        if c_plate in installed_plates:
            target_status = "Installed"
        elif c_plate in active_pending_plates:
            target_status = "Allocated"
        else:
            target_status = "New"
            new_kits_ready.add(c_plate)

        kit = existing_kits_map.get(c_plate) or existing_kits_map.get(f"IK-{c_plate}")
        if kit:
            changed = False
            # If kit has no orders and status was not New, reset to New
            if target_status == "New" and (kit.status or "").strip().lower() != "new":
                kit.status = "New"
                changed = True
            elif target_status in ("Installed", "Allocated") and kit.status != target_status:
                kit.status = target_status
                changed = True

            if not kit.warehouse or kit.warehouse == "Warehouse Stock":
                kit.warehouse = wh_name
                changed = True

            if changed:
                kits_to_update.append(kit)
        else:
            kits_to_create.append(
                InstallationKit(
                    kit_code=f"IK-{c_plate}",
                    registration_number=c_plate,
                    status=target_status,
                    warehouse=wh_name,
                    created_date=today_str,
                )
            )

    with transaction.atomic():
        if kits_to_create:
            InstallationKit.objects.bulk_create(kits_to_create, ignore_conflicts=True, batch_size=200)
        if kits_to_update:
            InstallationKit.objects.bulk_update(kits_to_update, ["status", "warehouse"], batch_size=200)

    # Compute series breakdown of kits ready as 'New'
    series_breakdown: Dict[str, int] = {}
    for p in new_kits_ready:
        s = p[6:] if len(p) >= 7 else "UG"
        series_breakdown[s] = series_breakdown.get(s, 0) + 1

    duration_ms = int((time.time() - start_time) * 1000)

    total_new_in_warehouse = InstallationKit.objects.filter(
        status__iexact="New"
    ).count()

    return {
        "success": True,
        "total_candidates_processed": len(candidate_plates),
        "kits_created": len(kits_to_create),
        "kits_updated": len(kits_to_update),
        "new_kits_ready_count": len(new_kits_ready),
        "total_warehouse_new_stock": total_new_in_warehouse,
        "warehouse_facility": wh_name,
        "itms_kits_synced": itms_synced_count,
        "itms_pages_crawled": itms_pages_crawled,
        "itms_status": itms_status,
        "itms_error": itms_error,
        "series_breakdown": dict(sorted(series_breakdown.items(), key=lambda x: -x[1])),
        "duration_ms": duration_ms,
        "message": (
            f"Synced & provisioned {len(new_kits_ready)} installation kits with status 'New' "
            f"in {wh_name}. ITMS: {itms_synced_count} fetched ({itms_pages_crawled} pages), "
            f"{len(kits_to_create)} created, {len(kits_to_update)} updated in local DB."
        ),
    }


def verify_scanned_kits_stock(
    scanned_plates: Iterable[str],
    check_itms_live: bool = True,
    facility_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Two-tier verification of plates scanned for dispatch/issuance:
    - Tier 1: Search local synced InstallationKit table.
    - Tier 2: If missing locally and check_itms_live=True, query ITMS /installation-kits live.
      If matched on ITMS: auto-syncs the kit into the local database and approves dispatch.
      If not found on ITMS: rejects the kit (not on stock, cannot be taken out).

    Returns:
    {
        "success": bool,
        "total_scanned": int,
        "verified_plates": List[str],
        "verified_kits": List[InstallationKit],
        "synced_from_itms": List[str],
        "rejected_not_on_stock": List[str],
        "already_installed": List[str],
        "details": Dict[str, Dict[str, Any]],
        "warehouse": str,
        "message": str,
    }
    """
    active_bond = bond_service.get_active_bond()
    wh_name = facility_name or active_bond.get("name", "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)")

    clean_plates: List[str] = []
    for raw_p in scanned_plates:
        c = normalizer.canonicalize(raw_p)
        if c and c not in clean_plates:
            clean_plates.append(c)

    if not clean_plates:
        return {
            "success": True,
            "total_scanned": 0,
            "verified_plates": [],
            "verified_kits": [],
            "synced_from_itms": [],
            "rejected_not_on_stock": [],
            "already_installed": [],
            "details": {},
            "warehouse": wh_name,
            "message": "No valid license plates provided for stock verification.",
        }

    # Step 1: Query local synced InstallationKit records
    target_codes = [f"IK-{p}" for p in clean_plates]
    local_kits_qs = InstallationKit.objects.filter(
        Q(registration_number__in=clean_plates) | Q(kit_code__in=target_codes)
    )

    local_map: Dict[str, InstallationKit] = {}
    for k in local_kits_qs:
        c = normalizer.canonicalize(k.registration_number)
        if c:
            local_map[c] = k
        if k.kit_code:
            local_map[k.kit_code.strip().upper()] = k

    # Pre-check for orders that are already installed or archived
    installed_orders = set(
        InstallationOrder.objects.filter(
            Q(registration_number__in=clean_plates),
            Q(is_archived=True) | Q(order_status__iexact="Installed"),
        ).values_list("registration_number", flat=True)
    )
    installed_orders_canonical = {normalizer.canonicalize(x) for x in installed_orders if x}

    verified_plates: List[str] = []
    verified_kits: List[InstallationKit] = []
    synced_from_itms: List[str] = []
    rejected_not_on_stock: List[str] = []
    already_installed: List[str] = []
    details: Dict[str, Dict[str, Any]] = {}
    missing_locally: List[str] = []

    for p in clean_plates:
        kit = local_map.get(p) or local_map.get(f"IK-{p}")
        if p in installed_orders_canonical or (kit and (kit.status or "").strip().lower() in ("installed", "archived")):
            already_installed.append(p)
            details[p] = {
                "valid": False,
                "status": "ALREADY_INSTALLED",
                "kit": kit,
                "reason": f"Plate {p} has already been installed / archived on an order.",
            }
        elif kit:
            verified_plates.append(p)
            verified_kits.append(kit)
            details[p] = {
                "valid": True,
                "status": "VERIFIED_LOCAL",
                "kit": kit,
                "reason": f"Verified in local warehouse stock ({kit.warehouse or wh_name}).",
            }
        else:
            missing_locally.append(p)

    # Step 2: Live ITMS Check for Plates Missing Locally
    if missing_locally and check_itms_live:
        try:
            client = get_web_client()
            for p in missing_locally:
                found_match = False
                display_p = format_display_plate(p)
                queries_to_try = [display_p] if display_p != p else [p]
                if p not in queries_to_try:
                    queries_to_try.append(p)

                for q_term in queries_to_try:
                    try:
                        res = client.fetch_installation_kits(page=1, search_params=q_term, allow_local_fallback=False)
                        if res.get("success"):
                            kits = res.get("kits", [])
                            for k in kits:
                                k_reg = normalizer.canonicalize(k.get("registration_number", ""))
                                k_code = (k.get("kit_code") or "").strip().upper()
                                if k_reg == p or k_code == f"IK-{p}":
                                    found_match = True
                                    # Sync into local DB
                                    client.sync_kits_to_local_db([k])
                                    new_kit = InstallationKit.objects.filter(
                                        Q(registration_number=p) | Q(kit_code=f"IK-{p}")
                                    ).first()

                                    k_st = (k.get("status") or "").strip().lower()
                                    if k_st in ("installed", "archived"):
                                        already_installed.append(p)
                                        details[p] = {
                                            "valid": False,
                                            "status": "ALREADY_INSTALLED",
                                            "kit": new_kit,
                                            "reason": f"Kit {p} in ITMS is already marked as '{k.get('status')}'.",
                                        }
                                    else:
                                        verified_plates.append(p)
                                        synced_from_itms.append(p)
                                        if new_kit:
                                            verified_kits.append(new_kit)
                                        details[p] = {
                                            "valid": True,
                                            "status": "VERIFIED_ITMS",
                                            "kit": new_kit,
                                            "reason": "Found on ITMS installation kits and synced to local database.",
                                        }
                                    break
                        if found_match:
                            break
                    except Exception as itms_exc:
                        logger.debug("ITMS live query failed for %s (%s): %s", p, q_term, itms_exc)

                if not found_match:
                    rejected_not_on_stock.append(p)
                    details[p] = {
                        "valid": False,
                        "status": "NOT_ON_STOCK",
                        "kit": None,
                        "reason": f"Kit {p} not found on ITMS installation kits (Not on stock).",
                    }
        except Exception as client_exc:
            logger.warning("Failed to initialize ITMS web client for stock verification: %s", client_exc)
            for p in missing_locally:
                if p not in details:
                    rejected_not_on_stock.append(p)
                    details[p] = {
                        "valid": False,
                        "status": "NOT_ON_STOCK",
                        "kit": None,
                        "reason": f"Kit {p} not found in local stock and ITMS query failed ({client_exc}).",
                    }
    elif missing_locally:
        for p in missing_locally:
            rejected_not_on_stock.append(p)
            details[p] = {
                "valid": False,
                "status": "NOT_ON_STOCK",
                "kit": None,
                "reason": f"Kit {p} not found in local stock and ITMS live check was disabled.",
            }

    success = len(rejected_not_on_stock) == 0 and len(already_installed) == 0
    return {
        "success": success,
        "total_scanned": len(clean_plates),
        "verified_plates": verified_plates,
        "verified_kits": verified_kits,
        "synced_from_itms": synced_from_itms,
        "rejected_not_on_stock": rejected_not_on_stock,
        "already_installed": already_installed,
        "details": details,
        "warehouse": wh_name,
        "message": (
            f"Verified {len(verified_plates)}/{len(clean_plates)} kits. "
            f"({len(synced_from_itms)} synced from ITMS, {len(rejected_not_on_stock)} not on stock, "
            f"{len(already_installed)} already installed)."
        ),
    }


def verify_scanned_kit_stock(
    plate: str,
    check_itms_live: bool = True,
    facility_name: Optional[str] = None,
) -> Tuple[bool, Optional[InstallationKit], str]:
    """
    Two-tier verification for a single scanned kit before dispatch/issuance:
    1. Tier 1: Search local synced InstallationKit table.
    2. Tier 2: If missing locally and check_itms_live=True, query ITMS /installation-kits/index live.
       If found on ITMS: auto-sync kit into local DB and approve.
       If not found on ITMS: reject (kit is not on stock, cannot be taken out).
    Returns: (is_valid, kit_instance, reason)
    """
    res = verify_scanned_kits_stock([plate], check_itms_live=check_itms_live, facility_name=facility_name)
    c = normalizer.canonicalize(plate)
    dt = res.get("details", {}).get(c, {})
    return dt.get("valid", False), dt.get("kit"), dt.get("reason", "Verification failed")


def validate_morning_dispatch_readiness(
    scanned_plates: Iterable[str],
    auto_enroll_missing: bool = False,
    check_itms_live: bool = True,
    facility_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validates a list of plates being scanned for morning dispatch against stock kits.
    Uses two-tier verification (local synced DB + ITMS live fallback).
    If auto_enroll_missing is True, any remaining unverified plates are auto-enrolled.
    """
    active_bond = bond_service.get_active_bond()
    wh_name = facility_name or active_bond.get("name", "AGM (INSTALLATION) SOLUTIONS UGANDA LIMITED (SPIRO)")

    verify_res = verify_scanned_kits_stock(
        scanned_plates,
        check_itms_live=check_itms_live,
        facility_name=wh_name,
    )

    clean_plates = verify_res["verified_plates"] + verify_res["rejected_not_on_stock"] + verify_res["already_installed"]
    already_in_stock_new = len(verify_res["verified_plates"])
    already_allocated_or_installed = len(verify_res["already_installed"])
    missing_plates = list(verify_res["rejected_not_on_stock"])

    auto_enrolled_count = 0
    today_str = timezone.localdate().strftime("%d.%m.%Y")

    if auto_enroll_missing and missing_plates:
        kits_to_create = [
            InstallationKit(
                kit_code=f"IK-{p}",
                registration_number=p,
                status="New",
                warehouse=wh_name,
                created_date=today_str,
            )
            for p in missing_plates
        ]
        InstallationKit.objects.bulk_create(kits_to_create, ignore_conflicts=True)
        auto_enrolled_count = len(kits_to_create)
        missing_plates = []

    return {
        "success": verify_res["success"] or (auto_enroll_missing and auto_enrolled_count > 0),
        "total_scanned": verify_res["total_scanned"],
        "already_in_stock_new": already_in_stock_new,
        "already_allocated_or_installed": already_allocated_or_installed,
        "missing_plates_count": len(missing_plates),
        "missing_count": len(missing_plates),
        "auto_enrolled_as_new": auto_enrolled_count,
        "kits_auto_enrolled": auto_enrolled_count,
        "synced_from_itms_count": len(verify_res["synced_from_itms"]),
        "synced_from_itms": verify_res["synced_from_itms"],
        "rejected_not_on_stock": missing_plates,
        "already_installed": verify_res["already_installed"],
        "verified_plates": verify_res["verified_plates"],
        "warehouse": wh_name,
        "verification_details": verify_res["details"],
    }


class MorningKitSyncDaemon:
    """
    Thread-safe background daemon worker for recurring morning installation kit
    synchronization and warehouse stock readiness.
    """

    _instance: Optional["MorningKitSyncDaemon"] = None
    _lock = threading.Lock()

    def __init__(self, interval_seconds: int = 900):  # Default 15 minutes
        self.interval = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
        self._last_run_time: Optional[datetime] = None
        self._last_result: Optional[Dict[str, Any]] = None
        self._run_count = 0

    @classmethod
    def get_instance(cls, interval_seconds: int = 900) -> "MorningKitSyncDaemon":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls(interval_seconds=interval_seconds)
            return cls._instance

    def start(self) -> bool:
        with self._lock:
            if self._is_running and self._thread and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name="MorningKitSyncDaemon",
            )
            self._is_running = True
            self._thread.start()
            logger.info("MorningKitSyncDaemon started in background (interval: %ds).", self.interval)
            return True

    def stop(self) -> None:
        with self._lock:
            if not self._is_running:
                return
            self._stop_event.set()
            self._is_running = False
            logger.info("MorningKitSyncDaemon stopping...")

    def trigger_sync(self, sync_itms: bool = True) -> Dict[str, Any]:
        """Runs an immediate synchronous pass and caches result."""
        res = sync_and_provision_warehouse_kits(sync_itms=sync_itms)
        self._last_run_time = timezone.now()
        self._last_result = res
        self._run_count += 1
        return res

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self._is_running,
            "interval_seconds": self.interval,
            "run_count": self._run_count,
            "last_run_time": self._last_run_time.isoformat() if self._last_run_time else None,
            "last_result": self._last_result,
        }

    def _run_loop(self) -> None:
        from django.db import connection, close_old_connections
        # Initial pass on startup
        try:
            close_old_connections()
            self.trigger_sync(sync_itms=True)
        except Exception as exc:
            logger.warning("Initial morning kit sync error: %s", exc)
        finally:
            try:
                connection.close()
            except Exception:
                pass

        while not self._stop_event.is_set():
            # Wait for interval or stop event
            if self._stop_event.wait(timeout=self.interval):
                break
            try:
                close_old_connections()
                self.trigger_sync(sync_itms=True)
            except Exception as exc:
                logger.warning("Periodic morning kit sync error: %s", exc)
            finally:
                try:
                    connection.close()
                except Exception:
                    pass
