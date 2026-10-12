# Request Correlation & Distributed Tracing

**Document ID:** ENT-13  
**Category:** API Design  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Frontend (React), Backend (FastAPI), Supabase, Error Tracking  

---

## 1. Current State

```
What exists:
  ✅ request_id generated in error_tracking.py (line: request_id = f"req_{int(time.time() * 1000)}")
  ✅ request_id used in error records and audit_logs
  ✅ Error tracking captures user_id, path, method, client_ip

What's missing:
  ✗ No trace_id generated at the edge (frontend or first middleware)
  ✗ request_id only generated on error, not on every request
  ✗ No correlation between frontend errors and backend logs
  ✗ No way to trace a request from frontend → backend → database → error table
  ✗ No trace context propagation (W3C Trace Context headers)
  ✗ No span-based tracing (can't see "DB query took 50ms within request X")
  ✗ No trace_id in Supabase query metadata
  ✗ Frontend error reports don't include a trace_id
```

### The Problem Today

```
Reporter in Uganda submits a report. It fails with 500.
Reporter screenshot: "An internal system error occurred."

Developer sees in errors table:
  message: "Connection timeout"
  error_type: "TimeoutError"
  path: "/api/v1/reports"
  user_id: "abc-123"
  timestamp: "2026-04-04T12:00:00Z"

But the developer CANNOT:
  → Find the exact frontend action that triggered it
  → See what other requests were happening at the same time
  → Know if this is the 1st or 100th occurrence
  → Trace the request through the Supabase query layer
  → Correlate with network logs, Redis logs, or Celery logs

With distributed tracing, all of the above becomes possible.
```

---

## 2. Tracing Strategy

### 2.1 Two-Tier Tracing

```
Tier 1: Request Correlation (Simple, Immediate, Essential)
──────────────────────────────────────────────────────────
What it does:
  → Every request gets a unique trace_id at the edge
  → trace_id is included in every log entry, error record, DB query
  → trace_id is returned in the response envelope (meta.trace_id)
  → Frontend includes trace_id in error reports
  → You can search "trace_id = abc123" and see the full request journey

Implementation effort: ~2 hours
Value: Enables debugging 90% of production issues

Tier 2: Distributed Tracing with Spans (Advanced, Future)
──────────────────────────────────────────────────────────
What it does:
  → W3C Trace Context headers (traceparent, tracestate)
  → Span-based timing: "HTTP request → Auth middleware → DB query → Response"
  → OpenTelemetry SDK with export to Jaeger/Tempo/Grafana
  → Visual trace waterfall diagram
  → Automatic error correlation across services

Implementation effort: ~8 hours
Value: Deep performance analysis, cross-service debugging
```

### Decision: Implement Tier 1 now, Tier 2 when the system scales past 500 concurrent users.

---

## 3. Tier 1: Request Correlation Implementation

### 3.1 Trace ID Generation Middleware

```python
# backend/app/middleware/request_tracing.py

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import time
import logging

logger = logging.getLogger(__name__)


class RequestTracingMiddleware(BaseHTTPMiddleware):
    """
    Generates a unique trace_id for every request and attaches it
    to the request state, all log entries, and the response.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate or forward trace_id
        trace_id = request.headers.get("X-Trace-ID")
        if not trace_id:
            trace_id = f"req_{int(time.time() * 1000)}_{id(object()):x}"

        # Attach to request state (available to all downstream code)
        request.state.trace_id = trace_id
        request.state.request_start_time = time.time()

        # Create a request-scoped logger with trace_id
        request_log = logging.LoggerAdapter(
            logger, {"trace_id": trace_id}
        )

        # Log the request
        request_log.info(
            f"{request.method} {request.url.path} "
            f"from {request.client.host if request.client else 'unknown'}"
        )

        try:
            response = await call_next(request)
        except Exception as e:
            # Log the error with trace_id before re-raising
            request_log.error(
                f"Request failed: {e}", exc_info=True
            )
            raise

        # Attach trace_id to response header
        response.headers["X-Trace-ID"] = trace_id

        # Log response time
        duration = time.time() - request.state.request_start_time
        request_log.info(
            f"Completed in {duration * 1000:.0f}ms — {response.status_code}"
        )

        return response
```

