# API Documentation & OpenAPI Standards

**Document ID:** ENT-03  
**Category:** API Design  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), All API Consumers  

---

## 1. Current State

FastAPI auto-generates Swagger UI at `/docs` and ReDoc at `/redoc`. The OpenAPI schema is automatically derived from route definitions and Pydantic models. However:

```
What exists:
  ✅ /docs (Swagger UI) — auto-generated
  ✅ /redoc — auto-generated
  ✅ /openapi.json — raw OpenAPI schema
  ✅ Some Pydantic models with descriptions

What's missing:
  ✗ No examples in request/response schemas
  ✗ No response examples in Swagger UI
  ✗ Inconsistent use of summary/description
  ✗ No tags organization (all endpoints in one group)
  ✗ No error response documentation
  ✗ No authentication examples in Swagger UI
  ✗ No API changelog
  ✗ No Postman/Insomnia collection
  ✗ No SDK generation
  ✗ No documentation CI validation
```

---

## 2. OpenAPI Documentation Standards

Every endpoint must be documented with these elements:

### 2.1 Required Elements Per Endpoint

```python
from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter()


# ── Request/Response Models with Examples ──

class ReportCreate(BaseModel):
    """
    Request body for creating a new report.
    """
    title: str = Field(
        ...,
        min_length=3,
        max_length=200,
        examples=["Weekly Program Attendance Report"],
        description="Descriptive title for the report",
    )
    content: str = Field(
        ...,
        min_length=10,
        max_length=10000,
        examples=["This week we visited 5 churches with total attendance of 247 people."],
        description="Report content and observations",
    )
    program_id: str = Field(
        ...,
        examples=["550e8400-e29b-41d4-a716-446655440000"],
        description="UUID of the program this report belongs to",
    )
    country_id: str = Field(
        ...,
        examples=["550e8400-e29b-41d4-a716-446655440001"],
        description="UUID of the country",
    )


class ReportResponse(BaseModel):
    """
    A report object returned by the API.
    """
    id: str = Field(..., examples=["rpt-abc123"])
    title: str = Field(..., examples=["Weekly Program Attendance Report"])
    content: str = Field(..., examples=["This week we visited 5 churches..."])
    submitted_by: str = Field(..., examples=["usr-def456"])
    status: str = Field(..., examples=["submitted"])
    created_at: str = Field(..., examples=["2026-04-04T12:00:00Z"])


# ── Endpoint with Full Documentation ──

@router.post(
    "/reports",
    response_model=ReportResponse,
    summary="Create a new report",
    description="""
    Submit a new program report.

    The report is created with status 'submitted' and will be
    visible to country administrators and top administrators.

    **Permissions:** Reporter, Country Admin
    """,
    response_model_exclude_none=True,
    responses={
        201: {
            "description": "Report created successfully",
            "content": {
                "application/json": {
                    "example": {
                        "data": {
                            "id": "rpt-abc123",
                            "title": "Weekly Program Attendance Report",
                            "content": "This week we visited 5 churches...",
                            "submitted_by": "usr-def456",
                            "status": "submitted",
                            "created_at": "2026-04-04T12:00:00Z",
                        },
                        "meta": {
                            "trace_id": "req_1234",
                            "timestamp": "2026-04-04T12:00:00Z",
                            "system_tier": "TIER0_HEALTHY",
                            "api_version": "v1",
                        },
                        "errors": [],
                    }
                }
            },
        },
        400: {
            "description": "Validation error",
            "content": {
                "application/json": {
                    "example": {
                        "data": None,
                        "meta": {"trace_id": "req_1234"},
                        "errors": [{
                            "code": "VALIDATION_ERROR",
                            "message": "Title must be at least 3 characters",
                            "field": "title",
                        }],
                    }
                }
            },
        },
        401: {
            "description": "Not authenticated",
            "content": {
                "application/json": {
                    "example": {
                        "data": None,
                        "meta": {"trace_id": "req_1234"},
                        "errors": [{
                            "code": "UNAUTHORIZED",
                            "message": "Could not validate credentials",
                        }],
                    }
                }
            },
        },
        403: {
            "description": "Insufficient permissions",
            "content": {
                "application/json": {
                    "example": {
                        "data": None,
                        "meta": {"trace_id": "req_1234"},
                        "errors": [{
                            "code": "FORBIDDEN",
                            "message": "You do not have permission to submit reports",
                        }],
                    }
                }
            },
        },
    },
)
async def create_report(report: ReportCreate):
    """Create a new program report."""
    # Implementation...
    pass
```

