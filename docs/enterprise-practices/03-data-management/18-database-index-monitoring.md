# Database Index Monitoring

**Document ID:** ENT-18  
**Category:** Data Management  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Supabase (PostgreSQL), Backend (FastAPI)  

---

## 1. Current State

```
What exists:
  ✅ Some indexes created in migration files
  ✅ Composite indexes for cursor pagination (documented in ENT-15)
  ✅ RLS policy indexes

What's missing:
  ✗ No index usage monitoring
  ✗ No detection of missing indexes
  ✗ No detection of unused indexes
  ✗ No index creation guidelines
  ✗ No automated index recommendation
  ✗ No periodic index review process
```

---

## 2. Index Monitoring Queries

### 2.1 Find Missing Indexes (Sequential Scans on Large Tables)

```sql
-- Run monthly to identify tables that need indexes

-- Tables with high sequential scan counts (missing index candidates)
SELECT
    schemaname,
    relname AS table_name,
    seq_scan,
    seq_tup_read,
    idx_scan,
    idx_tup_fetch,
    CASE 
        WHEN seq_scan > 0 THEN seq_tup_read / seq_scan
        ELSE 0
    END AS avg_rows_per_seq_scan,
    pg_size_pretty(pg_relation_size(relid)) AS table_size
FROM pg_stat_user_tables
WHERE seq_scan > 1000  -- More than 1000 sequential scans
ORDER BY seq_tup_read DESC
LIMIT 20;

-- Queries running without index support
SELECT
    query,
    calls,
    mean_exec_time,
    total_exec_time,
    rows
FROM pg_stat_statements
WHERE query LIKE '%SELECT%'
  AND query NOT LIKE '%pg_%'
  AND total_exec_time > 1000  -- Slower than 1 second total
ORDER BY total_exec_time DESC
LIMIT 20;
```

### 2.2 Find Unused Indexes

```sql
-- Indexes that are never or rarely used (waste of storage and write performance)
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch,
    pg_size_pretty(pg_relation_size(indexrelid)) AS index_size
FROM pg_stat_user_indexes
WHERE idx_scan = 0  -- Never used
  AND NOT indisunique  -- Not a unique index (keep these)
ORDER BY pg_relation_size(indexrelid) DESC;
```

### 2.3 Find Duplicate Indexes

```sql
-- Indexes on the same columns (redundant)
SELECT
    a.schemaname,
    a.tablename,
    a.indexname AS index1,
    b.indexname AS index2,
    pg_get_indexdef(a.indexrelid) AS index1_def,
    pg_get_indexdef(b.indexrelid) AS index2_def
FROM pg_indexes a
JOIN pg_indexes b 
    ON a.tablename = b.tablename
    AND a.indexname < b.indexname
    AND pg_get_indexdef(a.indexrelid) = pg_get_indexdef(b.indexrelid)
WHERE a.schemaname = 'public';
```

### 2.4 Index Bloat

```sql
-- Indexes that are bloated (need REINDEX)
SELECT
    schemaname,
    tablename,
    indexname,
    pg_size_pretty(pg_relation_size(indexrelid)) AS current_size,
    idx_scan,
    idx_tup_read
FROM pg_stat_user_indexes
WHERE pg_relation_size(indexrelid) > 10 * 1024 * 1024  -- > 10MB
ORDER BY pg_relation_size(indexrelid) DESC;
```

---

## 3. Automated Index Monitoring

