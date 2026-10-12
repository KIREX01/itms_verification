# Observability Stack

**Document ID:** ENT-07  
**Category:** Observability  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), Redis, Celery, Supabase  

---

## 1. Current State

```
What exists:
  ✅ Error tracking to Supabase errors table
  ✅ Audit logging (audit_logs table)
  ✅ Console logging (Python logging module)
  ✅ Security event logging
  ✅ Performance middleware (logs slow requests > 500ms)
  ✅ KPI service (calculates error rate, latency, success rate)
  ✅ Service status checks (6 services monitored)
  ✅ Module health models (HEALTHY → DEGRADED → UNHEALTHY → CRITICAL)

What's missing:
  ✗ No centralized log aggregation (logs scattered in console + DB)
  ✗ No metrics pipeline (no Prometheus, no Grafana)
  ✗ No distributed tracing (no trace_id propagation — see ENT-13)
  ✗ No alerting rules (no one gets paged at 3 AM)
  ✗ No SLO/SLI definitions
  ✗ No error budget tracking
  ✗ No dashboards for real-time system visibility
  ✗ No frontend performance monitoring
  ✗ No log retention or search capability beyond SQL queries
```

---

## 2. Three Pillars of Observability

```
┌─────────────────────────────────────────────────────────────────┐
│              Observability Pillars for OHAS                     │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   METRICS    │  │     LOGS     │  │    TRACES    │         │
│  │              │  │              │  │              │         │
│  │ What is the  │  │ Why is the   │  │ How did the  │         │
│  │ system doing?│  │ system doing │  │ request flow │         │
│  │              │  │ that?        │  │ through the  │         │
│  │ Numbers,     │  │ Text,        │  │ system?      │         │
│  │ gauges,      │  │ timestamps,  │  │              │         │
│  │ counters     │  │ stack traces │  │ Spans,       │         │
│  │              │  │              │  │ parent-child │         │
│  │ Prometheus   │  │ Structured   │  │ relationships│         │
│  │ + Grafana    │  │ JSON logs    │  │ OpenTelemetry│         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
│                                                                 │
│  Current status: Partial LOGS, no METRICS, no TRACES           │
│  Target: All three pillars implemented                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Pillar 1: Metrics (Prometheus + Grafana)

### 3.1 Prometheus Metrics Endpoint

```python
# backend/app/middleware/metrics.py

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from fastapi.responses import PlainTextResponse
import time
import logging

logger = logging.getLogger(__name__)

# ── Prometheus Metrics ──

# HTTP metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

http_exceptions_total = Counter(
    "http_exceptions_total",
    "Total HTTP exceptions",
    ["method", "endpoint", "exception_type"],
)

# Business metrics
reports_submitted_total = Counter(
    "reports_submitted_total",
    "Total reports submitted",
    ["country", "program"],
)

active_sessions = Gauge(
    "active_sessions",
    "Number of active user sessions",
)

users_by_role = Gauge(
    "users_by_role",
    "Number of users by role",
    ["role"],
)

