# Data Retention & Archival Policies

**Document ID:** ENT-04  
**Category:** Data Management  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Supabase (PostgreSQL), Backend (FastAPI), Celery Scheduled Tasks  

---

## 1. Current State

```
What exists:
  ✅ Tables have created_at/updated_at timestamps
  ✅ profiles table has deleted_at (soft delete column)
  ✅ audit_logs table grows indefinitely
  ✅ errors table grows indefinitely
  ✅ No automated cleanup or archival

What's missing:
  ✗ No retention policies defined
  ✗ No automated data archival
  ✗ No automated data deletion
  ✗ No GDPR-compliant data deletion
  ✗ No partitioned tables for easy pruning
  ✗ No data classification by sensitivity/retention need
  ✗ No compliance with data protection laws in operating countries
```

### The Risk

```
Current growth projection:
  audit_logs: ~500 entries/day → 180K/year → 1M+ in 5 years
  errors: ~50 entries/day → 18K/year
  security_threats: ~10 entries/day → 3.6K/year
  reports: ~500 entries/day → 180K/year

Without retention policies:
  → Database grows indefinitely
  → Query performance degrades (more rows to scan)
  → Backup size and cost increases
  → Compliance risk (holding data longer than legally allowed)
  → GDPR violation (data minimization principle)
```

---

## 2. Data Classification & Retention Schedule

```
┌────────────────────────┬──────────────┬─────────────┬──────────────┬──────────────┐
│ Data Type              │ Hot Storage  │ Cold Archive│ Delete After │ Legal Basis  │
│ (Table)                │ (Active DB)  │ (Archive)   │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Reports                │ Indefinite   │ N/A         │ Never        │ Organizational│
│ (reports)              │              │             │              │ memory,      │
│                        │              │             │              │ program      │
│                        │              │             │              │ accountability│
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Audit Logs             │ 1 year       │ 2 years     │ 3 years      │ Compliance,  │
│ (audit_logs)           │              │             │              │ security     │
│                        │              │             │              │ audit trail  │
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Error Tracking         │ 90 days      │ 1 year      │ 1 year       │ Debugging,   │
│ (errors)               │              │             │              │ no legal     │
│                        │              │             │              │ retention req│
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Security Threats       │ 6 months     │ 2 years     │ 2 years      │ Security     │
│ (security_threats)     │              │             │              │ compliance,  │
│                        │              │             │              │ incident     │
│                        │              │             │              │ response     │
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Session Records        │ 30 days      │ 90 days     │ 90 days      │ Security,    │
│ (sessions)             │              │             │              │ session      │
│                        │              │             │              │ management   │
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Module Health Logs     │ 30 days      │ N/A         │ 30 days      │ Operational  │
│ (module_health_logs)   │              │             │              │ monitoring,  │
│                        │              │             │              │ low long-term│
│                        │              │             │              │ value        │
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Notifications          │ 90 days      │ N/A         │ 90 days      │ User communi-│
│ (notifications)        │              │             │              │ cation,      │
│                        │              │             │              │ transient    │
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Inquiries              │ 2 years      │ 3 years     │ 5 years      │ Legal        │
│ (inquiries)            │              │             │              │ correspondence│
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Idempotency Keys       │ N/A          │ N/A         │ 24 hours     │ Technical    │
│ (idempotency_keys)     │              │             │              │ dedup only   │
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Blocked IPs            │ Indefinite   │ N/A         │ Never*       │ Security     │
│ (blocked_ips)          │              │             │ (*unless     │ policy       │
│                        │              │             │  unblocked)  │              │
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ Feature Flags          │ Indefinite   │ N/A         │ Never        │ Configuration│
│ (feature_flags)        │              │             │              │              │
│                        │              │             │              │              │
├────────────────────────┼──────────────┼─────────────┼──────────────┼──────────────┤
│                        │              │             │              │              │
│ User Profiles          │ Indefinite   │ N/A         │ On request   │ GDPR right to│
│ (profiles)             │              │             │ (deletion)   │ erasure      │
│                        │              │             │              │              │
└────────────────────────┴──────────────┴─────────────┴──────────────┴──────────────┘
```

---

## 3. Implementation: Automated Cleanup

### 3.1 Monthly Celery Cleanup Task

