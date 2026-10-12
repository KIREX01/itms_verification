# API Response Envelope Standard

**Document ID:** ENT-16  
**Category:** API Design  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), All API Consumers  

---

## 1. Current State

OHAS API responses are inconsistent across endpoints:

```javascript
// auth.py — returns bare object
{
    "access_token": "eyJ...",
    "token_type": "bearer",
    "user": { ... },
    "refresh_token": "eyJ..."
}

// countries.py — returns bare array
[
    { "id": "...", "name": "Uganda" },
    { "id": "...", "name": "Kenya" }
]

// errors — returns bare object
{
    "id": "abc123",
    "message": "...",
    "severity": "critical"
}

// security.py error handler — returns error object
{
    "detail": "An internal system error occurred. Our team has been notified.",
    "error_type": "ConnectionError",
    "timestamp": "2026-04-04T12:00:00Z"
}

// Feature flags — returns bare object
{
    "id": "...",
    "name": "multi_factor_auth",
    "is_enabled": false
}
```

### Problems

| Problem | Impact |
|---------|--------|
| No consistent shape | Frontend must handle each endpoint's response format differently |
| No pagination metadata | Consumers can't know total count, next page, etc. |
| No request tracing | Can't correlate a response with a specific request |
| No system context | Consumer doesn't know if response is from degraded system |
| No standardized errors | Error shape varies between endpoints |
| Bare arrays at top level | Can't add metadata without breaking changes |
| Some endpoints use `detail`, some use `message`, some use `error` | Frontend error handling is fragile |

---

## 2. Standard Response Envelope

All API responses (v2 and future) follow this shape:

```json
{
  "data": { },
  "meta": { },
  "errors": [ ]
}
```

### Field Definitions

| Field | Type | Always Present | Description |
|-------|------|----------------|-------------|
| `data` | object, array, or null | ✅ Yes | The actual response payload. `null` for 204 No Content. |
| `meta` | object | ✅ Yes | Request metadata (trace ID, pagination, system status, etc.) |
| `errors` | array | ✅ Yes | Array of error objects. Empty `[]` on success. |

### The `meta` Object

```json
{
  "meta": {
    "trace_id": "req_1712345678_abc123",
    "timestamp": "2026-04-04T12:00:00.000Z",
    "system_tier": "TIER0_HEALTHY",
    "data_freshness": "5s",
    "api_version": "v1",
    "pagination": {
      "page": 1,
      "per_page": 50,
      "total_items": 1247,
      "total_pages": 25,
      "has_next": true,
      "has_prev": false,
      "next_cursor": "eyJpZCI6NTB9",
      "prev_cursor": null
    },
    "rate_limit": {
      "limit": 120,
      "remaining": 115,
      "reset": 1712345738
    }
  }
}
```

| Meta Field | Type | When Present | Description |
|------------|------|--------------|-------------|
| `trace_id` | string | Always | Unique request ID for debugging |
| `timestamp` | string | Always | Server time when response was generated (ISO 8601) |
| `system_tier` | string | Always | Current degradation tier (see Graceful Degradation doc) |
| `data_freshness` | string | Read responses | How fresh the data is ("5s", "2m", "1h") |
| `api_version` | string | Always | API version that generated this response |
| `pagination` | object | List endpoints | Pagination metadata |
| `rate_limit` | object | Authenticated requests | Rate limit status for the user |

### The `errors` Array

On success: `"errors": []` (empty array)

On client error (4xx):
```json
{
  "data": null,
  "meta": {
    "trace_id": "req_123",
    "timestamp": "2026-04-04T12:00:00Z"
  },
  "errors": [
    {
      "code": "VALIDATION_ERROR",
      "message": "Invalid email address format",
      "field": "email",
      "details": {
        "value": "not-an-email",
        "expected_format": "user@domain.com"
      }
    }
  ]
}
```