### 3.2 Trace-Aware Logging Configuration

```python
# backend/app/core/logging_config.py

import logging
import json
from datetime import datetime, timezone


class TraceJsonFormatter(logging.Formatter):
    """
    JSON log formatter that includes trace_id from LogRecord.
    Every log line is a JSON object with trace_id for correlation.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None)
                        or getattr(record, "trace_id", "N/A"),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields from LogRecord
        for key in ["user_id", "endpoint", "duration_ms"]:
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry)


# Configure root logger
def setup_logging():
    """Configure application-wide logging with trace support."""
    
    # Console handler with JSON formatter
    handler = logging.StreamHandler()
    handler.setFormatter(TraceJsonFormatter())
    
    # Root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)
    
    # App logger
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.DEBUG)
    
    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
```

### 3.3 Trace ID in Error Tracking

```python
# backend/app/utils/exceptions.py — Modified global_exception_handler

async def global_exception_handler(request: Request, exc: Exception):
    """
    Catch-all for any unhandled exceptions.
    NOW INCLUDES TRACE_ID FOR CORRELATION.
    """
    trace_id = getattr(request.state, "trace_id", "unknown")
    error_msg = str(exc)
    stack_trace = traceback.format_exc()
    exc_type = type(exc).__name__

    logger.error(
        f"Unhandled Exception: {error_msg} [trace_id={trace_id}]\n"
        f"{stack_trace}"
    )

    # Create error record WITH trace_id
    await error_tracking_service.create_error(
        message=error_msg,
        error_type=exc_type,
        severity="critical",
        source="backend",
        user_id=getattr(request.state, "user_id", None),
        component=f"{request.method} {request.url.path}",
        stack=stack_trace,
        context={
            "trace_id": trace_id,  # ← NEW: Link error to request trace
            "url": str(request.url),
            "path": request.url.path,
            "method": request.method,
            "client_ip": request.client.host if request.client else "unknown",
            "timestamp": datetime.utcnow().isoformat(),
        },
    )

    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal system error occurred. Our team has been notified.",
            "trace_id": trace_id,  # ← NEW: Return trace_id to client for error reports
            "error_type": exc_type if logger.isEnabledFor(logging.DEBUG) else None,
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
```

### 3.4 Trace ID in Response Envelope

```python
# Every response now includes trace_id in meta:

{
  "data": { ... },
  "meta": {
    "trace_id": "req_1712345678_abc123",  # ← From request.state.trace_id
    "timestamp": "2026-04-04T12:00:00Z",
    "system_tier": "TIER0_HEALTHY",
    "api_version": "v1"
  },
  "errors": []
}
```

### 3.5 Frontend: Send and Store Trace ID

```javascript
// frontend/src/services/api.js

api.interceptors.response.use(
  (response) => {
    // Extract trace_id from response header
    const traceId = response.headers['x-trace-id'];
    
    // Store for potential error reporting
    if (traceId) {
      response._traceId = traceId;
    }
    
    // ... existing envelope unwrapping logic ...
    
    return response;
  },
  (error) => {
    // Extract trace_id from error response
    if (error.response?.data) {
      const data = error.response.data;
      
      // From envelope format
      error.traceId = data.meta?.trace_id;
      
      // From legacy format
      if (!error.traceId && data.trace_id) {
        error.traceId = data.trace_id;
      }
      
      // From error details
      if (!error.traceId && data.errors?.[0]?.details?.trace_id) {
        error.traceId = data.errors[0].details.trace_id;
      }
    }
    
    // Also check response header
    if (!error.traceId && error.response?.headers) {
      error.traceId = error.response.headers['x-trace-id'];
    }
    
    return Promise.reject(error);
  }
);
```

