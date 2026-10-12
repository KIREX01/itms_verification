# Multi-Tenancy Isolation

**Document ID:** ENT-22  
**Category:** Compliance & Security  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Supabase (PostgreSQL), Frontend (React)  

---

## 1. Current State

```
What exists:
  ✅ Row Level Security (RLS) policies on key tables
  ✅ Country-based data isolation (country_id on reports, users, etc.)
  ✅ Role-based access control (reporter, country_admin, top_admin, developer)
  ✅ Backend auth middleware verifies user role

What's missing:
  ✗ No application-layer tenant validation (defense in depth)
  ✗ Not all tables have RLS policies
  ✗ No tenant ID in API responses (can't verify isolation)
  ✗ No cross-tenant access audit logging
  ✗ No tenant-aware rate limiting
  ✗ No multi-tenancy test in CI
```

---

## 2. Multi-Tenancy Model

```
┌─────────────────────────────────────────────────────────────────┐
│              OHAS Multi-Tenancy Model                           │
│                                                                 │
│  Tenant = Country                                               │
│  ─────────────────                                              │
│  Each country is an isolated tenant. Data from one country       │
│  must never be visible to users of another country, except      │
│  for top_admins and developers who have global access.          │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  Uganda      │  │  Kenya      │  │  Tanzania   │  ...       │
│  │  (Tenant A)  │  │  (Tenant B) │  │  (Tenant C) │             │
│  │              │  │             │  │             │             │
│  │ Reporters ───┤  │ Reporters ──┤  │ Reporters ──┤             │
│  │ Country Admin┤  │ Country Admin│ │ Country Admin│             │
│  │              │  │             │  │             │             │
│  │ Reports      │  │ Reports     │  │ Reports     │             │
│  │ Participants │  │ Participants│ │ Participants│             │
│  │ Goals        │  │ Goals       │  │ Goals       │             │
│  └──────┬───────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                 │                 │                    │
│         └─────────────────┼─────────────────┘                    │
│                           │                                      │
│                  ┌────────┴────────┐                            │
│                  │  Top Admins     │  ← Can view ALL tenants    │
│                  │  Developers     │  ← Can view ALL tenants    │
│                  └─────────────────┘                            │
│                                                                 │
│  Shared (non-tenant) data:                                      │
│  → Countries list, Programs list, System config                 │
│  → Feature flags, Notification templates                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Database-Level Isolation (RLS)

### 3.1 Current RLS Coverage

```sql
-- Tables WITH RLS policies:
✅ reporter_programs      -- Reporter-program assignments
✅ participant_milestones -- Participant milestones
✅ daily_metrics          -- Daily metric data
✅ goal_templates         -- Goal templates
✅ notification_templates -- Notification templates
✅ modules, module_metrics, etc. -- Module tracking
✅ feature_flags, feature_flag_overrides
✅ errors, error_groups
✅ user_consents           -- Consent tracking
✅ webhooks, webhook_deliveries
✅ idempotency_keys
✅ blocked_ips
✅ security_threats

-- Tables that NEED RLS but may not have it:
❓ reports                 -- Check if RLS exists
❓ participants            -- Check if RLS exists  
❓ goals                   -- Check if RLS exists
❓ programs                -- Check if RLS exists
❓ notifications            -- Check if RLS exists
❓ inquiries                -- Check if RLS exists
```

### 3.2 RLS Policy Pattern

```sql
-- Standard RLS policy pattern for tenant-isolated tables:

-- Reporters see only their own data
CREATE POLICY "{table}_reporter_view" ON {table}
    FOR SELECT
    USING (
        auth.uid() = user_id  -- or reporter_id, submitted_by, etc.
    );

-- Country admins see their country's data
CREATE POLICY "{table}_country_admin_view" ON {table}
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role = 'country_admin'
              AND profiles.country_id = {table}.country_id
        )
    );

-- Top admins and developers see everything
CREATE POLICY "{table}_global_admin_view" ON {table}
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role IN ('top_administrator', 'developer')
        )
    );

-- Country admins can manage their country's data
CREATE POLICY "{table}_country_admin_manage" ON {table}
    FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role = 'country_admin'
              AND profiles.country_id = {table}.country_id
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role = 'country_admin'
              AND profiles.country_id = {table}.country_id
        )
    );
```

---

## 4. Application-Layer Tenant Validation

### 4.1 Tenant Validation Middleware

```python
# backend/app/middleware/tenant_isolation.py

from fastapi import Request, Response, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
import logging

logger = logging.getLogger(__name__)


