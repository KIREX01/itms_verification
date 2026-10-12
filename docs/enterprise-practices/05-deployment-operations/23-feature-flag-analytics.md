# Feature Flag Analytics

**Document ID:** ENT-23  
**Category:** Deployment & Operations  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), Supabase  

---

## 1. Current State

```
What exists:
  ✅ 24 feature flags seeded in database
  ✅ Feature flag API (CRUD + check endpoint)
  ✅ Country/program-level flag overrides
  ✅ Fail-open on error (defaults to enabled)

What's missing:
  ✗ No analytics on flag usage (who uses what, how often)
  ✗ No detection of stale/unused flags
  ✗ No flag change history
  ✗ No flag impact measurement
  ✗ No flag ownership assignment
  ✗ No automated cleanup of unused flags
```

---

## 2. Feature Flag Analytics Schema

```sql
-- supabase/migrations/20260404180000_feature_flag_analytics.sql

-- Flag change history
CREATE TABLE feature_flag_history (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_id UUID NOT NULL REFERENCES feature_flags(id) ON DELETE CASCADE,
    changed_by UUID REFERENCES profiles(id),
    old_value BOOLEAN,
    new_value BOOLEAN,
    reason VARCHAR(500),
    changed_at TIMESTAMPTZ DEFAULT NOW()
);

-- Flag usage tracking
CREATE TABLE feature_flag_usage (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_id UUID NOT NULL REFERENCES feature_flags(id) ON DELETE CASCADE,
    user_id UUID,
    country_id UUID,
    program_id UUID,
    checked_at TIMESTAMPTZ DEFAULT NOW(),
    result BOOLEAN  -- Was the flag enabled for this check?
);

-- Flag analytics (aggregated daily)
CREATE TABLE feature_flag_daily_stats (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    flag_id UUID NOT NULL REFERENCES feature_flags(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    total_checks INT DEFAULT 0,
    enabled_checks INT DEFAULT 0,
    disabled_checks INT DEFAULT 0,
    unique_users INT DEFAULT 0,
    unique_countries INT DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(flag_id, date)
);

-- Indexes
CREATE INDEX idx_flag_history_flag ON feature_flag_history(flag_id, changed_at DESC);
CREATE INDEX idx_flag_usage_flag ON feature_flag_usage(flag_id, checked_at DESC);
CREATE INDEX idx_flag_usage_date ON feature_flag_usage(checked_at::date);
CREATE INDEX idx_flag_daily_stats_flag ON feature_flag_daily_stats(flag_id, date DESC);
```

---

## 3. Usage Tracking

### 3.1 Flag Check Instrumentation

```python
# backend/app/services/feature_flag_service.py — Modified

class FeatureFlagService:
    async def check_flag(
        self,
        flag_name: str,
        user_id: Optional[str] = None,
        country_id: Optional[str] = None,
        program_id: Optional[str] = None,
    ) -> bool:
        """
        Check if a feature flag is enabled.
        NOW TRACKS USAGE ANALYTICS.
        """
        # Get flag
        flag = self._get_flag(flag_name)
        if not flag:
            # Flag doesn't exist — fail open (existing behavior)
            self._track_usage(flag_name, user_id, country_id, program_id, True)
            return True

        # Check overrides
        is_enabled = flag.is_enabled
        
        # Country override
        if country_id:
            country_override = self._get_country_override(flag_name, country_id)
            if country_override is not None:
                is_enabled = country_override

        # Track usage
        self._track_usage(flag_name, user_id, country_id, program_id, is_enabled)

        return is_enabled

    def _track_usage(
        self,
        flag_name: str,
        user_id: Optional[str],
        country_id: Optional[str],
        program_id: Optional[str],
        result: bool,
    ):
        """
        Track flag usage asynchronously (non-blocking).
        """
        try:
            supabase.table("feature_flag_usage").insert({
                "flag_name": flag_name,
                "user_id": user_id,
                "country_id": country_id,
                "program_id": program_id,
                "result": result,
                "checked_at": datetime.utcnow().isoformat(),
            }).execute()
        except Exception as e:
            # Non-critical — don't fail flag check on tracking error
            logger.debug(f"Flag usage tracking failed: {e}")
```

