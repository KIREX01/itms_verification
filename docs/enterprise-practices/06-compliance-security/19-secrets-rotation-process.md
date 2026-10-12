# Secrets Rotation Process

**Document ID:** ENT-19  
**Category:** Compliance & Security  
**Date:** 4 April 2026  
**Status:** Proposed  
**Applies To:** Backend (FastAPI), Supabase, Email (SMTP), Redis  

---

## 1. Current State

```
Secrets currently in use:
  ✅ SECRET_KEY — JWT signing key (hardcoded fallback in config.py)
  ✅ SUPABASE_URL — Supabase project URL
  ✅ SUPABASE_KEY — Supabase anon key
  ✅ SUPABASE_SERVICE_ROLE_KEY — Supabase admin key (bypasses RLS)
  ✅ SMTP_PASSWORD — Email service password
  ✅ REDIS_HOST/PORT — Redis connection details

What exists:
  ✅ .env files gitignored
  ✅ pydantic-settings for env var loading

What's missing:
  ✗ No rotation schedule
  ✗ No key versioning (can't run old + new key simultaneously)
  ✗ No automated rotation process
  ✗ No documented emergency revocation procedure
  ✗ No audit of who has access to secrets
  ✗ No secret scanning in code (prevent accidental commits)
  ✗ No differentiation between dev/staging/prod secrets
```

---

## 2. Secrets Inventory

```
┌──────────────────────────────┬──────────────┬─────────────┬────────────┐
│ Secret                       │ Rotation     │ Emergency   │ Impact if  │
│                              │ Schedule     │ Revocation  │ Compromised│
├──────────────────────────────┼──────────────┼─────────────┼────────────┤
│ SECRET_KEY (JWT signing)     │ 90 days      │ Immediate   │ 🔴 Critical│
│                              │              │             │ All tokens │
│                              │              │             │ can be     │
│                              │              │             │ forged     │
├──────────────────────────────┼──────────────┼─────────────┼────────────┤
│ SUPABASE_SERVICE_ROLE_KEY    │ 90 days      │ Immediate   │ 🔴 Critical│
│                              │              │             │ Full DB    │
│                              │              │             │ access,    │
│                              │              │             │ RLS bypass │
├──────────────────────────────┼──────────────┼─────────────┼────────────┤
│ SUPABASE_KEY (anon key)      │ 90 days      │ 24 hours    │ 🟠 High    │
│                              │              │             │ Can access │
│                              │              │             │ public     │
│                              │              │             │ data       │
├──────────────────────────────┼──────────────┼─────────────┼─────────────┤
│ SMTP_PASSWORD                  │ 90 days      │ 1 hour      │ 🟠 High    │
│                              │              │             │ Can send   │
│                              │              │             │ emails as  │
│                              │              │             │ OHAS       │
├──────────────────────────────┼──────────────┼─────────────┼─────────────┤
│ Redis password (if set)      │ 90 days      │ 1 hour      │ 🟡 Medium  │
│                              │              │             │ Can access │
│                              │              │             │ Celery     │
│                              │              │             │ queue      │
├──────────────────────────────┼──────────────┼─────────────┼─────────────┤
│ API keys for external         │ Per provider │ Per provider│ 🟡 Medium  │
│ services (geo-IP, etc.)      │              │             │ Varies     │
└──────────────────────────────┴──────────────┴─────────────┴────────────┘
```

---

## 3. Rotation Process Per Secret Type

### 3.1 JWT Secret Key Rotation

This is the most complex rotation because changing the secret invalidates all existing tokens.

