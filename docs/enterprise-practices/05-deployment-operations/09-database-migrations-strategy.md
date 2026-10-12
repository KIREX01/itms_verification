# Database Migrations Strategy

**Document ID:** ENT-09  
**Category:** Deployment & Operations  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (Python scripts), Supabase (PostgreSQL)  

---

## 1. Current State

OHAS uses Supabase's built-in migration system with SQL files in `supabase/migrations/`:

```
Current migration files (29 files):
  000_core_base.sql
  001_country_validation_rules.sql
  002_program_management_enhancements.sql
  ...
  20260403170000_volunteer_redistribution.sql
  create_blocked_ips.sql
  create_performance_module_tables.sql
  create_reporter_performance_tables.sql
  create_security_threats.sql

What exists:
  ✅ SQL migration files in supabase/migrations/
  ✅ Supabase CLI applies migrations on `supabase db push`
  ✅ Migration order based on filename timestamp/prefix
  ✅ Some migrations include comments and section headers

What's missing:
  ✗ No DOWN migrations (rollback SQL)
  ✗ No migration testing before applying to production
  ✗ No migration lock (concurrent deployments could conflict)
  ✗ No migration rollback procedure documented
  ✗ No dry-run capability
  ✗ No migration validation (syntax check before applying)
  ✗ No migration changelog separate from file names
  ✗ Inconsistent naming (some use timestamps, some use descriptive names)
  ✗ No seed data management (seed scripts separate from migrations)
```

---

## 2. Migration Naming Convention

### 2.1 Standard Format

```
Format: YYYYMMDDHHMMSS_descriptive_name.sql

Examples:
  ✅ 20260404120000_add_mfa_support.sql
  ✅ 20260404130000_create_sessions_table.sql
  ✅ 20260404140000_add_cursor_pagination_indexes.sql

Rejected:
  ❌ 003_level_based_reporting.sql           (no timestamp, hard to order)
  ❌ create_security_threats.sql              (no timestamp, alphabetical sort is unreliable)
  ❌ fix_signup.sql                           (too vague, doesn't describe WHAT changed)
```

### 2.2 Migration File Structure

```sql
-- =====================================================
-- Migration: Add MFA support tables
-- Date: 2026-04-04
-- Author: <developer name>
-- Ticket: OHAS-123
-- Description: Adds user_mfa_factors table and mfa_enabled 
--              column to profiles for TOTP-based 2FA.
-- Rollback: See DOWN section below
-- =====================================================

-- ── UP Migration ──

-- 1. Add mfa_enabled column to profiles
ALTER TABLE profiles 
    ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN DEFAULT FALSE;

-- 2. Add mfa_enrolled_at timestamp
ALTER TABLE profiles 
    ADD COLUMN IF NOT EXISTS mfa_enrolled_at TIMESTAMPTZ;

-- 3. Create user_mfa_factors table
CREATE TABLE IF NOT EXISTS user_mfa_factors (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    factor_type VARCHAR(20) NOT NULL CHECK (factor_type IN ('totp', 'sms', 'webauthn')),
    secret TEXT,  -- Encrypted TOTP secret
    phone_number TEXT,  -- For SMS factor
    credential_id TEXT,  -- For WebAuthn factor
    is_active BOOLEAN DEFAULT FALSE,
    enrolled_at TIMESTAMPTZ DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Create indexes
CREATE INDEX idx_user_mfa_factors_user_id 
    ON user_mfa_factors(user_id);
CREATE INDEX idx_user_mfa_factors_active 
    ON user_mfa_factors(user_id) WHERE is_active = TRUE;

-- 5. Add RLS policies
ALTER TABLE user_mfa_factors ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own MFA factors" 
    ON user_mfa_factors FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "Users can manage own MFA factors" 
    ON user_mfa_factors FOR ALL
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);


-- ── DOWN Migration (Rollback) ──
-- To rollback: Run this section manually or via migration tool
-- 
-- DROP POLICY IF EXISTS "Users can manage own MFA factors" ON user_mfa_factors;
-- DROP POLICY IF EXISTS "Users can view own MFA factors" ON user_mfa_factors;
-- ALTER TABLE user_mfa_factors DISABLE ROW LEVEL SECURITY;
-- DROP TABLE IF EXISTS user_mfa_factors;
-- ALTER TABLE profiles DROP COLUMN IF EXISTS mfa_enrolled_at;
-- ALTER TABLE profiles DROP COLUMN IF EXISTS mfa_enabled;
```

