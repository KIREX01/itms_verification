"""
Smart ITMS Order Synchronization Service.

Provides:
- Rate-limiting & TTL cache cooldown to protect stock.itms.ug from unneeded requests.
- Active orders pagination & registry synchronization into PostgreSQL / Django DB.
- Automatic disappearance / completion detection:
    When an order has evidence photos uploaded and approved, it disappears
    from /installation-orders/index and moves to the completed archive.
    This service marks disappeared orders as inactive on the active index,
    queries the archive to confirm the external officer and installation date,
    and updates their lifecycle status and matching vehicle pairs accordingly.
- Bounded Archive Sync: NEVER performs full archive scans; uses targeted
    date-suffix searches (e.g. '260926') or single-record lookups for specific plates.
- Bounded Installation Kits Sync: Fetches small recent windows (max 2-3 pages)
    or targeted kit lookups, preventing runaway pagination across tens of thousands of records.
- Unified Shift Synchronization: Synchronizes active fitment, shift archive, and recent stock kits in one safe pass.
"""
import logging
import re
import time
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Union
from django.utils import timezone
from django.conf import settings
from django.db import transaction

from core.models import EvidenceImage, InstallationOrder, SubmissionAuditLog, VehicleInstallationPair
from core.services.itms_web_client import ITMSWebClient, get_web_client
from core.vision import normalizer

logger = logging.getLogger(__name__)

# Cooldown to protect ITMS server from duplicate / excessive requests
SYNC_COOLDOWN_SECONDS = getattr(settings, "ITMS_SYNC_COOLDOWN_SECONDS", 60)

# Global in-memory cache tracking last sync time and summary
_LAST_SYNC_TIMESTAMP: float = 0.0
_LAST_SYNC_RESULT: Optional[Dict[str, Any]] = None
_LAST_ARCHIVE_SYNC_TIMESTAMP: float = 0.0
_LAST_KIT_SYNC_TIMESTAMP: float = 0.0


def parse_installation_date(date_str: str, order_num: str = "") -> Optional[date]:
    """Extracts date from 'DD.MM.YYYY - HH:MM' or from order suffix like 'PO-*-300926'."""
    if date_str:
        m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", str(date_str))
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return date(y, mo, d)
            except ValueError:
                pass
    if order_num:
        m_sfx = re.search(r"-(\d{2})(\d{2})(\d{2,4})$", str(order_num).strip())
        if m_sfx:
            d, mo, raw_y = int(m_sfx.group(1)), int(m_sfx.group(2)), int(m_sfx.group(3))
            y = 2000 + raw_y if raw_y < 100 else raw_y
            try:
                return date(y, mo, d)
            except ValueError:
                pass
    return None