On server error (5xx):
```json
{
  "data": null,
  "meta": {
    "trace_id": "req_123",
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER2_DEGRADED"
  },
  "errors": [
    {
      "code": "INTERNAL_ERROR",
      "message": "An internal system error occurred. Our team has been notified.",
      "details": {
        "trace_id": "req_123",
        "retry_after_seconds": 30
      }
    }
  ]
}
```

### Error Object Schema

| Field | Type | Always Present | Description |
|-------|------|----------------|-------------|
| `code` | string | ✅ Yes | Machine-readable error code (UPPER_SNAKE_CASE) |
| `message` | string | ✅ Yes | Human-readable error message |
| `field` | string | Validation errors only | Which input field caused the error |
| `details` | object | Optional | Additional context (varies by error type) |

### Standard Error Codes

| Code | HTTP Status | Meaning |
|------|-------------|---------|
| `VALIDATION_ERROR` | 400 | Request body failed validation |
| `UNAUTHORIZED` | 401 | Not authenticated |
| `FORBIDDEN` | 403 | Not authorized for this resource |
| `NOT_FOUND` | 404 | Resource doesn't exist |
| `CONFLICT` | 409 | Resource state conflict (e.g., version mismatch) |
| `RATE_LIMITED` | 429 | Too many requests |
| `SYSTEM_DEGRADED` | 503 | System in degraded mode |
| `INTERNAL_ERROR` | 500 | Unexpected server error |
| `SERVICE_UNAVAILABLE` | 503 | Dependency unavailable |
| `EMAIL_EXISTS` | 400 | Email already registered |
| `PROFILE_INCOMPLETE` | 403 | User must complete profile |
| `EMAIL_NOT_VERIFIED` | 403 | User must verify email |
| `TOKEN_EXPIRED` | 401 | Access token has expired |
| `TOKEN_INVALID` | 401 | Token is malformed or forged |

---

## 3. Response Examples by Endpoint Type

### 3.1 Single Resource (GET /profile/me)

```json
{
  "data": {
    "id": "abc-123",
    "full_name": "John Doe",
    "email": "john@example.com",
    "role": "reporter",
    "country_id": "def-456",
    "is_active": true,
    "created_at": "2026-01-15T08:00:00Z"
  },
  "meta": {
    "trace_id": "req_1712345678_abc",
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER0_HEALTHY",
    "api_version": "v1"
  },
  "errors": []
}
```

### 3.2 List Resource (GET /reports)

```json
{
  "data": [
    {
      "id": "rpt-001",
      "title": "Weekly Program Report",
      "submitted_by": "abc-123",
      "country_id": "def-456",
      "created_at": "2026-04-03T10:00:00Z"
    },
    {
      "id": "rpt-002",
      "title": "Monthly Metrics",
      "submitted_by": "abc-789",
      "country_id": "def-456",
      "created_at": "2026-04-02T14:30:00Z"
    }
  ],
  "meta": {
    "trace_id": "req_1712345678_def",
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER0_HEALTHY",
    "api_version": "v1",
    "data_freshness": "3s",
    "pagination": {
      "per_page": 50,
      "total_items": 1247,
      "total_pages": 25,
      "has_next": true,
      "has_prev": false,
      "next_cursor": "eyJpZCI6NTB9",
      "prev_cursor": null
    }
  },
  "errors": []
}
```

### 3.3 Create Resource (POST /reports)

```json
{
  "data": {
    "id": "rpt-003",
    "title": "Daily Attendance",
    "submitted_by": "abc-123",
    "status": "pending",
    "created_at": "2026-04-04T12:00:00Z"
  },
  "meta": {
    "trace_id": "req_1712345678_ghi",
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER0_HEALTHY",
    "api_version": "v1"
  },
  "errors": []
}
```

### 3.4 Async Accept (POST /reports — AP mode)

```json
{
  "data": {
    "id": "rpt-004",
    "status": "queued",
    "message": "Report queued for processing"
  },
  "meta": {
    "trace_id": "req_1712345678_jkl",
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER0_HEALTHY",
    "api_version": "v1"
  },
  "errors": []
}
```

### 3.5 Validation Error (POST /auth/signup)

