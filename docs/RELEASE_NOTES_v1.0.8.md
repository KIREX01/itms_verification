# Release Notes — ITMS Verification Copilot v1.0.8

**Release Date**: October 2026  
**Build Target**: Production Workstations & Mobile Companions (Windows, macOS, Linux, Android, iOS)  
**Release Tag**: `v1.0.8`

---

## 🎯 Executive Summary

Version 1.0.8 is an enterprise reliability and workflow release that equips the ITMS Verification Copilot with:
1. **24/7 Background Windows Service Architecture**: Continuous unattended operation surviving laptop lid-closes, user logoffs, and system idle states via Windows Away Mode (`SetThreadExecutionState`).
2. **Zero-Configuration Self-Healing SSL/HTTPS (Port 443 Default)**: Default offline self-signed certificates (`cert.crt`, `cert.key`) tracked directly in version control, self-healing 3-tier certificate generation fallback, and full resolution of `runserver_plus` certificate parameter bindings.
3. **Mobile Companion U-Turn Walk Session Batch Isolation**: Clean segmentation of off-conveyor walk photos into discrete session batches (`Batch 1`, `Batch 2`, etc.) with independent state resets on submission.
4. **3-Tier Pre-Dispatch Verification**: Multi-stage validation ladder (Local stock DB -> Live ITMS remote search & auto-provision -> Audio alert "NOT ON STOCK" and quarantine).
5. **Mobile Touch Responsiveness & Full-Screen Mobile Inspectors**: Natural scroll hierarchy for operator action buttons and dedicated full-screen inspector overlays with `◀ Back` navigation.
6. **Automated Service Lifecycle Integration**: Universal CLI management (`itms service [status|install|start|stop|restart|remove]`) with automatic service restart on `itms update` and clean removal on `itms uninstall`.

---

## 🚀 Detailed Features & Enhancements

### 1. 24/7 Windows Background Service with Away Mode
* **Continuous Unattended Execution**:
  * Implemented `windows_service.py` (`ITMSVerificationService` / `ITMS Verification Background Service`).
  * Concurrently executes the Django HTTPS Web Console (`run_web 0.0.0.0 443`) and the background 3-hour Safe Room Stock Crawler (`MorningKitSyncDaemon`).
* **Windows Away Mode Integration**:
  * Employs Win32 API `ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)`.
  * Prevents Windows from suspending the CPU, network socket, or background sync crawler when laptop lids are closed or the workstation enters idle/away mode.
* **Service Control Manager (SCM) Watchdog Recovery**:
  * Configured automated recovery actions in Windows SCM:
    * First failure: Restart service after 5 seconds.
    * Second failure: Restart service after 10 seconds.
    * Subsequent failures: Restart service after 30 seconds.
* **Clean Process Tree Termination**:
  * Implemented `taskkill /F /T /PID` on service stop to eliminate orphaned Python worker processes and guarantee port 443 is immediately released.
* **Universal CLI Service Commands**:
  * `itms service status`: Displays current service status (Running, Stopped, Not Installed).
  * `itms service install`: Registers the service with Windows SCM with automatic UAC elevation.
  * `itms service start`: Starts the background service.
  * `itms service stop`: Stops the background service.
  * `itms service restart`: Safely stops and restarts the service.
  * `itms service remove`: Unregisters and removes the service from Windows SCM.
* **Lifecycle Automation**:
  * `itms update`: Detects active Windows Service, stops it cleanly before pulling code, and restarts it automatically.
  * `itms uninstall`: Automatically unregisters and removes the Windows Service before purging files.

---

### 2. Zero-Configuration Self-Healing SSL/HTTPS & Certificate Tracking
* **Repository-Tracked Default Offline Certificates**:
  * Removed `cert.crt` and `cert.key` from `.gitignore`. Both dummy self-signed certificates for `localhost` / `*.localhost` are now tracked directly in version control.
  * Ensures that production installations, Git clones, and `itms update` pulls receive valid working certificates immediately out-of-the-box without missing file crashes.