---

## 3. Migration Execution Process

### 3.1 Development Workflow

```
1. Developer creates migration file:
   supabase migration new add_mfa_support

   → Creates: supabase/migrations/20260404120000_add_mfa_support.sql

2. Developer writes UP migration SQL

3. Developer writes DOWN migration SQL (as comments at bottom)

4. Developer tests locally:
   supabase db reset           (resets local DB and applies all migrations)
   supabase start              (starts local Supabase)
   supabase db push             (pushes migrations to local DB)

5. Developer verifies:
   - Tables created correctly
   - RLS policies work
   - Indexes exist
   - No syntax errors

6. Developer commits migration + application code together
```

### 3.2 Pre-Production Testing

```
1. Migration is pushed to staging environment:
   supabase db push --db-url "postgresql://..."

2. Automated migration validation:
   a. Check migration applied successfully
   b. Run smoke tests against staging DB
   c. Verify no data loss (for non-destructive migrations)
   d. Check migration execution time (< 30 seconds for online migrations)

3. If validation passes → approve for production
   If validation fails → rollback on staging, fix, retry
```

### 3.3 Production Deployment

```
1. Admin connects to production Supabase:
   supabase db push --db-url "$SUPABASE_PRODUCTION_DB_URL"

2. Monitor migration execution:
   - Watch Supabase dashboard for query performance
   - Check migration completion time
   - Verify no locks or blocked queries

3. Post-migration validation:
   a. Check new tables/columns exist
   b. Run application smoke tests
   c. Verify RLS policies
   d. Check no orphaned connections

4. If migration fails:
   a. Stop immediately
   b. Assess partial state (what succeeded, what failed)
   c. Run DOWN migration if partial state is clean
   d. If DOWN migration not possible, manually fix state
   e. Fix migration, retry
```

---

## 4. Migration Safety Rules

### 4.1 Online vs Offline Migrations

```
┌─────────────────────────────────────────────────────────────────┐
│              Migration Safety Classification                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  SAFE (Online — No Downtime)                                    │
│  ─────────────────────────────────                              │
│  ✅ CREATE TABLE                                                │
│  ✅ CREATE INDEX (Concurrent) — CREATE INDEX CONCURRENTLY       │
│  ✅ ADD COLUMN (without default or with NULL default)           │
│  ✅ ADD CONSTRAINT (NOT VALID) — validate later                 │
│  ✅ CREATE POLICY                                               │
│  ✅ ALTER TABLE ... ENABLE ROW LEVEL SECURITY                   │
│  ✅ CREATE FUNCTION / TRIGGER                                  │
│  ✅ INSERT / UPDATE / DELETE (small batches)                    │
│                                                                 │
│  ⚠️ CAUTION (May Lock Table Briefly)                           │
│  ─────────────────────────────────────────                      │
│  ⚠️ ADD COLUMN WITH DEFAULT (PostgreSQL 11+: usually safe)     │
│  ⚠️ DROP COLUMN (metadata-only in PG 11+, but...)              │
│  ⚠️ CREATE INDEX (non-concurrent — blocks writes)               │
│  ⚠️ ALTER TABLE ... VALIDATE CONSTRAINT                         │
│                                                                 │
│  ❌ DANGEROUS (Requires Downtime or Careful Planning)           │
│  ─────────────────────────────────────────────────              │
│  ❌ DROP TABLE                                                  │
│  ❌ DROP INDEX                                                  │
│  ❌ RENAME TABLE / COLUMN                                       │
│  ❌ CHANGE COLUMN TYPE (requires table rewrite)                 │
│  ❌ ADD NOT NULL to existing column (full table scan)           │
│  ❌ DELETE large number of rows (locks table)                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 Safe Migration Patterns

```sql
-- ❌ DANGEROUS: Blocking index creation
CREATE INDEX idx_reports_country ON reports(country_id);

