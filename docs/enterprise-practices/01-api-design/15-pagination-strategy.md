# Pagination Strategy

**Document ID:** ENT-15  
**Category:** API Design  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI + Supabase), Frontend (React)  

---

## 1. Current State

No pagination is implemented on any list endpoint. All list queries return every matching row:

```python
# Typical current pattern (no pagination)
reports = supabase.table("reports")\
    .select("*")\
    .eq("country_id", country_id)\
    .order("created_at", desc=True)\
    .execute()
# Returns ALL reports — could be thousands
```

### Why This Will Break

```
Growth projection:
  100 reporters × 5 submissions/day = 500 reports/day
  500 × 30 days = 15,000 reports/month
  15,000 × 12 months = 180,000 reports/year

At 180K rows:
  → Full table scan on every list request
  → ~50MB of JSON per response (all reports)
  → Network transfer takes seconds
  → Browser JSON parsing blocks UI thread
  → Dashboard load time: 10+ seconds
  → Supabase REST API timeout (default 60s)
```

---

## 2. Cursor-Based Pagination (Chosen Strategy)

### Why Cursor Over Offset

| Aspect | Offset-Based (`?offset=10000`) | Cursor-Based (`?cursor=eyJpZCI6NTB9`) |
|--------|-------------------------------|--------------------------------------|
| **Performance** | `OFFSET 10000` scans 10,001 rows then discards 10,000. Gets slower with deeper pages. | Uses indexed seek. Always O(log n) regardless of depth. |
| **Stability** | If a new row is inserted while paging, results shift. Same row may appear on two pages or be skipped. | Anchor to a specific row. New inserts don't affect current page. |
| **Use case** | Good for "go to page 17" navigation (rarely needed in OHAS). | Good for "load more" / infinite scroll (common in dashboards). |
| **Supabase support** | `.range(start, end)` — works but slow at depth. | `.lte('created_at', cursor_time).lt('id', cursor_id)` — fast with index. |
| **Complexity** | Simpler to implement. | Slightly more complex but worth it. |

**Decision: Cursor-based pagination** — because OHAS dashboards use infinite-scroll / load-more patterns, not page-number navigation, and data is continuously being inserted by reporters.

---

## 3. Cursor Format

### Cursor Structure

The cursor encodes the sort key values of the last row on the current page:

```json
// Cursor is a Base64-encoded JSON object:
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-04-04T12:00:00Z"
}
```

Encoded: `eyJpZCI6IjU1MGU4NDAw...LCJjcmVhdGVkX2F0IjoiMjAyNi0wNC0wNFQxMjowMDowMFoifQ==`

### Why Include Both `id` AND `created_at`

```
Scenario: Two reports submitted at the exact same timestamp.

Without id:
  Row A: created_at = "2026-04-04T12:00:00Z"
  Row B: created_at = "2026-04-04T12:00:00Z"
  
  Cursor: {"created_at": "2026-04-04T12:00:00Z"}
  Next query: WHERE created_at < "2026-04-04T12:00:00Z"
  → Both A and B are excluded! One row is lost.

With id (tiebreaker):
  Cursor: {"created_at": "2026-04-04T12:00:00Z", "id": "row_A_id"}
  Next query: WHERE (created_at, id) < ("2026-04-04T12:00:00Z", "row_A_id")
  → Row B is included (same time but different id). No data loss.
```

---

## 4. Implementation

### 4.1 Cursor Utilities