# Database metrics
db_query_duration_seconds = Histogram(
    "db_query_duration_seconds",
    "Database query duration in seconds",
    ["table", "operation"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

db_query_errors_total = Counter(
    "db_query_errors_total",
    "Total database query errors",
    ["table", "error_type"],
)

# Celery metrics
celery_tasks_total = Counter(
    "celery_tasks_total",
    "Total Celery tasks",
    ["task_name", "status"],  # status: success, failure, retry
)

celery_task_duration_seconds = Histogram(
    "celery_task_duration_seconds",
    "Celery task duration in seconds",
    ["task_name"],
)

celery_queue_depth = Gauge(
    "celery_queue_depth",
    "Number of pending Celery tasks",
)

# Degradation metrics
degradation_tier = Gauge(
    "degradation_tier",
    "Current degradation tier (0=healthy, 3=critical)",
)

error_budget_remaining = Gauge(
    "error_budget_remaining_percent",
    "Percentage of monthly error budget remaining",
)


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Records Prometheus metrics for every HTTP request.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.time()
        
        try:
            response = await call_next(request)
            
            duration = time.time() - start_time
            
            # Record metrics
            http_requests_total.labels(
                method=request.method,
                endpoint=request.url.path,
                status_code=response.status_code,
            ).inc()
            
            http_request_duration_seconds.labels(
                method=request.method,
                endpoint=request.url.path,
            ).observe(duration)
            
            return response
            
        except Exception as e:
            http_exceptions_total.labels(
                method=request.method,
                endpoint=request.url.path,
                exception_type=type(e).__name__,
            ).inc()
            raise


# Metrics endpoint for Prometheus scraper
from fastapi import APIRouter

metrics_router = APIRouter()

@metrics_router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    return PlainTextResponse(generate_latest())
```

### 3.2 Grafana Dashboards

```json
{
  "dashboard": {
    "title": "OHAS — System Overview",
    "panels": [
      {
        "title": "Request Rate (req/min)",
        "type": "graph",
        "targets": [{
          "expr": "rate(http_requests_total[5m]) * 60"
        }]
      },
      {
        "title": "Error Rate (%)",
        "type": "graph",
        "targets": [{
          "expr": "rate(http_requests_total{status_code=~\"5..\"}[5m]) / rate(http_requests_total[5m]) * 100"
        }],
        "thresholds": [
          {"value": 1, "color": "yellow"},
          {"value": 5, "color": "red"}
        ]
      },
      {
        "title": "p95 Latency (ms)",
        "type": "graph",
        "targets": [{
          "expr": "histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) * 1000"
        }],
        "thresholds": [
          {"value": 500, "color": "yellow"},
          {"value": 1000, "color": "red"}
        ]
      },
      {
        "title": "Active Sessions",
        "type": "singlestat",
        "targets": [{
          "expr": "active_sessions"
        }]
      },
      {
        "title": "Reports Submitted (24h)",
        "type": "singlestat",
        "targets": [{
          "expr": "increase(reports_submitted_total[24h])"
        }]
      },
      {
        "title": "Celery Queue Depth",
        "type": "graph",
        "targets": [{
          "expr": "celery_queue_depth"
        }],
        "thresholds": [
          {"value": 100, "color": "yellow"},
          {"value": 500, "color": "red"}
        ]
      },
      {
        "title": "Degradation Tier",
        "type": "singlestat",
        "targets": [{
          "expr": "degradation_tier"
        }]
      },
      {
        "title": "Error Budget Remaining (%)",
        "type": "gauge",
        "targets": [{
          "expr": "error_budget_remaining_percent"
        }],
        "thresholds": [
          {"value": 25, "color": "red"},
          {"value": 50, "color": "yellow"}
        ]
      }
    ]
  }
}
```

---

## 4. Pillar 2: Logs (Structured JSON)

### 4.1 Structured Logging Configuration

```python
# backend/app/core/logging_config.py

import logging
import json
import sys
from datetime import datetime, timezone


class StructuredFormatter(logging.Formatter):
    """
    JSON log formatter for centralized log aggregation.
    Every log line is a JSON object with consistent fields.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "thread": record.threadName,
        }

        # Add trace_id if present
        if hasattr(record, "trace_id"):
            log_entry["trace_id"] = record.trace_id

        # Add user_id if present
        if hasattr(record, "user_id"):
            log_entry["user_id"] = record.user_id

        # Add duration if present
        if hasattr(record, "duration_ms"):
            log_entry["duration_ms"] = record.duration_ms

        # Add exception info
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        # Add extra fields
        for key in ("endpoint", "status_code", "ip_address", "user_agent"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)

        return json.dumps(log_entry, default=str)


def setup_logging():
    """Configure application-wide structured logging."""
    
    # Console handler (stdout) — JSON format
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredFormatter())
    
    # Root logger
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    
    # App logger
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.DEBUG)
    
    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
```

### 4.2 Log Aggregation Options

```
┌─────────────────────────┬──────────────┬──────────────┬──────────────┐
│ Option                  │ Cost         │ Complexity   │ OHAS Fit     │
├─────────────────────────┼──────────────┼──────────────┼──────────────┤
│ Supabase + SQL queries  │ Free (uses   │ Low          │ ✅ Good for  │
│ (current approach)      │ existing DB) │              │ current scale│
├─────────────────────────┼──────────────┼──────────────┼──────────────┤
│ Self-hosted ELK         │ $50-200/mo   │ High         │ ⚠️ Overkill │
│ (Elasticsearch +        │ (server)     │              │ for 100 users│
│ Logstash + Kibana)      │              │              │              │
├─────────────────────────┼──────────────┼──────────────┼──────────────┤
│ Grafana Loki + Promtail │ $30-100/mo   │ Medium       │ ✅ Best      │
│                         │ (server)     │              │ balance      │
├─────────────────────────┼──────────────┼──────────────┼──────────────┤
│ Hosted (Datadog,        │ $100-500/mo  │ Low          │ ⚠️ Expensive│
│ New Relic, Loggly)      │              │              │ for scale    │
├─────────────────────────┼──────────────┼──────────────┼──────────────┤
│ CloudWatch (if on AWS)  │ $20-80/mo    │ Low-Medium   │ ✅ Good if   │
│                         │              │              │ already on AWS│
└─────────────────────────┴──────────────┴──────────────┴──────────────┘

Recommendation for OHAS: Start with structured JSON logs to stdout + 
periodic Supabase export. Migrate to Grafana Loki when log volume 
exceeds 10K entries/day.
```

---

## 5. Pillar 3: Distributed Tracing

See `docs/enterprise-practices/01-api-design/13-request-correlation-distributed-tracing.md` for the full tracing implementation.

Summary:
- Tier 1 (now): Request correlation with trace_id in every log, error, and response
- Tier 2 (future): OpenTelemetry + Jaeger/Tempo for span-based waterfall tracing

---

## 6. SLO/SLI Definitions

### 6.1 Service Level Objectives

```
┌──────────────────────────┬─────────────┬─────────────┬──────────────┐
│ SLI (Indicator)          │ SLO (Target)│ Measurement │ Error Budget │
├──────────────────────────┼─────────────┼─────────────┼──────────────┤
│ Availability             │ 99.9%       │ (Total req  │ 43 min/month │
│                          │             │  - 5xx) /   │              │
│                          │             │  Total req   │              │
├──────────────────────────┼─────────────┼─────────────┼──────────────┤
│ Latency (p95)            │ < 500ms     │ histogram   │ N/A          │
│                          │             │ quantile    │              │
├──────────────────────────┼─────────────┼─────────────┼──────────────┤
│ Latency (p99)            │ < 1000ms    │ histogram   │ N/A          │
│                          │             │ quantile    │              │
├──────────────────────────┼─────────────┼─────────────┼──────────────┤
│ Error Rate               │ < 1%        │ 5xx / total │ N/A          │
├──────────────────────────┼─────────────┼─────────────┼──────────────┤
│ Dashboard Data Freshness │ < 30s       │ X-Data-     │ N/A          │
│                          │             │ Freshness   │              │
│                          │             │ header      │              │
├──────────────────────────┼─────────────┼─────────────┼──────────────┤
│ Report Submission        │ 99.95%      │ Successful  │ 22 min/month │
│ Success Rate             │             │ submissions │              │
│                          │             │ / total     │              │
└──────────────────────────┴─────────────┴─────────────┴──────────────┘
```

### 6.2 Error Budget

```
Error Budget = (1 - SLO) × Total Requests

For Availability SLO of 99.9%:
  Monthly requests (estimated): 500,000
  Allowed failures: 500,000 × 0.001 = 500 failures/month
  Error budget: 500 failures = 43 minutes of downtime

Budget consumption rate:
  Each 5xx error burns the budget
  Each minute of downtime burns ~12 budget points

Budget exhaustion policy:
  → At 50% remaining: Warn team, review recent incidents
  → At 25% remaining: Freeze non-critical deployments
  → At 0% remaining: Freeze ALL deployments until budget refreshes
  → Budget refreshes monthly (on the 1st)
```

---

## 7. Alert Rules

```python
# Alert configuration for Prometheus AlertManager

groups:
  - name: ohas_critical
    rules:
      # Page immediately
      - alert: HighErrorRate
        expr: rate(http_requests_total{status_code=~"5.."}[5m]) / rate(http_requests_total[5m]) > 0.05
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Error rate above 5% for 5 minutes"
          action: "Page on-call developer"

      - alert: DatabaseDown
        expr: db_query_errors_total[5m] > 10
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "Database queries failing"
          action: "Page on-call developer, check Supabase status"

      - alert: DegradationTier3
        expr: degradation_tier >= 3
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "System in CRITICAL degradation mode"
          action: "Page all developers and top admins"

  - name: ohas_warning
    rules:
      # Warn (Slack notification, no page)
      - alert: HighLatency
        expr: histogram_quantile(0.95, rate(http_request_duration_seconds_bucket[5m])) > 0.5
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "p95 latency above 500ms for 10 minutes"

      - alert: CeleryQueueBacklog
        expr: celery_queue_depth > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Celery queue depth above 100"

      - alert: ErrorBudgetLow
        expr: error_budget_remaining_percent < 25
        for: 0m
        labels:
          severity: warning
        annotations:
          summary: "Error budget below 25%"

      - alert: DiskSpaceLow
        expr: node_filesystem_avail_bytes / node_filesystem_size_bytes < 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Disk space below 10%"
```

---

## 8. Cost Analysis

```
┌───────────────────────────────┬──────────────┬─────────────────────┐
│ Component                     │ Monthly Cost │ Notes               │
├───────────────────────────────┼──────────────┼─────────────────────┤
│ Prometheus (self-hosted)      │ $30-50       │ Small VPS           │
│ Grafana (self-hosted)         │ $0           │ Same VPS            │
│ Grafana Loki (self-hosted)    │ $30-50       │ Same VPS            │
│ AlertManager                  │ $0           │ Part of Prometheus  │
│                               │              │ stack               │
├───────────────────────────────┼──────────────┼─────────────────────┤
│ TOTAL (self-hosted)           │ $60-100/mo   │                     │
├───────────────────────────────┼──────────────┼─────────────────────┤
│ Datadog (hosted)              │ $150-300/mo  │ 5 hosts, 100K       │
│                               │              │ events/day          │
├───────────────────────────────┼──────────────┼─────────────────────┤
│ New Relic (hosted)            │ $100-200/mo  │ 100 GB ingest       │
└───────────────────────────────┴──────────────┴─────────────────────┘

Recommendation: Self-hosted Prometheus + Grafana + Loki on a small VPS.
At $60-100/month, this is affordable and provides complete control.
Migrate to hosted solution if team grows beyond 5 developers.
```

---

## 9. Implementation Roadmap

| Phase | Task | Effort | Priority |
|-------|------|--------|----------|
| 1 | Add Prometheus metrics middleware | 2 hrs | 🔴 |
| 2 | Set up Prometheus + Grafana (Docker) | 2 hrs | 🔴 |
| 3 | Configure Grafana dashboards | 3 hrs | 🔴 |
| 4 | Set up structured JSON logging | 1 hr | 🔴 |
| 5 | Define SLOs and error budget tracking | 2 hrs | 🟠 |
| 6 | Configure alert rules (AlertManager) | 2 hrs | 🟠 |
| 7 | Set up Slack/email notifications | 1 hr | 🟠 |
| 8 | Add frontend performance monitoring | 2 hrs | 🟡 |
| 9 | Set up Grafana Loki for log aggregation | 3 hrs | 🟡 |
| 10 | Implement OpenTelemetry tracing (Tier 2) | 4 hrs | 🟢 |

**Total effort: ~22 hours**

---

## 10. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Metrics tool | Prometheus + Grafana | Industry standard, open-source, well-documented |
| Log aggregation | Structured JSON → Supabase → Loki (future) | Start simple, scale when needed |
| Tracing | Tier 1 (trace_id) now, Tier 2 (OpenTelemetry) later | Tier 1 covers 90% of debugging needs |
| SLO availability | 99.9% (43 min/month) | Standard for B2B apps |
| SLO latency | p95 < 500ms | User-perceptible threshold |
| SLO error rate | < 1% | Industry standard |
| Error budget policy | Freeze deployments at 0% | Prevents making things worse |
| Alert severity | Critical (page) vs Warning (Slack) | Reduces alert fatigue |
| Hosting | Self-hosted on small VPS | Most cost-effective for current scale |
| Dashboard panels | Request rate, error rate, latency, sessions, queue, degradation | Covers all critical aspects |

---

*This document should be updated when new metrics are added, when SLOs change, or when the observability stack is upgraded.*