```python
# backend/app/worker.py — Add scheduled cleanup tasks

from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


@celery_app.task(name="cleanup_expired_data", bind=True)
async def cleanup_expired_data(self):
    """
    Monthly cleanup task that purges data past its retention period.
    Runs on the 1st of every month at 3:00 AM UTC.
    """
    logger.info("Starting monthly data cleanup")
    results = {}

    try:
        # 1. Clean up errors older than 90 days
        cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        result = supabase.table("errors")\
            .delete()\
            .lt("created_at", cutoff)\
            .execute()
        results["errors_deleted"] = len(result.data) if result.data else 0
        logger.info(f"Deleted {results['errors_deleted']} old error records")

        # 2. Clean up module health logs older than 30 days
        cutoff = (datetime.utcnow() - timedelta(days=30)).isoformat()
        result = supabase.table("module_health_logs")\
            .delete()\
            .lt("checked_at", cutoff)\
            .execute()
        results["health_logs_deleted"] = len(result.data) if result.data else 0
        logger.info(f"Deleted {results['health_logs_deleted']} old health log records")

        # 3. Clean up notifications older than 90 days
        cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        result = supabase.table("notifications")\
            .delete()\
            .lt("created_at", cutoff)\
            .execute()
        results["notifications_deleted"] = len(result.data) if result.data else 0
        logger.info(f"Deleted {results['notifications_deleted']} old notifications")

        # 4. Clean up expired idempotency keys
        cutoff = datetime.utcnow().isoformat()
        result = supabase.table("idempotency_keys")\
            .delete()\
            .lt("expires_at", cutoff)\
            .execute()
        results["idempotency_keys_deleted"] = len(result.data) if result.data else 0
        logger.info(f"Deleted {results['idempotency_keys_deleted']} expired idempotency keys")

        # 5. Clean up session records older than 90 days
        cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
        result = supabase.table("sessions")\
            .delete()\
            .lt("created_at", cutoff)\
            .execute()
        results["sessions_deleted"] = len(result.data) if result.data else 0
        logger.info(f"Deleted {results['sessions_deleted']} old session records")

        # 6. Archive audit_logs older than 1 year
        # (Move to audit_logs_archive table, then delete from active)
        cutoff = (datetime.utcnow() - timedelta(days=365)).isoformat()
        archived = await archive_audit_logs(cutoff)
        results["audit_logs_archived"] = archived

        logger.info(f"Monthly cleanup completed: {results}")
        return results

    except Exception as e:
        logger.error(f"Monthly cleanup failed: {e}")
        raise


async def archive_audit_logs(cutoff: str) -> int:
    """
    Move audit logs older than cutoff to archive table.
    Returns count of archived records.
    """
    try:
        # Fetch old logs
        old_logs = supabase.table("audit_logs")\
            .select("*")\
            .lt("created_at", cutoff)\
            .execute()

        if not old_logs.data:
            return 0

        # Insert into archive table
        supabase.table("audit_logs_archive").insert(old_logs.data).execute()

        # Delete from active table
        supabase.table("audit_logs")\
            .delete()\
            .lt("created_at", cutoff)\
            .execute()

        return len(old_logs.data)

    except Exception as e:
        logger.error(f"Audit log archival failed: {e}")
        return 0
```

### 3.2 Celery Beat Schedule

```python
# backend/app/worker.py — Celery Beat configuration

celery_app.conf.beat_schedule = {
    # ... existing schedules ...

    "monthly-data-cleanup": {
        "task": "cleanup_expired_data",
        "schedule": crontab(hour=3, minute=0, day_of_month=1),  # 1st of month, 3 AM
    },

    "daily-idempotency-cleanup": {
        "task": "cleanup_expired_idempotency_keys",
        "schedule": crontab(hour=2, minute=0),  # Daily at 2 AM
    },
}
```

---

## 4. Table Partitioning for Easy Pruning

For large tables, use PostgreSQL table partitioning by month:

```sql
-- supabase/migrations/20260404_partitioning.sql

-- Partition audit_logs by month
CREATE TABLE audit_logs (
    id UUID NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_id UUID,
    action VARCHAR(100) NOT NULL,
    -- ... other columns ...
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- Create monthly partitions (next 12 months)
CREATE TABLE audit_logs_y2026m04 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE audit_logs_y2026m05 PARTITION OF audit_logs
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
-- ... continue for each month ...

-- Benefits:
-- → To delete data older than 1 year: DROP TABLE audit_logs_y2025m03;
--   (instant, no row-by-row deletion)
-- → Queries with timestamp filters are much faster (partition pruning)
-- → Archive = detach partition + attach to archive schema
```

