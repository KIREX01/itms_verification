# Rate Limiting Strategy

**Document ID:** ENT-02  
**Category:** Rate Limiting  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Redis, Middleware  

---

## 1. Current State

OHAS has a single, global, in-memory rate limiter:

```python
# backend/app/core/security.py
class RateLimiter:
    def __init__(self, max_requests: int = 100, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: Dict[str, List[datetime]] = {}  # In-memory dict
```

Applied globally via middleware:

```python
# backend/app/middleware/security.py
app.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)
```

### Problems

| Problem | Impact | Severity |
|---------|--------|----------|
| **Single limit for all endpoints** | A user hitting dashboard refresh uses the same limit as someone brute-forcing login | 🔴 |
| **In-memory (per-process)** | With 4 workers, effective limit = 400 req/min, not 100 | 🔴 |
| **No role differentiation** | Reporter and developer get the same 100 req/min — developer may need more for debugging | 🟠 |
| **No endpoint-specific limits** | Login endpoint (brute-force target) has same limit as country list (public, harmless) | 🔴 |
| **Lost on restart** | Process restart clears all rate limit counters | 🟡 |
| **No Redis backing** | Already have Redis for Celery but not using it for rate limiting | 🟡 |
| **IP-based only** | Doesn't account for NAT (multiple users behind same IP in office networks) | 🟡 |

---

## 2. Rate Limiting Architecture

### 2.1 Two-Layer Rate Limiting

