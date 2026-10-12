# Webhook & Event-Driven Architecture

**Document ID:** ENT-11  
**Category:** Deployment & Operations  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Celery, External Integrations  

---

## 1. Current State

```
What exists:
  ✅ Celery async task queue (email notifications, scheduled tasks)
  ✅ Event-like patterns via audit_logs (actions logged)
  ✅ WebSocket connections for real-time updates

What's missing:
  ✗ No webhook system for external integrations
  ✗ No event bus/pub-sub pattern
  ✗ No way for external systems to react to OHAS events
  ✗ No event schema or versioning
  ✗ No webhook delivery guarantees
  ✗ No webhook management dashboard
  ✗ No event replay capability
```

---

## 2. Event Types

```
┌─────────────────────────────────────────────────────────────────┐
│              OHAS Event Catalog                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  user.*                                                         │
│  ├── user.created          → New user signed up                 │
│  ├── user.verified         → Email verified                     │
│  ├── user.deactivated      → Account disabled                   │
│  └── user.deleted          → Data deletion requested            │
│                                                                 │
│  report.*                                                       │
│  ├── report.submitted      → New report created                 │
│  ├── report.updated        → Report modified                    │
│  ├── report.approved       → Report approved by admin           │
│  └── report.rejected       → Report sent back for revision      │
│                                                                 │
│  participant.*                                                  │
│  ├── participant.enrolled  → New participant registered         │
│  ├── participant.updated   → Participant data changed           │
│  └── participant.milestone → Milestone achieved                 │
│                                                                 │
│  goal.*                                                         │
│  ├── goal.created          → New goal set                       │
│  ├── goal.achieved         → Goal completed                     │
│  ├── goal.missed           → Goal deadline passed, not achieved │
│  └── goal.updated          → Goal modified                      │
│                                                                 │
│  system.*                                                       │
│  ├── system.degraded       → System entered degraded mode       │
│  ├── system.recovered      → System recovered to healthy        │
│  └── system.maintenance    → Scheduled maintenance window       │
│                                                                 │
│  security.*                                                     │
│  ├── security.threat       → Security threat detected           │
│  ├── security.blocked_ip  → IP address blocked                  │
│  └── security.breach       → Data breach detected               │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Webhook System Design

### 3.1 Webhook Registration

```python
# backend/app/api/v1/webhooks.py

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, List
from uuid import UUID

router = APIRouter()


class WebhookCreate(BaseModel):
    """Register a new webhook endpoint."""
    name: str = Field(..., min_length=1, max_length=100, examples=["Analytics Dashboard"])
    url: HttpUrl = Field(..., examples=["https://analytics.example.com/ohas-webhook"])
    events: List[str] = Field(
        ...,
        min_items=1,
        examples=[["report.submitted", "report.approved"]],
        description="Event types to subscribe to",
    )
    secret: Optional[str] = Field(
        None,
        min_length=32,
        description="Shared secret for HMAC signature verification (auto-generated if omitted)",
    )
    description: Optional[str] = Field(None, max_length=500)


class WebhookResponse(BaseModel):
    id: UUID
    name: str
    url: str
    events: List[str]
    is_active: bool
    created_at: str
    last_delivery_at: Optional[str] = None
    last_delivery_status: Optional[str] = None
    consecutive_failures: int = 0


@router.post("/webhooks", response_model=WebhookResponse)
async def create_webhook(
    webhook: WebhookCreate,
    current_user = Depends(check_role(["top_administrator", "developer"])),
):
    """
    Register a webhook endpoint to receive OHAS events.
    
    The webhook URL will receive POST requests with event payloads
    whenever the subscribed events occur.
    """
    import secrets
    
    if not webhook.secret:
        webhook.secret = secrets.token_urlsafe(32)
    
    # Validate URL is reachable (optional connectivity check)
    # ... ping the URL ...
    
    result = supabase.table("webhooks").insert({
        "name": webhook.name,
        "url": str(webhook.url),
        "events": webhook.events,
        "secret": webhook.secret,
        "description": webhook.description,
        "is_active": True,
        "created_by": str(current_user.id),
    }).execute()
    
    return result.data[0]
```

### 3.2 Webhook Delivery

```python
# backend/app/services/webhook_service.py

