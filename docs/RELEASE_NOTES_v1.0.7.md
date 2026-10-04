# Release Notes — ITMS Verification Copilot v1.0.7

**Release Date**: October 2026  
**Build Target**: Production Workstations (Windows, macOS, Linux)  
**Release Tag**: `v1.0.7`

---

## 🎯 Executive Summary

Version 1.0.7 is a mission-critical operational stability, scalability, and audit accuracy release. It addresses two primary requirements from factory assembly and warehouse floor teams:
1. **Scaled Safe-Room Catalog Synchronization**: Increases kit crawling capacity to a minimum of 100 pages (2,000+ installation kits) with polite rate-limiting, 429 exponential backoff, and a dedicated 3-hour background synchronization daemon.
2. **Floor Discrepancy & MVR Docket Isolation**: Eliminates safe-room inventory kits and vision OCR noise from the floor unallocated discrepancy audit, ensuring `#table-report-unallocated` reflects strictly physical dispatch scans missing an ITMS order.

---

## 🚀 Key Features & Enhancements

### 1. High-Capacity Deep Installation Kit Crawler (100 Pages / 2,000+ Kits)
* **Scaled Page Limit**: Scaled the crawler default from 35 to 100 pages (`min 100 pages = 2,000+ kits`) across all configuration services, TUI tabs, Web templates, and CLI commands.
* **Polite Crawler Pacing**: Injects a polite `0.35s` sleep with randomized jitter between consecutive page requests to maintain negligible server load against `stock.itms.ug`.
* **Dynamic 429 Retry-After Backoff**: Automatically parses HTTP 429 response headers and applies exponential backoff with a circuit breaker to avoid hitting external rate limits.
* **Atomic Chunked Bulk Upserts**: Replaced row-by-row queries with atomic `bulk_create(ignore_conflicts=True)` and `bulk_update` in chunks of 200 items, preventing database locks.

### 2. Asynchronous 3-Hour Safe Room Sync Daemon
* **Recurring Background Execution**: Introduces `MorningKitSyncDaemon` configured with a 3-hour interval (10,800s) and 10-minute cooldown window, running seamlessly in the background without blocking UI event loops.
* **Global Crawl Concurrency Lock**: Protected by `_global_kit_crawl_lock` to ensure multiple workers (UI sync, CLI commands, background daemon) do not trigger overlapping network requests.

### 3. Strict Isolation of Safe-Room Stock from Floor Unallocated Discrepancies
* **Elimination of Safe-Room Stock from Floor Reports**: Previously, `report_service.py` (`Source B`) mistakenly appended all warehouse inventory kits with status `"New"` into `#table-report-unallocated`, displaying over 1,300 plates as discrepancies even when no plates were dispatched.
* **Decoupled Architecture**: Safe-room inventory now strictly belongs in warehouse stock balances. The floor unallocated table and MVR Exception Docket only display plates physically scanned out for dispatch (`StockDispatchScan`) that have no matching active order or archive in ITMS.
* **Accurate Elimination Math**:
  $$\text{Dispatched Scans} - \text{Returned Scans} - \text{Archived Orders} - \text{Active Orders} = \mathbf{Floor\ Unallocated\ Plates}$$

### 4. Elimination of Camera OCR Fragments from Stock Reconciliation
* **Zero OCR Misread Noise**: Removed `evidence_plates_set` (which queried detections from `VehicleInstallationPair` and `EvidenceImage`) from `compute_daily_reconciliation`.
* **Physical Scans as Ground Truth**: Daily stock reconciliation now relies exclusively on physical barcode/plate scans at dispatch, uninstalled safe-room returns, and digital ITMS orders, guaranteeing zero camera OCR misread fragments in stock ledgers.

### 5. Safe-Room "Set Aside" Pre-Dispatch Verification Workflow
* **Pre-Dispatch Validation**: Validates scanned kits against the synchronized safe-room ITMS stock catalog prior to releasing them to the assembly line.
* **Factory Floor Set-Aside Procedure**:
  - If a plate is physically present in the safe room but not yet entered on ITMS by the ITMS Stock Transfer Officer, it is immediately blocked and set aside.
  - The team leader substitutes another valid kit from safe-room stock so assembly line fitting continues without delay.
  - The unlisted kit is reported to the ITMS Stock Transfer Officer via email/WhatsApp and scanned once officially added.

---

## 🧪 Verification & Test Results

* **Automated Test Coverage**: 100% pass rate across 52 automated tests in `core.tests.test_reports_and_lifecycle` and `core.tests.test_stock_monitoring`.
* **Zero Database Locks**: Validated atomic bulk operations across PostgreSQL and SQLite under high-volume mock kit loads.

---

## 🛠️ CLI Quick Reference for v1.0.7

| Command | Action |
| :--- | :--- |
| `itms update` | Fetch and apply latest v1.0.7 updates from GitHub |
| `python manage.py sync_stock_kits --pages 100` | Run a deep 100-page crawl of all installation kits |
| `python manage.py reconcile_shift --date today` | Compute daily shift reconciliation and export docket CSV |
| `itms status` | Run diagnostic health check on database, daemons, and models |

---

## 🔒 Upgrade Instructions

To upgrade an existing installation:

```bash
# Using the universal ITMS CLI:
itms update

# Or manually:
git pull origin main
python manage.py migrate
```