```
┌─────────────────────────────────────────────────────────────────┐
│              Two-Layer Rate Limiting                            │
│                                                                 │
│  Layer 1: Global IP Rate Limit (DDoS protection)               │
│  ─────────────────────────────────────────                      │
│  Purpose: Protect infrastructure from abuse/DDoS                │
│  Scope: All endpoints, per IP address                           │
│  Limit: 300 requests/minute per IP (across all endpoints)       │
│  Backend: Redis (distributed across workers)                    │
│  Action on exceed: HTTP 429 Too Many Requests                   │
│                                                                 │
│  Layer 2: Per-User Per-Endpoint Rate Limit (abuse prevention)   │
│  ──────────────────────────────────────────────────────────     │
│  Purpose: Prevent authenticated users from abusing specific     │
│           endpoints beyond their role's allowance               │
│  Scope: Authenticated requests, per user_id + endpoint          │
│  Limit: Varies by role and endpoint (see §2.2)                  │
│  Backend: Redis (distributed across workers)                    │
│  Action on exceed: HTTP 429 with role-specific message          │
│                                                                 │
│  Request Flow:                                                  │
│  ┌────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐  │
│  │ Client │───→│ Layer 1  │───→│ Layer 2  │───→│  Handler  │  │
│  │        │    │ IP Check │    │ User +   │    │  (Route)  │  │
│  │        │    │ (Global) │    │ Endpoint │    │           │  │
│  │        │    │          │    │ Check    │    │           │  │
│  └────────┘    └──────────┘    └──────────┘    └───────────┘  │
│                     ↓ 429           ↓ 429                      │
│                 "Rate limited"   "Rate limited                 │
│                  (IP)"           (role/endpoint)"              │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Rate Limits by Role and Endpoint Category

```
┌────────────────────┬─────────┬─────────────┬──────────┬────────────┐
│ Endpoint Category  │Reporter │ Country     │ Top      │ Developer  │
│                    │         │ Admin       │ Admin    │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ Authentication     │ 10/min  │ 10/min      │ 10/min   │ 10/min     │
│ (login, signup,    │         │             │          │            │
│  reset password)   │         │             │          │            │
│                    │         │             │          │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ Report Submission  │ 60/min  │ 30/min      │ 30/min   │ 60/min     │
│ (POST /reports)    │         │             │          │            │
│                    │         │             │          │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ Dashboard Views    │ 30/min  │ 60/min      │ 120/min  │ 120/min    │
│ (GET /dashboard/*) │         │             │          │            │
│                    │         │             │          │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ Data Export        │ 5/min   │ 10/min      │ 20/min   │ 20/min     │
│ (POST /export/*)   │         │             │          │            │
│                    │         │             │          │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ User Management    │ N/A     │ 30/min      │ 60/min   │ 60/min     │
│ (CRUD /users)      │         │             │          │            │
│                    │         │             │          │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ System Config      │ N/A     │ N/A         │ 20/min   │ 30/min     │
│ (CRUD /config)     │         │             │          │            │
│                    │         │             │          │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ Bulk Operations    │ N/A     │ 5/min       │ 10/min   │ 10/min     │
│ (POST /bulk/*)     │         │             │          │            │
│                    │         │             │          │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ Developer Tools    │ N/A     │ N/A         │ N/A      │ 500/min    │
│ (GET /dev/*)       │         │             │          │            │
│                    │         │             │          │            │
├────────────────────┼─────────┼─────────────┼──────────┼────────────┤
│                    │         │             │          │            │
│ Global (all        │ 120/min │ 180/min     │ 300/min  │ 500/min    │
│ endpoints combined)│         │             │          │            │
│                    │         │             │          │            │
└────────────────────┴─────────┴─────────────┴──────────┴────────────┘

N/A = Role cannot access this endpoint (blocked by auth, not rate limiting)
```

### 2.3 Redis-Based Rate Limiter

```python
# backend/app/services/rate_limiter.py

import time
import logging
from typing import Optional, Tuple
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """
    Sliding window rate limiter backed by Redis.
    Uses sorted sets for accurate, distributed rate limiting.
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    async def is_allowed(
        self,
        key: str,
        max_requests: int,
        window_seconds: int,
    ) -> Tuple[bool, dict]:
        """
        Check if a request is allowed under the rate limit.

        Args:
            key: Unique identifier (e.g., "ip:192.168.1.1" or "user:abc123:endpoint:/reports")
            max_requests: Maximum requests allowed in the window
            window_seconds: Time window in seconds

        Returns:
            (allowed: bool, info: dict with limit, remaining, retry_after)
        """
        now = time.time()
        window_start = now - window_seconds

        # Use a Redis pipeline for atomic operations
        async with self.redis.pipeline(transaction=True) as pipe:
            try:
                # Remove entries outside the current window
                await pipe.zremrangebyscore(key, 0, window_start)

                # Count current entries in window
                await pipe.zcard(key)

                # Add current request timestamp
                # Use unique member to handle same-second requests
                member = f"{now}:{id(object())}"
                await pipe.zadd(key, {member: now})

                # Set expiry on the key (auto-cleanup)
                await pipe.expire(key, window_seconds + 1)

                results = await pipe.execute()

                # results[1] is the count (from zcard)
                current_count = results[1]

                if current_count <= max_requests:
                    # Allowed
                    remaining = max(0, max_requests - current_count)
                    return True, {
                        "limit": max_requests,
                        "remaining": remaining,
                        "reset": int(now + window_seconds),
                        "retry_after": None,
                    }
                else:
                    # Denied — calculate retry_after
                    # Find the oldest entry in the window
                    oldest = await pipe.zrange(key, 0, 0, withscores=True)
                    if oldest:
                        oldest_timestamp = oldest[0][1]
                        retry_after = int(oldest_timestamp + window_seconds - now) + 1
                    else:
                        retry_after = window_seconds

                    return False, {
                        "limit": max_requests,
                        "remaining": 0,
                        "reset": int(now + window_seconds),
                        "retry_after": max(1, retry_after),
                    }

            except Exception as e:
                logger.error(f"Rate limiter Redis error: {e}")
                # FAIL OPEN: If Redis is down, allow the request
                # This prevents rate limiter outage from blocking all traffic
                return True, {
                    "limit": max_requests,
                    "remaining": 0,
                    "reset": int(now + window_seconds),
                    "retry_after": None,
                    "warning": "rate_limiter_degraded",
                }


# Global instance (initialized on app startup)
rate_limiter: Optional[RedisRateLimiter] = None


def get_rate_limiter() -> RedisRateLimiter:
    if rate_limiter is None:
        raise RuntimeError(
            "Rate limiter not initialized. "
            "Call await init_rate_limiter(redis_client) on startup."
        )
    return rate_limiter


async def init_rate_limiter(redis_client: Redis):
    global rate_limiter
    rate_limiter = RedisRateLimiter(redis_client)
    logger.info("Redis rate limiter initialized")
```

### 2.4 Rate Limit Configuration

```python
# backend/app/core/rate_limit_config.py

from typing import Dict, List, Optional
from pydantic import BaseModel


class RateLimitRule(BaseModel):
    """A single rate limit rule."""
    max_requests: int
    window_seconds: int = 60  # Default: per minute


class RateLimitPolicy(BaseModel):
    """Rate limits for a specific role."""
    role: str
    # Per-endpoint-category limits
    authentication: RateLimitRule = RateLimitRule(max_requests=10)
    report_submission: RateLimitRule = RateLimitRule(max_requests=60)
    dashboard_views: RateLimitRule = RateLimitRule(max_requests=30)
    data_export: RateLimitRule = RateLimitRule(max_requests=5)
    user_management: RateLimitRule = RateLimitRule(max_requests=30)
    system_config: RateLimitRule = RateLimitRule(max_requests=20)
    bulk_operations: RateLimitRule = RateLimitRule(max_requests=5)
    developer_tools: RateLimitRule = RateLimitRule(max_requests=500)
    # Global limit (all endpoints combined)
    global_limit: RateLimitRule = RateLimitRule(max_requests=120)


# Global IP rate limit (unauthenticated, applies to all)
GLOBAL_IP_RATE_LIMIT = RateLimitRule(max_requests=300, window_seconds=60)

# Per-role rate limit policies
ROLE_RATE_LIMITS: Dict[str, RateLimitPolicy] = {
    "reporter": RateLimitPolicy(
        role="reporter",
        report_submission=RateLimitRule(max_requests=60),
        dashboard_views=RateLimitRule(max_requests=30),
        data_export=RateLimitRule(max_requests=5),
        global_limit=RateLimitRule(max_requests=120),
    ),
    "country_admin": RateLimitPolicy(
        role="country_admin",
        dashboard_views=RateLimitRule(max_requests=60),
        user_management=RateLimitRule(max_requests=30),
        data_export=RateLimitRule(max_requests=10),
        bulk_operations=RateLimitRule(max_requests=5),
        global_limit=RateLimitRule(max_requests=180),
    ),
    "top_administrator": RateLimitPolicy(
        role="top_administrator",
        dashboard_views=RateLimitRule(max_requests=120),
        user_management=RateLimitRule(max_requests=60),
        system_config=RateLimitRule(max_requests=20),
        data_export=RateLimitRule(max_requests=20),
        bulk_operations=RateLimitRule(max_requests=10),
        global_limit=RateLimitRule(max_requests=300),
    ),
    "developer": RateLimitPolicy(
        role="developer",
        dashboard_views=RateLimitRule(max_requests=120),
        report_submission=RateLimitRule(max_requests=60),
        user_management=RateLimitRule(max_requests=60),
        system_config=RateLimitRule(max_requests=30),
        data_export=RateLimitRule(max_requests=20),
        bulk_operations=RateLimitRule(max_requests=10),
        developer_tools=RateLimitRule(max_requests=500),
        global_limit=RateLimitRule(max_requests=500),
    ),
    "assistant": RateLimitPolicy(
        role="assistant",
        dashboard_views=RateLimitRule(max_requests=30),
        report_submission=RateLimitRule(max_requests=20),
        global_limit=RateLimitRule(max_requests=60),
    ),
}


# Endpoint-to-category mapping
# Used to determine which rate limit rule applies to which endpoint
ENDPOINT_CATEGORIES: Dict[str, str] = {
    # Authentication
    "POST /api/v*/auth/login": "authentication",
    "POST /api/v*/auth/signup": "authentication",
    "POST /api/v*/auth/forgot-password": "authentication",
    "POST /api/v*/auth/refresh": "authentication",

    # Report submission
    "POST /api/v*/reports": "report_submission",
    "POST /api/v*/reports/bulk": "bulk_operations",

    # Dashboard views
    "GET /api/v*/dashboard/*": "dashboard_views",
    "GET /api/v*/analytics/*": "dashboard_views",

    # Data export
    "POST /api/v*/export/*": "data_export",

    # User management
    "GET /api/v*/users*": "user_management",
    "POST /api/v*/users": "user_management",
    "PUT /api/v*/users/*": "user_management",
    "DELETE /api/v*/users/*": "user_management",

    # System config
    "GET /api/v*/system-config*": "system_config",
    "PUT /api/v*/system-config": "system_config",

    # Bulk operations
    "POST /api/v*/bulk/*": "bulk_operations",

    # Developer tools
    "GET /api/v*/dev/*": "developer_tools",
    "POST /api/v*/dev/*": "developer_tools",
}
```

### 2.5 Rate Limiting Middleware

```python
# backend/app/middleware/rate_limiting.py

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.rate_limiter import get_rate_limiter
from app.core.rate_limit_config import (
    GLOBAL_IP_RATE_LIMIT,
    ROLE_RATE_LIMITS,
    ENDPOINT_CATEGORIES,
)
import logging

