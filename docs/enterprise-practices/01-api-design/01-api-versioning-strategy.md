# API Versioning Strategy

**Document ID:** ENT-01  
**Category:** API Design  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), External Integrations  

---

## 1. Current State

OHAS currently has a single API version: `/api/v1/`. All 20+ endpoint modules live under this prefix. There is no versioning policy, no deprecation process, and no migration path for when breaking changes are needed.

```
Current structure:
  /api/v1/auth/login
  /api/v1/auth/signup
  /api/v1/reports
  /api/v1/users
  /api/v1/countries
  /api/v1/goals
  ... (all under v1)

Missing:
  ✗ No v2 namespace
  ✗ No deprecation headers
  ✗ No sunset dates for old versions
  ✗ No migration guide format
  ✗ No version negotiation
  ✗ No policy on what constitutes a "breaking change"
```

---

## 2. Versioning Policy

### 2.1 Versioning Method: URL Path Versioning

OHAS uses **URL path versioning** — the version is part of the URL:

```
/api/v1/reports     ← current stable version
/api/v2/reports     ← future version (when breaking changes are needed)
```

**Why URL path versioning:**
- Simplest for consumers to understand and implement
- Easy to route at the gateway/proxy level
- Browser-friendly (can paste URL, works with tools like curl, Postman)
- Works naturally with FastAPI's router prefix system

**Rejected alternatives:**
- Header versioning (`Accept: application/vnd.ohas.v2+json`) — harder for browser clients, harder to debug
- Query param versioning (`/api/reports?version=2`) — violates REST conventions, hard to cache

### 2.2 What Constitutes a Breaking Change

| Change Type | Breaking? | Action Required |
|-------------|-----------|-----------------|
| Remove an endpoint | ✅ Yes | New version |
| Remove a response field | ✅ Yes | New version |
| Change a field's type (string → number) | ✅ Yes | New version |
| Change a field's name (camelCase → snake_case) | ✅ Yes | New version |
| Change required request fields | ✅ Yes | New version |
| Change authentication method | ✅ Yes | New version |
| Add a new required field to request | ✅ Yes | New version |
| Add a new optional field to response | ❌ No | Same version |
| Add a new optional request parameter | ❌ No | Same version |
| Add a new endpoint | ❌ No | Same version |
| Fix a bug (response now matches documented shape) | ❌ No | Same version (document as bugfix) |
| Improve response time / performance | ❌ No | Same version |
| Change pagination from offset to cursor | ✅ Yes | New version (behavior change) |
| Change error response format | ✅ Yes | New version |

### 2.3 Version Lifecycle

```
┌─────────────────────────────────────────────────────────────────┐
│                    API Version Lifecycle                         │
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐ │
│  │  PLANNED │───→│  ACTIVE  │───→│DEPRECATED│───→│ SUNSET   │ │
│  │          │    │          │    │          │    │          │ │
│  │ In dev   │    │ Current  │    │ Old      │    │ Removed  │ │
│  │ Not live │    │ Default  │    │ Warning  │    │ Gone     │ │
│  │          │    │          │    │ headers  │    │          │ │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘ │
│                                                                 │
│  Timeline:                                                     │
│  PLANNED → ACTIVE:     When deployed to production              │
│  ACTIVE → DEPRECATED:  When new version becomes ACTIVE          │
│  DEPRECATED → SUNSET:  Minimum 6 months after deprecation       │
│  SUNSET:               Endpoints return 410 Gone                │
│                                                                 │
│  Minimum guarantee:                                            │
│  Each version is supported for at least 12 months total         │
│  (6 months active as primary + 6 months deprecated)             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Implementation

### 3.1 Project Structure for Multiple Versions

```
backend/app/api/
├── v1/                          # Current version
│   ├── api.py                   # Router aggregation
│   ├── auth.py
│   ├── users.py
│   ├── reports.py
│   ├── countries.py
│   ├── goals.py
│   ├── programs.py
│   ├── participants.py
│   ├── notifications.py
│   ├── inquiries.py
│   ├── performance.py
│   ├── profile.py
│   ├── system_config.py
│   ├── modules.py
│   ├── feature_flags.py
│   ├── errors.py
│   ├── database_management.py
│   ├── module_tracking.py
│   ├── goal_management.py
│   ├── security_threats.py
│   └── ...
│
├── v2/                          # Future version (created when needed)
│   ├── api.py                   # Router aggregation
│   ├── auth.py                  # Only files with breaking changes
│   ├── reports.py               # Only files with breaking changes
│   └── ...
│
└── shared/                      # Code shared across versions
    ├── schemas.py               # Common Pydantic models
    ├── validators.py            # Shared validation logic
    └── utils.py                 # Shared utilities
