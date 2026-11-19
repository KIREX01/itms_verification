"""
Quality flags: the Quality Inspector's gate on data going to ITMS.

  raise_flag(user, target_type, target_id, reason_code, note, suggested_plate)   # QI / TL
  resolve_flag(user, flag, outcome="apply"|"reject"|"resolve", note)           # TL only
  blocking_flags_for_pair(pair)  -> OPEN flags on the pair, its order, or that order's kit
  submission_block_message(pair) -> human message, or "" when the pair may be submitted

An OPEN flag blocks submission of the flagged item until the Team Leader resolves it.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from core.models import (
    InstallationKit,
    InstallationOrder,
    QualityFlag,
    SubmissionAuditLog,
    VehicleInstallationPair,
)
from governance import capabilities as C
from governance.services import authz

logger = logging.getLogger(__name__)


class QualityFlagError(ValueError):
    pass


def _canonical_plate(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    try:
        from core.vision import normalizer
        return normalizer.canonicalize(raw) or raw.replace(" ", "").upper()
    except Exception:
        return raw.replace(" ", "").upper()


def _kits_for_registration(registration: str) -> QuerySet:
    """Kits for a plate, ignoring spacing/case ("UMA 100AA" on kits vs "UMA100AA" on orders)."""
    from django.db.models import Value
    from django.db.models.functions import Replace, Upper

    plate = _canonical_plate(registration)
    if not plate:
        return InstallationKit.objects.none()
    return (InstallationKit.objects
            .annotate(_plate=Upper(Replace("registration_number", Value(" "), Value(""))))
            .filter(Q(_plate=plate) | Q(kit_code__iexact=f"IK-{plate}")))


def _resolve_target(target_type: str, target_id):
    target_type = (target_type or "").upper()
    if target_type == QualityFlag.TargetType.PAIR:
        obj = VehicleInstallationPair.objects.filter(pk=target_id).first()
        bond = getattr(obj, "facility_code", "") if obj else ""
    elif target_type == QualityFlag.TargetType.ORDER:
        obj = InstallationOrder.objects.filter(pk=target_id).first() if str(target_id).isdigit() else None
        if obj is None:
            obj = InstallationOrder.objects.filter(order_number=str(target_id)).first()
        bond = getattr(obj, "facility_code", "") if obj else ""
    elif target_type == QualityFlag.TargetType.KIT:
        obj = InstallationKit.objects.filter(pk=target_id).first() if str(target_id).isdigit() else None
        if obj is None:
            obj = InstallationKit.objects.filter(kit_code=str(target_id)).first()
        bond = getattr(obj, "facility_code", "") if obj else ""
    else:
        raise QualityFlagError("Flags can be raised on a pair, an order or an installation kit.")
    if obj is None:
        raise QualityFlagError(f"{target_type.title()} not found.")
    return target_type, obj, (bond or "").upper()


def _audit_pair(pair: Optional[VehicleInstallationPair], action: str, message: str, user, result=None) -> None:
    try:
        SubmissionAuditLog.objects.create(
            pair=pair,
            operator=user if getattr(user, "pk", None) else None,
            action=action,
            result=result or SubmissionAuditLog.ResultStatus.INFO,
            message=message[:1000],
        )
    except Exception as exc:
        logger.warning("Could not write quality-flag audit entry: %s", exc)


@transaction.atomic
def raise_flag(user, target_type: str, target_id, reason_code: str, note: str = "",
               suggested_plate: str = "") -> QualityFlag:
    target_type, obj, bond = _resolve_target(target_type, target_id)
    authz.require(user, C.FLAG_RAISE, bond or None)

    if reason_code not in QualityFlag.Reason.values:
        reason_code = QualityFlag.Reason.OTHER
    note = (note or "").strip()
    if reason_code == QualityFlag.Reason.OTHER and not note:
        raise QualityFlagError("Describe what is wrong.")
    suggestion = _canonical_plate(suggested_plate)
    if suggestion:
        try:
            from core.vision.normalizer import is_valid_plate
            if not is_valid_plate(suggestion):
                raise QualityFlagError(f"'{suggested_plate}' is not a valid Ugandan plate number.")
        except ImportError:
            pass

    kwargs = {QualityFlag.TargetType.PAIR: "pair", QualityFlag.TargetType.ORDER: "order",
              QualityFlag.TargetType.KIT: "kit"}
    field = kwargs[target_type]
    duplicate = QualityFlag.objects.filter(status=QualityFlag.Status.OPEN, reason_code=reason_code,
                                           **{field: obj}).first()
    if duplicate:
        raise QualityFlagError("This item already has an open flag for the same reason.")

    flag = QualityFlag.objects.create(
        target_type=target_type, bond_code=bond, reason_code=reason_code, note=note,
        suggested_plate=suggestion, raised_by=user, **{field: obj},
    )
    pair = obj if target_type == QualityFlag.TargetType.PAIR else None
    msg = f"Flag #{flag.pk} raised on {target_type.lower()} by {user.username}: {flag.get_reason_code_display()}"
    if suggestion:
        msg += f" (suggested plate {suggestion})"
    if note:
        msg += f" - {note}"
    _audit_pair(pair, SubmissionAuditLog.Action.FLAG_RAISED, msg, user)
    return flag


@transaction.atomic
def resolve_flag(user, flag: QualityFlag, outcome: str, note: str = "") -> QualityFlag:
    authz.require(user, C.FLAG_RESOLVE, flag.bond_code or None)
    if flag.status != QualityFlag.Status.OPEN:
        raise QualityFlagError("This flag is already closed.")
    outcome = (outcome or "").strip().lower()

    if outcome == "apply":
        if not flag.suggested_plate:
            raise QualityFlagError("This flag has no suggested plate to apply.")
        if flag.target_type != QualityFlag.TargetType.PAIR or flag.pair is None:
            raise QualityFlagError("Suggested plates can only be applied to a vehicle pair.")
        _apply_plate_to_pair(flag.pair, flag.suggested_plate, user, flag)
        flag.status = QualityFlag.Status.APPLIED
        action = SubmissionAuditLog.Action.FLAG_APPLIED
    elif outcome == "reject":
        flag.status = QualityFlag.Status.REJECTED
        action = SubmissionAuditLog.Action.FLAG_RESOLVED
    elif outcome == "resolve":
        flag.status = QualityFlag.Status.RESOLVED
        action = SubmissionAuditLog.Action.FLAG_RESOLVED
    else:
        raise QualityFlagError("Choose apply, reject or resolve.")

    flag.resolved_by = user
    flag.resolved_at = timezone.now()
    flag.resolution_note = (note or "").strip()
    flag.save()
    _audit_pair(flag.pair, action,
                f"Flag #{flag.pk} {flag.get_status_display().lower()} by {user.username}"
                + (f": {flag.resolution_note}" if flag.resolution_note else ""), user)
    return flag


def _apply_plate_to_pair(pair: VehicleInstallationPair, plate: str, user, flag: QualityFlag) -> None:
    """Applies the QI's corrected plate. If the linked order no longer matches, the pair is
    unlinked and sent back for review so the Team Leader re-links it."""
    previous = pair.manual_plate_override or pair.registration_number_detected
    pair.manual_plate_override = plate
    pair.is_manual_override = True
    pair.registration_number_detected = plate
    fields = ["manual_plate_override", "is_manual_override", "registration_number_detected"]
    if pair.order_id and _canonical_plate(pair.order.registration_number) != plate:
        pair.order = None
        pair.verification_status = VehicleInstallationPair.VerificationStatus.PENDING_REVIEW
        fields += ["order", "verification_status"]
    pair.save(update_fields=fields)
    _audit_pair(pair, SubmissionAuditLog.Action.MANUAL_PLATE_ASSIGN,
                f"Plate corrected {previous or '-'} -> {plate} from QI flag #{flag.pk}", user)


def blocking_flags_for_pair(pair: VehicleInstallationPair) -> QuerySet:
    q = Q(pair=pair)
    if pair.order_id:
        q |= Q(order_id=pair.order_id)
        kit_ids = list(_kits_for_registration(pair.order.registration_number).values_list("pk", flat=True))
        if kit_ids:
            q |= Q(kit_id__in=kit_ids)
    return QualityFlag.objects.filter(q, status=QualityFlag.Status.OPEN)


def submission_block_message(pair: VehicleInstallationPair) -> str:
    flags = list(blocking_flags_for_pair(pair)[:3])
    if not flags:
        return ""
    reasons = "; ".join(f"#{f.pk} {f.get_reason_code_display()}" for f in flags)
    return f"Blocked by open quality flag(s): {reasons}. The Team Leader must resolve them before submission."


def flag_to_dict(flag: QualityFlag) -> dict:
    return {
        "id": flag.pk,
        "target_type": flag.target_type,
        "pair_id": flag.pair_id,
        "order_id": flag.order_id,
        "order_number": getattr(flag.order, "order_number", "") if flag.order_id else "",
        "kit_id": flag.kit_id,
        "kit_code": getattr(flag.kit, "kit_code", "") if flag.kit_id else "",
        "bond_code": flag.bond_code,
        "reason_code": flag.reason_code,
        "reason": flag.get_reason_code_display(),
        "note": flag.note,
        "suggested_plate": flag.suggested_plate,
        "status": flag.status,
        "raised_by": getattr(flag.raised_by, "username", ""),
        "raised_at": flag.raised_at.isoformat() if flag.raised_at else "",
        "resolved_by": getattr(flag.resolved_by, "username", ""),
        "resolved_at": flag.resolved_at.isoformat() if flag.resolved_at else "",
        "resolution_note": flag.resolution_note,
    }