```python
# backend/app/worker.py — Monthly index review task

@celery_app.task(name="review_database_indexes")
async def review_database_indexes():
    """
    Monthly review of database indexes.
    Identifies missing, unused, and duplicate indexes.
    """
    results = {
        "missing_indexes": [],
        "unused_indexes": [],
        "duplicate_indexes": [],
        "recommendations": [],
    }
    
    # Check for high seq_scan tables
    seq_scan_query = """
    SELECT relname, seq_scan, seq_tup_read, idx_scan
    FROM pg_stat_user_tables
    WHERE seq_scan > 1000
    ORDER BY seq_tup_read DESC
    LIMIT 10
    """
    seq_scan_results = supabase.rpc("execute_sql", {"sql": seq_scan_query}).execute()
    
    for row in seq_scan_results.data:
        if row["seq_scan"] > row.get("idx_scan", 0) * 10:
            results["missing_indexes"].append({
                "table": row["relname"],
                "seq_scans": row["seq_scan"],
                "rows_read": row["seq_tup_read"],
                "recommendation": f"Consider adding an index on frequently queried columns in {row['relname']}",
            })
    
    # Check for unused indexes
    unused_query = """
    SELECT tablename, indexname, idx_scan
    FROM pg_stat_user_indexes
    WHERE idx_scan = 0 AND NOT indisunique
    """
    unused_results = supabase.rpc("execute_sql", {"sql": unused_query}).execute()
    
    for row in unused_results.data:
        results["unused_indexes"].append({
            "table": row["tablename"],
            "index": row["indexname"],
            "scans": row["idx_scan"],
            "recommendation": f"Consider dropping unused index {row['indexname']} on {row['tablename']}",
        })
    
    # Generate report
    if results["missing_indexes"] or results["unused_indexes"]:
        # Send to developer dashboard
        await notification_service.send_alert(
            title="Monthly Index Review",
            message=f"Found {len(results['missing_indexes'])} potential missing indexes and {len(results['unused_indexes'])} unused indexes",
            details=results,
            recipients=["developer"],
        )
    
    return results
```

---

## 4. Index Creation Guidelines

```
┌─────────────────────────────────────────────────────────────────┐
│              When to Create an Index                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  CREATE an index when:                                          │
│  ✅ Column is used in WHERE clauses frequently                  │
│  ✅ Column is used in JOIN conditions                           │
│  ✅ Column is used in ORDER BY or GROUP BY                      │
│  ✅ Table has > 10,000 rows and queries are slow                │
│  ✅ Query plan shows sequential scan on large table             │
│  ✅ Composite query pattern is common (create composite index)  │
│                                                                 │
│  DON'T create an index when:                                    │
│  ❌ Table has < 1,000 rows (sequential scan is fast enough)     │
│  ❌ Column is updated more often than queried                   │
│  ❌ Column has low cardinality (e.g., boolean with 50/50 split) │
│  ❌ Index already exists on same columns                        │
│  ❌ Index is only used for a rare admin query                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘

Index Types and When to Use:
────────────────────────────

B-Tree (default): Most queries
  → Equality: WHERE column = value
  → Range: WHERE column > value
  → Sorting: ORDER BY column

Composite: Multi-column queries
  → WHERE country_id = ? AND created_at > ?
  → Order matters: most selective column first
  → CREATE INDEX idx_reports_country_date ON reports(country_id, created_at DESC)

Partial: Subset of rows
  → WHERE deleted_at IS NULL (only active records)
  → CREATE INDEX idx_reports_active ON reports(created_at DESC) WHERE deleted_at IS NULL

Covering: Include extra columns in index
  → Index-only scan, no table lookup
  → CREATE INDEX idx_reports_covering ON reports(country_id, created_at) INCLUDE (title, status)
```

---

## 5. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Monitoring frequency | Monthly | Indexes don't change often |
| Missing index threshold | > 1000 seq scans | Below this, sequential scan is acceptable |
| Unused index threshold | 0 scans | If never used, it's wasting resources |
| Auto-drop unused indexes | No — recommend only | Human review needed before dropping |
| Index naming | `idx_{table}_{columns}` | Clear, consistent |
| Index creation | `CREATE INDEX CONCURRENTLY` | Non-blocking in production |
| Review process | Automated report → manual action | Automation finds candidates, humans decide |
| Bloat threshold | > 10MB | Significant enough to warrant REINDEX |
| Duplicate detection | Exact match on index definition | Prevents redundant indexes |

---

*This document should be updated when new index types are used or when monitoring thresholds are adjusted.*