```json
{
  "data": null,
  "meta": {
    "trace_id": "req_1712345678_mno",
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER0_HEALTHY",
    "api_version": "v1"
  },
  "errors": [
    {
      "code": "VALIDATION_ERROR",
      "message": "Please enter a valid email address",
      "field": "email",
      "details": {
        "value": "not-an-email"
      }
    },
    {
      "code": "VALIDATION_ERROR",
      "message": "Password must be at least 8 characters",
      "field": "password",
      "details": {
        "value": "short",
        "min_length": 8
      }
    }
  ]
}
```

### 3.6 Authentication Error (POST /auth/login)

```json
{
  "data": null,
  "meta": {
    "trace_id": "req_1712345678_pqr",
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER0_HEALTHY",
    "api_version": "v1"
  },
  "errors": [
    {
      "code": "UNAUTHORIZED",
      "message": "Invalid email or password"
    }
  ]
}
```

### 3.7 System Error (Any endpoint — 500)

```json
{
  "data": null,
  "meta": {
    "trace_id": "req_1712345678_stu",
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER2_DEGRADED",
    "api_version": "v1"
  },
  "errors": [
    {
      "code": "INTERNAL_ERROR",
      "message": "An internal system error occurred. Our team has been notified.",
      "details": {
        "trace_id": "req_1712345678_stu",
        "retry_after_seconds": 30
      }
    }
  ]
}
```

---

## 4. Implementation

### 4.1 Response Envelope Model

```python
# backend/app/models/response_envelope.py

from typing import Generic, TypeVar, Optional, List, Any
from pydantic import BaseModel, Field
from datetime import datetime, timezone

T = TypeVar("T")


class ErrorDetail(BaseModel):
    """A single error object in the errors array."""
    code: str = Field(..., description="Machine-readable error code")
    message: str = Field(..., description="Human-readable error message")
    field: Optional[str] = Field(None, description="Input field that caused the error")
    details: Optional[dict] = Field(None, description="Additional context")


class PaginationMeta(BaseModel):
    """Pagination metadata in the meta object."""
    per_page: int
    total_items: int
    total_pages: Optional[int] = None
    has_next: bool
    has_prev: bool
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None


class RateLimitMeta(BaseModel):
    """Rate limit metadata in the meta object."""
    limit: int
    remaining: int
    reset: int  # Unix timestamp


class ResponseMeta(BaseModel):
    """The meta object in every response."""
    trace_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    system_tier: str = "TIER0_HEALTHY"
    api_version: str = "v1"
    data_freshness: Optional[str] = None
    pagination: Optional[PaginationMeta] = None
    rate_limit: Optional[RateLimitMeta] = None


class APIResponse(BaseModel, Generic[T]):
    """
    Standard API response envelope.
    All endpoints should return this shape.
    """
    data: Optional[T] = None
    meta: ResponseMeta
    errors: List[ErrorDetail] = Field(default_factory=list)

    @classmethod
    def success(
        cls,
        data: T,
        meta: Optional[dict] = None,
        trace_id: str = "",
        system_tier: str = "TIER0_HEALTHY",
    ) -> "APIResponse[T]":
        """Create a success response."""
        return cls(
            data=data,
            meta=ResponseMeta(
                trace_id=trace_id,
                system_tier=system_tier,
                **(meta or {}),
            ),
            errors=[],
        )

    @classmethod
    def error(
        cls,
        errors: List[ErrorDetail],
        trace_id: str = "",
        system_tier: str = "TIER0_HEALTHY",
        data: Optional[Any] = None,
    ) -> "APIResponse[T]":
        """Create an error response."""
        return cls(
            data=data,
            meta=ResponseMeta(
                trace_id=trace_id,
                system_tier=system_tier,
            ),
            errors=errors,
        )

    @classmethod
    def validation_error(
        cls,
        field: str,
        message: str,
        details: Optional[dict] = None,
        trace_id: str = "",
    ) -> "APIResponse[T]":
        """Create a single-field validation error response."""
        return cls(
            data=None,
            meta=ResponseMeta(trace_id=trace_id),
            errors=[
                ErrorDetail(
                    code="VALIDATION_ERROR",
                    message=message,
                    field=field,
                    details=details,
                )
            ],
        )
```

