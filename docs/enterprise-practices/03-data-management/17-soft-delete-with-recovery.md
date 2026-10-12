# Soft Delete with Recovery

**Document ID:** ENT-17  
**Category:** Data Management  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Supabase (PostgreSQL), All CRUD Endpoints  

---

## 1. Current State

```
What exists:
  ✅ profiles table has deleted_at column (soft delete)
  ✅ No other tables have soft delete capability
  ✅ No recovery mechanism (can't undelete)
  ✅ No automated purge of soft-deleted records
  ✅ RLS policies don't consistently exclude soft-deleted records
  ✅ API endpoints don't consistently check deleted_at

What's missing:
  ✗ No soft delete for reports, participants, goals, etc.
  ✗ No "undelete" functionality
  ✗ No deletion reason/audit trail
  ✗ No automated cleanup of old soft-deleted records
  ✗ Inconsistent implementation across endpoints
```

---

## 2. Soft Delete Design

### 2.1 Soft Delete Columns

Add to ALL tables that support deletion:

```sql
-- supabase/migrations/20260404170000_soft_delete_columns.sql

ALTER TABLE reports ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE reports ADD COLUMN IF NOT EXISTS deleted_by UUID REFERENCES profiles(id);
ALTER TABLE reports ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(500);

ALTER TABLE participants ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE participants ADD COLUMN IF NOT EXISTS deleted_by UUID REFERENCES profiles(id);
ALTER TABLE participants ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(500);

ALTER TABLE goals ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE goals ADD COLUMN IF NOT EXISTS deleted_by UUID REFERENCES profiles(id);
ALTER TABLE goals ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(500);

ALTER TABLE programs ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE programs ADD COLUMN IF NOT EXISTS deleted_by UUID REFERENCES profiles(id);
ALTER TABLE programs ADD COLUMN IF NOT EXISTS deleted_reason VARCHAR(500);

-- Index for fast exclusion of soft-deleted records
CREATE INDEX idx_reports_active ON reports(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_participants_active ON participants(deleted_at) WHERE deleted_at IS NULL;
CREATE INDEX idx_goals_active ON goals(deleted_at) WHERE deleted_at IS NULL;
```

### 2.2 RLS Policies for Soft Delete

```sql
-- Update RLS policies to exclude soft-deleted records by default

-- Reports: only see non-deleted reports
DROP POLICY IF EXISTS "Reporters can view own reports" ON reports;
CREATE POLICY "Reporters can view own reports" ON reports
    FOR SELECT
    USING (
        auth.uid() = submitted_by
        AND deleted_at IS NULL  -- Exclude soft-deleted
    );

-- Admins can see soft-deleted reports
DROP POLICY IF EXISTS "Admins can view all reports" ON reports;
CREATE POLICY "Admins can view all reports" ON reports
    FOR SELECT
    USING (
        EXISTS (
            SELECT 1 FROM profiles
            WHERE profiles.id = auth.uid()
              AND profiles.role IN ('top_administrator', 'country_admin', 'developer')
        )
    );
-- Note: Admins CAN see soft-deleted. They can filter in the UI.
```

### 2.3 Soft Delete Endpoint

```python
# backend/app/api/v1/reports.py

class SoftDeleteRequest(BaseModel):
    """Request body for soft deletion."""
    reason: str = Field(
        ...,
        min_length=10,
        max_length=500,
        examples=["Report contains incorrect data"],
        description="Reason for deletion",
    )


@router.delete("/reports/{report_id}")
async def soft_delete_report(
    report_id: UUID,
    request: SoftDeleteRequest,
    current_user: Profile = Depends(get_current_user),
):
    """
    Soft delete a report.
    The report is hidden from normal queries but can be recovered
    within 30 days. After 30 days, it is permanently deleted.
    """
    # Verify ownership/permission
    report = supabase.table("reports")\
        .select("*")\
        .eq("id", str(report_id))\
        .single()\
        .execute()
    
    if not report.data:
        raise HTTPException(404, "Report not found")
    
    # Check permission
    if current_user.role == "reporter" and report.data["submitted_by"] != str(current_user.id):
        raise HTTPException(403, "You can only delete your own reports")
    
    # Soft delete
    supabase.table("reports")\
        .update({
            "deleted_at": datetime.utcnow().isoformat(),
            "deleted_by": str(current_user.id),
            "deleted_reason": request.reason,
        })\
        .eq("id", str(report_id))\
        .execute()
    
    # Audit log
    supabase.table("audit_logs").insert({
        "action": "REPORT_SOFT_DELETED",
        "user_id": str(current_user.id),
        "table_name": "reports",
        "record_id": str(report_id),
        "new_data": {"reason": request.reason},
        "created_at": datetime.utcnow().isoformat(),
    }).execute()
    
    return {"message": "Report deleted. It can be recovered within 30 days."}
```