### 2.2 Tag Organization

Group endpoints by functional area using tags:

```python
# backend/app/api/v1/api.py

from fastapi import APIRouter

api_router = APIRouter()

# Import all routers
from app.api.v1 import auth, reports, users, countries, goals

# Tag metadata for Swagger UI
TAGS_METADATA = [
    {"name": "Authentication", "description": "User signup, login, token management, and password reset"},
    {"name": "Reports", "description": "Program report submission, listing, and management"},
    {"name": "Users", "description": "User profile management and administration"},
    {"name": "Countries", "description": "Country data and configuration"},
    {"name": "Goals", "description": "Goal setting, tracking, and achievement"},
    {"name": "Programs", "description": "Program management and assignment"},
    {"name": "Participants", "description": "Participant enrollment and tracking"},
    {"name": "Notifications", "description": "User notifications and alerts"},
    {"name": "System", "description": "System configuration, health checks, and feature flags"},
    {"name": "Developer", "description": "Developer tools, module tracking, and error management"},
    {"name": "Security", "description": "Security threat management, blocked IPs, and audit logs"},
]


# Apply tags to routers
api_router.include_router(
    auth.router, prefix="/auth", tags=["Authentication"]
)
api_router.include_router(
    reports.router, prefix="/reports", tags=["Reports"]
)
api_router.include_router(
    users.router, prefix="/users", tags=["Users"]
)
# ... etc
```

### 2.3 Security Scheme Documentation

Document the authentication method in OpenAPI:

```python
# backend/app/main.py

from fastapi import FastAPI
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

app = FastAPI(
    title=settings.APP_NAME,
    description="""
    # OHAS API
    
    The One Hope Africa System backend API.
    
    ## Authentication
    
    Most endpoints require authentication using Bearer tokens.
    
    ### Getting a Token
    
    1. Call `POST /api/v1/auth/login` with your email and password
    2. The response includes `access_token` and `refresh_token`
    3. Include the `access_token` in the `Authorization` header:
       ```
       Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
       ```
    
    ### Token Refresh
    
    When your access token expires, call `POST /api/v1/auth/refresh`
    with your refresh token to get a new access token.
    
    ## Rate Limiting
    
    All endpoints are rate-limited. See the `X-RateLimit-*` headers
    in responses for your current usage.
    
    ## Pagination
    
    List endpoints use cursor-based pagination. Use the `cursor`
    parameter with the value from `next_cursor` in the previous response.
    
    ## Error Format
    
    All errors follow the standard envelope format with an `errors` array.
    See the API Response Envelope documentation for details.
    """,
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
    contact={
        "name": "OHAS Developer Support",
        "email": "developer-support@ohas.system",
    },
    license_info={
        "name": "MIT",
    },
)

# Configure security scheme
bearer_scheme = HTTPBearer()
app.openapi_schema = None  # Force regeneration

def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    
    openapi_schema = app.openapi()
    
    # Add security scheme documentation
    openapi_schema["components"]["securitySchemes"] = {
        "BearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Enter your access token from the login response",
        }
    }
    
    app.openapi_schema = openapi_schema
    return app.openapi_schema

app.openapi = custom_openapi
```

---

## 3. API Changelog

Maintain a changelog document for API changes:

```markdown
# API Changelog

## v1.2.0 — 2026-06-01
### Added
- `GET /api/v1/goals` — List goals with cursor pagination
- `POST /api/v1/goals` — Create new goals
- `PUT /api/v1/goals/{id}` — Update goal progress

### Changed
- `GET /api/v1/reports` — Added cursor pagination (replaced unpaginated response)
- `GET /api/v1/users` — Added cursor pagination

## v1.1.0 — 2026-04-15
### Added
- `POST /api/v1/auth/forgot-password` — Password reset request
- `GET /api/v1/countries` — Public country list (no auth required)

### Fixed
- `POST /api/v1/auth/login` — Now returns consistent error messages

## v1.0.0 — 2026-01-01
### Added
- Initial stable API release
- Authentication, reports, users, countries, goals
```

---

## 4. Postman/Insomnia Collection Generation

### 4.1 Generate from OpenAPI