```
Phase 1: Preparation (Day -7)
────────────────────────────
1. Generate new SECRET_KEY:
   openssl rand -hex 32
   → New key: a1b2c3d4e5f6...

2. Store both old and new keys in environment:
   SECRET_KEY_OLD=<current key>
   SECRET_KEY_NEW=<new key>
   SECRET_KEY_ROTATION=true

3. Deploy code that accepts BOTH keys for token validation:

# backend/app/api/deps.py
from app.core.config import settings

async def get_current_user(token: str = Depends(security)) -> Profile:
    """Accept tokens signed with either old or new key during rotation."""
    
    # Try new key first
    try:
        payload = jwt.decode(
            token.credentials, 
            settings.SECRET_KEY_NEW, 
            algorithms=[settings.ALGORITHM]
        )
    except JWTError:
        # Fall back to old key
        try:
            payload = jwt.decode(
                token.credentials, 
                settings.SECRET_KEY_OLD, 
                algorithms=[settings.ALGORITHM]
            )
        except JWTError:
            raise HTTPException(401, "Invalid token")
    
    # ... rest of validation ...

Phase 2: Transition (Day 0)
───────────────────────────
1. Deploy the dual-key validation code
2. Set SECRET_KEY_ROTATION=true
3. Monitor: all existing tokens still work (validated by old key)
4. New logins get tokens signed with new key
5. Wait 7 days (longer than longest token TTL)

Phase 3: Completion (Day +7)
─────────────────────────────
1. Verify no tokens are being validated with old key:
   SELECT COUNT(*) FROM audit_logs 
   WHERE action = 'TOKEN_VALIDATED' 
     AND details->>'key_version' = 'old';
   
2. If count is 0, all clients have new tokens
3. Remove old key from environment:
   SECRET_KEY=<new key>
   # Remove SECRET_KEY_OLD
   SECRET_KEY_ROTATION=false

4. Deploy updated code (single-key validation)
5. Rotation complete
```

### 3.2 Supabase Key Rotation

```
Phase 1: Generate New Key
──────────────────────────
1. Go to Supabase Dashboard → Project Settings → API
2. Click "Regenerate" on the service role key
3. Copy the new key (this is shown ONCE — save it securely)
4. The old key remains valid for 7 days (Supabase default)

Phase 2: Deploy New Key
────────────────────────
1. Update environment variable:
   SUPABASE_SERVICE_ROLE_KEY=<new key>
   SUPABASE_SERVICE_ROLE_KEY_OLD=<old key>

2. Deploy updated configuration
3. Monitor for errors (should be none — both keys work)

Phase 3: Remove Old Key (After 7 Days)
───────────────────────────────────────
1. Remove SUPABASE_SERVICE_ROLE_KEY_OLD from environment
2. Verify no errors in logs
3. Rotation complete
```

### 3.3 SMTP Password Rotation

```
1. Change password in Google/Gmail account (or email provider)
2. If using App Password:
   a. Go to Google Account → Security → App Passwords
   b. Generate new app password for "OHAS Email Service"
   c. Delete old app password
3. Update environment variable:
   SMTP_PASSWORD=<new password>
4. Deploy/restart backend
5. Test email sending:
   python -c "from app.services.email_service import email_service; 
              email_service.send_test_email('admin@ohas.system')"
6. Rotation complete
```

---

## 4. Automated Rotation Script

