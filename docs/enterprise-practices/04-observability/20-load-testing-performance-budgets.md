# Load Testing & Performance Budgets

**Document ID:** ENT-20  
**Category:** Observability  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), Supabase, Redis  

---

## 1. Current State

```
What exists:
  ✅ KPI service with latency thresholds
  ✅ Performance middleware (logs slow requests > 500ms)
  ✅ Service status checks with latency metrics

What's missing:
  ✗ No load testing ever performed
  ✗ No defined performance budgets
  ✗ No CI gate blocking slow code
  ✗ No frontend performance budgets
  ✗ No capacity planning
  ✗ No stress testing (breaking point unknown)
```

---

## 2. Performance Budgets

### 2.1 API Performance Budgets

```
┌──────────────────────────────────┬──────────────┬──────────────┬──────────────┐
│ Metric                          │ Budget       │ Warning      │ Critical     │
├──────────────────────────────────┼──────────────┼──────────────┼──────────────┤
│ API p50 latency                 │ < 100ms      │ 100-200ms    │ > 200ms      │
│ API p95 latency                 │ < 300ms      │ 300-500ms    │ > 500ms      │
│ API p99 latency                 │ < 500ms      │ 500-1000ms   │ > 1000ms     │
│ Error rate (5xx)                 │ < 0.1%       │ 0.1-1%       │ > 1%         │
│ Database query p95              │ < 50ms       │ 50-200ms     │ > 200ms      │
│ Supabase REST API p95           │ < 100ms      │ 100-300ms    │ > 300ms      │
│ Report submission (end-to-end)   │ < 500ms      │ 500-1000ms   │ > 1000ms     │
│ Dashboard load (all data)        │ < 1000ms     │ 1000-2000ms  │ > 2000ms     │
└──────────────────────────────────┴──────────────┴──────────────┴──────────────┘
```

### 2.2 Frontend Performance Budgets

```
┌──────────────────────────────────┬──────────────┬──────────────┬──────────────┐
│ Metric                          │ Budget       │ Warning      │ Critical     │
├──────────────────────────────────┼──────────────┼──────────────┼──────────────┤
│ First Contentful Paint (FCP)    │ < 1.0s       │ 1.0-1.8s     │ > 1.8s       │
│ Largest Contentful Paint (LCP)  │ < 2.0s       │ 2.0-3.0s     │ > 3.0s       │
│ Time to Interactive (TTI)       │ < 2.5s       │ 2.5-4.0s     │ > 4.0s       │
│ Total Bundle Size (initial)     │ < 300KB      │ 300-500KB    │ > 500KB      │
│ Total Bundle Size (total)       │ < 800KB      │ 800-1200KB   │ > 1200KB     │
│ API response parsing            │ < 50ms       │ 50-100ms     │ > 100ms      │
│ Dashboard render (after data)   │ < 200ms      │ 200-500ms    │ > 500ms      │
└──────────────────────────────────┴──────────────┴──────────────┴──────────────┘
```

### 2.3 Capacity Targets

```
┌──────────────────────────────────┬────────────────────────────────────────────┐
│ Parameter                        │ Target                                    │
├──────────────────────────────────┼────────────────────────────────────────────┤
│ Concurrent users                 │ 100 (current), 500 (12-month projection)  │
│ Requests per minute (sustained)  │ 500 rpm (current), 2000 rpm (12-month)    │
│ Peak requests per minute         │ 1000 rpm (current), 5000 rpm (12-month)   │
│ Database connections             │ < 50 of 100 max                           │
│ Redis memory usage               │ < 512MB of 1GB                            │
│ Storage growth                   │ < 1GB/month                               │
│ Report submission rate           │ 500/day (current), 2000/day (12-month)    │
│ Database size                    │ < 10GB (current), < 50GB (12-month)       │
└──────────────────────────────────┴────────────────────────────────────────────┘
```

---

## 3. Load Testing Strategy

### 3.1 Load Test Scenarios

