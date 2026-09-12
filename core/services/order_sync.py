"""
Smart ITMS Order Synchronization Service.

Provides:
- Rate-limiting & TTL cache cooldown to protect stock.itms.ug from unneeded requests.
- Active orders pagination & registry synchronization into PostgreSQL / Django DB.
- Automatic disappearance / completion detection:
    When an order has evidence photos uploaded and approved, it disappears
    from /installation-orders/index and moves to the completed archive.
    This service marks disappeared orders as inactive on the active index,
    and updates their lifecycle status accordingly.
- Archive request guard: NEVER performs full archive scans; only uses targeted
    single-record lookups for specific plates when needed.
"""
import logging
import time
from typing import Any, Dict, List, Optional, Set
from django.utils import timezone
from django.conf import settings

from core.models import InstallationOrder, SubmissionAuditLog, VehicleInstallationPair
from core.services.itms_web_client import ITMSWebClient, get_web_client
from core.vision import normalizer

logger = logging.getLogger(__name__)

# Cooldown to protect ITMS server from duplicate / excessive requests
SYNC_COOLDOWN_SECONDS = getattr(settings, "ITMS_SYNC_COOLDOWN_SECONDS", 60)

# Global in-memory cache tracking last sync time and summary
_LAST_SYNC_TIMESTAMP: float = 0.0
_LAST_SYNC_RESULT: Optional[Dict[str, Any]] = None


class OrderSyncService:
    """
    Coordinates synchronization between live ITMS installation orders and the local database.
    """

    def __init__(self, client: Optional[ITMSWebClient] = None):
        self.client = client or get_web_client()

    def get_sync_status(self) -> Dict[str, Any]:
        """Returns metadata on when the last sync took place and active orders count."""
        global _LAST_SYNC_TIMESTAMP, _LAST_SYNC_RESULT
        elapsed = time.time() - _LAST_SYNC_TIMESTAMP if _LAST_SYNC_TIMESTAMP > 0 else None
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
        max_pages: int = 5,
        search_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetches active installation orders from /installation-orders/index and synchronizes
        them with the local database.

        If called within the cooldown window and not forced, returns the cached result.
        Detects orders that have disappeared from the active index (e.g. photos uploaded & finalized)
        and marks them as inactive on ITMS.
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

        # 2. Paginate through active orders
        while page <= max_pages:
            fetch_res = None
            for attempt in range(1, 3):
                fetch_res = self.client.fetch_installation_orders(
                    page=page, search_params=search_filter, archive=False
                )
                if fetch_res.get("success"):
                    break
                # If network or connection reset, backoff and retry this page once
                time.sleep(0.5 * attempt)

            if not fetch_res or not fetch_res.get("success"):
                err = fetch_res.get("error", "Failed to fetch orders from ITMS.") if fetch_res else "Network timeout"
                logger.error("OrderSyncService page %d fetch failed: %s", page, err)
                if page == 1:
                    return {"success": False, "error": err, "page_failed": page}
                break

            orders = fetch_res.get("orders", [])
            all_active_orders.extend(orders)

            # If no next page or fewer than 20 items, we have reached the end of active orders
            if not fetch_res.get("has_next_page") or len(orders) < 20:
                break

            page += 1
            # Gentle pacing between pages to prevent remote server connection resets (10054)
            time.sleep(0.35)

        # 3. Synchronize active orders to database
        seen_order_numbers: Set[str] = set()
        created_count = 0
        updated_count = 0

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
                "last_synced_at": timezone.now(),
            }

            session_obj = getattr(getattr(self.client, "session_store", None), "session", None)
            email_val = getattr(session_obj, "user_email", "")
            uuid_val = getattr(session_obj, "user_uuid", "")
            curr_email = email_val.strip().lower() if isinstance(email_val, str) else ""
            curr_uuid = str(uuid_val) if isinstance(uuid_val, str) else ""
            if curr_email:
                defaults["account_email"] = curr_email
            if curr_uuid:
                defaults["account_uuid"] = curr_uuid

            obj, was_created = InstallationOrder.objects.update_or_create(
                order_number=order_num,
                defaults=defaults,
            )
            if was_created:
                created_count += 1
            else:
                updated_count += 1

        # 4. Detect Orders that Disappeared from Active Index (Completed / Uploaded)
        # Only run if we did a full unfiltered scan (page 1 to end)
        disappeared_count = 0
        if not search_filter and seen_order_numbers:
            session_obj = getattr(getattr(self.client, "session_store", None), "session", None)
            email_val = getattr(session_obj, "user_email", "")
            curr_email = email_val.strip().lower() if isinstance(email_val, str) else ""
            disappeared_qs = InstallationOrder.objects.filter(
                is_active_on_itms=True,
                is_archived=False,
            )
            # CRITICAL: Only mark disappeared orders within the currently active ITMS account!
            if curr_email:
                disappeared_qs = disappeared_qs.filter(account_email=curr_email)
            disappeared_qs = disappeared_qs.exclude(
                order_number__in=seen_order_numbers
            )

            from django.db.models import Q
            for dis_order in disappeared_qs:
                dis_order.is_active_on_itms = False
                dis_order.is_archived = True
                dis_order.order_status = "Installed"
                dis_order.status = InstallationOrder.Status.INSTALLED
                dis_order.itms_stage = "ARCHIVED"
                dis_order.last_synced_at = timezone.now()
                dis_order.save(update_fields=["is_active_on_itms", "is_archived", "order_status", "status", "itms_stage", "last_synced_at"])

                # Cross-verify and mark any matching vehicle installation pairs as SUBMITTED
                pairs = VehicleInstallationPair.objects.filter(
                    Q(order=dis_order) | Q(registration_number_detected=dis_order.registration_number)
                )
                for pair in pairs:
                    if pair.verification_status != VehicleInstallationPair.VerificationStatus.SUBMITTED:
                        pair.verification_status = VehicleInstallationPair.VerificationStatus.SUBMITTED
                        pair.order = dis_order
                        pair.save(update_fields=["verification_status", "order"])

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