```python
# scripts/rotate_secrets.py
"""
Automated secrets rotation script.
Generates new secrets, updates environment files,
and creates audit trail.

Usage:
  python scripts/rotate_secrets.py --secret jwt --dry-run
  python scripts/rotate_secrets.py --secret all --confirm
"""

import argparse
import os
import secrets
import subprocess
from datetime import datetime
from pathlib import Path


class SecretRotator:
    """Manages secrets rotation with audit trail."""

    ENV_TEMPLATE = """\
# OHAS Environment Configuration
# Generated by rotate_secrets.py on {date}
# DO NOT commit this file to version control

# JWT
SECRET_KEY={secret_key}
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Supabase
SUPABASE_URL={supabase_url}
SUPABASE_KEY={supabase_anon_key}
SUPABASE_SERVICE_ROLE_KEY={supabase_service_key}

# Redis
REDIS_HOST=localhost
REDIS_PORT=6373
REDIS_DB=0

# Email (SMTP)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME={smtp_username}
SMTP_PASSWORD={smtp_password}
SMTP_USE_TLS=true
FROM_EMAIL=no-reply@ohas.system
FROM_NAME=OHAS Platform

# Application
APP_NAME=One Hope Africa System
DEBUG=false
ENVIRONMENT=production
"""

    def __init__(self, env_dir: str = "."):
        self.env_dir = Path(env_dir)
        self.rotation_log = self.env_dir / "rotation_log.md"

    def rotate_jwt_secret(self) -> str:
        """Generate a new JWT secret key."""
        return secrets.token_hex(32)

    def rotate_smtp_password(self, length: int = 20) -> str:
        """Generate a new SMTP password."""
        return secrets.token_urlsafe(length)

    def rotate_all(self, current_env: dict) -> dict:
        """Generate all new secrets, preserving existing non-secret values."""
        new_secrets = {
            "SECRET_KEY": self.rotate_jwt_secret(),
            "SMTP_PASSWORD": self.rotate_smtp_password(),
            # Supabase keys must be regenerated via Supabase Dashboard
            "ROTATION_DATE": datetime.utcnow().isoformat(),
        }
        
        # Preserve non-secret values
        preserved = {
            k: v for k, v in current_env.items()
            if k not in ("SECRET_KEY", "SMTP_PASSWORD")
        }
        
        return {**preserved, **new_secrets}

    def write_env_file(self, secrets: dict, filename: str = ".env.production"):
        """Write new secrets to environment file."""
        env_content = self.ENV_TEMPLATE.format(**secrets, date=datetime.utcnow().isoformat())
        filepath = self.env_dir / filename
        
        with open(filepath, 'w') as f:
            f.write(env_content)
        
        # Set restrictive permissions (owner read/write only)
        os.chmod(filepath, 0o600)
        print(f"✅ Written: {filepath}")

    def log_rotation(self, secret_type: str, old_hash: str, new_hash: str):
        """Append rotation event to audit log."""
        log_entry = f"""
## {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC
- **Secret rotated**: {secret_type}
- **Old secret hash (SHA-256)**: {old_hash[:16]}...
- **New secret hash (SHA-256)**: {new_hash[:16]}...
- **Performed by**: {os.getenv('USER', 'unknown')}
- **Status**: {'DRY RUN' if os.getenv('DRY_RUN') else 'COMPLETED'}
"""
        with open(self.rotation_log, 'a') as f:
            f.write(log_entry)


def main():
    parser = argparse.ArgumentParser(description="Rotate OHAS secrets")
    parser.add_argument("--secret", choices=["jwt", "smtp", "all"], required=True)
    parser.add_argument("--dry-run", action="store_true", help="Generate but don't write")
    parser.add_argument("--confirm", action="store_true", help="Confirm rotation (skip prompt)")
    args = parser.parse_args()

    rotator = SecretRotator()

    if args.secret == "jwt":
        new_secret = rotator.rotate_jwt_secret()
        print(f"New JWT secret: {new_secret[:8]}...{new_secret[-8:]}")
        if not args.dry_run:
            rotator.write_env_file({"SECRET_KEY": new_secret})
    
    elif args.secret == "all":
        # Read current env
        current_env = {}
        env_file = rotator.env_dir / ".env.production"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    current_env[key.strip()] = value.strip()
        
        new_secrets = rotator.rotate_all(current_env)
        print("New secrets generated:")
        for key in ("SECRET_KEY", "SMTP_PASSWORD"):
            val = new_secrets.get(key, "")
            print(f"  {key}: {val[:8]}...{val[-8:]}")
        
        if not args.dry_run and args.confirm:
            rotator.write_env_file(new_secrets)
            print("✅ Secrets rotated successfully.")
        else:
            print("Use --confirm to write changes.")


if __name__ == "__main__":
    main()
```

---

## 5. Emergency Revocation Procedures

### 5.1 Compromised JWT Secret

```
IMMEDIATE ACTIONS (within 1 hour):
──────────────────────────────────
1. Generate new SECRET_KEY:
   openssl rand -hex 32

2. Deploy new key IMMEDIATELY:
   a. Update SECRET_KEY in environment
   b. Restart all backend instances
   c. All existing tokens are now INVALID

3. Communicate impact:
   → All users must log in again
   → Post notification in app: "Security update — please log in again"
   → Email all users explaining the situation

4. Investigate:
   → How was the key compromised?
   → Was it exposed in logs, code, or elsewhere?
   → Were any tokens actually forged by an attacker?

5. Post-incident:
   → Write incident report
   → Update rotation procedure if needed
   → Consider implementing key versioning (dual-key validation)
```