```python
# backend/app/utils/pagination.py

import base64
import json
from typing import Optional, Dict, Any, Tuple
from pydantic import BaseModel, Field


class CursorInfo(BaseModel):
    """Decoded cursor information."""
    id: str
    created_at: str


class PaginationParams(BaseModel):
    """Standard pagination parameters."""
    limit: int = Field(default=50, ge=1, le=200, description="Items per page (max 200)")
    cursor: Optional[str] = Field(default=None, description="Base64-encoded cursor from previous page")


class PaginationResult(BaseModel):
    """Pagination metadata returned in the response envelope."""
    per_page: int
    has_next: bool
    has_prev: bool
    next_cursor: Optional[str] = None
    prev_cursor: Optional[str] = None


def encode_cursor(id_value: str, created_at: str) -> str:
    """
    Create a Base64-encoded cursor from id and timestamp.
    
    Args:
        id_value: UUID string of the last row on this page
        created_at: ISO 8601 timestamp of the last row
    
    Returns:
        Base64-encoded cursor string
    """
    cursor_data = json.dumps({
        "id": id_value,
        "created_at": created_at,
    })
    return base64.urlsafe_b64encode(cursor_data.encode()).decode()


def decode_cursor(cursor: str) -> Optional[CursorInfo]:
    """
    Decode a Base64 cursor string.
    
    Args:
        cursor: Base64-encoded cursor string
    
    Returns:
        CursorInfo with id and created_at, or None if invalid
    """
    if not cursor:
        return None
    
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        data = json.loads(decoded)
        return CursorInfo(id=data["id"], created_at=data["created_at"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def build_pagination_metadata(
    results: list,
    params: PaginationParams,
    has_more: bool,
) -> PaginationResult:
    """
    Build pagination metadata from query results.
    
    Args:
        results: List of rows returned by the query
        params: Original pagination parameters
        has_more: Whether there are more results beyond this page
    
    Returns:
        PaginationResult with cursor info
    """
    next_cursor = None
    if has_more and results:
        last_row = results[-1]
        next_cursor = encode_cursor(
            id_value=str(last_row["id"]),
            created_at=last_row["created_at"],
        )

    # Previous cursor is not tracked in pure cursor pagination
    # (would need a双向 cursor approach). Set to None for simplicity.
    # If prev navigation is needed, use a windowed approach that
    # fetches one extra item backwards.
    
    return PaginationResult(
        per_page=params.limit,
        has_next=has_more,
        has_prev=bool(params.cursor),  # If we had a cursor, there is a prev page
        next_cursor=next_cursor,
        prev_cursor=None,  # Simplified — can be enhanced if needed
    )
```

### 4.2 Paginated Query Helper

```python
# backend/app/services/paginated_query.py

from typing import Optional, Tuple, List, Dict, Any
from app.utils.pagination import PaginationParams, decode_cursor, build_pagination_metadata


async def paginated_supabase_query(
    table_name: str,
    query_builder,
    params: PaginationParams,
    order_column: str = "created_at",
    id_column: str = "id",
    filters: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """
    Execute a paginated query against Supabase using cursor-based pagination.
    
    Args:
        table_name: Supabase table name
        query_builder: Function that builds the Supabase query
            Signature: (query, cursor_info, limit) -> query
        params: Pagination parameters (limit, cursor)
        order_column: Column to sort by (default: created_at)
        id_column: Column used as tiebreaker (default: id)
        filters: Additional filters to apply
    
    Returns:
        Tuple of (results, has_more)
        has_more is True if there are additional pages
    """
    # Fetch one extra item to detect if there are more pages
    fetch_limit = params.limit + 1
    
    # Decode cursor if provided
    cursor_info = decode_cursor(params.cursor)
    
    # Build the query
    query = supabase.table(table_name).select("*")
    
    # Apply additional filters
    if filters:
        for column, value in filters.items():
            query = query.eq(column, value)
    
    # Apply cursor-based filtering
    if cursor_info:
        # WHERE (order_column, id_column) < (cursor_created_at, cursor_id)
        # Supabase doesn't support tuple comparison, so we use:
        # (order_column < cursor_value) OR 
        # (order_column = cursor_value AND id_column < cursor_id)
        query = query.or_(
            f"{order_column}.lt.{cursor_info.created_at},"
            f"and({order_column}.eq.{cursor_info.created_at},{id_column}.lt.{cursor_info.id})"
        )
    
    # Order by (order_column DESC, id_column DESC)
    query = query.order(order_column, desc=True).order(id_column, desc=True)
    
    # Limit
    query = query.limit(fetch_limit)
    
    # Execute
    response = query_builder(query).execute()
    results = response.data or []
    
    # Check if there are more results (we fetched one extra)
    has_more = len(results) > params.limit
    
    # Remove the extra item if present
    if has_more:
        results = results[:params.limit]
    
    return results, has_more
```

### 4.3 Example: Paginated Reports Endpoint

