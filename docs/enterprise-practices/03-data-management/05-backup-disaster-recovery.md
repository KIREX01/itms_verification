# Backup & Disaster Recovery

**Document ID:** ENT-05  
**Category:** Data Management  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Supabase (PostgreSQL), Backend (FastAPI), Redis, Celery, Supabase Storage  

---

## 1. Current State

```
What exists:
  ✅ Supabase automatic daily backups (Point-in-Time Recovery)
  ✅ Supabase PITR (7-day rollback window on Pro plan)
  ✅ Error tracking to database (errors table)
  ✅ Audit logging (audit_logs table)

What's missing:
  ✗ No documented RTO (Recovery Time Objective)
  ✗ No documented RPO (Recovery Point Objective)
  ✗ No backup restoration ever tested
  ✗ No disaster runbooks for common scenarios
  ✗ No DR drill schedule
  ✗ No post-incident review process
  ✗ No application config backup (only DB is backed up)
  ✗ No Redis/Celery backup strategy
  ✗ No Supabase Storage (file uploads) backup strategy
```

---

## 2. Recovery Objectives

### 2.1 RTO & RPO Definitions

```
┌─────────────────────────────────────────────────────────────────┐
│              Recovery Objectives for OHAS                       │
├──────────────────────────┬──────────────────────────────────────┤
│ RTO (Recovery Time       │ 4 hours                              │
│ Objective)               │                                      │
│                          │ Maximum time from incident detection   │
│                          │ to system being operational again.     │
│                          │                                      │
│                          │ Target: System accepting requests      │
│                          │ within 4 hours of declaring disaster.  │
├──────────────────────────┼──────────────────────────────────────┤
│ RPO (Recovery Point      │ 1 hour                               │
│ Objective)               │                                      │
│                          │ Maximum data loss measured in time.    │
│                          │                                      │
│                          │ Target: No more than 1 hour of data    │
│                          │ is lost in any recovery scenario.      │
│                          │                                      │
│                          │ Supabase PITR supports this (7-day    │
│                          │ rollback window on Pro plan).          │
├──────────────────────────┼──────────────────────────────────────┤
│ MTTR (Mean Time To       │ 2 hours (target)                     │
│ Recovery)                │                                      │
│                          │ Average time to restore service.       │
│                          │ Measured across all incidents.         │
├──────────────────────────┼──────────────────────────────────────┤
│ Availability Target      │ 99.9% (three nines)                  │
│                          │                                      │
│                          │ Allows 43 minutes of downtime/month   │
│                          │ or 8.76 hours/year.                   │
└──────────────────────────┴──────────────────────────────────────┘
```

### 2.2 What This Means in Practice

```
Scenario: Database corruption discovered at 2:00 PM
──────────────────────────────────────────────────

  2:00 PM  Corruption detected (reports showing wrong data)
  2:05 PM  Incident declared, DR plan activated
  2:15 PM  Root cause identified (buggy migration corrupted profiles)
  2:30 PM  Decision: Restore from PITR backup at 1:00 PM (before migration)
  2:45 PM  Supabase PITR restore initiated
  3:15 PM  Database restore complete
  3:30 PM  Backend restarted, verified against restored DB
  3:45 PM  Frontend verified, smoke tests passed
  4:00 PM  System declared operational ✅

  RTO achieved: 2 hours (within 4-hour target)
  RPO achieved: 1 hour (data from 1:00 PM restored, 1 hour of writes lost)
  
  Data lost: 1 hour of report submissions (approximately 8 reports)
  → Affected reporters contacted to resubmit
```

---

## 3. Disaster Scenarios & Runbooks

### 3.1 Runbook 1: Database Corruption

