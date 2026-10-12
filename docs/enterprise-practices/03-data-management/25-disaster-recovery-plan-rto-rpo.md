# Disaster Recovery Plan (RTO/RPO)

**Document ID:** ENT-25  
**Category:** Data Management  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Entire OHAS System — Backend, Frontend, Database, Infrastructure  

---

## 1. Recovery Objectives

### 1.1 Definitions

```
┌─────────────────────────────────────────────────────────────────┐
│              Recovery Objectives                                 │
│                                                                 │
│  RTO (Recovery Time Objective): 4 hours                        │
│  ─────────────────────────────────                              │
│  Maximum acceptable time from incident declaration to           │
│  system being operational again.                                │
│                                                                 │
│  "If the system goes down at 2:00 PM, it must be back           │
│   online by 6:00 PM at the latest."                             │
│                                                                 │
│  RPO (Recovery Point Objective): 1 hour                        │
│  ─────────────────────────────────                              │
│  Maximum acceptable data loss measured in time.                 │
│  The system can lose up to 1 hour of data.                      │
│                                                                 │
│  "If disaster strikes at 2:00 PM, we can restore to             │
│   the state at 1:00 PM. The 1:00-2:00 PM window is lost."      │
│                                                                 │
│  Availability Target: 99.9%                                     │
│  ─────────────────────────────                                  │
│  Allows 43 minutes of unplanned downtime per month              │
│  or 8.76 hours per year.                                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 What These Numbers Mean in Practice

```
Worst-case scenario: Complete database corruption at peak hours

  14:00  Corruption detected (data integrity check fails)
  14:05  Incident commander declared, DR plan activated
  14:15  Root cause identified (buggy migration altered columns)
  14:30  Decision: Restore from PITR to 13:00 (before migration)
  14:45  Supabase PITR restore initiated
  15:30  Database restore complete
  15:45  Backend restarted and verified
  16:00  System operational ✅

  RTO: 2 hours (target: 4 hours) ✅
  RPO: 1 hour (data from 13:00 restored, 13:00-14:00 lost) ✅

  Data lost: ~1 hour of submissions
  → Approximately 8 reports from ~100 reporters
  → Reporters notified and asked to resubmit
```

---

## 2. Disaster Scenarios

```
┌───────────────────────┬──────────────┬────────────────────────────┐
│ Scenario              │ RTO          │ Recovery Method           │
├───────────────────────┼──────────────┼────────────────────────────┤
│ Database corruption   │ 2-4 hours    │ Supabase PITR restore      │
│ (buggy migration,     │              │ to point before            │
│  bad data)            │              │ corruption                 │
├───────────────────────┼──────────────┼────────────────────────────┤
│ Supabase region       │ External     │ Wait for Supabase recovery │
│ outage                │ (Supabase    │ Activate accept-only mode  │
│                       │  dependent)  │ → Process queued data      │
│                       │              │ when back online           │
├───────────────────────┼──────────────┼────────────────────────────┤
│ Service role key      │ 1-2 hours    │ Regenerate key → Update    │
│ compromised           │              │ env → Restart → Audit      │
├───────────────────────┼──────────────┼────────────────────────────┤
│ Breaking migration     │ 1-2 hours    │ DOWN migration or PITR     │
│ deployed              │              │ rollback                   │
├───────────────────────┼──────────────┼────────────────────────────┤
│ Ransomware /          │ 4-8 hours    │ Full PITR restore → Rotate │
│ malicious modification│              │ all secrets → Audit →      │
│                       │              │ Notify authorities         │
├───────────────────────┼──────────────┼────────────────────────────┤
│ Frontend deployment   │ 30 minutes   │ Redeploy previous version  │
│ breaks production     │              │ from build artifact        │
├───────────────────────┼──────────────┼────────────────────────────┤
│ Backend deployment    │ 1 hour       │ Auto-rollback via CI/CD    │
│ breaks production     │              │ or manual redeploy of      │
│                       │              │ previous Docker image      │
├───────────────────────┼──────────────┼────────────────────────────┤
│ Complete region       │ 4-8 hours    │ Provision new Supabase     │
│ failure (rare)        │              │ project in new region →    │
│                       │              │ Restore from backup →      │
│                       │              │ Reconfigure app            │
└───────────────────────┴──────────────┴────────────────────────────┘
```

---

## 3. Recovery Procedures

### 3.1 Database Corruption Recovery

```
STEP-BY-STEP: Database Corruption Recovery
──────────────────────────────────────────

Phase 1: Detection & Assessment (0-30 minutes)
──────────────────────────────────────────────

1. Detect the corruption:
   - Automated: Monitoring alert (error rate spike, query failures)
   - Manual: User reports incorrect data
   - Proactive: Post-migration verification check

2. Assess scope:
   SELECT table_name, 
          pg_size_pretty(pg_total_relation_size(quote_ident(table_name))) as size
   FROM information_schema.tables 
   WHERE table_schema = 'public'
   ORDER BY pg_total_relation_size(quote_ident(table_name)) DESC;