### 3.6 Frontend Error Reporting with Trace ID

```javascript
// frontend/src/utils/ErrorTracker.jsx — Modified

class ErrorTracker {
  static async reportError(error, context = {}) {
    const errorReport = {
      message: error.message || String(error),
      error_type: error.name || 'UnknownError',
      trace_id: error.traceId || 'unknown',  // ← Include trace_id
      url: window.location.href,
      user_agent: navigator.userAgent,
      timestamp: new Date().toISOString(),
      context: {
        ...context,
        trace_id: error.traceId,  // ← Also in context for backend correlation
      },
    };

    try {
      await api.post('/errors', errorReport);
      console.log(`Error reported (trace: ${error.traceId})`);
    } catch (reportError) {
      console.error('Failed to report error:', reportError);
    }
  }
}
```

### 3.7 Trace ID in Database Queries

For critical operations, include trace_id in Supabase metadata:

```python
# backend/app/services/audit_service.py

class AuditService:
    @staticmethod
    async def log_action(
        action: str,
        user_id: Optional[str] = None,
        table_name: Optional[str] = None,
        new_data: Optional[dict] = None,
        trace_id: Optional[str] = None,  # ← NEW
    ):
        """Log an action to the audit_logs table."""
        
        log_data = {
            "user_id": str(user_id) if user_id else None,
            "action": action,
            "table_name": table_name,
            "new_data": {
                **(new_data or {}),
                "trace_id": trace_id,  # ← Include in every audit log
            },
            "created_at": datetime.utcnow().isoformat(),
        }
        
        supabase.table("audit_logs").insert(log_data).execute()
```

---

## 4. Debugging Workflow with Trace IDs

### 4.1 The Developer's Debug Session

```
Step 1: User reports an error with trace_id
  "I got an error submitting my report. trace_id: req_1712345678_abc123"

Step 2: Developer searches errors table
  SELECT * FROM errors 
  WHERE context->>'trace_id' = 'req_1712345678_abc123'
  
  → Finds the error record with full stack trace

Step 3: Developer traces the full request path
  SELECT * FROM audit_logs 
  WHERE new_data->>'trace_id' = 'req_1712345678_abc123'
  ORDER BY created_at
  
  → Sees every audit log entry for this request:
    12:00:00.001 — REQUEST: POST /api/v1/reports from 196.43.x.x
    12:00:00.015 — Auth: get_current_user for user abc-123
    12:00:00.030 — Report validation passed
    12:00:00.045 — Supabase insert started
    12:00:05.045 — TIMEOUT: Supabase connection timed out
    12:00:05.046 — ERROR: Unhandled Exception: TimeoutError [trace_id=req_1712345678_abc123]

Step 4: Developer identifies root cause
  → Supabase query took 5 seconds (timeout)
  → Check if this is a one-time network issue or systemic
  → Search for other errors with similar timing

Step 5: Developer checks if it's a pattern
  SELECT COUNT(*) FROM errors 
  WHERE context->>'path' = '/api/v1/reports'
    AND created_at > NOW() - INTERVAL '1 hour'
  
  → 12 errors in the last hour → systemic issue, not one-off
```

### 4.2 Trace ID in Log Aggregation

When logs are centralized (see Observability doc), trace_id enables:

```
Grafana/Loki query:
  {trace_id="req_1712345678_abc123"}
  
Returns ALL log entries across ALL services for this request:
  → Frontend console errors (if reported)
  → API gateway access logs
  → FastAPI request/response logs
  → Middleware logs (auth, rate limiting, degradation)
  → Database query logs
  → Celery task logs (if async work was triggered)
  → Email service logs (if notification was sent)

All correlated by a single trace_id.
```

---

## 5. Tier 2: Distributed Tracing (Future)

When the system grows beyond 500 concurrent users, implement full distributed tracing:

### 5.1 W3C Trace Context