Automated partition creation:

```sql
-- Function to create next month's partition
CREATE OR REPLACE FUNCTION create_next_partition()
RETURNS void AS $$
DECLARE
    next_month DATE;
    month_after DATE;
    partition_name TEXT;
BEGIN
    next_month := DATE_TRUNC('month', NOW() + INTERVAL '2 months');
    month_after := next_month + INTERVAL '1 month';
    partition_name := 'audit_logs_y' || TO_CHAR(next_month, 'YYYY') || 'm' || TO_CHAR(next_month, 'MM');
    
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF audit_logs FOR VALUES FROM (%L) TO (%L)',
        partition_name, next_month, month_after
    );
END;
$$ LANGUAGE plpgsql;

-- Schedule this to run monthly via Celery or cron
```

---

## 5. GDPR Right to Deletion

### 5.1 User Data Deletion Procedure

```python
# backend/app/services/data_deletion.py

from typing import List, Dict
import logging

logger = logging.getLogger(__name__)


class UserDataDeletionService:
    """
    Handles GDPR-compliant user data deletion.
    Deletes or anonymizes all personal data for a given user.
    """

    async def delete_user_data(self, user_id: str) -> Dict[str, int]:
        """
        Delete all personal data for a user.
        Returns count of deleted records per table.
        """
        results = {}

        # Tables where user data is DELETED:
        delete_tables = {
            "sessions": "user_id",
            "notifications": "user_id",
            "idempotency_keys": "user_id",
            "module_health_logs": "actor_id",  # If applicable
        }

        for table, column in delete_tables.items():
            result = supabase.table(table)\
                .delete()\
                .eq(column, user_id)\
                .execute()
            results[table] = len(result.data) if result.data else 0

        # Tables where user data is ANONYMIZED (not deleted):
        # → reports (organizational data, anonymize reporter info)
        # → audit_logs (compliance, anonymize actor)
        # → security_threats (security, anonymize source)
        anonymize_tables = {
            "reports": {"submitted_by": "anonymous"},
            "audit_logs": {"actor_id": None},
            "security_threats": {"user_id": None},
        }

        for table, column_map in anonymize_tables.items():
            for column, value in column_map.items():
                supabase.table(table)\
                    .update({column: value})\
                    .eq(column, user_id)\
                    .execute()
            results[f"{table}_anonymized"] = True

        # Finally, delete the profile itself
        supabase.table("profiles")\
            .update({
                "full_name": "Deleted User",
                "email": f"deleted-{user_id}@deleted.ohas.system",
                "phone": None,
                "avatar_url": None,
                "deleted_at": datetime.utcnow().isoformat(),
                "is_active": False,
            })\
            .eq("id", user_id)\
            .execute()
        results["profile_anonymized"] = True

        # Log the deletion
        supabase.table("audit_logs").insert({
            "action": "USER_DATA_DELETION",
            "user_id": user_id,
            "new_data": {"tables_affected": list(results.keys())},
            "created_at": datetime.utcnow().isoformat(),
        }).execute()

        logger.info(f"User data deleted for {user_id}: {results}")
        return results
```

### 5.2 Deletion API Endpoint

```python
# backend/app/api/v1/users.py

@router.delete("/users/{user_id}/data")
async def delete_user_data(
    user_id: UUID,
    current_user: Profile = Depends(check_role(["top_administrator"])),
    confirmation: str = Body(..., embed=True),
):
    """
    Delete all personal data for a user (GDPR right to erasure).
    Requires top_admin role and explicit confirmation.
    
    The user's profile is anonymized (not fully deleted) to preserve
    referential integrity. Their reports and audit trail are anonymized
    but retained for organizational records.
    """
    if confirmation != "DELETE ALL DATA":
        raise HTTPException(400, "Confirmation text must be 'DELETE ALL DATA'")

    # Find the user
    target_user = await user_service.get_profile(user_id)
    if not target_user:
        raise HTTPException(404, "User not found")

    # Perform deletion
    results = await data_deletion_service.delete_user_data(str(user_id))

    return {
        "message": f"Data deleted for user {target_user.email}",
        "details": results,
    }
```

---

## 6. Compliance by Country