### 5.2 Compromised Supabase Service Role Key

```
IMMEDIATE ACTIONS (within 1 hour):
──────────────────────────────────
1. Regenerate the key in Supabase Dashboard:
   → Project Settings → API → Service Role Key → Regenerate
   → This INVALIDATES the old key IMMEDIATELY

2. Update environment variable:
   SUPABASE_SERVICE_ROLE_KEY=<new key>

3. Restart backend

4. Audit for unauthorized access:
   → Check audit_logs for unusual queries during compromise window
   → Check for unexpected data modifications
   → Review Supabase logs for unusual API key usage

5. If data was modified:
   → Restore from Supabase backup (Point-in-Time Recovery)
   → Notify affected users if personal data was accessed
```

### 5.3 Compromised SMTP Password

```
IMMEDIATE ACTIONS (within 1 hour):
──────────────────────────────────
1. Change Google account password (or email provider password)
2. Revoke all app passwords:
   → Google Account → Security → App Passwords → Remove all
3. Generate new app password for OHAS
4. Update SMTP_PASSWORD environment variable
5. Restart backend
6. Check sent emails for unauthorized messages:
   → Review Google "Sent" folder
   → If phishing emails were sent, notify recipients
```

---

## 6. Secret Scanning (Prevention)

```yaml
# .github/workflows/secret-scan.yml

name: Secret Scanning

on:
  pull_request:
  push:
    branches: [main]

jobs:
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
      
      - name: Check for hardcoded secrets
        run: |
          # Check for common secret patterns in code
          # (excluding .env files and test fixtures)
          git grep -n "SECRET_KEY.*=" -- '*.py' '*.js' '*.ts' '*.jsx' '*.tsx' | \
            grep -v "os.getenv" | \
            grep -v "test_" | \
            grep -v "# " && \
            echo "❌ Possible hardcoded secret found!" && \
            exit 1 || \
            echo "✅ No hardcoded secrets detected."
```

---

## 7. Rotation Schedule Summary

```
┌────────────────────────────────┬──────────┬──────────────┬───────────────┐
│ Secret                         │ Schedule │ Next Rotation│ Owner         │
├────────────────────────────────┼──────────┼──────────────┼───────────────┤
│ JWT SECRET_KEY                 │ 90 days  │ 2026-07-04   │ DevOps Lead   │
│ Supabase Service Role Key      │ 90 days  │ 2026-07-04   │ DevOps Lead   │
│ Supabase Anon Key              │ 90 days  │ 2026-07-04   │ DevOps Lead   │
│ SMTP Password                  │ 90 days  │ 2026-07-04   │ DevOps Lead   │
│ Redis Password (if set)        │ 90 days  │ 2026-07-04   │ DevOps Lead   │
│ External API keys              │ Per docs │ Varies       │ Service Owner │
└────────────────────────────────┴──────────┴──────────────┴───────────────┘

Automation:
  → Calendar reminder set 7 days before each rotation
  → rotation_log.md tracks all past rotations
  → gitleaks scans every PR for accidental commits
```

---

## 8. Decision Summary

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Rotation frequency | 90 days | Industry standard (PCI DSS, SOC 2) |
| JWT rotation method | Dual-key validation → single key | Zero downtime, no user disruption |
| Emergency revocation | Immediate (all tokens invalidated) | Security over convenience |
| Key generation | `secrets.token_hex(32)` / `secrets.token_urlsafe()` | Cryptographically secure |
| Storage | Environment variables via .env files | Standard, works with any deployment |
| Audit trail | rotation_log.md | Simple, version-controllable |
| Prevention | gitleaks in CI | Catches accidental commits before merge |
| Supabase key rotation | Via Dashboard (manual) + env update | Supabase manages key lifecycle |

---

*This document should be updated after each rotation event, when new secret types are added, or when the rotation procedure is refined based on incident experience.*