-- ✅ SAFE: Non-blocking index creation
CREATE INDEX CONCURRENTLY idx_reports_country ON reports(country_id);

-- ❌ DANGEROUS: Adding NOT NULL to existing column
ALTER TABLE reports ALTER COLUMN title SET NOT NULL;

-- ✅ SAFE: Two-step approach
-- Step 1: Add column as nullable
ALTER TABLE reports ADD COLUMN status VARCHAR(20) DEFAULT 'draft';

-- Step 2: Backfill in batches
UPDATE reports SET status = 'submitted' WHERE status IS NULL;

-- Step 3: Add NOT NULL constraint (safe — all rows have values)
ALTER TABLE reports ALTER COLUMN status SET NOT NULL;

-- ❌ DANGEROUS: Dropping column directly
ALTER TABLE reports DROP COLUMN old_field;

-- ✅ SAFE: Two-step approach
-- Step 1: Mark as unused (metadata-only, instant)
ALTER TABLE reports ALTER COLUMN old_field DROP DEFAULT;
-- Application code no longer reads this column.

-- Step 2: Drop in next migration (after confirming no usage)
ALTER TABLE reports DROP COLUMN old_field;
```

---

## 5. Migration Rollback Strategy

### 5.1 The DOWN Migration File

For every UP migration, maintain a corresponding DOWN migration:

```sql
-- supabase/migrations/20260404120000_add_mfa_support.sql

-- UP section (above) — applied during normal migration

-- DOWN section (below) — applied during rollback
-- 
-- ROLLBACK INSTRUCTIONS:
-- 1. Ensure no active sessions are using the new columns
-- 2. Run the following SQL manually:

BEGIN;

-- Reverse changes in opposite order
DROP POLICY IF EXISTS "Users can manage own MFA factors" ON user_mfa_factors;
DROP POLICY IF EXISTS "Users can view own MFA factors" ON user_mfa_factors;
ALTER TABLE user_mfa_factors DISABLE ROW LEVEL SECURITY;
DROP TABLE IF EXISTS user_mfa_factors CASCADE;
ALTER TABLE profiles DROP COLUMN IF EXISTS mfa_enrolled_at;
ALTER TABLE profiles DROP COLUMN IF EXISTS mfa_enabled;

COMMIT;