```
┌───────────────────┬──────────────────────────────────────────────────┐
│ Country           │ Data Protection Law                              │
├───────────────────┼──────────────────────────────────────────────────┤
│ Kenya             │ Data Protection Act, 2019                        │
│                   │ → Right to access, rectification, erasure        │
│                   │ → Data officer registration required             │
│                   │ → Cross-border transfer restrictions             │
├───────────────────┼──────────────────────────────────────────────────┤
│ Uganda            │ Data Protection and Privacy Act, 2019            │
│                   │ → Right to access and correction                 │
│                   │ → Consent required for processing                │
│                   │ → Data breach notification within 72 hours       │
├───────────────────┼──────────────────────────────────────────────────┤
│ Tanzania          │ Personal Data Protection Act, 2022               │
│                   │ → Data Protection Commission oversight           │
│                   │ → Consent, purpose limitation                    │
│                   │ → Cross-border transfer approval required        │
├───────────────────┼──────────────────────────────────────────────────┤
│ Rwanda            │ Law No. 058/2021 (pending full implementation)   │
│                   │ → Data protection principles modeled on GDPR     │
├───────────────────┼──────────────────────────────────────────────────┤
│ Ethiopia          │ No comprehensive data protection law (yet)       │
│                   │ → Computer crime proclamation has some provisions│
├───────────────────┼──────────────────────────────────────────────────┤
│ South Sudan       │ No data protection law                           │
│                   │ → Follow GDPR as best practice                   │
├───────────────────┼──────────────────────────────────────────────────┤
│ Burundi           │ No comprehensive data protection law             │
│                   │ → Follow GDPR as best practice                   │
├───────────────────┼──────────────────────────────────────────────────┤
│ Horn of Africa    │ Regional body (IGAD) — no unified data law       │
│ Region            │ → Follow GDPR as best practice                   │
└───────────────────┴──────────────────────────────────────────────────┘

Common denominator: Implement GDPR-level protections across ALL countries.
This covers the strictest requirements and simplifies compliance.
```

---

## 7. Monitoring & Alerts

```python
# Celery task to monitor table sizes and alert when approaching limits
@celery_app.task(name="monitor_table_sizes")
async def monitor_table_sizes():
    """Check table sizes and alert if approaching retention limits."""
    
    tables_to_monitor = {
        "audit_logs": {"warn_rows": 500_000, "critical_rows": 1_000_000},
        "errors": {"warn_rows": 50_000, "critical_rows": 100_000},
        "reports": {"warn_rows": 500_000, "critical_rows": 1_000_000},
    }
    
    alerts = []
    for table, thresholds in tables_to_monitor.items():
        result = supabase.table(table)\
            .select("id", count="exact")\
            .execute()
        count = result.count or 0
        
        if count >= thresholds["critical_rows"]:
            alerts.append(f"🔴 {table}: {count:,} rows (CRITICAL)")
        elif count >= thresholds["warn_rows"]:
            alerts.append(f"🟡 {table}: {count:,} rows (WARNING)")
    
    if alerts:
        # Send alert to developers
        await notification_service.send_alert(
            title="Database Table Size Alert",
            message="\n".join(alerts),
            recipients=["developer", "top_administrator"],
        )
    
    return {"tables_checked": len(tables_to_monitor), "alerts": alerts}
```

---

## 8. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Reports retention | Indefinite | Organizational memory, program accountability |
| Audit logs retention | 1yr hot + 2yr archive | Compliance requirement, security investigation |
| Error retention | 90 days hot + 1yr archive | Debugging value drops sharply after 90 days |
| Session retention | 90 days | Sufficient for security investigation |
| Health logs | 30 days | Operational monitoring, low long-term value |
| Notifications | 90 days | Transient communication, no long-term need |
| Inquiries | 5 years | Legal correspondence retention |
| Cleanup frequency | Monthly (1st of month, 3 AM) | Infrequent enough to be efficient, frequent enough to stay manageable |
| Deletion method | DELETE with WHERE (no partitioning yet) | Simpler for current scale; partitioning when tables exceed 500K rows |
| GDPR deletion | Anonymize, don't hard-delete | Preserves referential integrity, meets legal requirement |
| Compliance baseline | GDPR-level protections for all countries | Simplest approach, covers strictest requirements |

---

*This document should be updated when retention periods change, when new tables are created, or when country-specific compliance requirements evolve.*
