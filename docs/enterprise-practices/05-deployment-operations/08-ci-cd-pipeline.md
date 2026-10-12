# CI/CD Pipeline

**Document ID:** ENT-08  
**Category:** Deployment & Operations  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Frontend (React), GitHub Actions, Supabase  

---

## 1. Current State

```
What exists:
  ✅ GitHub repository
  ✅ .gitignore with proper exclusions
  ✅ npm scripts (dev, build, test, lint)
  ✅ Python requirements.txt
  ✅ Supabase migrations directory

What's missing:
  ✗ No CI pipeline (no automated testing on PR)
  ✗ No automated linting or type checking
  ✗ No staging environment
  ✗ No deployment automation
  ✗ No rollback mechanism
  ✗ No quality gates (test coverage, security scan)
  ✗ No environment parity (dev ≠ staging ≠ prod)
  ✗ No deployment notifications
  ✗ No migration testing before production
```

---

## 2. CI/CD Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│              OHAS CI/CD Pipeline                                │
│                                                                 │
│  Developer                                                      │
│  │                                                              │
│  ├─ git commit                                                  │
│  │                                                              │
│  ├─ git push ──→ GitHub                                         │
│  │                                                              │
│  │  ┌─────────────────────────────────────────────────────┐    │
│  │  │              CI Pipeline (GitHub Actions)            │    │
│  │  │                                                     │    │
│  │  │  Stage 1: Validate                                  │    │
│  │  │  ├─ Lint (ESLint, Ruff)                             │    │
│  │  │  ├─ Type check (TypeScript, mypy)                   │    │
│  │  │  ├─ Secret scan (gitleaks)                          │    │
│  │  │  └─ OpenAPI breaking change check                   │    │
│  │  │                                                     │    │
│  │  │  Stage 2: Test                                      │    │
│  │  │  ├─ Frontend tests (Vitest)                         │    │
│  │  │  ├─ Backend tests (pytest)                          │    │
│  │  │  ├─ Migration validation                            │    │
│  │  │  └─ Coverage check (>80%)                           │    │
│  │  │                                                     │    │
│  │  │  Stage 3: Build                                     │    │
│  │  │  ├─ Frontend: npm run build                         │    │
│  │  │  ├─ Backend: Docker image build                     │    │
│  │  │  └─ Docker push to registry                         │    │
│  │  └─────────────────────────────────────────────────────┘    │
│  │                                                              │
│  ├─ PR to main ──→ Gate checks pass?                           │
│  │    ├── No → Block merge, show errors                        │
│  │    └── Yes → Auto-merge                                     │
│  │                                                              │
│  └─ Merge to main ──→ CD Pipeline                               │
│       │                                                          │
│       ├─ Deploy to staging                                     │
│       │  ├─ Run migrations on staging DB                       │
│       │  ├─ Deploy frontend to staging URL                     │
│       │  ├─ Deploy backend to staging URL                      │
│       │  ├─ Run smoke tests against staging                    │
│       │  └─ Notify team: "Staging updated"                     │
│       │                                                          │
│       ├─ Manual approval (click "Approve" in GitHub)           │
│       │                                                          │
│       └─ Deploy to production                                  │
│          ├─ Run migrations on production DB                    │
│          ├─ Deploy backend (rolling update, zero downtime)      │
│          ├─ Deploy frontend (cache-busted)                      │
│          ├─ Run smoke tests against production                 │
│          ├─ If smoke tests fail → auto-rollback                 │
│          └─ Notify team: "Production updated vX.Y.Z"            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. CI Pipeline (GitHub Actions)

### 3.1 PR Validation Workflow