### 4.2 Response Wrapper Middleware

```python
# backend/app/middleware/response_envelope.py

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.degradation_controller import degradation_controller
import json
import time


class ResponseEnvelopeMiddleware(BaseHTTPMiddleware):
    """
    Wraps all JSON responses in the standard envelope:
    { data, meta, errors }

    Only wraps responses that are JSON (Content-Type: application/json).
    Passes through HTML, images, redirects, etc. unchanged.
    """

    # Paths that should NOT be wrapped
    UNWRAPPED_PATHS = [
        "/health",
        "/health/live",
        "/health/ready",
        "/docs",
        "/docs/v1",
        "/redoc",
        "/openapi.json",
        "/openapi/v1.json",
    ]

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in self.UNWRAPPED_PATHS:
            return await call_next(request)

        response = await call_next(request)

        # Only wrap JSON responses
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            return response

        # Read the response body
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # Try to parse as JSON
        try:
            original_body = json.loads(body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Not valid JSON — return as-is
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=content_type,
            )

        # Check if already wrapped (has "data" and "meta" keys)
        if "data" in original_body and "meta" in original_body:
            return Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=content_type,
            )

        # Determine if this is an error response
        is_error = response.status_code >= 400

        if is_error:
            # Wrap error response
            trace_id = request.headers.get(
                "X-Request-ID",
                f"req_{int(time.time() * 1000)}",
            )

            # Extract error message from various formats
            message = "An error occurred"
            if isinstance(original_body, dict):
                message = (
                    original_body.get("detail")
                    or original_body.get("message")
                    or original_body.get("error")
                    or message
                )

            wrapped = {
                "data": original_body.get("data"),
                "meta": {
                    "trace_id": trace_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "system_tier": degradation_controller.current_tier.name,
                    "api_version": "v1",
                },
                "errors": [
                    {
                        "code": self._status_code_to_error_code(response.status_code),
                        "message": message,
                        "details": original_body if isinstance(original_body, dict) else None,
                    }
                ],
            }
        else:
            # Wrap success response
            trace_id = request.headers.get(
                "X-Request-ID",
                f"req_{int(time.time() * 1000)}",
            )

            wrapped = {
                "data": original_body,
                "meta": {
                    "trace_id": trace_id,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "system_tier": degradation_controller.current_tier.name,
                    "api_version": "v1",
                },
                "errors": [],
            }

        # Return wrapped response
        return Response(
            content=json.dumps(wrapped).encode(),
            status_code=response.status_code,
            headers={
                k: v for k, v in response.headers.items()
                if k.lower() not in ("content-length",)  # Remove old content-length
            },
            media_type="application/json",
        )

    def _status_code_to_error_code(self, status_code: int) -> str:
        """Map HTTP status code to standard error code."""
        mapping = {
            400: "VALIDATION_ERROR",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            429: "RATE_LIMITED",
            500: "INTERNAL_ERROR",
            503: "SERVICE_UNAVAILABLE",
        }
        return mapping.get(status_code, "INTERNAL_ERROR")
```

### 4.3 Frontend Adaptation