```

### 3.2 Main App Router

```python
# backend/app/main.py

from app.api.v1.api import api_router as v1_router
# from app.api.v2.api import api_router as v2_router  # Future

app = FastAPI(
    title=settings.APP_NAME,
    description="Backend API for the One Hope Africa System (OHAS)",
    version="1.0.0",
)

# Mount v1
app.include_router(v1_router, prefix="/api/v1")

# Mount v2 (when ready)
# app.include_router(v2_router, prefix="/api/v2")
```

### 3.3 Deprecation Headers

Every response from a deprecated version includes these headers:

```
HTTP/1.1 200 OK
Deprecation: true
Sunset: Sat, 01 Oct 2026 00:00:00 GMT
Link: </api/v2/reports>; rel="successor-version"
```

Implementation via middleware:

```python
# backend/app/middleware/api_versioning.py

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime, timezone

# Registry of deprecated versions and their sunset dates
DEPRECATED_VERSIONS = {
    "v1": {
        "deprecated": False,  # Set to True when v2 launches
        "sunset_date": None,  # e.g., datetime(2026, 10, 1, tzinfo=timezone.utc)
        "successor_version": "v2",
        "migration_guide_url": "/docs/migration/v1-to-v2",
    },
}


class APIVersioningMiddleware(BaseHTTPMiddleware):
    """
    Adds versioning headers to responses from deprecated API versions.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        # Extract version from path: /api/v1/... → "v1"
        path_parts = request.url.path.strip("/").split("/")
        if len(path_parts) >= 2 and path_parts[0] == "api":
            version = path_parts[1]  # "v1", "v2", etc.

            if version in DEPRECATED_VERSIONS:
                version_info = DEPRECATED_VERSIONS[version]

                if version_info["deprecated"]:
                    response.headers["Deprecation"] = "true"

                    if version_info["sunset_date"]:
                        response.headers["Sunset"] = \
                            version_info["sunset_date"].strftime(
                                "%a, %d %b %Y %H:%M:%S GMT"
                            )

                    if version_info["successor_version"]:
                        # Add Link header pointing to successor
                        successor_path = request.url.path.replace(
                            f"/api/{version}/",
                            f"/api/{version_info['successor_version']}/",
                        )
                        response.headers["Link"] = \
                            f"<{successor_path}>; rel=\"successor-version\""

                    # Add migration guide URL
                    if version_info["migration_guide_url"]:
                        response.headers["X-Migration-Guide"] = \
                            version_info["migration_guide_url"]

        return response
```

### 3.4 Version-Aware OpenAPI Documentation

FastAPI auto-generates `/docs` (Swagger UI). With multiple versions, we need separate docs:

```python
# backend/app/main.py

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html

# Create separate OpenAPI schemas per version
app = FastAPI(
    title=settings.APP_NAME,
    docs_url=None,  # Disable default /docs
    redoc_url=None,  # Disable default /redoc
)

# Mount v1 router
app.include_router(v1_router, prefix="/api/v1")

# Mount v2 router (future)
# app.include_router(v2_router, prefix="/api/v2")


# Custom docs pages per version
@app.get("/docs/v1", include_in_schema=False)
async def docs_v1():
    return get_swagger_ui_html(
        openapi_url="/openapi/v1.json",
        title="OHAS API v1 Documentation",
    )


@app.get("/openapi/v1.json", include_in_schema=False)
async def openapi_v1():
    return app.openapi()  # Will include only v1 routes (for now)


# Future v2 docs
# @app.get("/docs/v2", include_in_schema=False)
# async def docs_v2():
#     return get_swagger_ui_html(
#         openapi_url="/openapi/v2.json",
#         title="OHAS API v2 Documentation",
#     )


@app.get("/docs", include_in_schema=False)
async def docs_index():
    """
    Index page listing all available API versions.
    """
    from fastapi.responses import HTMLResponse

    versions = [
        {"version": "v1", "status": "active", "url": "/docs/v1"},
        # {"version": "v2", "status": "planned", "url": "/docs/v2"},
    ]

    html = """
    <html>
    <head><title>OHAS API — Available Versions</title></head>
    <body style="font-family: sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px;">
        <h1>OHAS API Documentation</h1>
        <p>Select an API version to view its documentation:</p>
        <ul>
    """
    for v in versions:
        status_color = "green" if v["status"] == "active" else "gray"
        html += f"""
            <li>
                <a href="{v['url']}"><strong>{v['version']}</strong></a>
                <span style="color: {status_color}; margin-left: 8px;">
                    ({v['status']})
                </span>
            </li>
        """
    html += """
        </ul>
    </body>
    </html>
    """
    return HTMLResponse(content=html)
```

---

## 4. Migration Process: v1 → v2

When v2 is needed, follow this process:

### Phase 1: Planning (2-4 weeks before v2 development)

```
1. Identify all breaking changes needed
2. Document each change with rationale
3. Create migration guide draft (v1 → v2)
4. Estimate effort and create v2 development plan
5. Announce to all known API consumers (internal teams, external integrations)
```

### Phase 2: Development (v2 built alongside v1)

```
1. Create backend/app/api/v2/ directory
2. Copy v1 files that need changes into v2/
3. Apply breaking changes to v2 copies
4. Keep v1 files UNTOUCHED (they must keep working)
5. Share common code via backend/app/api/shared/
6. Write migration guide (finalized)
7. Test v2 thoroughly (automated tests + manual QA)
```

### Phase 3: Launch (v2 goes active, v1 starts deprecation clock)

```
1. Deploy v2 to production alongside v1
2. Update DEPRECATED_VERSIONS["v1"]["deprecated"] = True
3. Set sunset_date to 6 months from now
4. Update /docs index to show both versions
5. Send notification to all API consumers
6. Monitor v2 adoption via analytics
```

### Phase 4: Monitoring (6 months)

```
1. Track v1 vs v2 request volume
2. Reach out to teams still on v1
3. Offer migration assistance
4. Extend sunset if critical consumers need more time
```

### Phase 5: Sunset (after sunset_date)

```
1. v1 endpoints return 410 Gone:
   {
     "error": "API version v1 has been sunset",
     "message": "Please migrate to /api/v2/. See migration guide: /docs/migration/v1-to-v2",
     "sunset_date": "2026-10-01T00:00:00Z",
     "successor_version": "/api/v2/"
   }
2. Keep v1 code in repo for reference (tagged, not deleted)
3. Remove v1 router from main.py
4. Update /docs index to remove v1
```

---

## 5. Migration Guide Template

When creating a migration guide for v1 → v2, use this structure:

```markdown
# Migration Guide: v1 → v2

## Overview
- Version v2 released: <date>
- Version v1 deprecated: <date>
- Version v1 sunset: <date>
- Breaking changes: <count>
- Non-breaking additions: <count>

## Breaking Changes

### 1. Changed: Report submission response format
- v1: Returns bare report object
- v2: Returns `{data: report, meta: {trace_id, created_at}}`
- Migration: Update response parsing in frontend code
- Affected endpoints: `POST /api/v1/reports` → `POST /api/v2/reports`

### 2. Changed: Authentication now requires httpOnly cookies
- v1: Bearer token in Authorization header
- v2: httpOnly cookie (set on login)
- Migration: Remove localStorage token storage, set `withCredentials: true`
- Affected endpoints: All

### 3. Removed: Offset-based pagination
- v1: `GET /reports?page=1&limit=50`
- v2: `GET /reports?limit=50&cursor=<id>`
- Migration: Switch to cursor-based pagination
- Affected endpoints: All list endpoints

## New Features (v2 only)
- Feature A: ...
- Feature B: ...

## Non-Breaking Changes (available in both v1 and v2)
- Change A: ...

## Code Examples

### Before (v1)
```javascript
const response = await fetch('/api/v1/reports?page=2');
const reports = await response.json(); // array
```

### After (v2)
```javascript
const response = await fetch('/api/v2/reports?limit=50&cursor=abc123', {
    credentials: 'include'  // httpOnly cookies
});
const { data: reports, meta } = await response.json();
```

## Support
- Migration questions: developer-support@ohas.system
- Bug reports: GitHub Issues
- Documentation: /docs/v2
```

---

## 6. Future Version Preview: v2 Planned Changes

These are the breaking changes anticipated for v2:

| Change | v1 Behavior | v2 Behavior | Reason |
|--------|------------|-------------|--------|
| Response envelope | Bare objects/arrays | `{data, meta, errors}` | Consistency, observability |
| Auth token delivery | localStorage + Bearer header | httpOnly cookies | Security (XSS prevention) |
| Pagination | Offset-based (future) | Cursor-based | Performance at scale |
| Error format | `{detail: "..."}` | `{errors: [{code, message, field}]}` | Structured error handling |
| Report submission | Synchronous | Async (202 Accepted + polling) | AP architecture alignment |
| Date format | Mixed (ISO strings, timestamps) | ISO 8601 only | Consistency |
| Role field in profile | `top_admin`, `country_admin`, etc. | Standardized role names | Clarity |
| Bulk operations | Not standardized | Standard bulk API with atomic flag | Data integrity |

---

## 7. Configuration

```python
# backend/app/core/api_versioning.py

from typing import Dict, Optional
from datetime import datetime, timezone
from pydantic import BaseModel

class APIVersionInfo(BaseModel):
    """Metadata about an API version."""
    version: str
    status: str  # "planned", "active", "deprecated", "sunset"
    released_at: Optional[datetime] = None
    deprecated_at: Optional[datetime] = None
    sunset_at: Optional[datetime] = None
    successor_version: Optional[str] = None
    migration_guide_url: Optional[str] = None
    description: str = ""


# Version registry
API_VERSIONS: Dict[str, APIVersionInfo] = {
    "v1": APIVersionInfo(
        version="v1",
        status="active",
        released_at=datetime(2026, 1, 1, tzinfo=timezone.utc),  # Approximate
        description="Initial stable API version. Includes auth, reports, users, goals, programs.",
    ),
    # "v2": APIVersionInfo(
    #     version="v2",
    #     status="planned",
    #     successor_version=None,
    #     description="Next major version. Will include security improvements, consistent response format.",
    # ),
}


def get_active_versions() -> list:
    """Return list of active (non-sunset) API versions."""
    return [
        v for v in API_VERSIONS.values()
        if v.status != "sunset"
    ]


def get_current_version() -> str:
    """Return the current recommended API version."""
    for v in API_VERSIONS.values():
        if v.status == "active":
            return v.version
    return "v1"  # Fallback


def is_deprecated(version: str) -> bool:
    """Check if a version is deprecated."""
    info = API_VERSIONS.get(version)
    return info is not None and info.status in ("deprecated", "sunset")
```

---

## 8. Enforcement: Preventing Accidental Breaking Changes

Add a CI check that validates no breaking changes are introduced to existing versions:

```python
# scripts/check_api_breaking_changes.py
# Run in CI before merging to main

"""
Compares the current OpenAPI schema against the baseline (last released version).
Flags any breaking changes:
  - Removed endpoints
  - Removed response fields
  - Changed field types
  - Changed required fields
  - New required request fields