```yaml
# .github/workflows/ci.yml

name: CI — Pull Request Validation

on:
  pull_request:
    branches: [main, develop]

jobs:
  # ── Stage 1: Validate ──
  lint-frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm run lint
      - run: npx tsc --noEmit  # TypeScript type check

  lint-backend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: backend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
          cache-dependency-path: backend/requirements.txt
      - run: pip install -r requirements.txt
      - run: pip install ruff mypy
      - run: ruff check app/
      - run: ruff format --check app/
      - run: mypy app/ --ignore-missing-imports

  scan-secrets:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Run gitleaks
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

  check-api-breaking-changes:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install fastapi pydantic
      - name: Generate current OpenAPI
        run: |
          cd backend
          python -c "
          from app.main import app
          import json
          with open('../openapi-current.json', 'w') as f:
              json.dump(app.openapi(), f, indent=2)
          "
      - name: Check for breaking changes
        run: |
          python scripts/check_api_breaking_changes.py \
            --old docs/api/openapi/openapi-v1-baseline.json \
            --new openapi-current.json

  validate-migrations:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Validate migration files
        run: python scripts/validate_migration.py

  # ── Stage 2: Test ──
  test-frontend:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm test -- --coverage --run
      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: frontend-coverage
          path: frontend/coverage/

  test-backend:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7
        ports: ['6379:6379']
    env:
      SECRET_KEY: test-secret-key-for-ci
      SUPABASE_URL: http://localhost:54321
      SUPABASE_KEY: test-anon-key
      SUPABASE_SERVICE_ROLE_KEY: test-service-role-key
      REDIS_HOST: localhost
      REDIS_PORT: 6379
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
          cache-dependency-path: backend/requirements.txt
      - run: pip install -r requirements.txt
      - run: pip install pytest pytest-cov pytest-asyncio
      - name: Start Supabase locally
        run: |
          npm install -g supabase
          supabase init
          supabase start
      - name: Run tests
        run: |
          cd backend
          pytest app/ -v --cov=app --cov-report=xml
      - name: Upload coverage
        uses: actions/upload-artifact@v4
        with:
          name: backend-coverage
          path: backend/coverage.xml

  # ── Stage 3: Build ──
  build-frontend:
    runs-on: ubuntu-latest
    needs: [lint-frontend, test-frontend]
    defaults:
      run:
        working-directory: frontend
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: 'npm'
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
      - run: npm run build
      - name: Upload build
        uses: actions/upload-artifact@v4
        with:
          name: frontend-build
          path: frontend/dist/

  build-backend:
    runs-on: ubuntu-latest
    needs: [lint-backend, test-backend]
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker image
        run: docker build -t ohas-backend:${{ github.sha }} backend/
      - name: Push to registry
        if: github.ref == 'refs/heads/main'
        run: |
          echo ${{ secrets.DOCKER_PASSWORD }} | docker login -u ${{ secrets.DOCKER_USERNAME }} --password-stdin
          docker tag ohas-backend:${{ github.sha }} ${{ secrets.DOCKER_REPO }}/ohas-backend:${{ github.sha }}
          docker push ${{ secrets.DOCKER_REPO }}/ohas-backend:${{ github.sha }}
```

---

## 4. CD Pipeline (Deployment)

### 4.1 Staging Deployment

```yaml
# .github/workflows/deploy-staging.yml

name: CD — Deploy to Staging

on:
  push:
    branches: [main]

jobs:
  deploy-staging:
    runs-on: ubuntu-latest
    environment: staging
    steps:
      - uses: actions/checkout@v4

      - name: Run migrations on staging
        run: |
          supabase db push --db-url "${{ secrets.STAGING_DB_URL }}"

      - name: Deploy backend to staging
        run: |
          # Deploy to staging server
          ssh ${{ secrets.STAGING_SSH_HOST }} "cd /opt/ohas-backend && \
            docker pull ${{ secrets.DOCKER_REPO }}/ohas-backend:${{ github.sha }} && \
            docker-compose up -d --no-deps backend"

      - name: Deploy frontend to staging
        run: |
          # Download build artifact and deploy
          # (Specific to your hosting — Vercel, Netlify, or custom)

      - name: Run smoke tests
        run: |
          # Wait for deployment to settle
          sleep 30
          
          # Health check
          curl -f https://staging.ohas.system/api/v1/health || exit 1
          
          # Auth check
          curl -f -X POST https://staging.ohas.system/api/v1/auth/login \
            -d "username=test@ohas.system&password=testpass" || exit 1
          
          # Report submission check
          # ...

      - name: Notify team
        if: success()
        run: |
          curl -X POST ${{ secrets.SLACK_WEBHOOK }} \
            -d '{"text": "🟢 Staging updated: ${{ github.sha }}"}'
```

### 4.2 Production Deployment