```python
# tests/load_test.py
"""
Load tests using locust.io
Run: locust -f tests/load_test.py --headless -u 100 -r 10 --run-time 5m

This simulates 100 concurrent users ramping up at 10 users/second
for 5 minutes.
"""

from locust import HttpUser, task, between, events
import random
import json


class ReporterUser(HttpUser):
    """Simulates a reporter submitting reports."""
    wait_time = between(2, 10)  # Wait 2-10 seconds between tasks
    weight = 60  # 60% of traffic is reporters

    def on_start(self):
        """Login before running tasks."""
        response = self.client.post(
            "/api/v1/auth/login",
            data={
                "username": "reporter@test.com",
                "password": "testpass123",
            },
        )
        if response.status_code == 200:
            data = response.json()
            self.token = data.get("access_token") or data.get("data", {}).get("access_token")
            self.client.headers.update({
                "Authorization": f"Bearer {self.token}",
            })

    @task(5)
    def view_dashboard(self):
        """Reporter views their dashboard."""
        self.client.get("/api/v1/dashboard/reporter")

    @task(3)
    def list_my_reports(self):
        """Reporter lists their own reports."""
        self.client.get("/api/v1/reports?limit=50")

    @task(2)
    def submit_report(self):
        """Reporter submits a new report."""
        self.client.post(
            "/api/v1/reports",
            json={
                "title": f"Load Test Report {random.randint(1, 10000)}",
                "content": "This is a test report generated by the load test.",
                "program_id": "550e8400-e29b-41d4-a716-446655440000",
                "country_id": "550e8400-e29b-41d4-a716-446655440001",
                "participant_count": random.randint(10, 100),
            },
        )

    @task(1)
    def view_profile(self):
        """Reporter views their profile."""
        self.client.get("/api/v1/profile/users/me")


class AdminUser(HttpUser):
    """Simulates an admin viewing dashboards and managing users."""
    wait_time = between(5, 20)
    weight = 30  # 30% of traffic is admins

    def on_start(self):
        response = self.client.post(
            "/api/v1/auth/login",
            data={
                "username": "admin@test.com",
                "password": "testpass123",
            },
        )
        if response.status_code == 200:
            data = response.json()
            self.token = data.get("access_token") or data.get("data", {}).get("access_token")
            self.client.headers.update({
                "Authorization": f"Bearer {self.token}",
            })

    @task(5)
    def view_admin_dashboard(self):
        """Admin views dashboard with analytics."""
        self.client.get("/api/v1/dashboard/admin")

    @task(3)
    def list_users(self):
        """Admin lists users."""
        self.client.get("/api/v1/users?limit=50")

    @task(2)
    def view_reports(self):
        """Admin views all reports."""
        self.client.get("/api/v1/reports?limit=100")

    @task(1)
    def view_analytics(self):
        """Admin views analytics data."""
        self.client.get("/api/v1/analytics/summary")


class AnonymousUser(HttpUser):
    """Simulates unauthenticated traffic (health checks, public pages)."""
    wait_time = between(10, 30)
    weight = 10  # 10% of traffic is anonymous

    @task(3)
    def health_check(self):
        """Health check endpoint."""
        self.client.get("/api/v1/health")

    @task(2)
    def list_countries(self):
        """Public country list."""
        self.client.get("/api/v1/countries")

    @task(1)
    def view_homepage(self):
        """Homepage (if served by backend)."""
        self.client.get("/")
```

### 3.2 Load Test Execution

```bash
# Quick smoke test (10 users, 1 minute)
locust -f tests/load_test.py --headless -u 10 -r 2 --run-time 1m \
  --host http://localhost:8000

# Standard load test (100 users, 5 minutes)
locust -f tests/load_test.py --headless -u 100 -r 10 --run-time 5m \
  --host http://staging.ohas.system

# Stress test (find breaking point)
locust -f tests/load_test.py --headless -u 500 -r 20 --run-time 10m \
  --host http://staging.ohas.system

# With web UI (interactive)
locust -f tests/load_test.py --host http://localhost:8000
# Open http://localhost:8089 in browser
```