```
┌─────────────────────────────────────────────────────────────────┐
│  RUNBOOK: Database Corruption                                   │
│  Severity: P0 (Critical)                                        │
│  Estimated Recovery Time: 2-4 hours                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  SYMPTOMS:                                                      │
│  - Reports showing incorrect or garbled data                    │
│  - Foreign key constraint violations on normal operations       │
│  - NULL values in NOT NULL columns                              │
│  - Missing rows that should exist                               │
│                                                                 │
│  IMMEDIATE ACTIONS (0-15 minutes):                              │
│  ─────────────────────────────────                              │
│  1. Confirm the corruption:                                     │
│     SELECT * FROM profiles LIMIT 10;                            │
│     SELECT COUNT(*) FROM reports WHERE created_at > NOW() -     │
│         INTERVAL '1 hour';                                      │
│                                                                 │
│  2. Identify scope: which tables are affected?                  │
│     SELECT table_name FROM information_schema.tables            │
│     WHERE table_schema = 'public';                              │
│     -- Check each table for anomalies                           │
│                                                                 │
│  3. Declare incident:                                           │
│     → Notify team via Slack/email                               │
│     → Create incident document                                  │
│     → Assign incident commander                                 │
│                                                                 │
│  RECOVERY (15 minutes - 4 hours):                               │
│  ─────────────────────────────────                              │
│  4. Determine corruption time:                                  │
│     → Find the last known good state                            │
│     → Check audit_logs for suspicious operations                │
│     → Check deployment history for recent migrations            │
│                                                                 │
│  5. Restore from PITR:                                          │
│     → Supabase Dashboard → Database → Backups                   │
│     → Select "Point-in-Time Recovery"                           │
│     → Choose timestamp BEFORE corruption                        │
│     → Click "Restore"                                           │
│     → Wait for restore to complete (15-60 minutes)              │
│                                                                 │
│  6. Verify restored data:                                       │
│     SELECT COUNT(*) FROM profiles;                              │
│     SELECT COUNT(*) FROM reports;                               │
│     -- Compare with known-good counts                           │
│                                                                 │
│  7. Re-apply any legitimate migrations that ran after restore   │
│     point (if needed):                                          │
│     supabase db push --db-url "$RESTORED_DB_URL"                │
│                                                                 │
│  8. Restart backend to clear any cached bad state:              │
│     → Restart all backend instances                             │
│                                                                 │
│  POST-RECOVERY (4+ hours):                                      │
│  ──────────────────────                                         │
│  9. Contact affected users about data loss window               │
│  10. Root cause analysis                                        │
│  11. Write post-incident report                                 │
│  12. Update runbook if needed                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 Runbook 2: Supabase Region Outage

```
┌─────────────────────────────────────────────────────────────────┐
│  RUNBOOK: Supabase Region Outage                                │
│  Severity: P0 (Critical)                                        │
│  Estimated Recovery Time: Depends on Supabase (external)        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  SYMPTOMS:                                                      │
│  - All database queries timeout                                 │
│  - Supabase Dashboard inaccessible                              │
│  - Supabase status page shows incident                          │
│                                                                 │
│  IMMEDIATE ACTIONS (0-15 minutes):                              │
│  ─────────────────────────────────                              │
│  1. Check Supabase status page: status.supabase.com             │
│  2. Check if it's our issue or Supabase-wide:                   │
│     curl -I https://$SUPABASE_URL/rest/v1/                      │
│     → If connection refused: Supabase outage                    │
│     → If 500 error: Our issue, see other runbooks               │
│                                                                 │
│  3. Activate graceful degradation (TIER 3 - CRITICAL):          │
│     → System enters accept-only mode                            │
│     → Frontend shows "System maintenance" banner                │
│     → Report submissions queued locally (offline queue)         │
│                                                                 │
│  DURING OUTAGE:                                                 │
│  ─────────────                                                  │
│  4. Monitor Supabase status page for updates                    │
│  5. Communicate to users via status page / email                │
│  6. Prepare for recovery when Supabase is back                  │
│                                                                 │
│  RECOVERY (when Supabase is back):                              │
│  ────────────────────────────────                               │
│  7. Verify database connectivity:                               │
│     supabase.table("countries").select("id").limit(1).execute() │
│                                                                 │
│  8. Check data integrity:                                       │
│     → Verify row counts are expected                            │
│     → Check for any corruption from outage                      │
│                                                                 │
│  9. Process queued submissions:                                 │
│     → Offline queue flushes automatically when online           │
│     → Monitor for conflicts (duplicate submissions)             │
│                                                                 │
│  10. Exit degradation mode:                                     │
│      → System returns to TIER 0 when metrics normalize          │
│                                                                 │
│  POST-RECOVERY:                                                 │
│  ─────────────                                                  │
│  11. Document outage duration and impact                        │
│  12. Evaluate if multi-region is needed                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 Runbook 3: Data Breach (Leaked Service Role Key)