logger = logging.getLogger(__name__)


class RateLimitingMiddleware(BaseHTTPMiddleware):
    """
    Two-layer rate limiting:
    Layer 1: Global IP-based limit (all requests)
    Layer 2: Per-user per-endpoint limit (authenticated requests)
    """

    # Paths exempt from rate limiting
    EXEMPT_PATHS = [
        "/health",
        "/health/live",
        "/health/ready",
        "/docs",
        "/openapi.json",
        "/redoc",
    ]

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip exempt paths
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        # ── Layer 1: Global IP rate limit ──
        client_ip = self._get_client_ip(request)
        ip_key = f"ratelimit:ip:{client_ip}"

        allowed, info = await get_rate_limiter().is_allowed(
            key=ip_key,
            max_requests=GLOBAL_IP_RATE_LIMIT.max_requests,
            window_seconds=GLOBAL_IP_RATE_LIMIT.window_seconds,
        )

        if not allowed:
            return self._rate_limit_response(
                info=info,
                message="Rate limit exceeded (IP-based)",
                scope="global",
            )

        # ── Layer 2: Per-user per-endpoint rate limit ──
        user_id = getattr(request.state, "user_id", None)
        user_role = getattr(request.state, "user_role", None)

        if user_id and user_role:
            category = self._get_endpoint_category(request)
            policy = ROLE_RATE_LIMITS.get(user_role)

            if policy and category:
                rule = getattr(policy, category, None)
                if rule:
                    user_key = f"ratelimit:user:{user_id}:{category}"

                    allowed, user_info = await get_rate_limiter().is_allowed(
                        key=user_key,
                        max_requests=rule.max_requests,
                        window_seconds=rule.window_seconds,
                    )

                    if not allowed:
                        return self._rate_limit_response(
                            info=user_info,
                            message=f"Rate limit exceeded for {category} "
                                    f"(role: {user_role})",
                            scope="user",
                        )

                    # Merge user-specific limits into response headers
                    request.state.rate_limit_user_info = user_info

            # Also check global user limit
            if policy:
                global_key = f"ratelimit:user:{user_id}:global"
                allowed, global_info = await get_rate_limiter().is_allowed(
                    key=global_key,
                    max_requests=policy.global_limit.max_requests,
                    window_seconds=policy.global_limit.window_seconds,
                )

                if not allowed:
                    return self._rate_limit_response(
                        info=global_info,
                        message=f"Global rate limit exceeded "
                                f"(role: {user_role})",
                        scope="global_user",
                    )

        # ── Proceed to handler ──
        response = await call_next(request)

        # ── Add rate limit headers ──
        response.headers["X-RateLimit-Limit"] = str(info["limit"])
        response.headers["X-RateLimit-Remaining"] = str(info["remaining"])
        response.headers["X-RateLimit-Reset"] = str(info["reset"])

        if hasattr(request.state, "rate_limit_user_info"):
            user_info = request.state.rate_limit_user_info
            response.headers["X-RateLimit-User-Limit"] = str(
                user_info["limit"]
            )
            response.headers["X-RateLimit-User-Remaining"] = str(
                user_info["remaining"]
            )

        return response

    def _get_client_ip(self, request: Request) -> str:
        """Get real client IP, accounting for proxies."""
        x_forwarded_for = request.headers.get("x-forwarded-for")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _get_endpoint_category(self, request: Request) -> Optional[str]:
        """
        Map the request to an endpoint category for rate limiting.
        Uses prefix matching on method + path.
        """
        method_path = f"{request.method} {request.url.path}"

        for pattern, category in ENDPOINT_CATEGORIES.items():
            # Handle wildcard patterns like "GET /api/v*/dashboard/*"
            if pattern.endswith("*"):
                prefix = pattern.rstrip("*")
                if method_path.startswith(prefix):
                    return category
            elif method_path == pattern:
                return category

        return None  # No specific category, falls back to global limit only

    def _rate_limit_response(
        self, info: dict, message: str, scope: str
    ) -> JSONResponse:
        """Return a standardized 429 response."""
        retry_after = info.get("retry_after", 60)
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "code": "RATE_LIMITED",
                "message": message,
                "scope": scope,
                "retry_after_seconds": retry_after,
                "limit": info["limit"],
            },
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(info["limit"]),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(info["reset"]),
            },
        )
