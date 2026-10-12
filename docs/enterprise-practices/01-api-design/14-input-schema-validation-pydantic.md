# Input Schema Validation with Pydantic

**Document ID:** ENT-14  
**Category:** API Design  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), All API Endpoints  

---

## 1. Current State

OHAS has inconsistent input validation — some endpoints use Pydantic models, others use manual dict access:

```python
# ✅ GOOD: auth.py uses Pydantic models
class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: str = "reporter"
    country_id: UUID

@router.post("/signup", response_model=Token)
async def signup(signup_data: SignupRequest):
    # Type-safe access
    email = signup_data.email
    password = signup_data.password

# ❌ BAD: profile.py uses Dict[str, Any]
@router.post("/users/me/change-password")
async def change_password(
    password_data: Dict[str, str],  # No schema!
    current_user: Profile = Depends(get_current_user),
):
    current_password = password_data.get('current_password')  # No validation
    new_password = password_data.get('new_password')
    # What if these are None? What if extra fields are sent?

# ❌ BAD: users.py uses manual dict access
@router.post("/users/reporters")
async def create_reporter(
    request: Request,
    current_user: Profile = Depends(check_role(["country_admin"])),
):
    reporter_data = await request.json()  # Raw dict, no validation
    if not reporter_data.get("full_name"):  # Manual validation
        raise HTTPException(400, "full_name is required")
```

### Problems

| Problem | Impact | Example |
|---------|--------|---------|
| No input schema | Extra fields silently accepted (mass assignment risk) | `{"role": "top_admin"}` sent by reporter |
| Manual validation | Inconsistent error messages | "field required" vs "Missing required field: X" |
| Dict[str, Any] | No type checking | `password: 123` (int instead of str) accepted |
| Missing validators | No email format check, no length limits | `"email": "not-an-email"` passes through |
| No documentation | Swagger UI shows no request body schema | Users can't see what fields are needed |

---

## 2. Pydantic Validation Standards

### 2.1 Every Endpoint Must Have Typed Models

```python
# backend/app/models/report.py

from pydantic import BaseModel, Field, field_validator
from typing import Optional
from uuid import UUID


class ReportCreate(BaseModel):
    """
    Request body for creating a new report.
    All fields validated before reaching handler.
    """
    title: str = Field(
        ...,
        min_length=3,
        max_length=200,
        description="Descriptive title for the report",
    )
    content: str = Field(
        ...,
        min_length=10,
        max_length=10000,
        description="Report content and observations",
    )
    program_id: UUID = Field(
        ...,
        description="UUID of the program this report belongs to",
    )
    country_id: UUID = Field(
        ...,
        description="UUID of the country",
    )
    participant_count: int = Field(
        ...,
        ge=0,
        le=10000,
        description="Number of participants in the program",
    )
    notes: Optional[str] = Field(
        None,
        max_length=1000,
        description="Optional additional notes",
    )

    @field_validator("title")
    @classmethod
    def title_must_not_be_all_caps(cls, v: str) -> str:
        if v.isupper() and len(v) > 10:
            raise ValueError("Title should not be ALL CAPS")
        return v.strip()

    @field_validator("content")
    @classmethod
    def content_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Content cannot be only whitespace")
        return v


class ReportUpdate(BaseModel):
    """
    Request body for updating a report.
    All fields optional (partial update).
    """
    title: Optional[str] = Field(
        None,
        min_length=3,
        max_length=200,
    )
    content: Optional[str] = Field(
        None,
        min_length=10,
        max_length=10000,
    )
    participant_count: Optional[int] = Field(
        None,
        ge=0,
        le=10000,
    )
    notes: Optional[str] = Field(
        None,
        max_length=1000,
    )

    @field_validator("title", "content", "notes")
    @classmethod
    def strip_whitespace(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            return v.strip()
        return v
```

### 2.2 Endpoint Usage