import httpx
import hmac
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class WebhookService:
    """
    Manages webhook delivery with retry, signature verification,
    and delivery tracking.
    """
    
    MAX_RETRIES = 5
    RETRY_DELAYS = [60, 300, 900, 3600, 14400]  # 1m, 5m, 15m, 1h, 4h
    DELIVERY_TIMEOUT = 10  # seconds

    async def dispatch_event(self, event_type: str, payload: Dict[str, Any]):
        """
        Dispatch an event to all subscribed webhooks.
        Called whenever a significant event occurs in the system.
        """
        # Find active webhooks for this event
        webhooks = supabase.table("webhooks")\
            .select("*")\
            .eq("is_active", True)\
            .execute()
        
        for webhook in webhooks.data:
            if event_type not in webhook.get("events", []):
                continue
            
            # Dispatch to this webhook (async, fire-and-forget)
            await self._deliver_webhook(
                webhook_id=webhook["id"],
                url=webhook["url"],
                secret=webhook["secret"],
                event_type=event_type,
                payload=payload,
            )

    async def _deliver_webhook(
        self,
        webhook_id: str,
        url: str,
        secret: str,
        event_type: str,
        payload: Dict[str, Any],
        attempt: int = 0,
    ):
        """
        Deliver a webhook payload with HMAC signature and retry logic.
        """
        event_data = {
            "id": f"evt_{webhook_id[:8]}_{int(datetime.utcnow().timestamp())}",
            "type": event_type,
            "timestamp": datetime.utcnow().isoformat(),
            "data": payload,
        }
        
        # Generate HMAC signature
        body = json.dumps(event_data)
        signature = hmac.new(
            secret.encode(),
            body.encode(),
            hashlib.sha256,
        ).hexdigest()
        
        headers = {
            "Content-Type": "application/json",
            "X-OHAS-Signature": f"sha256={signature}",
            "X-OHAS-Event": event_type,
            "X-OHAS-Delivery-Id": event_data["id"],
            "X-OHAS-Attempt": str(attempt + 1),
        }
        
        try:
            async with httpx.AsyncClient(timeout=self.DELIVERY_TIMEOUT) as client:
                response = await client.post(url, headers=headers, content=body)
                response.raise_for_status()
            
            # Log successful delivery
            supabase.table("webhook_deliveries").insert({
                "webhook_id": webhook_id,
                "event_type": event_type,
                "status": "delivered",
                "response_code": response.status_code,
                "attempt": attempt + 1,
                "delivered_at": datetime.utcnow().isoformat(),
            }).execute()
            
            # Update webhook last delivery status
            supabase.table("webhooks")\
                .update({
                    "last_delivery_at": datetime.utcnow().isoformat(),
                    "last_delivery_status": "delivered",
                    "consecutive_failures": 0,
                })\
                .eq("id", webhook_id)\
                .execute()
            
        except Exception as e:
            # Log failed delivery
            supabase.table("webhook_deliveries").insert({
                "webhook_id": webhook_id,
                "event_type": event_type,
                "status": "failed",
                "error": str(e)[:500],
                "attempt": attempt + 1,
                "delivered_at": datetime.utcnow().isoformat(),
            }).execute()
            
            # Retry if under max attempts
            if attempt < self.MAX_RETRIES:
                delay = self.RETRY_DELAYS[attempt]
                logger.warning(
                    f"Webhook delivery failed (attempt {attempt + 1}/{self.MAX_RETRIES}). "
                    f"Retrying in {delay}s: {e}"
                )
                
                # Schedule retry via Celery
                from app.worker import retry_webhook_delivery
                retry_webhook_delivery.apply_async(
                    args=[webhook_id, url, secret, event_type, payload, attempt + 1],
                    countdown=delay,
                )
            else:
                # Max retries exceeded — disable webhook
                supabase.table("webhooks")\
                    .update({
                        "is_active": False,
                        "last_delivery_status": "disabled_max_retries",
                        "consecutive_failures": self.MAX_RETRIES,
                    })\
                    .eq("id", webhook_id)\
                    .execute()
                
                logger.error(
                    f"Webhook {webhook_id} disabled after {self.MAX_RETRIES} failed deliveries"
                )
```

### 3.3 Event Emission Points

```python
# Example: Emit events from existing endpoints

# backend/app/api/v1/reports.py