```python
# Standard headers for cross-service trace propagation
# https://www.w3.org/TR/trace-context/

# traceparent: version-traceId-parentId-flags
# Example: 00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01
request.headers["traceparent"] = "00-{trace_id}-{parent_span_id}-01"
request.headers["tracestate"] = "ohas=reporter_dashboard"
```

### 5.2 OpenTelemetry Integration

```python
# Future: backend/app/services/tracing.py

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

# Initialize OpenTelemetry
provider = TracerProvider()
exporter = OTLPSpanExporter(endpoint="http://jaeger:4317")
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)
```

### 5.3 Trace Visualization

```
With Jaeger or Grafana Tempo:

┌─────────────────────────────────────────────────────────────────┐
│                    Trace Waterfall                              │
│                                                                 │
│ POST /api/v1/reports [trace: abc123]  Total: 245ms              │
│                                                                 │
│ ┌─ HTTP Server (FastAPI) ────────────────────── 245ms ────────┐│
│ │ ┌─ Auth Middleware ───────────────── 35ms ─────────────────┐││
│ │ │ supabase.auth.get_user               35ms                │││
│ │ └──────────────────────────────────────────────────────────┘││
│ │ ┌─ Rate Limiting ──────────────────── 5ms ─────────────────┐││
│ │ │ Redis: check rate limit               5ms                │││
│ │ └──────────────────────────────────────────────────────────┘││
│ │ ┌─ Report Handler ───────────────── 180ms ─────────────────┐││
│ │ │ validate_report                        10ms               │││
│ │ │ supabase.reports.insert               150ms               │││
│ │ │ dispatch_notification (Celery)         20ms               │││
│ │ └──────────────────────────────────────────────────────────┘││
│ └──────────────────────────────────────────────────────────────┘│
│                                                                 │
│ ✗ Error: TimeoutError at supabase.reports.insert (150ms)       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Implementation Checklist

| Step | Task | File(s) | Effort |
|------|------|---------|--------|
| 1 | Create request tracing middleware | `backend/app/middleware/request_tracing.py` | 30 min |
| 2 | Create JSON log formatter | `backend/app/core/logging_config.py` | 30 min |
| 3 | Register middleware in main.py | `backend/app/main.py` | 5 min |
| 4 | Add trace_id to exception handler | `backend/app/utils/exceptions.py` | 15 min |
| 5 | Add trace_id to audit log entries | `backend/app/services/audit_service.py` | 15 min |
| 6 | Add trace_id to response envelope | `backend/app/middleware/response_envelope.py` | 10 min |
| 7 | Add trace_id to error reports | `backend/app/middleware/error_tracking.py` | 15 min |
| 8 | Frontend: extract trace_id from responses | `frontend/src/services/api.js` | 30 min |
| 9 | Frontend: include trace_id in error reports | `frontend/src/utils/ErrorTracker.jsx` | 15 min |
| 10 | Configure structured logging in production | Docker/deployment config | 30 min |

**Total effort: ~3 hours**

---

## 7. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Trace ID format | `req_{timestamp_ms}_{random_hex}` | Human-readable timestamp + unique suffix |
| Generation point | First middleware (edge of backend) | Every request gets one, including health checks |
| Propagation | request.state.trace_id → all downstream code | FastAPI's request.state is the standard mechanism |
| Response header | `X-Trace-ID` | Standard convention, easy to extract |
| Response body | meta.trace_id (in envelope) | Available to frontend even without header parsing |
| Log format | JSON with trace_id field | Machine-parseable for log aggregation |
| Frontend extraction | Axios response interceptor | Single point of extraction for all responses |
| Frontend reporting | Include trace_id in error POST | Backend can correlate frontend + backend errors |
| Tier 2 timeline | After 500 concurrent users | Not needed at current scale, but plan exists |
| Trace retention | Same as error retention (90 days) | Aligned with existing data retention policy |

---

*This document should be updated when trace ID format changes, when Tier 2 distributed tracing is implemented, or when new services are added that need trace propagation.*