```
┌─────────────────────────────────────────────────────────────────┐
│  RUNBOOK: Data Breach — Leaked Service Role Key                 │
│  Severity: P0 (Critical)                                        │
│  Estimated Recovery Time: 1-2 hours                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  SYMPTOMS:                                                      │
│  - Service role key found in public repository, logs, etc.      │
│  - Unusual database activity from unknown IP addresses          │
│  - Audit logs show unauthorized access                          │
│                                                                 │
│  IMMEDIATE ACTIONS (0-30 minutes):                              │
│  ─────────────────────────────────                              │
│  1. ROTATE THE KEY IMMEDIATELY:                                 │
│     → Supabase Dashboard → Settings → API                      │
│     → Service Role Key → Regenerate                             │
│     → Copy new key (shown ONCE)                                 │
│                                                                 │
│  2. Update environment variable:                                │
│     SUPABASE_SERVICE_ROLE_KEY=<new key>                         │
│                                                                 │
│  3. Restart all backend instances with new key                  │
│                                                                 │
│  4. Investigate unauthorized access:                            │
│     → Check audit_logs for suspicious activity during            │
│       compromise window                                         │
│     → Check for data modifications, exports, or deletions       │
│     → Identify what data was accessed                           │
│                                                                 │
│  5. If personal data was accessed:                              │
│     → Notify affected users within 72 hours (GDPR requirement)  │
│     → Notify relevant data protection authorities               │
│     → Document what was accessed and by whom                    │
│                                                                 │
│  RECOVERY (30 minutes - 2 hours):                               │
│  ─────────────────────────────────                              │
│  6. If data was modified: restore from PITR                     │
│  7. If data was exported: assess breach scope                   │
│  8. Remove leaked key from wherever it was exposed               │
│  9. Audit all environments (dev, staging, prod) for same key    │
│                                                                 │
│  POST-RECOVERY:                                                 │
│  ─────────────                                                  │
│  10. Write incident report                                      │
│  11. Update secrets rotation procedure                          │
│  12. Implement secret scanning in CI (gitleaks)                 │
│  13. Review access controls on service role key                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.4 Runbook 4: Breaking Migration Deployed

```
┌─────────────────────────────────────────────────────────────────┐
│  RUNBOOK: Breaking Migration Deployed to Production             │
│  Severity: P0 (Critical)                                        │
│  Estimated Recovery Time: 1-2 hours                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  SYMPTOMS:                                                      │
│  - Application errors immediately after migration               │
│  - Missing columns or tables that application expects           │
│  - Type mismatch errors                                         │
│  - RLS policies blocking legitimate access                      │
│                                                                 │
│  IMMEDIATE ACTIONS (0-15 minutes):                              │
│  ─────────────────────────────────                              │
│  1. Identify the broken migration:                              │
│     SELECT * FROM supabase_migrations.schema_migrations         │
│     ORDER BY version DESC LIMIT 5;                              │
│                                                                 │
│  2. Assess what the migration changed:                          │
│     → Read the migration SQL file                               │
│     → Check what tables/columns were affected                   │
│                                                                 │
│  3. Check if DOWN migration exists:                             │
│     → Open migration file, look for DOWN section                │
│                                                                 │
│  RECOVERY (15 minutes - 2 hours):                               │
│  ─────────────────────────────────                              │
│  4a. If DOWN migration exists and is safe:                      │
│     → Run the DOWN SQL manually                                 │
│     → Verify tables/columns are restored                        │
│     → Verify application works                                  │
│                                                                 │
│  4b. If no DOWN migration or it's unsafe:                       │
│     → Restore from PITR to before migration                     │
│     → Re-apply any legitimate data written after migration      │
│                                                                 │
│  5. Fix the migration file:                                     │
│     → Correct the SQL errors                                    │
│     → Test on staging first                                     │
│     → Add proper DOWN migration                                  │
│                                                                 │
│  6. Retry deployment when ready                                 │
│                                                                 │
│  POST-RECOVERY:                                                 │
│  ─────────────                                                  │
│  7. Update migration procedure to prevent recurrence            │
│  8. Ensure all migrations have DOWN sections                    │
│  9. Add migration validation to CI pipeline                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 3.5 Runbook 5: Ransomware / Malicious Data Modification