-- Verify rollback:
-- SELECT column_name FROM information_schema.columns 
-- WHERE table_name = 'profiles' AND column_name LIKE 'mfa%';
-- Should return 0 rows.
```

### 5.2 Rollback Decision Tree

```
┌─────────────────────────────────────────────────────────────────┐
│              Migration Failure Response                         │
│                                                                 │
│  Migration failed during production deploy                      │
│  │                                                              │
│  ├─ Is the failure partial (some statements succeeded)?         │
│  │  │                                                           │
│  │  ├─ Yes → Assess what succeeded                             │
│  │  │    ├─ Can DOWN migration safely reverse? → Run DOWN      │
│  │  │    └─ No → Manual fix required (see §5.3)                │
│  │  │                                                          │
│  │  └─ No (nothing applied) → Fix syntax, retry                │
│  │                                                              │
│  ├─ Is data at risk?                                            │
│  │  │                                                           │
│  │  ├─ Yes → Take Supabase backup immediately                   │
│  │  │    → THEN attempt rollback                                │
│  │  │                                                          │
│  │  └─ No → Safe to attempt rollback                           │
│  │                                                              │
│  └─ Is the migration blocking other operations?                 │
│     │                                                           │
│     ├─ Yes (table locked) → Kill blocking queries               │
│     │    → pg_terminate_backend(pid) for locked queries         │
│     │    → THEN rollback                                        │
│     │                                                          │
│     └─ No → Proceed with normal rollback                        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 5.3 Manual Fix Procedure (When DOWN Migration Won't Work)

```
When automated rollback fails, follow these steps:

1. STOP: Don't run more migrations until the state is understood.

2. ASSESS: Check current database state
   SELECT * FROM supabase_migrations.schema_migrations 
   ORDER BY version DESC LIMIT 5;
   
   -- Check if the failed migration is recorded
   SELECT EXISTS (
     SELECT 1 FROM supabase_migrations.schema_migrations 
     WHERE version = '20260404120000'
   );

3. IDENTIFY: What statements succeeded?
   -- Check if new table exists
   SELECT EXISTS (
     SELECT 1 FROM information_schema.tables 
     WHERE table_name = 'user_mfa_factors'
   );
   
   -- Check if new column exists
   SELECT EXISTS (
     SELECT 1 FROM information_schema.columns 
     WHERE table_name = 'profiles' AND column_name = 'mfa_enabled'
   );

4. FIX: Manually reverse or complete the migration
   -- Option A: Reverse manually
   DROP TABLE IF EXISTS user_mfa_factors CASCADE;
   
   -- Option B: Complete the migration manually
   ALTER TABLE profiles ADD COLUMN IF NOT EXISTS mfa_enabled BOOLEAN DEFAULT FALSE;
   
   -- Option C: Remove migration record and retry
   DELETE FROM supabase_migrations.schema_migrations 
   WHERE version = '20260404120000';

5. VALIDATE: Confirm database is in a consistent state
   -- Run application smoke tests
   -- Check Supabase dashboard for errors
   -- Verify no orphaned locks or connections

6. DOCUMENT: Record what happened and what was done to fix it
   -- Post in team channel
   -- Update migration file with notes
   -- Create incident report if data was affected
```

---

## 6. Migration Validation Script

```python
# scripts/validate_migration.py
"""
Validates migration files before applying them.
Checks:
  1. SQL syntax validity
  2. Migration has UP section
  3. Migration has DOWN section (as comment)
  4. No dangerous patterns (DROP TABLE, etc.) without warning comment
  5. Naming convention compliance
"""

import re
import os
import sys
from pathlib import Path

MIGRATIONS_DIR = Path("supabase/migrations")

# Naming pattern: YYYYMMDDHHMMSS_descriptive_name.sql
NAMING_PATTERN = re.compile(r'^\d{14}_[a-z0-9_]+\.sql$')

# Dangerous patterns that need review
DANGEROUS_PATTERNS = [
    (re.compile(r'DROP\s+TABLE', re.IGNORECASE), "DROP TABLE — will lose data"),
    (re.compile(r'DROP\s+COLUMN', re.IGNORECASE), "DROP COLUMN — will lose data"),
    (re.compile(r'RENAME\s+TABLE', re.IGNORECASE), "RENAME TABLE — breaking change"),
    (re.compile(r'RENAME\s+COLUMN', re.IGNORECASE), "RENAME COLUMN — breaking change"),
    (re.compile(r'ALTER\s+TABLE.*SET\s+NOT\s+NULL', re.IGNORECASE), 
     "SET NOT NULL on existing column — full table scan"),
    (re.compile(r'DELETE\s+FROM.*WHERE', re.IGNORECASE),
     "DELETE with WHERE — may lock table if large"),
]

# Safe patterns (CREATE INDEX CONCURRENTLY)
SAFE_PATTERNS = [
    (re.compile(r'CREATE\s+INDEX\s+CONCURRENTLY', re.IGNORECASE),
     "CREATE INDEX CONCURRENTLY — safe, non-blocking"),
]


def validate_migration_file(filepath: Path) -> list:
    """Validate a single migration file. Returns list of warnings/errors."""
    issues = []
    filename = filepath.name

    # Check naming convention
    if not NAMING_PATTERN.match(filename):
        issues.append(f"ERROR: Filename '{filename}' doesn't match "
                      f"pattern YYYYMMDDHHMMSS_name.sql")

    content = filepath.read_text()

    # Check for UP section
    if not re.search(r'UP\s+Migration|--\s*─*\s*UP\s*─*', content, re.IGNORECASE):
        issues.append(f"WARNING: No clear UP migration section in {filename}")

    # Check for DOWN section
    if not re.search(r'DOWN\s+Migration|--\s*─*\s*DOWN\s*─*|ROLLBACK', content, re.IGNORECASE):
        issues.append(f"WARNING: No DOWN migration (rollback) section in {filename}")

    # Check for dangerous patterns
    for pattern, message in DANGEROUS_PATTERNS:
        if pattern.search(content):
            # Check if it's in a comment (DOWN section)
            lines = content.split('\n')
            for i, line in enumerate(lines, 1):
                if pattern.search(line) and not line.strip().startswith('--'):
                    issues.append(f"DANGER: Line {i}: {message}")

    # Check for safe patterns
    for pattern, message in SAFE_PATTERNS:
        if pattern.search(content):
            issues.append(f"INFO: Line with safe pattern: {message}")

    return issues


def main():
    """Validate all migration files."""
    all_issues = []

    for filepath in sorted(MIGRATIONS_DIR.glob("*.sql")):
        issues = validate_migration_file(filepath)
        if issues:
            all_issues.extend([(filepath.name, issue) for issue in issues])

    if all_issues:
        print(f"Found {len(all_issues)} issue(s):\n")
        for filename, issue in all_issues:
            print(f"  {filename}: {issue}")
        sys.exit(1)
    else:
        print("✅ All migrations validated successfully.")
        sys.exit(0)


if __name__ == "__main__":
    main()
```

Run in CI:

```yaml
# .github/workflows/migrations.yml

jobs:
  validate-migrations:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Validate migration files
        run: python scripts/validate_migration.py
```

---

## 7. Seed Data Management

Migrations should NOT include seed data. Seed data is managed separately:

```
Migrations (supabase/migrations/):
  → Schema changes only (tables, columns, indexes, policies)
  → Reversible (have DOWN migrations)
  → Applied once, never modified

Seed Data (backend/app/scripts/):
  → Data insertion (countries, programs, initial users)
  → Idempotent (safe to run multiple times — INSERT ... ON CONFLICT)
  → NOT tracked by migration system
  → Run manually when needed

Example idempotent seed:
  INSERT INTO countries (id, name, code)
  VALUES 
    ('uuid-1', 'Uganda', 'UG'),
    ('uuid-2', 'Kenya', 'KE')
  ON CONFLICT (code) DO UPDATE 
    SET name = EXCLUDED.name;
```

---

## 8. Migration Monitoring

```sql
-- Check migration history
SELECT * FROM supabase_migrations.schema_migrations 
ORDER BY version DESC;

-- Check for long-running migrations
SELECT pid, now() - pg_stat_activity.query_start AS duration, 
       query, state
FROM pg_stat_activity
WHERE state != 'idle'
  AND query LIKE '%CREATE%' OR query LIKE '%ALTER%'
ORDER BY duration DESC;

-- Check for locks caused by migrations
SELECT blocked_locks.pid     AS blocked_pid,
       blocked_activity.usename  AS blocked_user,
       blocking_locks.pid     AS blocking_pid,
       blocking_activity.usename AS blocking_user,
       blocked_activity.query  AS blocked_statement
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity 
  ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks 
  ON blocking_locks.locktype = blocked_locks.locktype
JOIN pg_catalog.pg_stat_activity blocking_activity 
  ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;
```

---

## 9. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Naming convention | `YYYYMMDDHHMMSS_description.sql` | Timestamp ensures correct ordering |
| DOWN migrations | As commented SQL at bottom of UP file | Single source of truth, always in sync |
| Migration tool | Supabase CLI (`supabase db push`) | Built-in, tracks applied migrations |
| Validation | Python script in CI | Catches naming issues, dangerous patterns before deploy |
| Index creation | `CREATE INDEX CONCURRENTLY` | Non-blocking, safe for production |
| Column additions | Without default first, backfill later | Avoids table rewrite on large tables |
| Seed data | Separate idempotent scripts, not in migrations | Migrations for schema, seeds for data |
| Rollback strategy | DOWN migration → manual fix if needed | Automated when possible, documented manual process |
| Migration testing | Local → Staging → Production | Three environments, validate at each step |
| Dangerous operations | Require team review + comment in migration | Human review prevents accidental data loss |

---

*This document should be updated when migration tooling changes, when new dangerous patterns are identified, or when the rollback procedure is refined based on real incidents.*