```bash
# Install OpenAPI CLI tools
pip install openapi-python-client

# Generate Postman collection from OpenAPI schema
# First, get the OpenAPI JSON
curl http://localhost:8000/openapi.json -o openapi.json

# Generate Postman collection
npx openapi-to-postmanv2 -s openapi.json -o postman-collection.json

# Or use the Postman app directly:
# 1. Import → Link → http://localhost:8000/openapi.json
# 2. Postman auto-generates the collection
```

### 4.2 Include in Repository

```
docs/api/
├── postman/
│   ├── OHAS-API-v1.postman_collection.json
│   └── OHAS-API-v1-environment.json
├── openapi/
│   ├── openapi-v1.json
│   └── openapi-v1-baseline.json  # For breaking change detection
└── changelog.md
```

---

## 5. Documentation CI Pipeline

### 5.1 Validate OpenAPI Schema on PR

```yaml
# .github/workflows/api-docs.yml

name: API Documentation Validation

on:
  pull_request:
    paths:
      - 'backend/app/api/**'
      - 'backend/app/models/**'

jobs:
  validate-openapi:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: pip install fastapi pydantic httpx
      
      - name: Generate OpenAPI schema
        run: |
          cd backend
          python -c "
          from app.main import app
          import json
          with open('../openapi-current.json', 'w') as f:
              json.dump(app.openapi(), f, indent=2)
          "
      
      - name: Check for breaking changes
        run: |
          python scripts/check_api_breaking_changes.py \
            --old docs/api/openapi/openapi-v1-baseline.json \
            --new openapi-current.json
      
      - name: Validate OpenAPI schema
        run: |
          npx @openapitools/openapi-generator-cli validate \
            -i openapi-current.json
      
      - name: Upload current schema
        if: github.ref == 'refs/heads/main'
        run: |
          cp openapi-current.json docs/api/openapi/openapi-v1.json
          cp openapi-current.json docs/api/openapi/openapi-v1-baseline.json
          git add docs/api/openapi/
          git commit -m "chore: update OpenAPI schema" || true
          git push
```

### 5.2 Auto-Generate Client SDKs

```yaml
# .github/workflows/generate-sdk.yml

name: Generate API Client SDKs

on:
  push:
    paths:
      - 'docs/api/openapi/openapi-v1.json'

jobs:
  generate-sdk:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Generate TypeScript client
        uses: openapi-generators/openapitools-generator-action@v1
        with:
          generator: typescript-axios
          openapi-file: docs/api/openapi/openapi-v1.json
          output-dir: sdk/typescript
      
      - name: Generate Python client
        uses: openapi-generators/openapitools-generator-action@v1
        with:
          generator: python
          openapi-file: docs/api/openapi/openapi-v1.json
          output-dir: sdk/python
      
      - name: Publish SDKs
        run: |
          # Push SDKs to separate repos or packages
          # ...
```

---

## 6. Documentation Checklist

Before shipping any new endpoint:

- [ ] `summary` — Short, action-oriented description ("Create a new report")
- [ ] `description` — Longer explanation with permission requirements
- [ ] `tags` — At least one tag for grouping
- [ ] `response_model` — Pydantic model for response shape
- [ ] `responses` — Document all possible response codes (200, 400, 401, 403, 404, 500)
- [ ] `examples` — At least one example per field in Pydantic models
- [ ] `Field(description=...)` — Every field has a description
- [ ] `Field(examples=[...])` — Every field has at least one example
- [ ] Error response examples in the `responses` dict
- [ ] Endpoint added to appropriate tag group
- [ ] Changelog updated
- [ ] Postman collection regenerated

---

## 7. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Documentation tool | FastAPI auto-generated Swagger UI + ReDoc | Already available, no extra infrastructure |
| Tag groups | 11 functional groups (Auth, Reports, Users, etc.) | Matches dashboard organization |
| Examples | In Pydantic `Field(examples=[...])` and `responses` dict | Visible in Swagger UI, aids understanding |
| Changelog | Markdown file in `docs/api/` | Version-controlled, linkable, searchable |
| Postman collection | Auto-generated from OpenAPI in CI | Never out of sync with actual API |
| SDK generation | TypeScript + Python, auto-generated in CI | Frontend team has typed client, no manual typing |
| Breaking change detection | CI script comparing OpenAPI schemas | Prevents accidental breaks before they merge |
| Security scheme | Bearer JWT, documented in OpenAPI | Swagger UI "Authorize" button works out of the box |

---

*This document should be updated when new tags are added, when the documentation tooling changes, or when new SDK targets are added.*
