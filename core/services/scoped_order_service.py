"""
Scoped Order & Kit Service Layer.
Enforces the Zero-Trust Request Security Envelope boundary across all database queries.
All reads and writes are strictly scoped to the operator's verified facility (e.g. AGM, SPIRO),
preventing loose queries (InstallationOrder.objects.all()) and managing multi-operator cache deduplication.
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional
from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import QuerySet
from django.utils import timezone

from core.models import (
    FacilitySyncMetadata,
    IngestionBatch,
    InstallationKit,
    InstallationOrder,
    OperatorProfile,
    VehicleInstallationPair,
)
from core.services.security_envelope import OperatorRequestEnvelope

logger = logging.getLogger(__name__)
User = get_user_model()


def get_or_create_operator_profile(user: Any, default_facility: str = "AGM") -> OperatorProfile:
    """Retrieves or provisions an OperatorProfile for facility tracking."""
    profile, _ = OperatorProfile.objects.get_or_create(
        user=user,
        defaults={"active_facility": default_facility.strip().upper(), "allowed_facilities": [default_facility.strip().upper()]},
    )
    return profile


def switch_operator_facility(user: Any, new_facility: str) -> OperatorProfile:
    """
    Switches an operator's active facility context (e.g. from AGM to SPIRO).
    Updates OperatorProfile and OperatorITMSSession active_bond_code for immediate envelope re-derivation.
    """
    clean_code = str(new_facility).strip().upper()
    profile = get_or_create_operator_profile(user, default_facility=clean_code)
    profile.active_facility = clean_code
    if clean_code not in profile.allowed_facilities:
        profile.allowed_facilities.append(clean_code)
    profile.save()

    try:
        from core.models import OperatorITMSSession
        itms_sess = OperatorITMSSession.objects.filter(user=user).first()
        if itms_sess:
            itms_sess.active_bond_code = clean_code
            itms_sess.save(update_fields=["active_bond_code"])
    except Exception as exc:
        logger.debug("Could not update OperatorITMSSession active bond: %s", exc)

    return profile


def get_scoped_orders(
    envelope: OperatorRequestEnvelope,
    search: str = "",
    status: Optional[str] = None,
    is_archived: bool = False,
) -> QuerySet[InstallationOrder]:
    """
    Returns InstallationOrder queryset strictly scoped to envelope.facility_code.
    Eliminates loose global queries across facilities.
    """
    if not envelope.is_valid:
        return InstallationOrder.objects.none()

    qs = InstallationOrder.objects.filter(
        facility_code=envelope.facility_code,
        is_archived=is_archived,
    )
    if status:
        qs = qs.filter(status=status)
    if search:
        s = search.strip()
        s_clean = s.replace(" ", "").upper()
        # If search is purely numeric and short (e.g. "600", "692"):
        # Match only registration_number or order_number containing the digits.
        # Strictly prevent false matches against arbitrary internal substrings of 17-char VINs or plate serials.
        if s.isdigit() and len(s) < 6:
            qs = qs.filter(
                models.Q(registration_number__icontains=s)
                | models.Q(order_number__icontains=s)
            )
        else:
            cond = (
                models.Q(registration_number__icontains=s)
                | models.Q(registration_number__icontains=s_clean)
                | models.Q(order_number__icontains=s)
            )
            # Only match VIN if search query looks like a VIN / VIN prefix (contains both alpha & digits or length >= 6)
            if len(s) >= 6 or (any(c.isalpha() for c in s) and any(c.isdigit() for c in s)):
                cond |= models.Q(vin__icontains=s)
            # Only match plate_serial if query is long enough to be an actual serial number (>= 6 digits)
            if len(s) >= 6:
                cond |= models.Q(plate_serial__icontains=s)
            qs = qs.filter(cond)
    return qs


def get_scoped_kits(
    envelope: OperatorRequestEnvelope,
    search: str = "",
    status: Optional[str] = None,
) -> QuerySet[InstallationKit]:
    """
    Returns InstallationKit queryset strictly scoped to envelope.facility_code.
    """
    if not envelope.is_valid:
        return InstallationKit.objects.none()

    qs = InstallationKit.objects.filter(facility_code=envelope.facility_code)
    if status:
        qs = qs.filter(status=status)
    if search:
        s = search.strip()
        qs = qs.filter(
            models.Q(kit_code__icontains=s)
            | models.Q(registration_number__icontains=s)
            | models.Q(front_plate__icontains=s)
            | models.Q(rear_plate__icontains=s)
        )
    return qs


def get_facility_sync_status(facility_code: str) -> FacilitySyncMetadata:
    """Returns or creates the sync cache metadata for a given bond facility."""
    clean_code = str(facility_code).strip().upper() or "AGM"
    meta, _ = FacilitySyncMetadata.objects.get_or_create(
        facility_code=clean_code,
        defaults={"total_kits": 0, "total_orders": 0},
    )
    return meta


def should_sync_facility_kits(
    envelope: OperatorRequestEnvelope,
    force: bool = False,
    ttl_minutes: int = 180,
) -> bool:
    """
    Facility-Aware Deduplication Rule:
    If another operator at the same facility synced kits within the TTL window (default 3 hours),
    returns False so the local database pool is reused with ZERO network strain.
    """
    if force:
        return True
    meta = get_facility_sync_status(envelope.facility_code)
    return not meta.is_kits_fresh(ttl_minutes=ttl_minutes)


def record_facility_kits_synced(
    facility_code: str,
    total_kits: int,
    operator: Optional[Any] = None,
) -> FacilitySyncMetadata:
    """Updates facility sync timestamp and kit count following an ITMS scrape."""
    meta = get_facility_sync_status(facility_code)
    meta.last_kits_synced_at = timezone.now()
    meta.total_kits = max(0, total_kits)
    if operator and getattr(operator, "is_authenticated", False):
        meta.synced_by_operator = operator
    if not meta.warehouse:
        from core.models import Warehouse
        wh = Warehouse.objects.filter(code=meta.facility_code).first()
        if wh:
            meta.warehouse = wh
            meta.warehouse_uuid = wh.warehouse_uuid
            meta.warehouse_name = wh.name
    meta.save()
    return meta


def record_facility_orders_synced(
    facility_code: str,
    total_orders: int,
    operator: Optional[Any] = None,
) -> FacilitySyncMetadata:
    """Updates facility sync timestamp and order count following an ITMS scrape."""
    meta = get_facility_sync_status(facility_code)
    meta.last_orders_synced_at = timezone.now()
    meta.total_orders = max(0, total_orders)
    if operator and getattr(operator, "is_authenticated", False):
        meta.synced_by_operator = operator
    if not meta.warehouse:
        from core.models import Warehouse
        wh = Warehouse.objects.filter(code=meta.facility_code).first()
        if wh:
            meta.warehouse = wh
            meta.warehouse_uuid = wh.warehouse_uuid
            meta.warehouse_name = wh.name
    meta.save()
    return meta


def stamp_batch_with_envelope(
    batch: IngestionBatch,
    envelope: OperatorRequestEnvelope,
    user: Optional[Any] = None,
) -> None:
    """Stamps private operational batch with local operator attribution and facility context."""
    if user and getattr(user, "is_authenticated", False):
        batch.operator = user
    batch.facility_code = envelope.facility_code
    batch.account_email = envelope.itms_email


def stamp_pair_with_envelope(
    pair: VehicleInstallationPair,
    envelope: OperatorRequestEnvelope,
    user: Optional[Any] = None,
) -> None:
    """Stamps private operational pair with local operator attribution and facility context."""
    if user and getattr(user, "is_authenticated", False):
        pair.operator = user
    pair.facility_code = envelope.facility_code
    pair.account_email = envelope.itms_email