### 3.2 Change History Tracking

```python
# Updated flag update method

async def update_flag(self, flag_name: str, update: FeatureFlagUpdate):
    """Update a feature flag and record change history."""
    # Get current value
    current = self._get_flag(flag_name)
    old_value = current.is_enabled if current else None

    # Apply update
    result = supabase.table("feature_flags")\
        .update({"is_enabled": update.is_enabled, "updated_at": datetime.utcnow().isoformat()})\
        .eq("name", flag_name)\
        .execute()

    # Record change history
    supabase.table("feature_flag_history").insert({
        "flag_id": current.id if current else None,
        "flag_name": flag_name,
        "changed_by": str(update.changed_by),
        "old_value": old_value,
        "new_value": update.is_enabled,
        "reason": update.reason,
        "changed_at": datetime.utcnow().isoformat(),
    }).execute()

    return result.data[0]
```

---

## 4. Analytics Dashboard

### 4.1 Flag Analytics API

```python
# backend/app/api/v1/feature_flags.py

@router.get("/feature-flags/{flag_name}/analytics")
async def get_flag_analytics(
    flag_name: str,
    days: int = Query(default=30, ge=1, le=365),
    current_user = Depends(check_role(["top_administrator", "developer"])),
):
    """
    Get usage analytics for a feature flag.
    """
    # Get flag
    flag = supabase.table("feature_flags")\
        .select("*")\
        .eq("name", flag_name)\
        .single()\
        .execute()
    
    if not flag.data:
        raise HTTPException(404, f"Flag not found: {flag_name}")

    # Get daily stats
    daily_stats = supabase.table("feature_flag_daily_stats")\
        .select("*")\
        .eq("flag_id", flag.data["id"])\
        .gte("date", (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d"))\
        .order("date", desc=False)\
        .execute()

    # Get recent change history
    history = supabase.table("feature_flag_history")\
        .select("*, profiles(full_name)")\
        .eq("flag_id", flag.data["id"])\
        .order("changed_at", desc=True)\
        .limit(20)\
        .execute()

    # Calculate summary
    total_checks = sum(s["total_checks"] for s in daily_stats.data or [])
    enabled_checks = sum(s["enabled_checks"] for s in daily_stats.data or [])
    enabled_percent = (enabled_checks / total_checks * 100) if total_checks > 0 else 0

    return {
        "flag": flag.data,
        "summary": {
            "period_days": days,
            "total_checks": total_checks,
            "enabled_percent": round(enabled_percent, 1),
            "avg_daily_checks": round(total_checks / days, 1) if days > 0 else 0,
            "is_stale": total_checks == 0 and days >= 30,  # No usage in 30+ days
        },
        "daily_stats": daily_stats.data or [],
        "recent_changes": history.data or [],
    }


@router.get("/feature-flags/analytics/summary")
async def get_all_flags_analytics(
    current_user = Depends(check_role(["top_administrator", "developer"])),
):
    """
    Get a summary of all feature flags with usage analytics.
    Identifies stale flags (no usage in 90 days).
    """
    flags = supabase.table("feature_flags").select("*").execute()
    
    flag_analytics = []
    for flag in flags.data:
        # Get last 90 days stats
        stats = supabase.table("feature_flag_daily_stats")\
            .select("total_checks")\
            .eq("flag_id", flag["id"])\
            .gte("date", (datetime.utcnow() - timedelta(days=90)).strftime("%Y-%m-%d"))\
            .execute()
        
        total_90d = sum(s["total_checks"] for s in stats.data or [])
        last_change = supabase.table("feature_flag_history")\
            .select("changed_at")\
            .eq("flag_id", flag["id"])\
            .order("changed_at", desc=True)\
            .limit(1)\
            .execute()
        
        flag_analytics.append({
            "name": flag["name"],
            "is_enabled": flag["is_enabled"],
            "description": flag["description"],
            "total_checks_90d": total_90d,
            "is_stale": total_90d == 0,
            "last_changed": last_change.data[0]["changed_at"] if last_change.data else None,
        })
    
    # Sort: stale flags at the bottom
    flag_analytics.sort(key=lambda x: (x["is_stale"], x["name"]))
    
    return {
        "total_flags": len(flag_analytics),
        "stale_flags": sum(1 for f in flag_analytics if f["is_stale"]),
        "flags": flag_analytics,
    }
```