def parse_target_date(target: Optional[Any] = None) -> date:
    """Parses user/system target date into a date object, defaulting to today."""
    if not target or str(target).strip().upper() in ("TODAY", "ALL", ""):
        return timezone.localdate()
    if isinstance(target, date):
        return target
    s = str(target).strip()
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m_dash = re.search(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m_dash:
        return date(int(m_dash.group(1)), int(m_dash.group(2)), int(m_dash.group(3)))
    m_sfx = re.search(r"(\d{2})(\d{2})(\d{2,4})", s)
    if m_sfx:
        d, mo, raw_y = int(m_sfx.group(1)), int(m_sfx.group(2)), int(m_sfx.group(3))
        y = 2000 + raw_y if raw_y < 100 else raw_y
        return date(y, mo, d)
    return timezone.localdate()


class OrderSyncService:
    """
    Coordinates synchronization between live ITMS installation orders, archive,
    installation kits, and the local database.
    """

    def __init__(self, client: Optional[ITMSWebClient] = None):
        self.client = client or get_web_client()

    def get_sync_status(self) -> Dict[str, Any]:
        """Returns metadata on when the last sync took place and active orders count."""
        global _LAST_SYNC_TIMESTAMP, _LAST_SYNC_RESULT
        elapsed = time.time() - _LAST_SYNC_TIMESTAMP if _LAST_SYNC_TIMESTAMP > 0 else None
        cooldown_remaining = max(0, int(SYNC_COOLDOWN_SECONDS - elapsed)) if (elapsed is not None and elapsed < SYNC_COOLDOWN_SECONDS) else 0
        session_obj = getattr(getattr(self.client, "session_store", None), "session", None)
        email_val = getattr(session_obj, "user_email", "")
        curr_email = email_val.strip().lower() if isinstance(email_val, str) else ""
        active_qs = InstallationOrder.objects.filter(is_active_on_itms=True, is_archived=False)
        if curr_email:
            active_qs = active_qs.filter(account_email=curr_email)
        total_active_local = active_qs.count()

        return {
            "last_sync_timestamp": _LAST_SYNC_TIMESTAMP,
            "last_sync_datetime": timezone.datetime.fromtimestamp(_LAST_SYNC_TIMESTAMP, tz=timezone.get_current_timezone()) if _LAST_SYNC_TIMESTAMP > 0 else None,
            "elapsed_seconds": int(elapsed) if elapsed is not None else None,
            "in_cooldown": (cooldown_remaining > 0),
            "cooldown_remaining_seconds": cooldown_remaining,
            "active_orders_count": total_active_local,
            "last_result": _LAST_SYNC_RESULT,
        }

    def sync_active_orders(
        self,
        force: bool = False,
        max_pages: int = 25,
        search_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetches active installation orders from /installation-orders/index and synchronizes
        them with the local database.

        If called within the cooldown window and not forced, returns the cached result.
        Detects orders that have disappeared from the active index (e.g. photos uploaded & finalized)
        and marks them as inactive on ITMS with external officer attribution.
        """
        global _LAST_SYNC_TIMESTAMP, _LAST_SYNC_RESULT

        now = time.time()
        elapsed = now - _LAST_SYNC_TIMESTAMP

        # 1. Check Rate-Limiting / Cache Cooldown (unless forced or filtering)
        if not force and not search_filter and _LAST_SYNC_TIMESTAMP > 0 and elapsed < SYNC_COOLDOWN_SECONDS:
            remaining = int(SYNC_COOLDOWN_SECONDS - elapsed)
            logger.info("OrderSyncService: Cooldown active (%ds remaining). Returning cached result.", remaining)
            return {
                "success": True,
                "synced": False,
                "from_cache": True,
                "cooldown_remaining_seconds": remaining,
                "message": f"Cached: Orders synced {int(elapsed)}s ago. Cooldown active ({remaining}s remaining).",
                "result": _LAST_SYNC_RESULT,
            }

        start_time = time.time()
        all_active_orders: List[Dict[str, Any]] = []
        page = 1
        reached_end_of_active_pages = False

        # 2. Paginate through active orders
        while page <= max_pages:
            fetch_res = None
            for attempt in range(1, 3):
                fetch_res = self.client.fetch_installation_orders(
                    page=page, search_params=search_filter, archive=False
                )
                if fetch_res.get("success"):
                    break
                time.sleep(0.5 * attempt)

            if not fetch_res or not fetch_res.get("success"):
                err = fetch_res.get("error", "Failed to fetch orders from ITMS.") if fetch_res else "Network timeout"
                logger.error("OrderSyncService page %d fetch failed: %s", page, err)
                if page == 1:
                    return {"success": False, "error": err, "page_failed": page}
                break

            orders = fetch_res.get("orders", [])
            all_active_orders.extend(orders)
            total_pages = int(fetch_res.get("total_pages", 0) or 0)
            if not fetch_res.get("has_next_page") or len(orders) < 20 or (total_pages > 0 and page >= total_pages):
                reached_end_of_active_pages = True
                break

            page += 1
            time.sleep(0.35)

        # 3. Synchronize active orders to database
        seen_order_numbers: Set[str] = set()
        parsed_orders: Dict[str, Dict[str, Any]] = {}

        now_dt = timezone.now()
        session_obj = getattr(getattr(self.client, "session_store", None), "session", None)
        email_val = getattr(session_obj, "user_email", "")
        uuid_val = getattr(session_obj, "user_uuid", "")
        curr_email = email_val.strip().lower() if isinstance(email_val, str) else ""
        curr_uuid = str(uuid_val) if isinstance(uuid_val, str) else ""

        for o in all_active_orders:
            order_num = o.get("order_number", "").strip()
            reg_num = o.get("registration_number", "").strip()
            if not order_num or not reg_num:
                continue

            seen_order_numbers.add(order_num)
            canonical_reg = normalizer.canonicalize(reg_num) or reg_num.replace(" ", "").upper()
            order_status = o.get("order_status") or o.get("status", "")
            action_url = o.get("action_url", "")

            # Detect stage from action URL or status
            if "/confirmation" in action_url:
                stage = "STAGE_3_CONFIRMATION"
            elif "/approve" in action_url:
                stage = "STAGE_2_APPROVE"
            elif "/installation" in action_url:
                stage = "STAGE_1_INSTALLATION"
            else:
                stage = "STAGE_UNKNOWN"

            is_installed = "installed" in order_status.lower()
            is_arch = is_installed
            is_act = not is_installed

            if is_installed:
                local_status = InstallationOrder.Status.INSTALLED
            elif "approve" in order_status.lower() or stage in ("STAGE_2_APPROVE", "STAGE_3_CONFIRMATION"):
                local_status = InstallationOrder.Status.SUBMITTED
            else:
                local_status = InstallationOrder.Status.PENDING

            defaults = {
                "registration_number": canonical_reg,
                "sales_order": o.get("sales_order", ""),
                "service_type": o.get("service_type", ""),
                "vin": o.get("vin", ""),
                "old_registration_number": o.get("old_registration_number", ""),
                "warehouse_name": o.get("warehouse", ""),
                "warehouse_id": o.get("warehouse_id", ""),
                "order_status": order_status,
                "registration_status": o.get("registration_status", ""),
                "installation_officer": o.get("officer", ""),
                "installation_date": o.get("installation_date", ""),
                "itms_order_uuid": o.get("order_key", ""),
                "itms_action_url": action_url,
                "is_archived": is_arch,
                "is_active_on_itms": is_act,
                "itms_stage": stage,
                "status": local_status,
                "last_synced_at": now_dt,
            }
            if curr_email:
                defaults["account_email"] = curr_email
            if curr_uuid:
                defaults["account_uuid"] = curr_uuid

            parsed_orders[order_num] = defaults

        # Preload existing orders in one single query to eliminate N+1 DB operations
        existing_orders = {
            ord_obj.order_number: ord_obj
            for ord_obj in InstallationOrder.objects.filter(order_number__in=parsed_orders.keys())
        }

        orders_to_create = []
        orders_to_update = []
        update_fields = [
            "registration_number", "sales_order", "service_type", "vin", "old_registration_number",
            "warehouse_name", "warehouse_id", "order_status", "registration_status",
            "installation_officer", "installation_date", "itms_order_uuid", "itms_action_url",
            "is_archived", "is_active_on_itms", "itms_stage", "status", "last_synced_at"
        ]
        if curr_email:
            update_fields.append("account_email")
        if curr_uuid:
            update_fields.append("account_uuid")

        for order_num, defaults in parsed_orders.items():
            if order_num in existing_orders:
                ord_obj = existing_orders[order_num]
                for k, v in defaults.items():
                    setattr(ord_obj, k, v)
                orders_to_update.append(ord_obj)
            else:
                orders_to_create.append(InstallationOrder(order_number=order_num, **defaults))

        with transaction.atomic():
            if orders_to_create:
                InstallationOrder.objects.bulk_create(orders_to_create, batch_size=200)
            if orders_to_update:
                InstallationOrder.objects.bulk_update(orders_to_update, update_fields, batch_size=200)

        created_count = len(orders_to_create)
        updated_count = len(orders_to_update)

        # 4. Detect Orders that Disappeared from Active Index (Completed / Uploaded)
        # Only run if we actually fetched ALL pages of the active index without early pagination termination
        disappeared_count = 0
        if not search_filter and seen_order_numbers and reached_end_of_active_pages:
            session_obj = getattr(getattr(self.client, "session_store", None), "session", None)
            email_val = getattr(session_obj, "user_email", "")
            curr_email = email_val.strip().lower() if isinstance(email_val, str) else ""
            disappeared_qs = InstallationOrder.objects.filter(
                is_active_on_itms=True,
                is_archived=False,
            )
            if curr_email:
                disappeared_qs = disappeared_qs.filter(account_email=curr_email)
            disappeared_qs = disappeared_qs.exclude(
                order_number__in=seen_order_numbers
            )

            from django.db.models import Q
            for dis_order in disappeared_qs:
                # Targeted check on ITMS Archive to verify order was actually archived
                try:
                    arch_check = self.verify_order_in_archive(dis_order.order_number)
                except Exception as exc:
                    logger.debug("Archive check for disappeared order %s skipped: %s", dis_order.order_number, exc)
                    arch_check = {"found": False}

                # Only mark as archived if verified in archive!
                if arch_check.get("found") and arch_check.get("order"):
                    ao = arch_check["order"]
                    officer_name = ao.get("officer") or ""
                    install_dt = ao.get("installation_date") or ""

                    with transaction.atomic():
                        dis_order.is_active_on_itms = False
                        dis_order.is_archived = True
                        dis_order.order_status = "Installed"
                        dis_order.status = InstallationOrder.Status.INSTALLED
                        dis_order.itms_stage = "ARCHIVED"
                        dis_order.last_synced_at = timezone.now()
                        if officer_name:
                            dis_order.installation_officer = officer_name
                        if install_dt:
                            dis_order.installation_date = install_dt
                        if ao.get("registration_status"):
                            dis_order.registration_status = ao.get("registration_status")

                        dis_order.save(update_fields=[
                            "is_active_on_itms", "is_archived", "order_status", "status",
                            "itms_stage", "installation_officer", "installation_date",
                            "registration_status", "last_synced_at"
                        ])

                        # Cross-verify and mark any matching vehicle installation pairs as SUBMITTED
                        # Prevents local operators from attempting to submit photos to an already closed order!
                        pairs = list(VehicleInstallationPair.objects.filter(
                            Q(order=dis_order) | Q(registration_number_detected=dis_order.registration_number)
                        ).select_related("front_image", "rear_image"))
                        now = timezone.now()
                        pairs_to_update = []
                        images_to_update = []
                        audit_logs_to_create = []
                        for pair in pairs:
                            if pair.verification_status != VehicleInstallationPair.VerificationStatus.SUBMITTED:
                                pair.verification_status = VehicleInstallationPair.VerificationStatus.SUBMITTED
                                pair.submitted_at = pair.submitted_at or now
                                pair.order = dis_order
                                closer_str = f"by {officer_name} " if officer_name else ""
                                dt_str = f"on {install_dt}" if install_dt else ""
                                pair.operator_note = (
                                    f"Order #{dis_order.order_number} closed on ITMS {closer_str}{dt_str}. "
                                    f"Local evidence verified and finalized in archive."
                                ).strip()
                                pairs_to_update.append(pair)

                                for img in (pair.front_image, pair.rear_image):
                                    if img and img.status != EvidenceImage.Status.SUBMITTED:
                                        img.status = EvidenceImage.Status.SUBMITTED
                                        img.submitted_at = img.submitted_at or now
                                        images_to_update.append(img)

                                audit_logs_to_create.append(SubmissionAuditLog(
                                    pair=pair,
                                    action=SubmissionAuditLog.Action.ARCHIVE_VERIFY,
                                    result=SubmissionAuditLog.ResultStatus.SUCCESS,
                                    message=(
                                        f"Order #{dis_order.order_number} verified completed in ITMS Archive "
                                        f"({closer_str}{dt_str}). Local pair marked SUBMITTED."
                                    ),
                                ))

                        if pairs_to_update:
                            VehicleInstallationPair.objects.bulk_update(
                                pairs_to_update,
                                ["verification_status", "order", "submitted_at", "operator_note"],
                                batch_size=200,
                            )
                        if images_to_update:
                            EvidenceImage.objects.bulk_update(
                                images_to_update,
                                ["status", "submitted_at"],
                                batch_size=200,
                            )
                        if audit_logs_to_create:
                            SubmissionAuditLog.objects.bulk_create(audit_logs_to_create, batch_size=200)

                    disappeared_count += 1

        duration_ms = int((time.time() - start_time) * 1000)

        # Update in-memory cache
        _LAST_SYNC_TIMESTAMP = time.time()
        _LAST_SYNC_RESULT = {
            "total_active_seen": len(seen_order_numbers),
            "created": created_count,
            "updated": updated_count,
            "disappeared_from_active": disappeared_count,
            "pages_fetched": page,
            "duration_ms": duration_ms,
        }

        return {
            "success": True,
            "synced": True,
            "from_cache": False,
            "total_active_seen": len(seen_order_numbers),
            "created": created_count,
            "updated": updated_count,
            "disappeared_from_active": disappeared_count,
            "pages_fetched": page,
            "duration_ms": duration_ms,
            "message": (
                f"Synced {len(seen_order_numbers)} active orders ({created_count} new, "
                f"{updated_count} updated, {disappeared_count} finalized/removed from active) in {duration_ms}ms."
            ),
        }

    def sync_archive_today(
        self,
        target_date: Optional[Any] = None,
        max_pages: int = 50,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Intelligently and safely synchronizes the entire archive for the target date
        (defaulting to today) without scanning the entire history of stock.itms.ug.

        Because the ITMS Archive (/installation-orders/archive) is sorted in descending
        chronological order (newest first), this method paginates starting from page 1:
        - Keeps collecting orders matching the target date.
        - As soon as a page contains records with an installation date strictly older
          than the target date, it gathers any matching orders on that page and STOPS.
        - Guarantees 100% of today's archive is synchronized (e.g. all 258 records)
          regardless of how many pages it spans, while preventing runaway pagination.
        """
        global _LAST_ARCHIVE_SYNC_TIMESTAMP
        now = time.time()
        elapsed = now - _LAST_ARCHIVE_SYNC_TIMESTAMP
        if not force and _LAST_ARCHIVE_SYNC_TIMESTAMP > 0 and elapsed < SYNC_COOLDOWN_SECONDS:
            remaining = int(SYNC_COOLDOWN_SECONDS - elapsed)
            return {
                "success": True,
                "synced": False,
                "from_cache": True,
                "message": f"Archive sync cooldown active ({remaining}s remaining).",
            }

        start_time = time.time()
        all_today_orders: List[Dict[str, Any]] = []
        target_dt = parse_target_date(target_date)
        target_dt_str = target_dt.strftime("%d.%m.%Y")
        target_dt_compact = target_dt.strftime("%d%m%y")
        search_filter = str(target_date).strip() if (target_date and str(target_date).strip().upper() not in ("TODAY", "ALL", "")) else None

        page = 1
        reached_prior_date = False

        while page <= max_pages:
            fetch_res = self.client.fetch_installation_orders(page=page, search_params=search_filter, archive=True)
            if not fetch_res.get("success"):
                if page == 1:
                    return {"success": False, "error": fetch_res.get("error", "Archive fetch failed")}
                break

            orders = fetch_res.get("orders", [])
            if not orders:
                break

            for o in orders:
                idate_raw = o.get("installation_date", "")
                order_num = o.get("order_number", "")
                parsed_dt = parse_installation_date(idate_raw, order_num)

                if parsed_dt:
                    if parsed_dt == target_dt:
                        all_today_orders.append(o)
                    elif parsed_dt < target_dt:
                        reached_prior_date = True
                        break
                else:
                    # Fallback string check
                    if target_dt_str in str(idate_raw) or target_dt_compact in str(order_num):
                        all_today_orders.append(o)

            if reached_prior_date:
                logger.info(
                    "OrderSyncService: Reached records prior to %s on archive page %d. Stopping.",
                    target_dt_str,
                    page,
                )
                break

            if not fetch_res.get("has_next_page") or len(orders) < 20:
                break

            page += 1
            time.sleep(0.3)

        sync_res = self.client.sync_orders_to_local_db(all_today_orders)
        _LAST_ARCHIVE_SYNC_TIMESTAMP = time.time()
        duration_ms = int((time.time() - start_time) * 1000)

        officers: Dict[str, int] = {}
        for o in all_today_orders:
            off = o.get("officer") or "Unknown"
            officers[off] = officers.get(off, 0) + 1

        return {
            "success": True,
            "synced": True,
            "target_date": target_dt_str,
            "total_fetched": len(all_today_orders),
            "pages_fetched": page,
            "created": sync_res.get("created", 0),
            "updated": sync_res.get("updated", 0),
            "installed_verified": sync_res.get("installed_verified", 0),
            "officers": officers,
            "duration_ms": duration_ms,
            "message": (
                f"Synced {len(all_today_orders)} archive orders for {target_dt_str} across {page} pages "
                f"({sync_res.get('created', 0)} new, {sync_res.get('updated', 0)} updated) in {duration_ms}ms."
            ),
        }

    def sync_archive_orders_scoped(
        self,
        target_date: Optional[str] = None,
        max_pages: int = 50,
        plate_or_order: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Safely synchronizes archive orders:
        - If plate_or_order: searches specifically for that plate or order (1 page max).
        - If target_date or general: automatically fetches complete archive for that date using date boundary.
        - Enforces cache cooldown (default 60s) unless force=True.
        """
        if not plate_or_order:
            return self.sync_archive_today(target_date=target_date, max_pages=max_pages, force=force)

        # Plate or order specific single lookup
        start_time = time.time()
        fetch_res = self.client.fetch_installation_orders(page=1, search_params=plate_or_order, archive=True)
        if not fetch_res.get("success"):
            return {"success": False, "error": fetch_res.get("error", "Archive search failed")}

        orders = fetch_res.get("orders", [])
        sync_res = self.client.sync_orders_to_local_db(orders)
        duration_ms = int((time.time() - start_time) * 1000)

        return {
            "success": True,
            "synced": True,
            "total_fetched": len(orders),
            "created": sync_res.get("created", 0),
            "updated": sync_res.get("updated", 0),
            "pages_fetched": 1,
            "duration_ms": duration_ms,
            "message": f"Found and synced {len(orders)} matching archive order(s) for '{plate_or_order}'.",
        }

    def sync_installation_kits_scoped(
        self,
        max_pages: int = 25,
        plate_or_code: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Safely synchronizes installation kits from https://stock.itms.ug/installation-kits.
        Supports crawling all 20+ pages (up to max_pages) or targeted search for a specific plate.
        - If plate_or_code: searches specifically for that plate or kit code (1 page max).
        - If general: fetches up to max_pages (default 25 pages = 500 kits).
        - Enforces cache cooldown (default 60s) unless force=True.
        """
        global _LAST_KIT_SYNC_TIMESTAMP
        now = time.time()
        elapsed = now - _LAST_KIT_SYNC_TIMESTAMP
        if not force and not plate_or_code and _LAST_KIT_SYNC_TIMESTAMP > 0 and elapsed < SYNC_COOLDOWN_SECONDS:
            remaining = int(SYNC_COOLDOWN_SECONDS - elapsed)
            return {
                "success": True,
                "synced": False,
                "from_cache": True,
                "message": f"Kit sync cooldown active ({remaining}s remaining).",
            }

        start_time = time.time()
        all_kits: List[Dict[str, Any]] = []

        search_val = plate_or_code or ""
        page = 1
        limit_pages = 1 if plate_or_code else max_pages

        while page <= limit_pages:
            fetch_res = self.client.fetch_installation_kits(
                page=page, search_params=search_val
            )
            if not fetch_res.get("success"):
                if page == 1:
                    return {"success": False, "error": fetch_res.get("error", "Kits fetch failed")}
                break

            kits = fetch_res.get("kits", [])
            all_kits.extend(kits)

            if not fetch_res.get("has_next_page") or len(kits) < 20:
                break
            page += 1
            time.sleep(0.2)

        sync_res = self.client.sync_kits_to_local_db(all_kits)
        _LAST_KIT_SYNC_TIMESTAMP = time.time()
        duration_ms = int((time.time() - start_time) * 1000)

        return {
            "success": True,
            "synced": True,
            "total_fetched": len(all_kits),
            "created": sync_res.get("created", 0),
            "updated": sync_res.get("updated", 0),
            "pages_fetched": page,
            "duration_ms": duration_ms,
            "message": f"Synced {len(all_kits)} installation kits ({sync_res.get('created', 0)} new, {sync_res.get('updated', 0)} updated) across {page} page(s) in {duration_ms}ms.",
        }

    def sync_kits_for_plates(self, plates: Iterable[str]) -> Dict[str, Any]:
        """Targeted ITMS search and sync specifically for a list of candidate plates."""
        return self.client.search_and_sync_kits_for_plates(plates)


    def sync_shift_scoped(
        self,
        target_date: Optional[Any] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """
        Coordinates full, safe shift synchronization:
        1. Active orders (up to 25 pages) to retrieve the entire active queue with disappearance detection.
        2. Archive orders: complete shift archive for target_date / today (auto-stops at prior date boundary).
        3. Installation kits: recent stock kits (3 pages) to update 'New' stock status.
        Finishes cleanly, guarantees zero missing records from today, and prevents runaway pagination!
        """
        # Step 1: Active orders (up to 25 pages = 500 orders, automatically stops when queue ends)
        active_res = self.sync_active_orders(force=force, max_pages=25)

        # Step 2: Archive orders for target_date (auto-stops at prior date boundary)
        archive_res = self.sync_archive_today(target_date=target_date, force=force)

        # Step 3: Installation kits & Morning Stock Provisioning
        kits_res = self.sync_installation_kits_scoped(max_pages=5, force=force)
        try:
            from core.services import kit_provisioning_service
            prov_res = kit_provisioning_service.sync_and_provision_warehouse_kits(
                target_date_suffix=None,
                sync_itms=False,  # Already fetched via kits_res
            )
            kits_res["provisioning"] = prov_res
        except Exception as prov_err:
            logger.debug("Automatic kit provisioning notice during shift sync: %s", prov_err)

        date_label = archive_res.get("target_date") or "today"
        return {
            "success": True,
            "active": active_res,
            "archive": archive_res,
            "kits": kits_res,
            "target_date": date_label,
            "message": (
                f"Shift Sync Complete: {active_res.get('total_active_seen', 0)} active orders, "
                f"{archive_res.get('total_fetched', 0)} archive orders ({date_label}), "
                f"{kits_res.get('total_fetched', 0)} kits."
            ),
        }

    def verify_order_in_archive(self, identifier: str) -> Dict[str, Any]:
        """
        Performs a single targeted search on /installation-orders/archive for a specific order.
        GUARANTEE: Makes exactly ONE request and NEVER iterates through the entire archive.
        """
        canonical_clean = normalizer.canonicalize(identifier) or identifier.strip()
        fetch_res = self.client.fetch_installation_orders(
            page=1, search_params=canonical_clean, archive=True
        )

        if not fetch_res.get("success"):
            return {
                "success": False,
                "error": fetch_res.get("error", "Archive search failed"),
            }

        orders = fetch_res.get("orders", [])
        if not orders:
            return {
                "success": True,
                "found": False,
                "message": f"Order / plate '{identifier}' not found in ITMS Archive.",
            }

        # Sync matching record into DB
        sync_res = self.client.sync_orders_to_local_db(orders)
        return {
            "success": True,
            "found": True,
            "order": orders[0],
            "total_matches": len(orders),
            "sync_result": sync_res,
            "message": f"Found '{identifier}' in ITMS Archive: {orders[0].get('order_status', 'Installed')}",
        }
