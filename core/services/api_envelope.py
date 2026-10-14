"""
Production API Response Envelope Protocol (OHAS Protocol).
Supports content negotiation via Accept header:
- 'Accept: application/vnd.ohas.envelope+json' -> Wrapped in APIResponse envelope
- 'Accept: application/vnd.ohas.v1.bare+json'   -> Bare payload (legacy mode)
- Default (No custom Accept header)            -> Bare payload (backward compatibility)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Generic, List, Optional, TypeVar, Union

from django.http import HttpRequest, HttpResponse, JsonResponse

try:
    from pydantic import BaseModel
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    class BaseModel:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any):
            for k, v in kwargs.items():
                setattr(self, k, v)

        def model_dump(self) -> Dict[str, Any]:
            res: Dict[str, Any] = {}
            for k, v in self.__dict__.items():
                if isinstance(v, datetime):
                    res[k] = v.isoformat()
                elif hasattr(v, "model_dump"):
                    res[k] = v.model_dump()
                elif isinstance(v, list):
                    res[k] = [item.model_dump() if hasattr(item, "model_dump") else item for item in v]
                else:
                    res[k] = v
            return res

T = TypeVar("T")


class ResponseMeta(BaseModel):
    """Metadata included with every enveloped response."""
    trace_id: str
    timestamp: Union[datetime, str]
    system_tier: str = "TIER0_HEALTHY"
    api_version: str = "v1"

    if PYDANTIC_AVAILABLE:
        model_config = {"arbitrary_types_allowed": True}


class ErrorDetail(BaseModel):
    """Structured error information."""
    code: str
    message: str
    field: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


class PaginationMeta(BaseModel):
    """Metadata for paginated list responses."""
    total_count: Optional[int] = None
    has_next: bool = False
    has_prev: bool = False
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None
    limit: int = 50


class APIResponse(BaseModel, Generic[T]):
    """The main envelope response model."""
    data: Optional[T] = None
    meta: ResponseMeta
    errors: List[ErrorDetail] = []


HEADER_ENVELOPE = "application/vnd.ohas.envelope+json"
HEADER_BARE = "application/vnd.ohas.v1.bare+json"


def is_envelope_requested(request: Optional[HttpRequest]) -> bool:
    """Checks whether the client explicitly requested an enveloped response."""
    if not request:
        return False
    accept = request.headers.get("Accept", "").lower()
    return HEADER_ENVELOPE in accept


def generate_trace_id(request: Optional[HttpRequest] = None) -> str:
    """Extracts or generates an end-to-end request tracing identifier."""
    if request:
        existing = request.headers.get("X-Trace-ID") or request.headers.get("X-Request-ID")
        if existing:
            return str(existing).strip()
    return f"req_{uuid.uuid4().hex[:12]}"


def create_error_detail(code: str, message: str, field: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> ErrorDetail:
    """Factory for ErrorDetail instances."""
    return ErrorDetail(code=code, message=message, field=field, details=details)


def format_api_response(
    data: Optional[Any] = None,
    errors: Optional[List[ErrorDetail]] = None,
    meta: Optional[ResponseMeta] = None,
    status_code: int = 200,
    trace_id: Optional[str] = None,
    system_tier: str = "TIER0_HEALTHY",
    api_version: str = "v1",
    request: Optional[HttpRequest] = None,
) -> HttpResponse:
    """
    Constructs an HTTP response using content negotiation:
    - If envelope mode requested: returns full APIResponse envelope.
    - If bare mode (default): returns standard un-enveloped JSON for backward compatibility.
    Always includes X-Trace-ID response header.
    """
    resolved_trace = trace_id or getattr(request, "trace_id", None) or generate_trace_id(request)
    resolved_errors = errors or []

    # Construct ResponseMeta
    if meta is None:
        meta = ResponseMeta(
            trace_id=resolved_trace,
            timestamp=datetime.now(timezone.utc).isoformat(),
            system_tier=system_tier,
            api_version=api_version,
        )

    envelope_mode = is_envelope_requested(request)

    if envelope_mode:
        meta_dict = meta.model_dump() if hasattr(meta, "model_dump") else meta.__dict__
        errs_list = [e.model_dump() if hasattr(e, "model_dump") else e.__dict__ for e in resolved_errors]

        payload = {
            "data": data,
            "meta": meta_dict,
            "errors": errs_list,
        }
        resp = JsonResponse(payload, status=status_code, safe=False)
        resp["Content-Type"] = "application/vnd.ohas.envelope+json"
    else:
        # Bare mode (legacy compatibility)
        if resolved_errors and (data is None or status_code >= 400):
            err0 = resolved_errors[0]
            payload = {
                "success": False,
                "error": err0.message,
                "code": err0.code,
                "field": err0.field,
                "details": err0.details,
            }
        elif isinstance(data, dict):
            payload = dict(data)
            if "success" not in payload and status_code < 400:
                payload["success"] = True
        else:
            payload = {"success": status_code < 400, "data": data}

        resp = JsonResponse(payload, status=status_code, safe=False)

    resp["X-Trace-ID"] = resolved_trace
    return resp
