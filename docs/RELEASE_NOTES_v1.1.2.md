# Release Notes: CLOSE Copilot v1.1.2

**Release Date:** November 2026
**Milestone:** v1.1.2: Security hardening (Phase 0 of the Backoffice plan)
**Baseline:** v1.1.1 (production)

---

## Summary

v1.1.2 closes the most dangerous access-control gaps found while planning the central backoffice.
There are no database migrations and no new features. Some actions that any logged-in user could
previously perform now require an **administrator (superuser)** account.

---

## Security Fixes

| # | Area | Before | After |
|---|---|---|---|
| 1 | Security & access APIs (`/api/security/overview/`, `/api/security/action/`) | Any logged-in user could approve/unblock devices, invite users, change policy and read activation codes | Superuser only |
| 2 | Settings API (`/api/settings/`) | Any user could flip `itms.enabled`, `maintenance.*`, `security.*`, `submission.*`, `outbox.*` | Non-admins may only change operator-level settings (active bond, sync cadence, shift date, dry-run, theme). Every key in a request is validated before any is written. |
| 2 | Apply update (`/api/updates/apply/`) | Any logged-in user | Superuser only |
| 3 | Emailed "approve access" link (`/access/review/<token>/`) | A bare GET approved or blocked with **no login** | Requires a signed-in superuser; GET shows a confirmation page; the decision is a CSRF-protected POST |
| 4 | Maintenance mode | `?bypass_maintenance=1` let anyone in; staff bypass never worked; the page was rewritten to disk on every blocked request | Query bypass removed; signed-in superusers can use the system; `/login/` stays reachable; no disk writes per request |
| 5 | Device access guard | An internal error allowed the request (fail-open) | Fails closed (503) on protected routes; login stays reachable |
| 6 | Revocation | "Extend" (incl. TUI Master PIN override) silently un-blocked devices; re-invites, approvals and activation codes re-enabled admin-deactivated accounts; 6-digit codes could be brute-forced | Extensions never lift blocks; only never-signed-in pending signups are auto-activated; 5 wrong codes → 15-minute lockout |
| 7 | TUI settings save | Saving a stale config after toggling maintenance left maintenance mode stuck on | Pane settings are saved first, maintenance applied last |
| 8 | Public `/api/itms/status/` | Exposed the connected ITMS operator email, user UUID and bond; anonymous callers could force live portal pings | Anonymous callers get reachability only; forced pings need a login |
| 9 | Bugs | TUI renewal-code unlock crashed (`request=None`); non-2FA signup raised `NameError` | Fixed |
| 10 | Signup | Plaintext password held in the session; 6-digit code placed in the verify URL; the first person to register became superuser | Only a password hash is stored; the code is never in a URL; signup never creates admins (the installer does) |

---

## Upgrade Notes
- **Who is affected:** operators who used the web Settings → Security panel, flipped kill switches from
  the web, or applied updates from the web now need an administrator account. TUI behaviour is unchanged.
- **Fresh installs:** the administrator account comes from the installer (`create_superuser_if_none`),
  not from the first signup.
- Maintenance mode: administrators sign in at `/login/` during maintenance to verify the system.

### Production EC2 Server
```bash
sudo -u www-data git -C /opt/itms pull origin release/linux
sudo systemctl restart itms
itms status   # should report v1.1.2
```

### Rolling Back
`sudo itms rollback v1.1.1`. There are no migrations, so rollback is safe.