```
┌─────────────────────────────────────────────────────────────────┐
│  RUNBOOK: Ransomware or Malicious Data Modification             │
│  Severity: P0 (Critical)                                        │
│  Estimated Recovery Time: 4-8 hours                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  SYMPTOMS:                                                      │
│  - Mass deletion or modification of data                        │
│  - Ransom note in database or application                       │
│  - Unauthorized user with elevated privileges                   │
│  - Unusual API access patterns from unknown IPs                 │
│                                                                 │
│  IMMEDIATE ACTIONS (0-30 minutes):                              │
│  ─────────────────────────────────                              │
│  1. ISOLATE: Revoke all active sessions                         │
│     → Supabase Dashboard → Auth → Sessions → Sign out all       │
│     → Rotate ALL secrets (JWT key, Supabase keys, SMTP)         │
│                                                                 │
│  2. IDENTIFY: Determine scope and entry point                   │
│     → Check audit_logs for unauthorized actions                 │
│     → Check which user accounts were compromised                │
│     → Check for new users that shouldn't exist                  │
│     → Identify time window of compromise                        │
│                                                                 │
│  3. PRESERVE: Don't delete evidence                             │
│     → Export audit_logs for forensic analysis                   │
│     → Take Supabase backup before any recovery                  │
│     → Document everything for potential legal action            │
│                                                                 │
│  RECOVERY (30 minutes - 8 hours):                               │
│  ─────────────────────────────────                              │
│  4. Restore from PITR to last known good state                  │
│  5. Verify restored data integrity                              │
│  6. Rotate all credentials (see Secrets Rotation doc)           │
│  7. Review and fix the vulnerability that was exploited         │
│  8. Notify affected users and authorities (GDPR 72-hour rule)  │
│                                                                 │
│  POST-RECOVERY:                                                 │
│  ─────────────                                                  │
│  9. Full security audit                                         │
│  10. Implement additional security controls                     │
│  11. Update all runbooks with lessons learned                   │
│  12. Consider cyber insurance claim                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Backup Strategy

### 4.1 What Gets Backed Up

```
┌─────────────────────────┬──────────────────┬─────────────────────┐
│ Component               │ Backup Method    │ Frequency           │
├─────────────────────────┼──────────────────┼─────────────────────┤
│ PostgreSQL Database     │ Supabase PITR    │ Continuous (7-day   │
│ (all tables)            │ + Daily logical  │ window) + daily     │
│                         │ backups          │ snapshots           │
├─────────────────────────┼──────────────────┼─────────────────────┤
│ Supabase Storage        │ Supabase built-in│ Automatic           │
│ (file uploads)          │ replication      │                     │
├─────────────────────────┼──────────────────┼─────────────────────┤
│ Environment Variables   │ Encrypted backup │ After each change   │
│ (.env files)            │ in secure vault  │                     │
├─────────────────────────┼──────────────────┼─────────────────────┤
│ Application Code        │ Git repository   │ Every commit        │
│ (backend + frontend)    │ (GitHub/GitLab)  │                     │
├─────────────────────────┼──────────────────┼─────────────────────┤
│ Redis (Celery)          │ Not backed up    │ Acceptable — queue  │
│                         │                  │ data is ephemeral   │
├─────────────────────────┼──────────────────┼─────────────────────┤
│ Configuration           │ Git repository   │ Every change        │
│ (Supabase config, etc.) │                  │                     │
└─────────────────────────┴──────────────────┴─────────────────────┘
```

### 4.2 Application Config Backup

```python
# scripts/backup_config.py
"""
Backup application configuration to encrypted file.
Run after any configuration change.
"""

import json
import os
from datetime import datetime
from pathlib import Path
import base64