Usage:
  python scripts/check_api_breaking_changes.py \
    --old openapi_v1_baseline.json \
    --new openapi_v1_current.json
"""

import json
import argparse
import sys

def detect_breaking_changes(old_schema, new_schema):
    breaking = []

    old_paths = old_schema.get("paths", {})
    new_paths = new_schema.get("paths", {})

    # Check for removed endpoints
    for path in old_paths:
        if path not in new_paths:
            breaking.append(f"REMOVED endpoint: {path}")

    # Check for changed response schemas
    for path, methods in new_paths.items():
        if path not in old_paths:
            continue  # New endpoint, not breaking

        for method, spec in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue

            old_method = old_paths.get(path, {}).get(method, {})
            old_responses = old_method.get("responses", {})
            new_responses = spec.get("responses", {})

            # Check 200 response schema
            old_200 = old_responses.get("200", {})
            new_200 = new_responses.get("200", {})

            old_schema_ref = old_200.get("content", {}).get(
                "application/json", {}
            ).get("schema", {})
            new_schema_ref = new_200.get("content", {}).get(
                "application/json", {}
            ).get("schema", {})

            if old_schema_ref != new_schema_ref and old_schema_ref:
                breaking.append(
                    f"CHANGED response schema for {method.upper()} {path}: "
                    f"{old_schema_ref} → {new_schema_ref}"
                )

    return breaking


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--old", required=True, help="Baseline OpenAPI JSON")
    parser.add_argument("--new", required=True, help="Current OpenAPI JSON")
    args = parser.parse_args()

    with open(args.old) as f:
        old = json.load(f)
    with open(args.new) as f:
        new = json.load(f)

    breaking = detect_breaking_changes(old, new)

    if breaking:
        print(f"❌ {len(breaking)} breaking change(s) detected:")
        for change in breaking:
            print(f"  - {change}")
        print("\nTo introduce breaking changes, create a new API version (v2).")
        sys.exit(1)
    else:
        print("✅ No breaking changes detected.")
        sys.exit(0)
```

---

## 9. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Versioning method | URL path (`/api/v1/`) | Simplest, most discoverable, gateway-friendly |
| Number of supported versions | 2 (current + previous) | Balance between innovation and maintenance burden |
| Deprecation period | 6 months minimum | Enough time for consumers to migrate |
| Total version lifespan | 12 months minimum | 6 months active + 6 months deprecated |
| Breaking change detection | CI script comparing OpenAPI schemas | Automated, prevents accidental breaks |
| Migration guides | Markdown in `/docs/migration/` | Searchable, version-controlled, linkable |
| Documentation | Separate Swagger UI per version (`/docs/v1`, `/docs/v2`) | Clear separation, no confusion |
| Sunset response | HTTP 410 Gone with migration link | Standard, informative, not a 500 error |

---

## 10. Checklist: When Creating v2

- [ ] Identify all breaking changes and document them
- [ ] Create `backend/app/api/v2/` directory structure
- [ ] Create `backend/app/api/shared/` for common code
- [ ] Copy and modify only the endpoints with breaking changes
- [ ] Write comprehensive migration guide
- [ ] Update `API_VERSIONS` registry
- [ ] Configure deprecation middleware
- [ ] Add v2 OpenAPI docs endpoint (`/docs/v2`)
- [ ] Add breaking change CI check
- [ ] Test v2 with existing frontend (should not break v1 frontend)
- [ ] Notify all known API consumers
- [ ] Update this document with v2 details

---

*This document should be updated whenever a new API version is created or when an existing version's lifecycle status changes.*