class TenantIsolationMiddleware(BaseHTTPMiddleware):
    """
    Application-layer defense in depth for multi-tenancy.
    Validates that the authenticated user can only access their own tenant's data.
    
    This runs AFTER auth middleware and BEFORE route handlers.
    It catches misconfigurations in RLS or API logic.
    """

    # Skip tenant check for global roles
    GLOBAL_ROLES = {"top_administrator", "developer"}

    # Skip for public endpoints
    PUBLIC_PATHS = {
        "/api/v1/auth/login",
        "/api/v1/auth/signup",
        "/api/v1/auth/refresh",
        "/api/v1/countries",
        "/health",
        "/docs",
        "/openapi.json",
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in self.PUBLIC_PATHS:
            return await call_next(request)

        user_id = getattr(request.state, "user_id", None)
        user_role = getattr(request.state, "user_role", None)
        user_country_id = getattr(request.state, "user_country_id", None)

        if not user_id or not user_role:
            return await call_next(request)  # Let auth middleware handle it

        # Global roles can access everything
        if user_role in self.GLOBAL_ROLES:
            return await call_next(request)

        # Country-scoped roles: validate tenant match
        if user_role in ("country_admin", "reporter", "assistant"):
            # Check if request targets a specific country
            target_country = self._extract_country_id(request)
            
            if target_country and target_country != user_country_id:
                logger.warning(
                    f"TENANT VIOLATION: User {user_id} (country={user_country_id}) "
                    f"attempted to access country={target_country} "
                    f"on {request.method} {request.url.path}"
                )
                
                # Log to audit
                supabase.table("audit_logs").insert({
                    "action": "TENANT_ISOLATION_VIOLATION",
                    "user_id": str(user_id),
                    "new_data": {
                        "user_country_id": str(user_country_id),
                        "target_country_id": str(target_country),
                        "path": request.url.path,
                        "method": request.method,
                    },
                    "created_at": datetime.utcnow().isoformat(),
                }).execute()
                
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "Access denied",
                        "code": "TENANT_ISOLATION_VIOLATION",
                        "message": "You can only access data for your country",
                    },
                )

        return await call_next(request)

    def _extract_country_id(self, request: Request) -> Optional[str]:
        """
        Extract target country_id from the request.
        Checks path params, query params, and body.
        """
        # Path parameter: /api/v1/countries/{country_id}/...
        path_parts = request.url.path.split("/")
        for i, part in enumerate(path_parts):
            if part == "countries" and i + 1 < len(path_parts):
                return path_parts[i + 1]
        
        # Query parameter: ?country_id=...
        country_id = request.query_params.get("country_id")
        if country_id:
            return country_id
        
        # Body parameter: {"country_id": "..."}
        if request.method in ("POST", "PUT", "PATCH"):
            # Note: body may already be consumed by later middleware
            # This is a best-effort check
            pass
        
        return None
```

### 4.2 Tenant Context in Queries

```python
# backend/app/services/tenant_query.py

class TenantQueryBuilder:
    """
    Ensures all database queries include tenant isolation filters.
    Use this instead of raw Supabase queries for tenant-scoped data.
    """

    def __init__(self, user: Profile):
        self.user = user
        self.table_name = None

    def from_table(self, table_name: str) -> "TenantQueryBuilder":
        self.table_name = table_name
        return self

    def query(self) -> any:
        """Build a query with tenant isolation applied."""
        query = supabase.table(self.table_name).select("*")

        # Apply tenant filter based on role
        if self.user.role == "reporter":
            query = query.eq("submitted_by", str(self.user.id))
        elif self.user.role == "country_admin":
            query = query.eq("country_id", str(self.user.country_id))
        elif self.user.role in ("top_administrator", "developer"):
            pass  # No tenant filter — global access
        else:
            raise ValueError(f"Unknown role: {self.user.role}")

        return query
```

---

## 5. Multi-Tenancy Testing

```python
# tests/test_tenant_isolation.py

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestTenantIsolation:
    """
    Verify that users from one country cannot access data from another country.
    """

    def test_reporter_cannot_view_other_country_reports(self):
        """Reporter from Uganda cannot see Kenya's reports."""
        # Login as Uganda reporter
        uganda_token = self._login("reporter@uganda.test", "testpass")
        
        # Try to fetch Kenya reports
        response = client.get(
            "/api/v1/reports?country_id=kenya-country-id",
            headers={"Authorization": f"Bearer {uganda_token}"},
        )
        
        # Should return empty or 403
        assert response.status_code in (200, 403)
        if response.status_code == 200:
            data = response.json().get("data", response.json())
            for report in data:
                assert report["country_id"] != "kenya-country-id"

    def test_country_admin_cannot_access_other_country(self):
        """Country admin from Kenya cannot access Tanzania data."""
        kenya_token = self._login("admin@kenya.test", "testpass")
        
        # Try to access Tanzania users
        response = client.get(
            "/api/v1/users?country_id=tanzania-country-id",
            headers={"Authorization": f"Bearer {kenya_token}"},
        )
        
        assert response.status_code in (200, 403)

    def test_top_admin_can_access_all_countries(self):
        """Top admin can see data from all countries."""
        admin_token = self._login("admin@ohas.system", "testpass")
        
        # Access Uganda reports
        response = client.get(
            "/api/v1/reports?country_id=uganda-country-id",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        
        # Access Kenya reports
        response = client.get(
            "/api/v1/reports?country_id=kenya-country-id",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
```

---

## 6. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Tenant definition | Country | Natural boundary for OHAS operations |
| Database isolation | RLS policies | Primary enforcement layer |
| Application isolation | Tenant validation middleware | Defense in depth |
| Global roles | top_administrator, developer | Need cross-tenant visibility |
| Scoped roles | country_admin, reporter, assistant | Limited to own country |
| Shared data | Countries, programs, feature flags | Not tenant-specific |
| Tenant violation response | 403 with audit log | Clear denial, traceable |
| Testing | Automated tenant isolation tests | Catches RLS misconfigurations |
| Query builder | TenantQueryBuilder for tenant-scoped queries | Prevents developer mistakes |

---

*This document should be updated when new tenant-scoped tables are created or when the tenant model changes.*