```python
# backend/app/api/v1/reports.py

from fastapi import APIRouter, Query, Depends
from typing import Optional
from app.utils.pagination import PaginationParams, build_pagination_metadata
from app.api.deps import get_current_user
from app.models.profile import Profile
from app.models.response_envelope import APIResponse, ResponseMeta, PaginationMeta

router = APIRouter()


@router.get("/reports")
async def list_reports(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None),
    current_user: Profile = Depends(get_current_user),
):
    """
    List reports with cursor-based pagination.
    
    - **limit**: Number of items per page (1-200, default 50)
    - **cursor**: Base64 cursor from previous page's `next_cursor`
    """
    params = PaginationParams(limit=limit, cursor=cursor)
    
    # Build filters based on user role
    filters = {}
    if current_user.role == "reporter":
        filters["submitted_by"] = str(current_user.id)
    elif current_user.role == "country_admin":
        filters["country_id"] = str(current_user.country_id)
    # top_admin and developer see all reports (no filter)
    
    # Fetch one extra to detect more pages
    fetch_limit = params.limit + 1
    
    query = supabase.table("reports").select("*")
    
    # Apply filters
    for column, value in filters.items():
        query = query.eq(column, value)
    
    # Apply cursor
    from app.utils.pagination import decode_cursor
    cursor_info = decode_cursor(params.cursor)
    if cursor_info:
        query = query.or_(
            f"created_at.lt.{cursor_info.created_at},"
            f"and(created_at.eq.{cursor_info.created_at},id.lt.{cursor_info.id})"
        )
    
    query = query.order("created_at", desc=True).order("id", desc=True).limit(fetch_limit)
    
    response = query.execute()
    results = response.data or []
    
    has_more = len(results) > params.limit
    if has_more:
        results = results[:params.limit]
    
    # Build pagination metadata
    pagination_meta = build_pagination_metadata(results, params, has_more)
    
    return {
        "data": results,
        "meta": {
            "pagination": pagination_meta.model_dump(exclude_none=True),
        },
        "errors": [],
    }
```

### 4.4 Frontend Usage

```javascript
// frontend/src/hooks/usePaginatedReports.js

import { useState, useCallback } from 'react';
import api from '../services/api';

export function usePaginatedReports(filters = {}) {
  const [reports, setReports] = useState([]);
  const [nextCursor, setNextCursor] = useState(null);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(false);

  const loadPage = useCallback(async (cursor = null) => {
    if (loading) return;
    setLoading(true);

    try {
      const params = new URLSearchParams({
        limit: '50',
        ...(cursor && { cursor }),
        ...filters,
      });

      const response = await api.get(`/reports?${params}`);
      const { data, meta } = response.data;

      // Append to existing list (or replace if first page)
      setReports(prev => cursor ? [...prev, ...data] : data);
      setNextCursor(meta.pagination?.next_cursor);
      setHasMore(meta.pagination?.has_next ?? false);
    } catch (error) {
      console.error('Failed to load reports:', error);
    } finally {
      setLoading(false);
    }
  }, [filters, loading]);

  const loadMore = useCallback(() => {
    if (hasMore && nextCursor) {
      loadPage(nextCursor);
    }
  }, [hasMore, nextCursor, loadPage]);

  const reset = useCallback(() => {
    setReports([]);
    setNextCursor(null);
    setHasMore(true);
    loadPage(null);
  }, [loadPage]);

  return {
    reports,
    loading,
    hasMore,
    loadMore,
    reset,
  };
}
```

---

## 5. Supabase Index Requirements

Cursor-based pagination requires proper indexes for performance:

```sql
-- Migration: 20260404_pagination_indexes.sql

-- Composite index for cursor-based pagination on reports
-- Supports: ORDER BY created_at DESC, id DESC with WHERE clause
CREATE INDEX idx_reports_cursor_pagination 
    ON reports (created_at DESC, id DESC);

-- Same pattern for other frequently-paginated tables
CREATE INDEX idx_participants_cursor_pagination 
    ON participants (created_at DESC, id DESC);

CREATE INDEX idx_goals_cursor_pagination 
    ON goals (created_at DESC, id DESC);

CREATE INDEX idx_notifications_cursor_pagination 
    ON notifications (created_at DESC, id DESC);

CREATE INDEX idx_audit_logs_cursor_pagination 
    ON audit_logs (created_at DESC, id DESC);

CREATE INDEX idx_errors_cursor_pagination 
    ON errors (created_at DESC, id DESC);
```

Without these indexes, the cursor-based query still works but performs a full table scan — defeating the purpose.

---

## 6. Edge Cases

### 6.1 Deleted Rows