3. Check recent changes:
   - Recent migrations? (supabase_migrations.schema_migrations)
   - Recent deployments? (check CI/CD logs)
   - Manual database changes? (check audit_logs)

4. Declare incident:
   - Assign incident commander
   - Create incident channel (Slack/Teams)
   - Begin timeline documentation


Phase 2: Recovery Decision (30-60 minutes)
───────────────────────────────────────────

5. Determine recovery point:
   - Find last known good state
   - Check audit_logs for last clean operation
   - Check deployment history for last stable release
   - Example: "Last good state was at 13:00, before migration at 13:15"

6. Decision: Restore or fix forward?
   - If data corruption is widespread → Restore from PITR
   - If limited to few rows → Fix individual rows
   - If structural (schema) change needed → Fix forward with patch


Phase 3: Recovery Execution (1-3 hours)
────────────────────────────────────────

7. Execute PITR restore:
   a. Go to Supabase Dashboard → Database → Backups
   b. Select "Point-in-Time Recovery"
   c. Choose timestamp: [time before corruption]
   d. Click "Restore"
   e. Monitor restore progress (typically 15-60 minutes)

8. Verify restored data:
   a. Check row counts match expectations:
      SELECT COUNT(*) FROM profiles;
      SELECT COUNT(*) FROM reports;
      SELECT COUNT(*) FROM countries;
   
   b. Check data integrity:
      SELECT * FROM profiles WHERE id = 'known-good-id';
      SELECT * FROM reports ORDER BY created_at DESC LIMIT 10;
   
   c. Check RLS policies still work:
      SELECT count(*) FROM reports WHERE country_id = 'specific-country';

9. Re-apply legitimate migrations (if needed):
   - If PITR rolled back good migrations that ran before corruption
   - supabase db push --db-url "$RESTORED_DB_URL"
   - Test each migration individually

10. Restart backend:
    - docker-compose restart backend
    - Wait for health check: curl -f https://api.ohas.system/health


Phase 4: Post-Recovery (3-4 hours)
───────────────────────────────────

11. Verify system end-to-end:
    - User login works
    - Report submission works
    - Dashboard loads correctly
    - Admin functions work

12. Assess data loss:
    - Time window of lost data: [restore_point] to [incident_time]
    - Estimated lost records: ~8 reports, ~5 participant updates
    - Affected users: contact and request resubmission

13. Communicate:
    - Internal: Incident report to team
    - External: Notification to affected users (if personal data involved)

14. Document:
    - Timeline of events
    - Root cause
    - Recovery steps taken
    - Lessons learned

15. Schedule post-mortem within 1 week
```

### 3.2 Complete System Recovery (Nuclear Option)

```
When everything is lost and you need to rebuild from scratch:

Phase 1: Infrastructure Setup (1-2 hours)
─────────────────────────────────────────

1. Provision new Supabase project in available region
2. Configure Supabase settings:
   - Enable email confirmations
   - Configure custom domain
   - Set up storage buckets
   - Configure SMTP settings
3. Create new Redis instance
4. Provision backend hosting server
5. Provision frontend hosting (Vercel/Netlify/custom)

Phase 2: Database Restoration (30 minutes - 2 hours)
─────────────────────────────────────────────────────

6. Restore from latest backup:
   - Supabase Dashboard → Settings → Backups → Restore
   - OR: pg_restore from logical backup file

7. Verify data integrity:
   - Run data validation scripts
   - Check row counts against known-good numbers
   - Verify RLS policies

Phase 3: Application Deployment (30 minutes)
────────────────────────────────────────────

8. Deploy backend:
   - Update environment variables with new Supabase URL/keys
   - docker-compose up -d (or deploy to cloud platform)
   - Run health checks

9. Deploy frontend:
   - npm run build
   - Deploy to hosting platform
   - Verify frontend connects to backend

10. Verify end-to-end:
    - User login
    - Report submission
    - Dashboard viewing
    - Admin functions

Phase 4: Data Reconciliation (1-2 hours)
─────────────────────────────────────────

11. Compare restored data with last known state
12. Identify data loss window
13. Contact affected users
14. Request resubmission of lost data

Phase 5: Communication & Documentation (1 hour)
────────────────────────────────────────────────

