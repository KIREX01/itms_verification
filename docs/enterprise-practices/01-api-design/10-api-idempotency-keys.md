# API Idempotency Keys

**Document ID:** ENT-10  
**Category:** API Design  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), All Mutation Endpoints  

---

## 1. Current State

OHAS has no idempotency mechanism for mutations:

```
What exists:
  ✅ request_id generated on error (error_tracking.py)
  ✅ No duplicate detection on POST requests
  ✅ No idempotency key header support

What's missing:
  ✗ No Idempotency-Key header processing
  ✗ No request deduplication
  ✗ No idempotency store (map of key → result)
  ✗ No protection against network retry storms
  ✗ No safe retry semantics for clients
```

### The Problem

```
Scenario: Reporter submits a report. Network times out before response arrives.
          HTTP client (or user) retries the same request.

Without idempotency:
  Request 1: POST /reports {title: "Weekly Report", ...}
  → Server processes, creates report #1234
  → Network timeout — client never gets response
  
  Request 2: POST /reports {title: "Weekly Report", ...} (same data)
  → Server processes, creates report #1235
  → Client receives response
  
  Result: TWO reports created from one user action.
  Metrics are inflated. Admin sees duplicate data.
```

---

## 2. Idempotency Strategy

### 2.1 Idempotency Key Header

Clients send a unique key with each mutation:

```
POST /api/v1/reports
Idempotency-Key: idem_abc123-def456-ghi789
Content-Type: application/json

{
  "title": "Weekly Attendance Report",
  "content": "...",
  "participant_count": 50
}
```

### 2.2 Idempotency Behavior

```
First request with key "idem_abc123":
  1. Server checks: has this key been seen before? → No
  2. Server processes the request normally
  3. Server stores: {key: "idem_abc123", status_code: 201, body: {...}, headers: {...}}
  4. Server returns the response to client
  5. Key expires after 24 hours

Duplicate request with same key "idem_abc123" (within 24 hours):
  1. Server checks: has this key been seen before? → Yes
  2. Server returns the STORED response (same status code, same body, same headers)
  3. Server does NOT re-process the request
  4. No duplicate report is created
```

### 2.3 Idempotent vs Non-Idempotent Endpoints

```
┌─────────────────────────────────────────────────────────────────┐
│              Idempotency Requirements by Method                 │
├──────────────────┬──────────────────────────────────────────────┤
│ HTTP Method      │ Idempotency Requirement                      │
├──────────────────┼──────────────────────────────────────────────┤
│ GET              │ Naturally idempotent. No key needed.         │
│ HEAD             │ Naturally idempotent. No key needed.         │
│ OPTIONS          │ Naturally idempotent. No key needed.         │
│ PUT              │ Naturally idempotent (replaces resource).    │
│                  │ Key optional but recommended.                │
│ DELETE           │ Naturally idempotent (deletes resource).     │
│                  │ Key optional but recommended.                │
│ PATCH            │ NOT naturally idempotent. Key recommended.   │
│ POST             │ NOT naturally idempotent. Key REQUIRED.      │
└──────────────────┴──────────────────────────────────────────────┘
```

---

## 3. Implementation

### 3.1 Idempotency Store

```python
# backend/app/services/idempotency_service.py

from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


class IdempotencyService:
    """
    Manages idempotency keys and their associated responses.
    Uses Supabase as the backing store for distributed deployments.
    """
    
    TABLE_NAME = "idempotency_keys"
    TTL_HOURS = 24  # Keys expire after 24 hours

    def __init__(self, supabase_client):
        self.supabase = supabase_client

    async def get_response(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """
        Check if an idempotency key has been seen before.
        Returns the stored response if found, None otherwise.
        """
        try:
            result = self.supabase.table(self.TABLE_NAME)\
                .select("*")\
                .eq("key", idempotency_key)\
                .eq("is_completed", True)\
                .single()\
                .execute()
            
            if result.data:
                # Update last_used_at for TTL management
                self.supabase.table(self.TABLE_NAME)\
                    .update({"last_used_at": datetime.utcnow().isoformat()})\
                    .eq("id", result.data["id"])\
                    .execute()
                
                return {
                    "status_code": result.data["response_status_code"],
                    "body": json.loads(result.data["response_body"]),
                    "headers": json.loads(result.data.get("response_headers", "{}")),
                }
            
            return None
        except Exception as e:
            logger.error(f"Error checking idempotency key: {e}")
            return None

    async def store_response(
        self,
        idempotency_key: str,
        user_id: str,
        status_code: int,
        body: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        request_method: str = "POST",
        request_path: str = "",
    ):
        """
        Store a response for an idempotency key.
        """
        try:
            self.supabase.table(self.TABLE_NAME).insert({
                "key": idempotency_key,
                "user_id": user_id,
                "request_method": request_method,
                "request_path": request_path,
                "response_status_code": status_code,
                "response_body": json.dumps(body),
                "response_headers": json.dumps(headers or {}),
                "is_completed": True,
                "created_at": datetime.utcnow().isoformat(),
                "last_used_at": datetime.utcnow().isoformat(),
                "expires_at": (
                    datetime.utcnow() + timedelta(hours=self.TTL_HOURS)
                ).isoformat(),
            }).execute()
        except Exception as e:
            logger.error(f"Error storing idempotency response: {e}")

    async def create_pending(
        self,
        idempotency_key: str,
        user_id: str,
        request_method: str,
        request_path: str,
    ) -> bool:
        """
        Mark an idempotency key as "in progress."
        Returns True if this is the first request with this key,
        False if another request is already processing it.
        """
        try:
            # Try to insert with is_completed=False
            # Use upsert to handle concurrent requests
            result = self.supabase.table(self.TABLE_NAME).insert({
                "key": idempotency_key,
                "user_id": user_id,
                "request_method": request_method,
                "request_path": request_path,
                "is_completed": False,
                "created_at": datetime.utcnow().isoformat(),
                "last_used_at": datetime.utcnow().isoformat(),
                "expires_at": (
                    datetime.utcnow() + timedelta(hours=self.TTL_HOURS)
                ).isoformat(),
            }).execute()
            
            return True
        except Exception as e:
            # Check if the key already exists as pending
            existing = await self.get_pending(idempotency_key)
            if existing:
                return False  # Another request is processing
            raise

    async def get_pending(self, idempotency_key: str) -> Optional[Dict]:
        """Check if a key is currently being processed."""
        try:
            result = self.supabase.table(self.TABLE_NAME)\
                .select("*")\
                .eq("key", idempotency_key)\
                .eq("is_completed", False)\
                .single()\
                .execute()
            
            return result.data if result.data else None
        except Exception:
            return None


# Global instance
idempotency_service: Optional[IdempotencyService] = None
```