```
Scenario: User is on page 2 (cursor = row_50). 
          Meanwhile, row_25 is soft-deleted (deleted_at set).

Cursor behavior:
  Query: WHERE (created_at, id) < (row_50.created_at, row_50.id)
  
  Soft-deleted row_25 is excluded by a separate filter:
  WHERE deleted_at IS NULL AND (created_at, id) < cursor
  
  Result: Page still has 50 items (skips deleted rows).
  No gaps, no missing data from the user's perspective.
```

### 6.2 Rapid Inserts During Pagination

```
Scenario: User loads page 1. While reading it, 10 new reports arrive.
          User clicks "load more" (page 2).

Cursor behavior:
  Page 2 query uses the cursor from page 1's last row.
  New reports have created_at > cursor, so they're NOT included in page 2.
  User sees a stable, consistent view.
  New reports will appear when they refresh or load page 1 again.

This is CORRECT behavior — prevents items from appearing twice or being skipped.
```

### 6.3 Empty Results

```
Scenario: Cursor points to a row that no longer matches the filters
          (e.g., report was reassigned to a different country).

Cursor behavior:
  Query returns 0 results (no rows match the cursor + filters).
  has_more = false.
  Frontend shows "No more results."
  User can refresh to start from page 1 with current filters.

Graceful degradation — no error, just an empty list.
```

### 6.4 Invalid/Expired Cursor

```
Scenario: User sends a corrupted cursor or one from a different endpoint.

Server behavior:
  decode_cursor() returns None (invalid Base64 or missing fields).
  Query runs without cursor filter → returns first page.
  No error — treated as a fresh request.

This is intentional — invalid cursors silently reset to page 1
rather than returning a 400 error that breaks the UX.
```

---

## 7. Total Count: The Pagination Trade-off

Cursor-based pagination cannot efficiently return `total_items` without a separate count query:

```
Offset-based advantage:
  SELECT COUNT(*) FROM reports WHERE country_id = 'abc' → total = 15,247
  Then: OFFSET 0 LIMIT 50 → page 1 of 305

Cursor-based limitation:
  Cannot know total without a separate COUNT query.
  COUNT is expensive on large tables.
  
OHAS Solution:
  Option A: Omit total_items. Show "Load more" button instead of page numbers.
  Option B: Run COUNT query separately, cache the result for 5 minutes.
  Option C: Estimate from pg_class.reltuples (PostgreSQL's approximate row count).
  
Decision: Option A for now (no total). Add Option B if users demand it.
  Most dashboards use infinite scroll — users don't need "page 17 of 305."
  They need "load more" until they find what they're looking for.
```

---

## 8. Migration from Offset-Based (If Ever Used)

If any endpoints currently use offset-based pagination:

```python
# Before (offset-based — if implemented)
@router.get("/reports")
async def list_reports(page: int = 1, limit: int = 50):
    offset = (page - 1) * limit
    results = query.range(offset, offset + limit - 1).execute()

# After (cursor-based — drop-in replacement)
@router.get("/reports")
async def list_reports(
    limit: int = Query(default=50),
    cursor: Optional[str] = Query(default=None),
):
    params = PaginationParams(limit=limit, cursor=cursor)
    # ... cursor-based logic ...
```

Frontend migration:
```javascript
// Before
const loadPage = (page) => api.get(`/reports?page=${page}&limit=50`);

// After
const loadMore = (cursor) => api.get(`/reports?limit=50&cursor=${cursor}`);
```

---

## 9. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Pagination type | Cursor-based | Stable during inserts, O(log n) at any depth |
| Cursor encoding | Base64 JSON | Opaque to consumers, easy to decode server-side |
| Cursor contents | `{id, created_at}` | Tiebreaker prevents same-timestamp data loss |
| Max page size | 200 | Prevents memory exhaustion on a single request |
| Default page size | 50 | Balance between network transfer and UX |
| Total count | Omitted initially | Requires expensive COUNT; add later if demanded |
| Invalid cursor handling | Silently reset to page 1 | Better UX than 400 error |
| Previous page support | Not in v1 (prev_cursor = null) | Requires bidirectional cursors — complex, rarely needed |
| Sort order | `created_at DESC, id DESC` | Newest first, stable ordering |
| Index requirement | Composite `(created_at DESC, id DESC)` | Required for cursor pagination performance |

---

*This document should be updated when new paginated endpoints are added or when the cursor format changes.*
