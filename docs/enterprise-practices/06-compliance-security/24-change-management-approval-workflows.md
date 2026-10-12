# Change Management & Approval Workflows

**Document ID:** ENT-24  
**Category:** Compliance & Security  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), All Administrative Operations  

---

## 1. Current State

```
What exists:
  ✅ Role-based access control (reporter → top_admin)
  ✅ Top admins and developers can perform any admin action
  ✅ Audit logging tracks who did what and when

What's missing:
  ✗ No approval workflow for sensitive actions
  ✗ Single admin can create/delete users without oversight
  ✗ No dual-approval for system config changes
  ✗ No change request tracking
  ✗ No change approval history
  ✗ No rollback of admin actions
```

---

## 2. Change Classification

```
┌─────────────────────────────────────────────────────────────────┐
│              OHAS Change Classification                          │
├──────────────┬──────────────────────────────────────────────────┤
│              │                                                  │
│ AUTO-APPROVED│ Actions any authorized user can perform          │
│ (No review)  │                                                  │
│              │ → Submit a report                                │
│              │ → View own profile and data                      │
│              │ → Update own password                            │
│              │ → View dashboard                                 │
│              │                                                  │
├──────────────┼──────────────────────────────────────────────────┤
│              │                                                  │
│ SINGLE-APPR  │ Actions an admin can perform alone               │
│ (1 admin)    │                                                  │
│              │ → Create a reporter account                       │
│              │ → Assign programs to reporters                   │
│              │ → Create/edit goals                              │
│              │ → Edit participant data                          │
│              │ → Send notifications                             │
│              │ → Export data                                     │
│              │                                                  │
├──────────────┼──────────────────────────────────────────────────┤
│              │                                                  │
│ DUAL-APPROVAL│ Sensitive actions requiring 2 admins             │
│ (2 admins)   │                                                  │
│              │ → Create a country admin account                 │
│              │ → Delete any user account                        │
│              │ → Change system configuration (SMTP, etc.)       │
│              │ → Enable/disable feature flags                   │
│              │ → Bulk import data (>100 records)                │
│              │ → Change RLS policies                            │
│              │                                                  │
├──────────────┼──────────────────────────────────────────────────┤
│              │                                                  │
│ TOP-ADMIN    │ Highest-risk actions requiring top admin         │
│ ONLY         │                                                  │
│              │ → Create/delete top admin accounts               │
│              │ → Change developer access                        │
│              │ → Modify country boundaries/assignments          │
│              │ → Emergency system-wide changes                  │
│              │ → Data breach response actions                   │
│              │                                                  │
└──────────────┴──────────────────────────────────────────────────┘
```

---

## 3. Approval Workflow Implementation

### 3.1 Change Request Table

```sql
-- supabase/migrations/20260404190000_change_requests.sql

CREATE TYPE change_request_status AS ENUM (
    'pending',
    'approved',
    'rejected',
    'executed',
    'cancelled',
    'expired'
);

CREATE TABLE change_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title VARCHAR(200) NOT NULL,
    description TEXT NOT NULL,
    change_type VARCHAR(50) NOT NULL,  -- user_create, user_delete, config_change, etc.
    requested_by UUID NOT NULL REFERENCES profiles(id),
    required_approvals INT DEFAULT 1,  -- 1 for single, 2 for dual
    current_approvals INT DEFAULT 0,
    status change_request_status DEFAULT 'pending',
    target_resource_type VARCHAR(50),  -- users, system_config, feature_flags, etc.
    target_resource_id UUID,
    proposed_data JSONB,  -- What the change would do
    executed_by UUID REFERENCES profiles(id),
    executed_at TIMESTAMPTZ,
    execution_result JSONB,
    expires_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '7 days'),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE change_request_approvals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    change_request_id UUID NOT NULL REFERENCES change_requests(id) ON DELETE CASCADE,
    approved_by UUID NOT NULL REFERENCES profiles(id),
    approval_comment VARCHAR(500),
    approved_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(change_request_id, approved_by)  -- One approval per person per request
);

-- Indexes
CREATE INDEX idx_change_requests_status ON change_requests(status, created_at DESC);
CREATE INDEX idx_change_requests_type ON change_requests(change_type);
CREATE INDEX idx_change_requests_requested_by ON change_requests(requested_by);
CREATE INDEX idx_change_request_approvals_request ON change_request_approvals(change_request_id);

-- RLS
ALTER TABLE change_requests ENABLE ROW LEVEL SECURITY;
ALTER TABLE change_request_approvals ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Change requests viewable by admins" ON change_requests
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role IN ('top_administrator', 'country_admin', 'developer')
        )
    );

CREATE POLICY "Change requests manageable by admins" ON change_requests
    FOR ALL
    USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role IN ('top_administrator', 'country_admin', 'developer')
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role IN ('top_administrator', 'country_admin', 'developer')
        )
    );
```