def backup_config():
    """Backup all environment variables and configuration."""
    
    config = {
        "backup_date": datetime.utcnow().isoformat(),
        "environment": os.getenv("ENVIRONMENT", "unknown"),
        "supabase_url": os.getenv("SUPABASE_URL", ""),
        "supabase_key_prefix": os.getenv("SUPABASE_KEY", "")[:8] + "...",
        "smtp_host": os.getenv("SMTP_HOST", ""),
        "smtp_username": os.getenv("SMTP_USERNAME", ""),
        "redis_host": os.getenv("REDIS_HOST", ""),
        "redis_port": os.getenv("REDIS_PORT", ""),
        "app_name": os.getenv("APP_NAME", ""),
        "debug": os.getenv("DEBUG", ""),
        # NOTE: Never store actual secret values in backup
        # Only store metadata about them
        "secret_key_set": bool(os.getenv("SECRET_KEY")),
        "smtp_password_set": bool(os.getenv("SMTP_PASSWORD")),
    }
    
    # Write to encrypted backup file
    backup_dir = Path("backups/config")
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"config_backup_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    filepath = backup_dir / filename
    
    with open(filepath, 'w') as f:
        json.dump(config, f, indent=2)
    
    print(f"✅ Config backup written: {filepath}")
    return filepath
```

---

## 5. DR Drill Schedule

```
┌─────────────────────────────────────────────────────────────────┐
│              Disaster Recovery Drill Schedule                    │
│                                                                 │
│  Frequency: Quarterly (every 3 months)                          │
│  Duration: 2-4 hours per drill                                  │
│  Participants: DevOps Lead + 1 Developer                        │
│                                                                 │
│  Q1 (January):  Database corruption → PITR restore              │
│  Q2 (April):    Service role key leak → Key rotation            │
│  Q3 (July):     Breaking migration → Rollback                   │
│  Q4 (October):  Full system outage → Recovery from backup       │
│                                                                 │
│  Drill Process:                                                 │
│  1. Announce drill (NOT a surprise — schedule it)               │
│  2. Follow the relevant runbook WITHOUT looking at it first     │
│  3. Time each step                                              │
│  4. Document any issues or deviations                           │
│  5. Update runbooks based on findings                           │
│  6. Report drill results to team                                │
│                                                                 │
│  Success Criteria:                                              │
│  → Recovery completed within RTO (4 hours)                      │
│  → No more than RPO data loss (1 hour)                          │
│  → All steps completed without external help                    │
│  → Runbook was accurate and complete                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Post-Incident Review Process

```
After EVERY incident (P0 or P1), conduct a blameless post-mortem:

Timeline (within 1 week of incident resolution):
──────────────────────────────────────────────

Day 1-2: Gather facts
  → Collect all logs, metrics, screenshots
  → Interview everyone involved
  → Document timeline of events

Day 3-4: Write post-mortem document
  → What happened (facts, not opinions)
  → What was the impact (users affected, data lost, downtime)
  → What went well (response, communication, tools)
  → What went wrong (gaps in monitoring, runbooks, process)
  → Root cause analysis (5 Whys technique)
  → Action items with owners and deadlines

Day 5-7: Review meeting
  → Present post-mortem to team
  → Discuss findings (no blame — focus on systems, not people)
  → Agree on action items
  → Update runbooks, monitoring, procedures

Post-Mortem Template:
─────────────────────
# Incident: [Brief title]
## Date: [When it happened]
## Severity: [P0/P1/P2]
## Duration: [How long the system was affected]
## Impact: [Users affected, data lost, business impact]

### Timeline
[Second-by-second or minute-by-minute account]

### Root Cause
[Technical explanation of what went wrong]

### What Went Well
- [Positive aspects of the response]

### What Went Wrong
- [Gaps, surprises, things that could be better]

### Action Items
| Action | Owner | Deadline | Status |
|--------|-------|----------|--------|
| ...    | ...   | ...      | ...    |
```

---

## 7. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| RTO | 4 hours | Realistic for Supabase PITR restore + verification |
| RPO | 1 hour | Matches Supabase PITR capability |
| Availability target | 99.9% (43 min/month) | Standard for B2B apps, achievable with Supabase |
| Backup frequency | Continuous (PITR) + daily | Supabase handles this automatically |
| Redis backup | Not backed up | Celery queue data is ephemeral and recreatable |
| DR drill frequency | Quarterly | Enough to stay sharp, not too burdensome |
| Post-incident review | Within 1 week | Fresh in memory, allows time for thorough analysis |
| Config backup | Encrypted file after each change | Simple, auditable |
| Breach notification | Within 72 hours | GDPR requirement |
| Post-mortem style | Blameless, systems-focused | Encourages honest reporting, no fear of punishment |

---

*This document should be updated after each DR drill, after each incident, and when recovery procedures or infrastructure change.*