```javascript
// frontend/src/services/api.js

import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const api = axios.create({
  baseURL: API_URL,
  withCredentials: true,
});

// Response interceptor — unwrap the envelope
api.interceptors.response.use(
  (response) => {
    // If response is wrapped in envelope, unwrap it
    if (response.data && 'data' in response.data && 'meta' in response.data) {
      const envelope = response.data;

      // Store metadata for consumers who need it
      response._meta = envelope.meta;
      response._pagination = envelope.meta?.pagination;

      // If there are errors in a 2xx response, handle them
      if (envelope.errors && envelope.errors.length > 0) {
        console.warn('API returned errors in success response:', envelope.errors);
      }

      // Return the actual data
      response.data = envelope.data;
    }

    return response;
  },
  (error) => {
    if (error.response) {
      const responseData = error.response.data;

      // If error response is wrapped in envelope, unwrap errors
      if (responseData && 'errors' in responseData && Array.isArray(responseData.errors)) {
        error.errors = responseData.errors;
        error.traceId = responseData.meta?.trace_id;
        error.systemTier = responseData.meta?.system_tier;

        // Use first error's message for backwards compatibility
        if (responseData.errors.length > 0) {
          error.message = responseData.errors[0].message;
          error.code = responseData.errors[0].code;
          error.field = responseData.errors[0].field;
        }
      }
    }

    return Promise.reject(error);
  }
);

export default api;
```

---

## 5. Migration Plan: v1 Envelope Adoption

The envelope should be adopted gradually in v1 to avoid breaking existing consumers:

| Phase | Approach | Duration |
|-------|----------|----------|
| **Phase 1: Dual mode** | Responses include both old format AND envelope. Use `Accept: application/vnd.ohas.envelope+json` header to opt-in to envelope-only responses | 1 month |
| **Phase 2: Default envelope** | Envelope is default. Old format available via `Accept: application/vnd.ohas.v1.bare+json` | 2 months |
| **Phase 3: Envelope only** | Only envelope responses. Bare format removed. | Ongoing |

### Phase 1 Implementation (Dual Mode)

```python
# In ResponseEnvelopeMiddleware:

async def dispatch(self, request: Request, call_next) -> Response:
    response = await call_next(request)

    accept = request.headers.get("accept", "")

    # Client explicitly requests envelope
    if "application/vnd.ohas.envelope+json" in accept:
        return await self._wrap_response(request, response)

    # Client opts out of envelope
    if "application/vnd.ohas.v1.bare+json" in accept:
        return response

    # Default: current behavior (bare), with envelope added in Phase 2
    return response  # TODO: Switch to _wrap_response in Phase 2
```

---

## 6. OpenAPI Schema Update

Document the envelope in OpenAPI:

```python
# backend/app/main.py

app = FastAPI(
    title=settings.APP_NAME,
    openapi_components={
        "schemas": {
            "APIResponse": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "object",
                        "description": "Response payload (varies by endpoint)",
                    },
                    "meta": {"$ref": "#/components/schemas/ResponseMeta"},
                    "errors": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/ErrorDetail"},
                    },
                },
                "required": ["data", "meta", "errors"],
            },
            "ResponseMeta": {
                "type": "object",
                "properties": {
                    "trace_id": {"type": "string"},
                    "timestamp": {"type": "string", "format": "date-time"},
                    "system_tier": {"type": "string", "enum": [
                        "TIER0_HEALTHY", "TIER1_STRESSED",
                        "TIER2_DEGRADED", "TIER3_CRITICAL",
                    ]},
                    "api_version": {"type": "string"},
                    "data_freshness": {"type": "string"},
                    "pagination": {"$ref": "#/components/schemas/PaginationMeta"},
                },
            },
            "ErrorDetail": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Machine-readable error code"},
                    "message": {"type": "string", "description": "Human-readable message"},
                    "field": {"type": "string", "description": "Input field name"},
                    "details": {"type": "object"},
                },
                "required": ["code", "message"],
            },
        }
    },
)
```

---

## 7. Benefits

| Benefit | Description |
|---------|-------------|
| **Consistent frontend code** | One response shape for all endpoints |
| **Debugging** | `trace_id` in every response links frontend errors to backend logs |
| **System awareness** | Frontend knows system tier and can adapt UI |
| **Pagination** | Standard pagination metadata, no guessing |
| **Multiple errors** | Errors array supports multiple validation errors at once |
| **Forward compatible** | Adding new meta fields doesn't break consumers |
| **API evolution** | Envelope enables v2 migration without consumer surprise |

---

*This document should be updated when new error codes are added, when the envelope schema changes, or when v2 adopts it as the default.*