```

### 2.6 Integration with Main App

```python
# backend/app/main.py — Add to middleware stack

# Existing imports
from app.middleware.rate_limiting import RateLimitingMiddleware

# ... existing setup ...

# Register rate limiting middleware (AFTER CORS, BEFORE other middleware)
# Order matters: this runs AFTER auth middleware (to know user_id/role)
# but BEFORE route handlers
app.add_middleware(RateLimitingMiddleware)
```

### 2.7 Startup Initialization

```python
# backend/app/main.py — startup_event

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    # Initialize Redis client (should already exist for Celery)
    import redis.asyncio as redis
    from app.core.config import settings
    from app.services.rate_limiter import init_rate_limiter

    redis_client = redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=1,  # Use separate DB from Celery (Celery uses db 0)
        decode_responses=True,
    )

    await init_rate_limiter(redis_client)

    logger.info("✅ Rate limiter initialized on Redis db 1")
```

---

## 3. Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Algorithm | Sliding window (Redis sorted sets) | Accurate, no boundary burst issue |
| Backend | Redis (async) | Already available for Celery; distributed |
| Separate Redis DB | db 0 for Celery, db 1 for rate limits | Prevents key collision, independent flushing |
| Fail behavior | Fail open (allow requests if Redis is down) | Better to have no rate limiting than block all traffic |
| Global IP limit | 300/min | High enough for normal use, low enough to deter abuse |
| Per-user limits | Role-based | Developers need more access; reporters need fewer |
| Key format | `ratelimit:ip:{ip}` and `ratelimit:user:{user_id}:{category}` | Namespaced, easy to debug/clean |
| Auto-cleanup | Redis TTL on each key | No manual cleanup needed |

---

## 4. Monitoring

Rate limit data exposed via health endpoint:

```python
# In /health response:
{
    "rate_limiting": {
        "status": "healthy",
        "backend": "redis://localhost:6379/1",
        "total_keys": 1547,
        "memory_used_kb": 2048,
        "top_limited_ips": [
            {"ip": "196.43.x.x", "requests_per_min": 280, "limit": 300},
            {"ip": "41.215.x.x", "requests_per_min": 150, "limit": 300},
        ]
    }
}
```

Dashboard metrics for developer dashboard:

```python
# Rate limit statistics (aggregated from Redis)
async def get_rate_limit_stats() -> dict:
    """Get rate limiting statistics for monitoring."""
    redis = rate_limiter.redis

    # Count all rate limit keys
    ip_keys = await redis.keys("ratelimit:ip:*")
    user_keys = await redis.keys("ratelimit:user:*")

    # Sample some IPs to find most-limited
    top_ips = []
    for key in ip_keys[:100]:  # Sample 100
        count = await redis.zcard(key)
        top_ips.append({
            "key": key,
            "requests_in_window": count,
        })

    top_ips.sort(key=lambda x: x["requests_in_window"], reverse=True)

    return {
        "total_ip_keys": len(ip_keys),
        "total_user_keys": len(user_keys),
        "top_active_ips": top_ips[:10],
    }
