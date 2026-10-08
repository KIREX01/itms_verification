# Release Notes — ITMS Verification Copilot v1.0.7

**Release Date**: October 2026  
**Build Target**: Production Workstations & Mobile Companions (Windows, macOS, Linux, Android, iOS)  
**Release Tag**: `v1.0.7`

---

## 🎯 Executive Summary

Version 1.0.7 is a major operational milestone that brings deep crawler scaling, floor discrepancy isolation, mobile companion U-Turn walk isolation, strict real-time dispatch verification, and mobile touch UI responsiveness to the ITMS Verification Copilot. It ensures rock-solid stock ledger integrity by isolating physical floor liabilities from safe-room stock and vision OCR fragments, provides a discrete walk pairing workflow for off-conveyor motorcycle staging, and delivers full mobile responsiveness for all queue and table inspectors on mobile screens.

---

## 🚀 Key Features & Enhancements

### 1. High-Capacity Deep Kit Crawler (100 Pages / 2,000+ Kits)
* **Scaled Crawler Traversal**: Expanded default crawler traversal from 35 to 100 pages (`min 100 pages = 2,000+ installation kits`) across all UI tabs, background sync commands, and REST APIs.
* **Polite Crawl Pacing**: Configured polite crawl pacing (`0.35s` sleep + randomized jitter) to maintain low load against `stock.itms.ug`.
* **Rate-Limit Resilience**: Added HTTP 429 `Retry-After` header parsing, dynamic exponential backoff, and circuit breaker protection.
* **Atomic Bulk Upserts**: Replaced row-by-row queries with atomic chunked bulk upserts (`bulk_create` / `bulk_update` in chunks of 200 items), eliminating database table locking during active verification shifts.

### 2. 3-Hour Safe Room Background Sync Daemon
* **`MorningKitSyncDaemon`**: Configured with a 3-hour interval (10,800s) and 10-minute cooldown window, auto-starting in `run_web.py` and `app.py`.
* **Global Concurrency Lock**: Protected by `_global_kit_crawl_lock` to prevent overlapping crawls between manual UI triggers and background jobs.

### 3. Floor Discrepancy Isolation & Clean Stock Dockets
* **Safe-Room vs. Floor Isolation**: Completely decoupled safe-room warehouse stock (`InstallationKit` status `"New"`) from unallocated exception dockets. Safe-room stock belongs exclusively in inventory ledger balances.
* **Elimination of Vision OCR Noise**: Eliminated camera OCR detections (`EvidenceImage`, `VehicleInstallationPair`) from stock reconciliation, guaranteeing zero partial text fragments or misread plates in stock dockets.
* **Anchored Physical Liabilities**: Ground truth floor liabilities are strictly anchored to physical barcode/plate scans (`StockDispatchScan`).

### 4. Mobile Companion U-Turn Walk Isolation
* **Discrete Walk Batches**: Off-conveyor motorcycle walks are segmented into discrete batches (`Batch 1`, `Batch 2`, etc.) with independent state resets on submission.
* **Clean Pair Isolation**: Off-conveyor pairing algorithms now maintain session-scoped pairing arrays, preventing new walk batches (e.g., Rear 3 & 4 with Front 4 & 3) from cross-pairing with previously completed or submitted batch pairs (Rear 1 & 2 with Front 2 & 1).
* **Independent Batch Limit**: Off-conveyor walk batches operate independently from the 100-pair conveyor limit.

### 5. Strict Dispatch Verification Flow with Live ITMS Fallback
* **Three-Tier Verification Flow**:
  1. **Local Database Check**: When a dispatch plate/barcode is scanned, check local database. If kit exists, mark as dispatched immediately.
  2. **Live ITMS Query**: If not found locally, query the live ITMS server (`check_itms_live`). If available, automatically provision the kit into local storage and mark dispatched.
  3. **Not On Stock Alert**: If the plate does not exist in the local database or live ITMS, trigger an audible "NOT ON STOCK" alert and display a prominent set-aside prompt to prevent unregistered plates from reaching assembly fitters.

### 6. Mobile Touch Responsiveness & Full-Screen Inspectors
* **Queue Action Bar Scrollability**: Relocated operator decision action buttons (`Approve`, `Edit`, `Swap`, `Submit`) from fixed sticky bottom bar into the natural scroll hierarchy on mobile screens (`position: relative !important; bottom: auto !important; margin-bottom: 60px; min-height: 48px;`), ensuring full touch visibility and tap target accessibility on phone screens.
* **Mobile ITMS Table Inspector**: Added full-screen mobile inspector overlay (`openItmsMobileInspector` / `closeItmsMobileInspector`) with dedicated `◀ Back` navigation button for inspecting active orders, archives, and installation kits on phone screens.
* **Mobile Stock Ledger Inspector**: Added full-screen mobile inspector overlay (`openStockMobileInspector` / `closeStockMobileInspector`) with dedicated `◀ Back` navigation button for inspecting daily stock ledgers on phone screens.

### 7. Codebase Clean-Up & Production Security Guardrails
* **Removed Diagnostic Files**: Cleaned up all temporary scratch scripts, diagnostic dumps, and obsolete patch utilities.
* **SSL Certificate Protection**: Added `.gitignore` patterns for local SSL certificates and private keys (`*.crt`, `*.key`, `cert.crt`, `cert.key`).

---

## 🛠️ CLI Quick Reference for v1.0.7

| Command | Action |
| :--- | :--- |
| `itms` | Launch Web Operator Console in default browser |
| `itms --tui` | Launch Textual Terminal UI |
| `itms status` | Diagnostic health check (database, AI models, OCR) |
| `itms update` | Check for and apply latest updates from GitHub |
| `python manage.py sync_stock_kits --max-pages 100` | Crawl up to 100 pages (2,000+ kits) from ITMS |
| `python manage.py reconcile_shift --date today` | Audit today's shift and export certified bond ledger CSV |
| `python itms_cli.py scan-camera` | Launch Mobile Camera Barcode Scanner CLI |

---

## 🔒 Upgrade & Migration Instructions

Upgrading from v1.0.6 to v1.0.7 preserves all local data, SQLite/PostgreSQL databases, and media vaults:

```bash
# Using the universal CLI:
itms update

# Or via git:
git fetch origin main
git pull --rebase
python manage.py migrate
```

---

## 🧪 Verification & Test Results

All regression and unit tests pass with zero errors:
* `core.tests.test_mobile_companion`: 16 passed (`OK`)
* `core.tests.test_stock_monitoring`: 56 passed (`OK`)
* `core.tests.test_tui`: 6 passed (`OK`)
* `core.tests.test_ssl_service`: passed (`OK`)