### 4.2 Daily Stats Aggregation

```python
# backend/app/worker.py — Daily aggregation task

@celery_app.task(name="aggregate_feature_flag_stats")
async def aggregate_feature_flag_stats():
    """
    Aggregate feature flag usage into daily stats.
    Runs daily at 1:00 AM UTC.
    """
    yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Get all flags
    flags = supabase.table("feature_flags").select("id, name").execute()
    
    for flag in flags.data:
        # Aggregate yesterday's usage
        usage = supabase.table("feature_flag_usage")\
            .select("user_id, country_id, result")\
            .eq("flag_name", flag["name"])\
            .gte("checked_at", f"{yesterday}T00:00:00")\
            .lt("checked_at", f"{yesterday}T23:59:59")\
            .execute()
        
        if not usage.data:
            continue
        
        total = len(usage.data)
        enabled = sum(1 for u in usage.data if u["result"])
        unique_users = len(set(u["user_id"] for u in usage.data if u["user_id"]))
        unique_countries = len(set(u["country_id"] for u in usage.data if u["country_id"]))
        
        # Upsert daily stats
        supabase.table("feature_flag_daily_stats").upsert({
            "flag_id": flag["id"],
            "date": yesterday,
            "total_checks": total,
            "enabled_checks": enabled,
            "disabled_checks": total - enabled,
            "unique_users": unique_users,
            "unique_countries": unique_countries,
        }, on_conflict="flag_id,date").execute()
    
    # Clean up old usage data (keep 90 days of raw data, daily stats are kept forever)
    cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
    supabase.table("feature_flag_usage")\
        .delete()\
        .lt("checked_at", cutoff)\
        .execute()
```

---

## 5. Stale Flag Detection & Cleanup

```python
# Monthly stale flag review
@celery_app.task(name="review_stale_feature_flags")
async def review_stale_feature_flags():
    """
    Identify stale feature flags and notify owners.
    Runs monthly on the 15th.
    """
    summary = await feature_flag_service.get_all_flags_analytics()
    
    stale_flags = [f for f in summary["flags"] if f["is_stale"]]
    
    if stale_flags:
        # Notify developers
        await notification_service.send_alert(
            title=f"Stale Feature Flags: {len(stale_flags)} flags unused for 90+ days",
            details=stale_flags,
            recipients=["developer", "top_administrator"],
        )
    
    return {"total_flags": summary["total_flags"], "stale_flags": len(stale_flags)}
```

---

## 6. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Usage tracking | Async insert on every flag check | Non-blocking, doesn't slow flag checks |
| Raw data retention | 90 days | Enough for analysis, not indefinite storage |
| Aggregated stats | Daily, kept forever | Long-term trends without raw data cost |
| Stale threshold | 90 days with zero checks | Long enough to catch seasonal flags |
| Change history | Track all changes with reason | Audit trail, accountability |
| Flag ownership | `changed_by` field in history | Implicit ownership through who manages it |
| Stale flag review | Monthly automated check | Prevents flag accumulation |
| Dashboard | API endpoint for frontend dashboard | Flexible, queryable |
| Fail-open on tracking error | Yes | Flag check must not fail if tracking fails |

---

*This document should be updated when new analytics fields are added or when stale flag thresholds change.*