```

---

## 5. Migration from Current State

| Step | Action | File(s) | Effort |
|------|--------|---------|--------|
| 1 | Create Redis rate limiter service | `backend/app/services/rate_limiter.py` | 2 hrs |
| 2 | Create rate limit configuration | `backend/app/core/rate_limit_config.py` | 1 hr |
| 3 | Create rate limiting middleware | `backend/app/middleware/rate_limiting.py` | 2 hrs |
| 4 | Register middleware in main.py | `backend/app/main.py` | 5 min |
| 5 | Initialize on startup (Redis client) | `backend/app/main.py` | 15 min |
| 6 | Remove old in-memory RateLimiter | `backend/app/core/security.py`, `backend/app/middleware/security.py` | 15 min |
| 7 | Test with load (verify distributed behavior) | Manual | 1 hr |
| 8 | Add rate limit stats to health endpoint | `backend/app/api/v1/health.py` | 30 min |

**Total effort: ~7 hours**

---

## 6. Tuning Guidelines

After deployment, monitor and adjust:

```
If legitimate users are hitting rate limits:
  → Increase their role's limit by 20%
  → Review endpoint category assignment

If abuse is still occurring:
  → Decrease the specific endpoint's limit
  → Consider IP-level blocking for repeat offenders

General tuning cadence:
  Week 1: Monitor, don't change (gather baseline)
  Week 2: Adjust limits based on real usage patterns
  Week 3: Set final limits, document expected ranges
  Monthly: Review and adjust as user base grows
```

---

*This document should be updated when rate limits are tuned, when new roles are added, or when new endpoint categories are created.*