### 3.3 Pass/Fail Criteria

```
┌──────────────────────────────────┬─────────────┬─────────────┐
│ Criteria                        │ Pass        │ Fail        │
├──────────────────────────────────┼─────────────┼─────────────┤
│ p95 latency                     │ < 500ms     │ > 1000ms    │
│ Error rate                      │ < 1%        │ > 5%        │
│ Throughput                      │ > 100 req/s │ < 50 req/s  │
│ No 500 errors                   │ ✅          │ ❌          │
│ Database connection pool        │ < 80% full  │ > 95% full  │
│ Memory growth (over test)       │ < 10%       │ > 50%       │
│ CPU utilization                  │ < 80%       │ > 95%       │
└──────────────────────────────────┴─────────────┴─────────────┘

If ANY fail criteria is met → investigate and fix before deploying.
```

---

## 4. CI Performance Gate

```yaml
# .github/workflows/performance.yml

name: Performance Budget Check

on:
  pull_request:
    branches: [main]

jobs:
  frontend-budget:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
      - run: npm ci
      - run: npm run build
      - name: Check bundle size
        run: |
          total_size=$(du -sk dist/ | cut -f1)
          if [ "$total_size" -gt 500 ]; then
            echo "❌ Bundle size ${total_size}KB exceeds 500KB budget"
            exit 1
          fi
          echo "✅ Bundle size: ${total_size}KB (budget: 500KB)"

  api-budget:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7
        ports: ['6379:6379']
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r backend/requirements.txt
      - name: Start test server
        run: |
          cd backend
          uvicorn app.main:app --host 0.0.0.0 --port 8000 &
          sleep 5
      - name: Run API performance tests
        run: |
          pip install locust
          locust -f tests/load_test.py --headless -u 10 -r 2 \
            --run-time 30s --host http://localhost:8000 \
            --csv=performance_results
      - name: Check performance results
        run: |
          # Parse locust CSV results
          # Fail if p95 > 500ms or error rate > 1%
          python scripts/check_performance_budget.py \
            --csv performance_results_stats.csv \
            --p95-budget 500 \
            --error-rate-budget 1
```

---

## 5. Capacity Planning

```
Growth projection and infrastructure needs:

Current (100 concurrent users):
  → Single FastAPI instance on 2-core, 4GB RAM server
  → Supabase Pro plan (shared CPU, 8GB RAM, 50GB storage)
  → Redis on same server (512MB)
  → Total cost: ~$50-100/month

At 500 concurrent users (12-month projection):
  → 2-3 FastAPI instances behind load balancer
  → Supabase Pro plan (or dedicated CPU upgrade)
  → Redis on separate server
  → Total cost: ~$150-300/month

At 1000+ concurrent users (24-month projection):
  → Managed Kubernetes or ECS for auto-scaling
  → Supabase Team plan (dedicated resources)
  → Managed Redis (AWS ElastiCache or similar)
  → CDN for static frontend assets
  → Total cost: ~$500-1000/month
```

---

## 6. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Load testing tool | Locust | Python-based, easy to write scenarios, integrates with our stack |
| Test environment | Staging (never production) | Realistic without risking production |
| Test frequency | Before each major release + quarterly | Catches performance regressions |
| p95 latency budget | < 500ms | User-perceptible threshold |
| Error rate budget | < 1% during load | Allows for transient failures |
| Bundle size budget | < 500KB initial | Fast mobile load on 3G |
| Capacity planning | Review quarterly | Stay ahead of growth |
| CI performance gate | Block PR if budgets exceeded | Prevents performance regression |
| Stress testing | Find breaking point, then add 2x headroom | Know limits before they hit in production |

---

*This document should be updated when performance budgets change, when new load test scenarios are added, or when capacity is upgraded.*