### 3.2 Idempotency Middleware

```python
# backend/app/middleware/idempotency.py

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.idempotency_service import idempotency_service
import json
import time
import logging

logger = logging.getLogger(__name__)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """
    Handles idempotency for mutation endpoints (POST, PATCH).
    
    If a request includes an Idempotency-Key header:
      - First request: processes normally, stores response
      - Duplicate request: returns stored response without re-processing
    """

    # Only apply to mutation methods
    MUTATION_METHODS = {"POST", "PATCH"}

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip for non-mutation methods
        if request.method not in self.MUTATION_METHODS:
            return await call_next(request)

        # Check for idempotency key
        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            # No key — proceed normally (no idempotency guarantee)
            return await call_next(request)

        # Validate key format (alphanumeric + hyphens, 20-100 chars)
        if not self._is_valid_key(idempotency_key):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Invalid Idempotency-Key format",
                    "message": "Key must be 20-100 alphanumeric characters or hyphens",
                },
            )

        # Check if we have a stored response
        stored = await idempotency_service.get_response(idempotency_key)
        if stored:
            # Return stored response without re-processing
            logger.info(
                f"Idempotency hit: {idempotency_key} — returning stored response"
            )
            return JSONResponse(
                status_code=stored["status_code"],
                content=stored["body"],
                headers={
                    **stored.get("headers", {}),
                    "Idempotency-Replayed": "true",
                    "X-Idempotency-Key": idempotency_key,
                },
            )

        # Check if another request is already processing this key
        pending = await idempotency_service.get_pending(idempotency_key)
        if pending:
            # Another request is in flight — tell client to retry later
            return JSONResponse(
                status_code=409,
                content={
                    "error": "Request in progress",
                    "message": "A request with this Idempotency-Key is already being processed. Please retry later.",
                    "retry_after_seconds": 30,
                },
                headers={"Retry-After": "30"},
            )

        # Mark as pending
        user_id = getattr(request.state, "user_id", "anonymous")
        await idempotency_service.create_pending(
            idempotency_key, user_id, request.method, request.url.path
        )

        # Process the request
        response = await call_next(request)

        # Read response body
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        try:
            body_dict = json.loads(body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            body_dict = {"raw": body.decode(errors="replace")}

        # Store the response
        await idempotency_service.store_response(
            idempotency_key=idempotency_key,
            user_id=user_id,
            status_code=response.status_code,
            body=body_dict,
            headers=dict(response.headers),
            request_method=request.method,
            request_path=request.url.path,
        )

        # Return response with idempotency header
        return Response(
            content=body,
            status_code=response.status_code,
            headers={
                **dict(response.headers),
                "X-Idempotency-Key": idempotency_key,
            },
            media_type=response.media_type,
        )

    def _is_valid_key(self, key: str) -> bool:
        """Validate idempotency key format."""
        import re
        return bool(re.match(r'^[a-zA-Z0-9_-]{20,100}$', key))
```

### 3.3 Database Migration