@router.post("/reports", status_code=201)
async def create_report(
    report: ReportCreate,
    current_user: Profile = Depends(get_current_user),
):
    # Create the report
    result = await report_service.create_report(
        reporter_id=current_user.id,
        report_data=report.model_dump(),
    )
    
    # Emit event (fire-and-forget)
    await webhook_service.dispatch_event("report.submitted", {
        "report_id": str(result["id"]),
        "reporter_id": str(current_user.id),
        "country_id": str(report.country_id),
        "program_id": str(report.program_id),
        "title": report.title,
        "submitted_at": result["created_at"],
    })
    
    return result
```

### 3.4 Webhook Database Schema

```sql
-- supabase/migrations/20260404160000_create_webhooks.sql

CREATE TABLE webhooks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(100) NOT NULL,
    url TEXT NOT NULL,
    events TEXT[] NOT NULL,  -- Array of event types
    secret TEXT NOT NULL,    -- HMAC signing secret
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    created_by UUID REFERENCES profiles(id),
    last_delivery_at TIMESTAMPTZ,
    last_delivery_status VARCHAR(50),
    consecutive_failures INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE webhook_deliveries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id UUID REFERENCES webhooks(id) ON DELETE CASCADE,
    event_type VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL,  -- delivered, failed, pending
    response_code INT,
    error TEXT,
    attempt INT DEFAULT 1,
    delivered_at TIMESTAMPTZ DEFAULT NOW(),
    payload JSONB  -- Store a copy of what was sent
);

CREATE INDEX idx_webhooks_active ON webhooks(is_active);
CREATE INDEX idx_webhook_deliveries_webhook ON webhook_deliveries(webhook_id);
CREATE INDEX idx_webhook_deliveries_status ON webhook_deliveries(status);

-- RLS
ALTER TABLE webhooks ENABLE ROW LEVEL SECURITY;
ALTER TABLE webhook_deliveries ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Webhooks managed by admins and developers" ON webhooks
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role IN ('top_administrator', 'developer')
        )
    );
```

---

## 4. Webhook Consumer Guide

```markdown
# Receiving OHAS Webhooks

## 1. Register your endpoint

POST /api/v1/webhooks
{
  "name": "My Analytics System",
  "url": "https://my-app.example.com/ohas-events",
  "events": ["report.submitted", "report.approved"],
  "description": "Receives report events for analytics dashboard"
}

Response includes a `secret` for signature verification.

## 2. Verify incoming webhooks

Every webhook includes an HMAC signature in the `X-OHAS-Signature` header.
Verify it before processing:

```python
import hmac
import hashlib

def verify_webhook(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature)
```

## 3. Handle the payload

```json
{
  "id": "evt_abc123_1712345678",
  "type": "report.submitted",
  "timestamp": "2026-04-04T12:00:00Z",
  "data": {
    "report_id": "rpt-xyz789",
    "reporter_id": "usr-abc123",
    "country_id": "ctry-def456",
    "title": "Weekly Report",
    "submitted_at": "2026-04-04T12:00:00Z"
  }
}
```

## 4. Respond quickly

Return HTTP 200 within 10 seconds. Longer responses are treated as failures.
Do your processing asynchronously.

## 5. Handle duplicates

The same event may be delivered more than once (network issues, retries).
Use the `id` field to deduplicate.
```

---

## 5. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Event format | JSON with type, timestamp, data fields | Standard, easy to parse |
| Delivery method | HTTP POST with HMAC signature | Secure, widely supported |
| Retry strategy | 5 attempts with exponential backoff (1m, 5m, 15m, 1h, 4h) | Covers transient failures without overwhelming |
| Timeout | 10 seconds per delivery | Fast enough to not block, slow enough for most endpoints |
| Idempotency | Event ID in payload — consumer must deduplicate | Standard webhook pattern |
| Max failures | 5 failures → webhook disabled | Prevents hammering dead endpoints |
| Event catalog | Namespaced (user.*, report.*, etc.) | Organized, easy to subscribe |
| Signing | HMAC-SHA256 | Industry standard, fast |
| Storage | Supabase tables for webhooks + deliveries | Same infrastructure, queryable |
| Self-consumption | OHAS frontend can subscribe to its own events | Enables real-time dashboard updates |

---

*This document should be updated when new event types are added or when the webhook delivery mechanism changes.*
