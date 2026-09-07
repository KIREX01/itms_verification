"""
Simulated ITMS (Intelligent Transport Management System) service.

Stands in for the real external ITMS API during development so the full
submission workflow can be stress-tested (timeouts, validation rejections,
server errors) without touching production. Swap this module's calls for
real HTTP client calls once the study case graduates to production
integration -- the 4-step contract below is designed to mirror the real
system described in the proposal.

Steps: order lookup -> serial verification -> front upload -> rear upload
       -> final validation & token issuance.
"""
import random
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from django.conf import settings

_LATENCY_MS = getattr(settings, "ITMS_SIMULATED_LATENCY_MS", 150)
_FAILURE_RATE = getattr(settings, "ITMS_FAILURE_INJECTION_RATE", 0.0)


class ITMSError(Exception):
    """Raised for any simulated ITMS failure (network, validation, server error)."""


@dataclass
class StepResult:
    success: bool
    message: str
    token: Optional[str] = None


def _simulate_latency():
    time.sleep(_LATENCY_MS / 1000.0)


def _maybe_inject_failure(step_name: str):
    if _FAILURE_RATE > 0 and random.random() < _FAILURE_RATE:
        failure_kind = random.choice(["TIMEOUT", "VALIDATION_REJECTED", "SERVER_ERROR"])
        raise ITMSError(f"{step_name} failed: simulated {failure_kind}")


def lookup_order(order_number: str) -> StepResult:
    _simulate_latency()
    _maybe_inject_failure("ORDER_LOOKUP")
    if not order_number:
        raise ITMSError("ORDER_LOOKUP failed: empty order_number")
    return StepResult(success=True, message=f"Order {order_number} located in ITMS registry.")


def verify_serial(order_number: str, plate_serial: str, tracker_id: str) -> StepResult:
    _simulate_latency()
    _maybe_inject_failure("SERIAL_VERIFY")
    return StepResult(
        success=True,
        message=f"Serial/tracker verified for {order_number} (serial={plate_serial or 'N/A'}, tracker={tracker_id or 'N/A'}).",
    )


def upload_front_photo(order_number: str, vault_path: str) -> StepResult:
    _simulate_latency()
    _maybe_inject_failure("UPLOAD_FRONT")
    return StepResult(success=True, message=f"Front evidence uploaded for {order_number}: {vault_path}")


def upload_rear_photo(order_number: str, vault_path: str) -> StepResult:
    _simulate_latency()
    _maybe_inject_failure("UPLOAD_REAR")
    return StepResult(success=True, message=f"Rear evidence uploaded for {order_number}: {vault_path}")


def validate_and_finalize(order_number: str) -> StepResult:
    _simulate_latency()
    _maybe_inject_failure("VALIDATE")
    token = f"ITMS-SIM-{uuid.uuid4().hex[:12].upper()}"
    return StepResult(success=True, message=f"Submission validated for {order_number}.", token=token)