### 3.2 Change Request API

```python
# backend/app/api/v1/change_requests.py

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Any
from uuid import UUID

router = APIRouter()


class ChangeRequestCreate(BaseModel):
    title: str = Field(..., min_length=5, max_length=200)
    description: str = Field(..., min_length=20, max_length=5000)
    change_type: str = Field(...)  # user_create, user_delete, config_change, etc.
    required_approvals: int = Field(default=1, ge=1, le=3)
    target_resource_type: Optional[str] = None
    target_resource_id: Optional[UUID] = None
    proposed_data: Optional[dict] = None


class ApprovalRequest(BaseModel):
    comment: Optional[str] = Field(None, max_length=500)


@router.post("/change-requests", status_code=201)
async def create_change_request(
    request: ChangeRequestCreate,
    current_user: Profile = Depends(get_current_user),
):
    """
    Create a change request for a sensitive action.
    The request must be approved by the required number of admins
    before it can be executed.
    """
    # Validate change type
    valid_types = [
        "user_create", "user_delete", "user_role_change",
        "config_change", "feature_flag_change", "bulk_import",
        "rls_policy_change", "country_change",
    ]
    if request.change_type not in valid_types:
        raise HTTPException(400, f"Invalid change type. Must be one of: {valid_types}")

    # Auto-approve single-approval requests for non-sensitive changes
    if request.required_approvals == 1 and request.change_type in ("user_create",):
        # Execute immediately — no approval needed
        result = await execute_change(request, current_user)
        return {
            "message": "Change executed immediately (no approval required)",
            "change_request_id": result["change_request_id"],
            "result": result,
        }

    # Create pending change request
    change = supabase.table("change_requests").insert({
        "title": request.title,
        "description": request.description,
        "change_type": request.change_type,
        "requested_by": str(current_user.id),
        "required_approvals": request.required_approvals,
        "current_approvals": 0,
        "status": "pending",
        "target_resource_type": request.target_resource_type,
        "target_resource_id": str(request.target_resource_id) if request.target_resource_id else None,
        "proposed_data": request.proposed_data,
        "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat(),
    }).execute()

    # Notify potential approvers
    await notification_service.send_alert(
        title=f"Change Request: {request.title}",
        message=f"{current_user.full_name} requests approval for: {request.description}",
        recipients=["top_administrator"],
        details={"change_request_id": str(change.data[0]["id"])},
    )

    return change.data[0]


@router.post("/change-requests/{change_id}/approve")
async def approve_change_request(
    change_id: UUID,
    approval: ApprovalRequest,
    current_user: Profile = Depends(get_current_user),
):
    """
    Approve a pending change request.
    """
    # Get the change request
    change = supabase.table("change_requests")\
        .select("*")\
        .eq("id", str(change_id))\
        .single()\
        .execute()
    
    if not change.data:
        raise HTTPException(404, "Change request not found")
    
    if change.data["status"] != "pending":
        raise HTTPException(400, f"Change request is {change.data['status']}, not pending")
    
    if change.data["expires_at"] and datetime.fromisoformat(change.data["expires_at"]) < datetime.utcnow():
        # Expire it
        supabase.table("change_requests")\
            .update({"status": "expired"})\
            .eq("id", str(change_id))\
            .execute()
        raise HTTPException(410, "Change request has expired")

    # Record approval
    supabase.table("change_request_approvals").insert({
        "change_request_id": str(change_id),
        "approved_by": str(current_user.id),
        "approval_comment": approval.comment,
    }).execute()

    # Increment approval count
    new_count = change.data["current_approvals"] + 1
    supabase.table("change_requests")\
        .update({"current_approvals": new_count, "updated_at": datetime.utcnow().isoformat()})\
        .eq("id", str(change_id))\
        .execute()

    # Check if enough approvals
    if new_count >= change.data["required_approvals"]:
        supabase.table("change_requests")\
            .update({"status": "approved", "updated_at": datetime.utcnow().isoformat()})\
            .eq("id", str(change_id))\
            .execute()
        
        return {
            "message": "Change request approved and ready for execution",
            "approvals": new_count,
            "required": change.data["required_approvals"],
        }

    return {
        "message": f"Approval recorded ({new_count}/{change.data['required_approvals']})",
        "approvals": new_count,
        "required": change.data["required_approvals"],
    }


@router.post("/change-requests/{change_id}/execute")
async def execute_change_request(
    change_id: UUID,
    current_user: Profile = Depends(check_role(["top_administrator"])),
):
    """
    Execute an approved change request.
    Only top administrators can execute changes.
    """
    change = supabase.table("change_requests")\
        .select("*")\
        .eq("id", str(change_id))\
        .single()\
        .execute()
    
    if not change.data:
        raise HTTPException(404, "Change request not found")
    
    if change.data["status"] != "approved":
        raise HTTPException(400, f"Change request status is '{change.data['status']}', must be 'approved'")
    
    # Execute the change
    result = await execute_change(change.data, current_user)
    
    # Update change request
    supabase.table("change_requests")\
        .update({
            "status": "executed",
            "executed_by": str(current_user.id),
            "executed_at": datetime.utcnow().isoformat(),
            "execution_result": result,
        })\
        .eq("id", str(change_id))\
        .execute()
    
    return {
        "message": "Change executed successfully",
        "result": result,
    }


async def execute_change(change: dict, executor: Profile) -> dict:
    """
    Execute the actual change based on change type.
    """
    change_type = change["change_type"]
    proposed_data = change.get("proposed_data", {})
    
    if change_type == "user_create":
        # Create the user
        result = await user_service.create_user(
            email=proposed_data["email"],
            full_name=proposed_data["full_name"],
            role=proposed_data["role"],
            country_id=proposed_data.get("country_id"),
        )
        return {"user_id": str(result.id), "action": "created"}
    
    elif change_type == "user_delete":
        # Soft delete the user
        supabase.table("profiles")\
            .update({
                "deleted_at": datetime.utcnow().isoformat(),
                "is_active": False,
            })\
            .eq("id", change["target_resource_id"])\
            .execute()
        return {"user_id": change["target_resource_id"], "action": "deleted"}
    
    elif change_type == "config_change":
        # Update system configuration
        supabase.table("system_config")\
            .update(proposed_data)\
            .execute()
        return {"action": "config_updated"}
    
    elif change_type == "feature_flag_change":
        # Toggle feature flag
        supabase.table("feature_flags")\
            .update({"is_enabled": proposed_data.get("is_enabled")})\
            .eq("name", change["target_resource_id"])\
            .execute()
        return {"action": "flag_updated", "flag": change["target_resource_id"]}
    
    else:
        raise ValueError(f"Unknown change type: {change_type}")
```