```sql
-- supabase/migrations/20260404150000_create_idempotency_keys.sql

CREATE TABLE IF NOT EXISTS idempotency_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key TEXT NOT NULL UNIQUE,
    user_id TEXT NOT NULL,
    request_method VARCHAR(10) NOT NULL,
    request_path TEXT NOT NULL,
    response_status_code INT NOT NULL,
    response_body JSONB NOT NULL,
    response_headers JSONB DEFAULT '{}',
    is_completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);

-- Index for fast lookup
CREATE INDEX idx_idempotency_keys_key 
    ON idempotency_keys(key) WHERE is_completed = TRUE;

-- Index for pending requests
CREATE INDEX idx_idempotency_keys_pending 
    ON idempotency_keys(key) WHERE is_completed = FALSE;

-- Index for cleanup
CREATE INDEX idx_idempotency_keys_expires 
    ON idempotency_keys(expires_at);

-- RLS
ALTER TABLE idempotency_keys ENABLE ROW LEVEL SECURITY;

-- Users can only see their own idempotency keys
CREATE POLICY "Users can view own idempotency keys" 
    ON idempotency_keys FOR SELECT
    USING (auth.uid()::text = user_id);

-- System (service role) can insert/update
CREATE POLICY "System can manage idempotency keys" 
    ON idempotency_keys FOR ALL
    USING (true)
    WITH CHECK (true);
```

### 3.4 Cleanup Cron

```python
# backend/app/worker.py — Add scheduled task

@celery_app.task(name="cleanup_expired_idempotency_keys")
async def cleanup_expired_idempotency_keys():
    """Remove expired idempotency keys."""
    try:
        result = supabase.table("idempotency_keys")\
            .delete()\
            .lt("expires_at", datetime.utcnow().isoformat())\
            .execute()
        
        if result.data:
            logger.info(f"Cleaned up {len(result.data)} expired idempotency keys")
    except Exception as e:
        logger.error(f"Error cleaning up idempotency keys: {e}")
```

---

## 4. Frontend Usage

### 4.1 Generating Idempotency Keys

```javascript
// frontend/src/services/api.js

import { v4 as uuidv4 } from 'uuid';

const api = axios.create({ baseURL: API_URL });

// Request interceptor — add idempotency key to mutations
api.interceptors.request.use((config) => {
  if (['POST', 'PATCH'].includes(config.method?.toUpperCase())) {
    // Generate unique key for this request
    // Use a combination of timestamp + random UUID
    const idempotencyKey = `idem_${Date.now()}_${uuidv4().replace(/-/g, '').slice(0, 16)}`;
    config.headers['Idempotency-Key'] = idempotencyKey;
    
    // Store key for potential retry
    config._idempotencyKey = idempotencyKey;
  }
  
  return config;
});

// Response interceptor — handle idempotency conflicts
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 409) {
      // Another request with same key is in progress
      // Wait and retry with the SAME key
      const retryAfter = error.response.data?.retry_after_seconds || 30;
      console.log(`Request in progress, retrying in ${retryAfter}s...`);
      
      await new Promise(resolve => setTimeout(resolve, retryAfter * 1000));
      
      // Retry with same idempotency key
      const originalRequest = error.config;
      return api(originalRequest);
    }
    
    return Promise.reject(error);
  }
);

export default api;
```

### 4.2 Manual Idempotency for Critical Operations

```javascript
// frontend/src/services/reportService.js

import api from './api';
import { v4 as uuidv4 } from 'uuid';

class ReportService {
  /**
   * Submit a report with idempotency guarantee.
   * The same reportData + idempotencyKey will never create duplicates.
   */
  async submitReport(reportData, idempotencyKey = null) {
    // If no key provided, generate one from the report data
    // This ensures the same data always gets the same key
    if (!idempotencyKey) {
      const dataHash = await this._hashData(reportData);
      idempotencyKey = `report_${dataHash}`;
    }

    const response = await api.post('/reports', reportData, {
      headers: { 'Idempotency-Key': idempotencyKey },
    });

    return response.data;
  }

  async _hashData(data) {
    const str = JSON.stringify(data);
    const encoder = new TextEncoder();
    const hashBuffer = await crypto.subtle.digest(
      'SHA-256',
      encoder.encode(str)
    );
    return Array.from(new Uint8Array(hashBuffer))
      .map(b => b.toString(16).padStart(2, '0'))
      .join('')
      .slice(0, 32);
  }
}
```

---

## 5. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Idempotency mechanism | `Idempotency-Key` header | Stripe-inspired standard pattern |
| Key format | Alphanumeric + hyphens, 20-100 chars | Long enough to be unique, short enough for headers |
| Storage | Supabase `idempotency_keys` table | Same infrastructure, no new dependencies |
| TTL | 24 hours | Long enough for network issues, short enough to not accumulate |
| Conflict response | 409 with Retry-After | Standard, tells client exactly what to do |
| Replay header | `Idempotency-Replayed: true` | Client knows this is a stored response |
| Key generation | Client-generated (timestamp + UUID) | Server doesn't need to generate, client controls uniqueness |
| Content-based keys | Hash of request body for critical ops | Same data → same key → automatic dedup |
| Cleanup | Celery scheduled task | Automated, no manual intervention needed |
| RLS | Users see only own keys | Privacy, security |

---

*This document should be updated when new idempotent endpoints are added or when the idempotency storage mechanism changes.*