### 2.4 Recovery (Undelete) Endpoint

```python
# backend/app/api/v1/reports.py

@router.post("/reports/{report_id}/undelete")
async def undelete_report(
    report_id: UUID,
    current_user: Profile = Depends(check_role(["top_administrator", "developer"])),
):
    """
    Recover a soft-deleted report.
    Only available within 30 days of deletion.
    """
    # Find the soft-deleted report
    report = supabase.table("reports")\
        .select("*")\
        .eq("id", str(report_id))\
        .not_.is_("deleted_at", None)\
        .single()\
        .execute()
    
    if not report.data:
        raise HTTPException(404, "Report not found or already permanently deleted")
    
    # Check if within recovery window
    deleted_at = datetime.fromisoformat(report.data["deleted_at"])
    if datetime.utcnow() - deleted_at > timedelta(days=30):
        raise HTTPException(
            410,
            "Report has been permanently deleted and cannot be recovered",
        )
    
    # Restore
    supabase.table("reports")\
        .update({
            "deleted_at": None,
            "deleted_by": None,
            "deleted_reason": None,
        })\
        .eq("id", str(report_id))\
        .execute()
    
    # Audit log
    supabase.table("audit_logs").insert({
        "action": "REPORT_UNDELETED",
        "user_id": str(current_user.id),
        "table_name": "reports",
        "record_id": str(report_id),
        "created_at": datetime.utcnow().isoformat(),
    }).execute()
    
    return {"message": "Report recovered successfully"}
```

### 2.5 Hard Delete (After 30 Days)

```python
# backend/app/worker.py — Monthly cleanup task

@celery_app.task(name="purge_expired_soft_deletes")
async def purge_expired_soft_deletes():
    """
    Permanently delete records that have been soft-deleted for more than 30 days.
    """
    cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
    
    tables = ["reports", "participants", "goals", "programs"]
    results = {}
    
    for table in tables:
        # First, archive the data
        archived = supabase.table(f"{table}_archive")\
            .insert(
                supabase.table(table)\
                    .select("*")\
                    .not_.is_("deleted_at", None)\
                    .lt("deleted_at", cutoff)\
                    .execute().data
            )\
            .execute()
        
        # Then delete from active table
        result = supabase.table(table)\
            .delete()\
            .not_.is_("deleted_at", None)\
            .lt("deleted_at", cutoff)\
            .execute()
        
        results[table] = len(result.data) if result.data else 0
    
    logger.info(f"Purged expired soft deletes: {results}")
    return results
```

---

## 3. API Design: Consistent Soft Delete Pattern

### 3.1 List Endpoints

```python
# All list endpoints should exclude soft-deleted by default
@router.get("/reports")
async def list_reports(
    include_deleted: bool = Query(False, description="Include soft-deleted reports"),
    ...
):
    query = supabase.table("reports").select("*")
    
    if not include_deleted:
        query = query.is_("deleted_at", None)
    
    # ... rest of query ...
```

### 3.2 GET Single Resource

```python
# Return 410 Gone for soft-deleted resources
@router.get("/reports/{report_id}")
async def get_report(report_id: UUID, ...):
    report = supabase.table("reports")\
        .select("*")\
        .eq("id", str(report_id))\
        .single()\
        .execute()
    
    if not report.data:
        raise HTTPException(404, "Report not found")
    
    if report.data.get("deleted_at"):
        # Return 410 with recovery info
        raise HTTPException(
            410,
            content={
                "code": "RESOURCE_DELETED",
                "message": "This report has been deleted",
                "deleted_at": report.data["deleted_at"],
                "deleted_by": report.data.get("deleted_by"),
                "deleted_reason": report.data.get("deleted_reason"),
                "recoverable_until": (
                    datetime.fromisoformat(report.data["deleted_at"])
                    + timedelta(days=30)
                ).isoformat(),
            },
        )
    
    return report.data
```

---

## 4. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Soft delete mechanism | `deleted_at` timestamp column | Standard pattern, easy to query |
| Recovery window | 30 days | Enough time to notice and recover, not indefinite |
| Hard delete after | 30 days → archive → delete | Prevents indefinite data growth |
| Who can soft delete | Resource owner + admins | Standard permission model |
| Who can undelete | Top admins + developers only | Recovery is a privileged operation |
| Default list behavior | Exclude soft-deleted | Users expect to see active data |
| GET deleted resource | Return 410 Gone with recovery info | Clear, informative |
| Archive before hard delete | Copy to `{table}_archive` first | Safety net, compliance audit trail |
| Audit trail | Log delete and undelete actions | Full traceability |
| RLS for soft delete | Exclude deleted rows for owners, include for admins | Users don't see their deleted data, admins can review |

---

*This document should be updated when new tables get soft delete support or when the recovery window changes.*