```python
# backend/app/api/v1/reports.py

from fastapi import APIRouter, Depends, HTTPException, status
from app.models.report import ReportCreate, ReportUpdate
from app.api.deps import get_current_user
from app.models.profile import Profile

router = APIRouter()


@router.post("/reports", status_code=status.HTTP_201_CREATED)
async def create_report(
    report: ReportCreate,  # ← Pydantic validates automatically
    current_user: Profile = Depends(get_current_user),
):
    """
    Create a new program report.
    
    - **title**: Report title (3-200 chars)
    - **content**: Detailed report content (10-10000 chars)
    - **program_id**: UUID of the associated program
    - **country_id**: UUID of the country
    - **participant_count**: Number of participants (0-10000)
    - **notes**: Optional additional notes (max 1000 chars)
    """
    # At this point, report.title, report.content etc. are
    # guaranteed to be valid. No manual checks needed.
    
    result = await report_service.create_report(
        reporter_id=current_user.id,
        report_data=report.model_dump(),
    )
    
    return result


@router.patch("/reports/{report_id}")
async def update_report(
    report_id: UUID,
    report: ReportUpdate,  # ← Partial update, all fields optional
    current_user: Profile = Depends(get_current_user),
):
    """
    Update an existing report.
    Only provided fields will be updated.
    """
    # Only non-None fields are included
    update_data = report.model_dump(exclude_unset=True)
    
    if not update_data:
        raise HTTPException(400, "No fields provided for update")
    
    result = await report_service.update_report(
        report_id=report_id,
        update_data=update_data,
        user_id=current_user.id,
    )
    
    return result
```

### 2.3 Automatic Validation Errors

FastAPI automatically returns structured errors when Pydantic validation fails:

```json
// Request: {"title": "AB", "content": "short", "program_id": "not-a-uuid", ...}
// Response: 422 Unprocessable Entity

{
  "detail": [
    {
      "type": "string_too_short",
      "loc": ["body", "title"],
      "msg": "String should have at least 3 characters",
      "input": "AB"
    },
    {
      "type": "string_too_short",
      "loc": ["body", "content"],
      "msg": "String should have at least 10 characters",
      "input": "short"
    },
    {
      "type": "uuid_parsing",
      "loc": ["body", "program_id"],
      "msg": "Input should be a valid UUID",
      "input": "not-a-uuid"
    }
  ]
}
```

---

## 3. Common Pydantic Patterns

### 3.1 Email Validation

```python
from pydantic import EmailStr, field_validator

class UserAuth(BaseModel):
    email: EmailStr  # Built-in email format validation
    
    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.lower()
```

### 3.2 Phone Number Validation

```python
import re

class ContactInfo(BaseModel):
    phone: Optional[str] = Field(None, max_length=20)
    
    @field_validator("phone")
    @classmethod
    def validate_phone(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        # E.164 format: +[country code][number]
        if not re.match(r'^\+[1-9]\d{1,14}$', v):
            raise ValueError(
                "Phone must be in E.164 format (e.g., +256700123456)"
            )
        return v
```

### 3.3 Password Validation

```python
class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=8, max_length=128)
    
    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain an uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain a lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain a digit")
        if not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in v):
            raise ValueError("Password must contain a special character")
        return v
```

### 3.4 Date Range Validation

```python
from datetime import date

class ReportDateRange(BaseModel):
    start_date: date
    end_date: date
    
    @model_validator(mode="after")
    def check_date_order(self) -> "ReportDateRange":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be after start_date")
        if self.start_date > date.today():
            raise ValueError("start_date cannot be in the future")
        return self
```

### 3.5 Preventing Mass Assignment

```python
class UserUpdate(BaseModel):
    """
    Fields that a user can update on their OWN profile.
    Note: role, country_id, is_active are NOT included.
    Users cannot change these through this endpoint.
    """
    full_name: Optional[str] = Field(None, min_length=2, max_length=200)
    phone: Optional[str] = Field(None, max_length=20)
    avatar_url: Optional[str] = Field(None, max_length=500)


class AdminUserUpdate(BaseModel):
    """
    Fields that an admin can update on ANY user's profile.
    Includes role and active status.
    """
    full_name: Optional[str] = Field(None, min_length=2, max_length=200)
    phone: Optional[str] = Field(None, max_length=20)
    avatar_url: Optional[str] = Field(None, max_length=500)
    is_active: Optional[bool] = None
    # Note: role is STILL not included — changing role requires
    # a separate endpoint with additional authorization
```

---

## 4. Migration Plan: Convert All Endpoints

### Endpoints That Need Pydantic Models