---

## 4. Frontend Approval Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│              Change Request UI Flow                              │
│                                                                 │
│  1. Admin initiates sensitive action                            │
│     → "Delete user: john@example.com"                          │
│     → System detects: This requires DUAL-APPROVAL              │
│     → Shows: "This action requires approval from another admin" │
│                                                                 │
│  2. Admin creates change request                                │
│     → Fills in: title, description, reason                      │
│     → Submits → Status: "Pending (0/2 approvals)"              │
│     → Other admins receive notification                        │
│                                                                 │
│  3. Second admin reviews                                      │
│     → Sees in "Pending Changes" dashboard                      │
│     → Reviews: what, who, why                                  │
│     → Approves with comment → Status: "Pending (1/2)"          │
│                                                                 │
│  4. Third admin (or same if only 1 needed) approves             │
│     → Status: "Approved — Ready to Execute"                    │
│     → Original requester notified                              │
│                                                                 │
│  5. Top admin executes                                         │
│     → Clicks "Execute Change"                                  │
│     → Change is applied                                        │
│     → Status: "Executed by [name] at [time]"                   │
│     → All parties notified                                     │
│                                                                 │
│  6. If rejected:                                               │
│     → Status: "Rejected"                                       │
│     → Requester sees rejection reason                          │
│     → Can resubmit with modifications                          │
│                                                                 │
│  7. If expired (7 days):                                       │
│     → Status: "Expired"                                        │
│     → Requester notified                                       │
│     → Can resubmit                                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Approval levels | Single, Dual, Top-Admin-Only | Proportional to risk |
| Change request expiry | 7 days | Prevents stale requests from accumulating |
| Required approvals | 1 or 2 (configurable per type) | Flexible for different risk levels |
| Self-approval | Allowed (can approve own request if dual-approval not required) | Small teams may have limited approvers |
| Execution | Only by top_admin | Highest-risk step requires highest privilege |
| Notification | All relevant admins notified | Ensures visibility |
| Audit trail | Full history of requests, approvals, executions | Accountability |
| Expired requests | Auto-expire after 7 days | Clean state, no zombie requests |

---

*This document should be updated when new change types are added or when approval requirements change.*
