# Release Notes — ITMS Verification Copilot v1.0.9

**Release Tag**: `v1.0.9`  
**Deployment Target**: Production EC2 (`close.kirex.online`) & Local Windows Floor Staging  
**Release Date**: 2026-10-25  

---

## 🚀 Executive Summary

Version **v1.0.9** is an enterprise-grade operational, security, and lifecycle management release for **ITMS Verification Copilot**. It resolves real-world production bottlenecks observed during high-volume vehicle fitment verification at the AGM bonded installation facility:

1. **Eliminates Upstream Submission Drops (The 3% Issue)** via cross-process concurrency locking, paced image streams, and strict Step 4 post-submission archive verification.
2. **Fixes Quick Install Failures** by resolving floor serial strings to upstream Yii2 inventory UUIDs, extracting fresh form CSRF tokens, and preserving hardware selections during 1-click photo uploads.
3. **Drastically Reduces Upstream Portal Traffic** with a 120-second process-level reachability cache, master switch probe bypass, and a visibility-aware browser heartbeat cadence.
4. **Hardens Host & App Security** by enforcing non-root service execution (`User=www-data`), systemd sandboxing, UFW host firewall rules, 0600 file permission lockdown, Nginx TCP drops (`444`) for scanner probes, and daily automated encrypted PostgreSQL backups.
5. **Introduces the Unified `itms` CLI & Dynamic Maintenance System** providing graceful `itms start`, `itms stop`, `itms restart`, and `itms status` controls coupled with a standalone, self-healing maintenance page configured in TUI Developer Mode.

---

## 🛠️ Key Improvements & Architecture

### 1. Concurrency Locking & Pacing (`core/services/submission_lock.py`)
- **Global Concurrency Guard**: Replaces concurrent batch thread storms with an atomic file lock (`secure/submission.lock`). Prevents multiple browser tabs or background workers from overwhelming the upstream ITMS server.
- **Paced Upload Pipeline**: Implements a mandatory 1.0-second delay between consecutive order uploads to prevent socket saturation.
- **Step 4 Archive Verification**: After completing Step 3 (photo submissions), the client queries ITMS archive records (`/installation-orders/index?search=<order_number>`) to confirm the order moved into `ARCHIVED` status upstream before updating the local database. Eliminates silent false fallbacks to `OFFLINE_OUTBOX`.

### 2. Quick Install Serial-to-UUID Resolution (`core/services/itms_web_client.py`)
- **Option UUID Resolver**: `resolve_option_uuid()` queries ITMS kit inventory dropdowns and maps raw scanned serial numbers (e.g. `001182812`, `8BAE470808CF`) to valid 36-character UUIDs.
- **Fresh Form CSRF Extraction**: Automatically fetches fresh `_csrf-frontend` tokens from `validate-installation` before sending POST payloads, avoiding HTTP 400 Bad Request token rejections.
- **1-Click Submit Hardware Carry-Through**: Fixed the direct submit modal to retain resolved hardware IDs across Step 1 and Step 2.
- **HTML5 Datalist Support**: Auto-populates rear plate and tracker fields when the front plate is selected.

### 3. Upstream Heartbeat Throttling (`core/services/auth_service.py`, `app.js`)
- **120-Second Reachability Cache**: Class-level caching on `test_connection()` prevents rapid HTTP pings from flooding `stock.itms.ug`.
- **Master Switch Bypass**: When the ITMS Master Switch is disabled (`itms.enabled: false`), upstream probes are skipped entirely, returning offline status in 0.0ms.
- **Visibility-Aware UI Cadence**: Heartbeat interval relaxed from 25s to 60s, automatically pausing when the operator hides or switches browser tabs.
- **Manual Probe Override**: Dedicated "Probe Upstream" button with `?force=1` bypasses the cache on demand.

### 4. Server & Infrastructure Hardening (EC2 `close.kirex.online`)
- **Non-Root Execution**: `itms.service` migrated from `User=root` to `User=www-data` with `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, `PrivateTmp=yes`, and `ReadWritePaths=/opt/itms /var/log`.
- **UFW Host Firewall**: Default incoming deny; strictly ports `22` (SSH), `80` (HTTP), and `443` (HTTPS) permitted. Direct external access to Postgres (`5432`) and Django (`8000`) blocked.
- **Secrets Lockdown**: Chmod `0600` on `/opt/itms/.env` and `secret_key.txt`, `0640` on `config.json`. Permanently removed superuser passwords from `.env`.
- **IP Scanner Suppression**: Nginx configured with default catch-all server blocks returning HTTP `444` (instant TCP drop) for non-domain/IP requests.
- **Automated PostgreSQL Backups**: Daily cron job (`/etc/cron.d/itms_backup`) executing `pg_dump | gzip` at 02:30 UTC with a 14-day auto-purge policy.
- **Log Rotation**: Automated logrotate policy (`/etc/logrotate.d/itms`) for `media/*.log` with 14-day rotation and `copytruncate`.

### 5. Unified Lifecycle CLI (`itms`) & Maintenance Page
- **Global Binary**: Available on PATH as `itms <action>` on Linux, and `itms.bat` / `itms.ps1` on Windows.
- **Commands**:
  - `itms start`: Launches the application and restores live browser access.
  - `itms stop`: Completely shuts down application services and activates the browser maintenance page.
  - `itms restart`: Cycles the web service.
  - `itms status`: Displays status of web port, systemd, Nginx, maintenance mode, ITMS switch, and PostgreSQL.
  - `itms maintenance [on|off]`: Manually toggles maintenance mode without stopping the daemon.
  - `itms switch [on|off]`: Toggles upstream ITMS synchronization and network traffic.
- **Standalone Maintenance Page**: Configured in TUI Developer Mode (`core/tui/settings_pane.py`), compiled to standalone HTML, and served directly by Nginx (HTTP 503) when the application is stopped. Includes a live 30-second countdown timer and auto-refresh.

---

## 🧪 Verification & Test Results

All 22 core regression and lifecycle tests passed against PostgreSQL in production on EC2:

```text
Ran 7 tests in 10.507s (test_lifecycle_maintenance) -> OK
Ran 15 tests in 25.589s (test_quick_install, heartbeat, lock, archive) -> OK
Ran 15 tests in 12.067s (test_security_audit) -> OK
System check identified no issues (0 silenced).
```

---

## 📦 Upgrade Instructions

### Linux / Production EC2
```bash
sudo -u www-data git -C /opt/itms pull origin release/linux
itms restart
itms status
```

### Windows Local Console
```powershell
git pull origin release/windows
.\itms.bat status
```