* **`runserver_plus` Certificate Binding Fix**:
  * Resolved parameter incompatibility where `django-extensions` `runserver_plus` expects `options["cert_path"]` and `options["key_file_path"]` rather than `cert_file` / `key_file`.
  * Fixed bug where `runserver_plus` silently reverted to plain HTTP on port 443, which caused Firefox connection failure (`connectionFailure`) and Chrome provisional header warnings.
* **Cascading 3-Tier Self-Healing SSL Generation**:
  * `core/services/ssl_service.py` provides independent certificate auto-generation if certificates are ever missing or corrupt:
    1. Python `cryptography` in-memory X.509 generator.
    2. Windows native PowerShell .NET in-memory PKI generator.
    3. Pre-embedded 10-year verified fallback certificate and RSA private key.
* **Decoupled Module Imports**:
  * `ssl_service.py` safely computes `BASE_DIR` from its own filesystem location, enabling early bootstrap certificate verification without requiring Django settings to be pre-configured.

---

### 3. Mobile Companion U-Turn Walk Session Batch Isolation
* **Discrete Session Batches**:
  * Off-conveyor walks are now isolated into numbered batches (`Batch 1`, `Batch 2`, etc.).
  * Prevents photos from a new walk (e.g. rear 3 & 4, front 4 & 3) from inadvertently pairing with leftover photos from a previous walk (e.g. rear 1 & 2, front 2 & 1).
* **Independent State Reset on Submit**:
  * Submitting a walk batch resets the local session state completely, increments the batch counter, and readies the queue for the next walk session.
* **Dedicated Batch Limits**:
  * Off-conveyor walks operate on their own batch size limit, completely separated from the 100-pair conveyor limit.

---

### 4. 3-Tier Pre-Dispatch Verification Flow
* **Tier 1: Local Stock DB Verification**:
  * Verifies scanned plate/barcode against locally cached installation kits.
* **Tier 2: Real-Time ITMS Remote Query & Auto-Provision**:
  * When an item is not found locally, the system queries `stock.itms.ug` live via `check_itms_live`.
  * If found on ITMS remotely, it auto-provisions the record into the local SQLite database and marks it valid.
* **Tier 3: Audio Alert & Quarantine**:
  * If absent from both local and remote ITMS, the system plays an audible "NOT ON STOCK" alert.
  * The kit is routed to the side-car quarantine table for manual audit by the Stock Transfer Officer, preventing unregistered plates from reaching assembly fitters.

---

### 5. Mobile Touch UI & Full-Screen Mobile Inspectors
* **Natural Scroll Action Bar**:
  * Updated `.operator-decision-bar` on mobile viewports: set `position: relative !important; bottom: auto !important; margin-bottom: 60px; min-height: 48px;`.
  * Operator action buttons flow naturally below the content and never obstruct table rows or touch controls.
* **Full-Screen Mobile Inspectors with `◀ Back` Navigation**:
  * `openItmsMobileInspector(record)`: Full-screen mobile view for ITMS verification queue records.
  * `openStockMobileInspector(record)`: Full-screen mobile view for Stock ledger records.
  * Minimum 44px/48px touch targets across all mobile buttons and momentum horizontal scrolling for wide data tables.

---

### 6. Repository & Codebase Clean-Up
* **Removed Obsolete Scripts**:
  * Purged all 13 scratch and temporary patch scripts (`fix.py`, `temp.txt`, `update_*.py`, etc.) ensuring a pristine codebase for production deployments.

---

## 🔒 Upgrade & Migration Instructions

To upgrade an existing installation to v1.0.8:

```bash
itms update
```

The updater will:
1. Detect any running background Windows Service and stop it cleanly.
2. Pull latest code, default SSL certificates, and fixes from GitHub.
3. Apply any database migrations.
4. Auto-restart or install the Windows Service.

To check service status after updating:
```bash
itms service status
```

To open the console:
```bash
itms
```
Navigate your browser to: `https://127.0.0.1/` (or `https://<LAN-IP>/` for mobile companion devices).