```yaml
# .github/workflows/deploy-production.yml

name: CD — Deploy to Production

on:
  workflow_dispatch:  # Manual trigger
    inputs:
      version:
        description: 'Version tag (e.g., v1.2.0)'
        required: true

jobs:
  deploy-production:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v4

      - name: Pre-deployment checks
        run: |
          # Verify staging is healthy
          curl -f https://staging.ohas.system/api/v1/health || exit 1
          
          # Verify no open P0 incidents
          # (Check incident tracking system)

      - name: Run migrations on production
        run: |
          supabase db push --db-url "${{ secrets.PRODUCTION_DB_URL }}"

      - name: Deploy backend (rolling update)
        run: |
          # Deploy new version, keep old version running
          ssh ${{ secrets.PRODUCTION_SSH_HOST }} "cd /opt/ohas-backend && \
            docker pull ${{ secrets.DOCKER_REPO }}/ohas-backend:${{ github.sha }} && \
            docker-compose up -d --no-deps backend"
          
          # Wait for health check
          for i in {1..30}; do
            if curl -f https://api.ohas.system/api/v1/health; then
              echo "Backend is healthy"
              break
            fi
            echo "Waiting for backend to be healthy... ($i/30)"
            sleep 10
          done

      - name: Deploy frontend
        run: |
          # Deploy with cache busting
          # ...

      - name: Post-deployment smoke tests
        run: |
          sleep 30
          
          # Test critical paths
          curl -f https://api.ohas.system/api/v1/health || exit 1
          curl -f https://app.ohas.system/ || exit 1
          
          # Test login
          # ...
          
          # Test report submission
          # ...

      - name: Auto-rollback on failure
        if: failure()
        run: |
          echo "Deployment failed — rolling back..."
          
          # Rollback backend
          ssh ${{ secrets.PRODUCTION_SSH_HOST }} "cd /opt/ohas-backend && \
            docker-compose up -d --no-deps backend"  # Previous version
          
          # Rollback migrations (if possible)
          supabase db reset --db-url "${{ secrets.PRODUCTION_DB_URL }}" \
            --until  # Rollback to before this migration
          
          # Notify team
          curl -X POST ${{ secrets.SLACK_WEBHOOK }} \
            -d '{"text": "🔴 Production deployment FAILED — auto-rolled back"}'

      - name: Tag release
        if: success()
        run: |
          git tag ${{ inputs.version }}
          git push origin ${{ inputs.version }}

      - name: Notify team
        if: success()
        run: |
          curl -X POST ${{ secrets.SLACK_WEBHOOK }} \
            -d '{"text": "✅ Production deployed: ${{ inputs.version }} (${{ github.sha }})"}'
```

---

## 5. Environment Parity

```
┌──────────────────┬──────────────────┬──────────────────┬──────────────────┐
│                  │ Development      │ Staging          │ Production       │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Database         │ Supabase local   │ Supabase project │ Supabase project │
│                  │ (Docker)         │ (staging)        │ (production)     │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Redis            │ localhost:6379   │ Staging Redis    │ Production Redis │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Backend          │ localhost:8000   │ staging.api...   │ api.ohas.system  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Frontend         │ localhost:3000   │ staging.app...   │ app.ohas.system  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ DEBUG mode       │ True             │ False            │ False            │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Data             │ Seed data        │ Sanitized prod   │ Real data        │
│                  │ (fake)           │ copy             │                  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Monitoring       │ Console only     │ Full observability│ Full observability│
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Email            │ InBucket (local) │ Test SMTP        │ Production SMTP  │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│ Feature flags    │ All enabled      │ Production copy  │ Production       │
└──────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

---

## 6. Rollback Procedure

```
┌─────────────────────────────────────────────────────────────────┐
│              Rollback Procedure                                  │
│                                                                 │
│  Automatic rollback triggers when:                              │
│  → Post-deployment smoke tests fail                             │
│  → Error rate spikes above 5% within 10 minutes of deploy       │
│  → Health check fails 3 consecutive times                       │
│                                                                 │
│  Manual rollback:                                               │
│  1. Click "Rollback" in GitHub Actions workflow                  │
│  2. Select the previous successful deployment                   │
│  3. Workflow runs:                                              │
│     a. Revert database migrations (if reversible)               │
│     b. Deploy previous backend Docker image                     │
│     c. Deploy previous frontend build                           │
│     d. Run smoke tests                                          │
│     e. Notify team                                              │
│                                                                 │
│  If rollback fails:                                             │
│  1. SSH into production server                                  │
│  2. Manually start previous Docker container                    │
│  3. If DB is corrupted: restore from PITR                       │
│  4. Declare incident, begin post-mortem                         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| CI tool | GitHub Actions | Integrated with repo, free tier sufficient |
| Staging environment | Separate Supabase project + server | Environment parity, safe testing |
| Production deploy trigger | Manual (workflow_dispatch) | Prevents accidental production deploys |
| Migration timing | Before code deploy | DB schema must exist before code uses it |
| Rollback strategy | Auto on smoke test failure + manual trigger | Fast recovery, human override available |
| Docker for backend | Yes | Consistent across environments |
| Frontend build artifact | Upload/download in pipeline | No need to rebuild on deploy |
| Notifications | Slack webhook | Simple, team already uses Slack |
| Environment secrets | GitHub Environments with protection rules | Staging and prod have separate secrets |
| Tagging | Semantic versioning on successful deploy | Traceable releases |

---

*This document should be updated when the CI/CD pipeline changes, when new environments are added, or when deployment procedures are refined.*
