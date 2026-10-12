"""
Middleware for Request Envelope tracing and security context attachment.
Assigns an end-to-end trace_id and builds OperatorRequestEnvelope on every request.
Handles production OHAS content negotiation for 'Accept: application/vnd.ohas.envelope+json'.
"""
import json
from datetime import datetime, timezone
from typing import Callable

from django.http import HttpRequest, HttpResponse

from core.services.api_envelope import (
    ResponseMeta,
    create_error_detail,
    generate_trace_id,
    is_envelope_requested,
)
from core.services.security_envelope import build_operator_envelope


class EnvelopeTracingMiddleware:
    """Attaches distributed trace_id, security envelope, and handles content negotiation."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]):
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # 1. Establish distributed trace_id
        trace_id = generate_trace_id(request)
        setattr(request, "trace_id", trace_id)

        # 2. Attach security envelope
        envelope = build_operator_envelope(request)
        setattr(request, "envelope", envelope)

        # 3. Process request
        response = self.get_response(request)

        # 4. Ensure X-Trace-ID header on outgoing response
        if "X-Trace-ID" not in response:
            response["X-Trace-ID"] = trace_id

        # 5. Envelope content negotiation (OHAS Protocol)
        if is_envelope_requested(request) and hasattr(response, "content"):
            content_type = response.get("Content-Type", "")
            if "application/json" in content_type:
                try:
                    raw_content = response.content.decode("utf-8")
                    if raw_content:
                        payload = json.loads(raw_content)
                        # Check if already enveloped
                        if not (isinstance(payload, dict) and "meta" in payload and "data" in payload and "errors" in payload):
                            meta = ResponseMeta(
                                trace_id=trace_id,
                                timestamp=datetime.now(timezone.utc).isoformat(),
                                system_tier="TIER0_HEALTHY",
                                api_version="v1",
                            )
                            meta_dict = meta.model_dump() if hasattr(meta, "model_dump") else meta.__dict__

                            if response.status_code >= 400:
                                err_code = "REQUEST_ERROR"
                                err_msg = "An error occurred."
                                err_field = None
                                err_details = None
                                if isinstance(payload, dict):
                                    err_code = str(payload.get("code") or payload.get("error_code") or "REQUEST_ERROR")
                                    err_msg = str(payload.get("error") or payload.get("message") or err_msg)
                                    err_field = payload.get("field")
                                    err_details = payload.get("details")
                                err_item = create_error_detail(
                                    code=err_code,
                                    message=err_msg,
                                    field=err_field,
                                    details=err_details,
                                )
                                err_dict = err_item.model_dump() if hasattr(err_item, "model_dump") else err_item.__dict__
                                enveloped = {
                                    "data": None,
                                    "meta": meta_dict,
                                    "errors": [err_dict],
                                }
                            else:
                                enveloped = {
                                    "data": payload,
                                    "meta": meta_dict,
                                    "errors": [],
                                }
                            response.content = json.dumps(enveloped).encode("utf-8")
                            response["Content-Length"] = str(len(response.content))
                            response["Content-Type"] = "application/vnd.ohas.envelope+json"
                except Exception:
                    pass

        return response