| Endpoint | Current Input | Status |
|----------|--------------|--------|
| `POST /auth/signup` | `SignupRequest` (Pydantic) | ✅ Done |
| `POST /auth/login` | `OAuth2PasswordRequestForm` (FastAPI) | ✅ Done |
| `POST /auth/forgot-password` | `ForgotPasswordRequest` (Pydantic) | ✅ Done |
| `POST /auth/refresh` | `RefreshTokenRequest` (Pydantic) | ✅ Done |
| `POST /users/reporters` | `await request.json()` (raw dict) | ❌ Needs model |
| `POST /users/me/change-password` | `Dict[str, str]` | ❌ Needs model |
| `PUT /profile/users/me` | Partial model — needs review | ⚠️ Review |
| `POST /reports` | Needs model — check current impl | ❌ Check |
| `PUT /reports/{id}` | Needs model | ❌ Needs model |
| `POST /goals` | Needs model | ❌ Needs model |
| `PUT /goals/{id}` | Needs model | ❌ Needs model |
| `POST /participants` | Needs model | ❌ Needs model |
| `PUT /system-config` | `SystemConfigUpdate` exists — check | ⚠️ Review |
| `POST /inquiries` | Needs model | ❌ Needs model |
| `POST /bulk/*` | Needs model | ❌ Needs model |

### Migration Priority

| Priority | Endpoints | Effort | Reason |
|----------|-----------|--------|--------|
| 🔴 P0 | `/users/reporters`, `/users/me/change-password` | 1 hr | User creation and password change — security critical |
| 🟠 P1 | `/reports`, `/goals`, `/participants` | 3 hrs | Core data submission — validation ensures data quality |
| 🟡 P2 | `/inquiries`, `/bulk/*` | 2 hrs | Secondary features |
| 🟢 P3 | Remaining PUT/PATCH endpoints | 2 hrs | Polish and completeness |

---

## 5. Testing Validation

```python
# tests/test_validation.py

import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


class TestReportValidation:
    """Test report submission validation."""

    def test_valid_report(self, auth_token):
        response = client.post(
            "/api/v1/reports",
            json={
                "title": "Weekly Attendance Report",
                "content": "This week we had great participation...",
                "program_id": "550e8400-e29b-41d4-a716-446655440000",
                "country_id": "550e8400-e29b-41d4-a716-446655440001",
                "participant_count": 50,
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 201

    def test_title_too_short(self, auth_token):
        response = client.post(
            "/api/v1/reports",
            json={
                "title": "AB",  # Too short (min 3)
                "content": "This week we had great participation...",
                "program_id": "550e8400-e29b-41d4-a716-446655440000",
                "country_id": "550e8400-e29b-41d4-a716-446655440001",
                "participant_count": 50,
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 422
        assert any(
            "title" in str(err.get("loc", []))
            for err in response.json().get("detail", [])
        )

    def test_negative_participant_count(self, auth_token):
        response = client.post(
            "/api/v1/reports",
            json={
                "title": "Weekly Attendance Report",
                "content": "This week we had great participation...",
                "program_id": "550e8400-e29b-41d4-a716-446655440000",
                "country_id": "550e8400-e29b-41d4-a716-446655440001",
                "participant_count": -5,  # Negative not allowed
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        assert response.status_code == 422

    def test_extra_fields_rejected(self, auth_token):
        """Extra fields should be ignored or rejected."""
        response = client.post(
            "/api/v1/reports",
            json={
                "title": "Weekly Report",
                "content": "Great week...",
                "program_id": "550e8400-e29b-41d4-a716-446655440000",
                "country_id": "550e8400-e29b-41d4-a716-446655440001",
                "participant_count": 50,
                "role": "top_admin",  # Mass assignment attempt
                "is_active": False,    # Should be rejected
            },
            headers={"Authorization": f"Bearer {auth_token}"},
        )
        # Pydantic v2 ignores extra fields by default (extra="ignore")
        # Or use extra="forbid" to reject them with 422
        assert response.status_code == 201
```

---

## 6. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Validation library | Pydantic v2 (built into FastAPI) | No extra dependencies, auto-generates OpenAPI schema |
| Extra fields | `extra="ignore"` (default) | Forward-compatible — new fields in requests don't break old servers |
| Field defaults | `...` (required) for POST, `None` (optional) for PATCH | POST creates full objects, PATCH updates partial |
| Validation location | Pydantic model, not in handler | Separation of concerns, auto-documented, testable |
| Custom validators | `@field_validator` for single fields, `@model_validator` for cross-field | Pydantic v2 patterns |
| Error format | FastAPI default 422 (list of errors with loc, msg, type) | Standard, matches OpenAPI spec |
| Password rules | Enforced in Pydantic model, not just frontend | Frontend can be bypassed, backend is source of truth |
| Email normalization | Lowercase in validator | Consistent storage, case-insensitive comparison |
| Mass assignment prevention | Separate models per role/endpoint (UserUpdate vs AdminUserUpdate) | Different permissions, different allowed fields |

---

*This document should be updated when new validation patterns are adopted or when Pydantic version is upgraded.*