15. Notify all users of system recovery
16. Explain what happened (transparency)
17. Provide timeline for full data restoration
18. Document entire recovery process
19. Schedule post-mortem
```

---

## 4. DR Drill Procedure

```
┌─────────────────────────────────────────────────────────────────┐
│              Quarterly DR Drill                                  │
│                                                                 │
│  Schedule: Every 3 months (Jan, Apr, Jul, Oct)                  │
│  Duration: 2-4 hours                                            │
│  Participants: DevOps Lead + 1 Developer                        │
│                                                                 │
│  Drill Procedure:                                                │
│  ───────────────                                                │
│                                                                 │
│  1. Preparation (1 week before)                                 │
│     → Announce drill to team (NOT a surprise)                   │
│     → Prepare staging environment for safe testing              │
│     → Take backup of staging database                           │
│     → Prepare drill checklist                                   │
│                                                                 │
│  2. Execute Drill (scheduled day)                               │
│     → Follow the relevant runbook WITHOUT looking at it first   │
│     → Time each step                                            │
│     → Document deviations from expected procedure               │
│     → Record what worked and what didn't                        │
│                                                                 │
│  3. Restore (after drill)                                       │
│     → Restore staging to pre-drill state                        │
│     → Verify everything works normally                          │
│     → Confirm no production impact                              │
│                                                                 │
│  4. Review (within 1 week)                                      │
│     → Document findings                                         │
│     → Update runbooks based on lessons learned                  │
│     → Report results to team                                    │
│     → Update RTO/RPO if needed                                  │
│                                                                 │
│  Quarterly Drill Schedule:                                      │
│  ──────────────────────────                                     │
│  Q1 (January):  Database corruption → PITR restore              │
│  Q2 (April):    Service role key leak → Key rotation            │
│  Q3 (July):     Breaking migration → Rollback                   │
│  Q4 (October):  Full system outage → Recovery from backup       │
│                                                                 │
│  Success Criteria:                                              │
│  ───────────────                                                │
│  ✅ Recovery completed within RTO (4 hours)                     │
│  ✅ No more than RPO data loss (1 hour)                         │
│  ✅ All steps completed without external help                   │
│  ✅ Runbook was accurate and complete                           │
│  ✅ No production systems affected                              │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. Post-Incident Review

```
After EVERY incident or DR drill:

Post-Incident Report Template:
──────────────────────────────

# Incident: [Brief descriptive title]
## Date & Time: [When it started and ended]
## Severity: [P0/Critical, P1/High, P2/Medium, P3/Low]
## Duration: [How long the system was affected]
## RTO Achieved: [Actual recovery time vs 4-hour target]
## RPO Achieved: [Actual data loss vs 1-hour target]

### Timeline
[Second-by-second or minute-by-minute account]

### Impact
- Users affected: [count and description]
- Data lost: [description and estimated volume]
- Business impact: [revenue, reputation, compliance]

### Root Cause
[Technical explanation using 5 Whys technique]

### What Went Well
- [Positive aspects of detection, response, recovery]

### What Went Wrong
- [Gaps in monitoring, runbooks, process, tools]

### Action Items
| # | Action | Owner | Deadline | Status |
|---|--------|-------|----------|--------|
| 1 | [Specific improvement] | [Name] | [Date] | [Open/Done] |
| 2 | ... | ... | ... | ... |

### Lessons Learned
[Key takeaways for the team]

### Runbook Updates Needed
[Which runbooks need updating and why]
```

---

## 6. DR Readiness Checklist

```
┌─────────────────────────────────────────────────────────────────┐
│              DR Readiness Checklist                              │
│              Check monthly, drill quarterly                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Infrastructure:                                                │
│  ☐ Supabase PITR is enabled and within retention window         │
│  ☐ Latest backup can be restored (tested last quarter)          │
│  ☐ All environment variables backed up securely                │
│  ☐ Docker images available in registry                          │
│  ☐ Frontend build artifacts preserved                           │
│                                                                 │
│  Documentation:                                                 │
│  ☐ All runbooks are up to date                                  │
│  ☐ Runbooks are accessible offline (not just in repo)           │
│  ☐ Emergency contacts are current                               │
│  ☐ Supabase support contact info is available                   │
│  ☐ Legal counsel contact for breach scenarios                   │
│                                                                 │
│  People:                                                        │
│  ☐ At least 2 people know the DR procedures                     │
│  ☐ Incident commander role is assigned                          │
│  ☐ Team members know their roles                                │
│  ☐ Last DR drill was within 3 months                            │
│                                                                 │
│  Monitoring:                                                    │
│  ☐ Alerts configured for critical failures                      │
│  ☐ Alert recipients are current                                 │
│  ☐ Alert channels are monitored (not going to empty Slack)      │
│  ☐ Error rate and latency monitoring active                     │
│                                                                 │
│  Recovery Tools:                                                │
│  ☐ Supabase CLI installed and configured                        │
│  ☐ Database access credentials available                        │
│  ☐ Deployment scripts tested and working                        │
│  ☐ Rollback procedures documented and tested                    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| RTO | 4 hours | Realistic for PITR restore + verification at current scale |
| RPO | 1 hour | Matches Supabase PITR capability |
| Availability target | 99.9% (43 min/month) | Standard for B2B, achievable with Supabase |
| Recovery method | Supabase PITR as primary | Built-in, tested, reliable |
| Nuclear option | Full rebuild from backup | Last resort when PITR is insufficient |
| DR drill frequency | Quarterly | Enough to stay sharp, not too burdensome |
| Post-incident review | Within 1 week of every incident | Fresh memory, allows thorough analysis |
| Documentation accessibility | Both in repo AND offline | If repo is inaccessible, docs must still be available |
| Recovery testing | On staging environment | Safe, realistic, no production impact |
| Communication | Transparent to users about data loss | Builds trust, meets GDPR requirements |

---

*This document should be updated after every DR drill, after every incident, and when infrastructure or recovery procedures change.*
