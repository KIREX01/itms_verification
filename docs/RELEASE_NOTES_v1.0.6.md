# Release Notes — ITMS Verification Copilot v1.0.6

**Release Date**: October 2026  
**Build Target**: Production Workstations (Windows, macOS, Linux)  
**Release Tag**: `v1.0.6`

---

## 🎯 Executive Summary

Version 1.0.6 is a comprehensive reliability, usability, and operational release designed for high-throughput vehicle verification and bond warehouse management. It introduces the **Live Mobile Camera Stock Scanner**, an **Asynchronous Multi-Page Installation Kit Crawler**, **Daily Shift Stock Reconciliation**, **Order Unlinking in the Review Queue**, a **Standardized Non-Scrolling TUI Tab Bar**, and **Standardized Developer vs. User Settings**.

---

## 🚀 Key Features & Enhancements

### 1. Standardized Non-Scrolling Terminal UI (TUI)
* **Single-Word Uppercase Tabs**: Replaced elongated, emoji-laden tab headers with concise, clean single uppercase labels:
  ```text
  ┌────────────────────────────────────────────────────────────────────────┐
  │  DASHBOARD │ ITMS │ BATCHES │ REVIEW │ AUDIT │ REPORTS │ SETTINGS      │
  └────────────────────────────────────────────────────────────────────────┘
  ```
* **Zero Horizontal Scrolling**: Tab bar width reduced from >140 columns to under 50 columns, guaranteeing clean rendering without horizontal scrollbars on standard 80-column terminals.
* **Streamlined Tab Ordering**: Reordered tabs so `REPORTS` immediately follows `AUDIT` and precedes `SETTINGS`:
  - `[1]` / `F1` / `Ctrl+1`: `DASHBOARD`
  - `[2]` / `F2` / `Ctrl+2`: `ITMS`
  - `[3]` / `F3` / `Ctrl+3`: `BATCHES`
  - `[4]` / `F4` / `Ctrl+4`: `REVIEW`
  - `[5]` / `F5` / `Ctrl+5`: `AUDIT`
  - `[6]` / `F6` / `Ctrl+6`: `REPORTS`
  - `[7]` / `F7` / `Ctrl+7`: `SETTINGS`

### 2. Review Queue Order Unlinking (`X` / `Ctrl+U`)
* **Rapid Operator Disassociation**: Technicians can now disassociate an incorrectly matched or obsolete ITMS order from a vehicle pair directly from their keyboard using `X` or `Ctrl+U`.
* **State Reset**: Clears the pair's linked order, resets its status back to `UNMATCHED`, and logs a structured `UNLINK` entry in the submission audit log.
* **Modal Unlink Action**: Added an explicit **`[Unlink Order]`** button inside the pair inspector modal.

### 3. Mobile Live Camera QR & Barcode Stock Scanner
* **In-Browser Hardware Camera Access**: Technicians can point their smartphone camera at hardware kits, QR codes, or barcode labels (`/mobile/stock-scanner/`) with zero native app installation.
* **Dual-Engine Decoding**: Accelerated `BarcodeDetector` API with seamless fallback to client-side ZXing-JS decoding.
* **Industrial UX**: Features an animated laser targeting reticle, audio beep on scan, instant camera switching (front/rear), and hardware torch toggle.
* **Automatic Ledger Sync**: Barcodes and tracker IDs are instantly validated and registered into the local bond stock registry.

### 4. Multi-Page Installation Kit Crawler & Morning Provisioning
* **Asynchronous Deep Crawler**: Traverses up to 35 pages of ITMS installation kits without blocking the user interface or freezing event loops.
* **Live Progress Reporting**: Real-time feedback during sync operations showing page counts, records parsed, and newly registered kits.
* **Morning Provisioning**: Flags kits prepared for the morning installation shift against active factory orders.

### 5. Shift Stock Reconciliation & Dynamic Audit Dockets
* **Automated Discrepancy Auditing**: Compares physical stock kits against ITMS active orders and completed installations.
* **Timestamped CSV Exports**: Automatically exports daily bond stock audits (`itms_bond_stock_<date>_<timestamp>.csv`).
* **Semantic Release Attribution**: Dynamic report header attributing all certified stock dockets to `v1.0.6`.

### 6. Standardized Developer vs. User Settings
* **Hierarchical Separation**:
  - **User Settings**: Report output destination, default photo batch directory, preferred OCR engine, auto-approval thresholds.
  - **Developer Settings**: Fine-tuned YOLO weight model selector (`models/*.pt`), detection confidence threshold, camera latency budgets, background sync intervals.
* **Native OS Folder Selectors**: Integrated native operating system folder pickers for selecting report export folders and image batch directories.

### 7. ITMS Hub Page Indicator Visibility
* **Fixed Missing Page Counters**: Resolved issue where page numbers for Active Orders, Archive, and Installation Kits were not displayed after fetching.
* **Interactive Pagination**: Seamlessly navigates pages with active position indicators (`Page 1 of 35 (700 Total)`).

### 8. Self-Healing Dependency Installation During Updates
* **Automated `requirements.txt` Ingestion**: `itms update`, `python manage.py check_updates --apply`, and the Web Console update manager now automatically install any new or missing Python dependencies directly into the active `.venv` environment prior to running database migrations.
* **SHA-256 Dependency Caching**: Caches `.reqs_hash` to skip redundant pip operations when dependencies are already up to date, completing checks in milliseconds.
* **System Health Diagnostics**: `itms status` and `itms bootstrap` now audit dependencies against `requirements.txt` using non-destructive environment metadata scanning.

---

## 🛠️ CLI Quick Reference for v1.0.6

| Command | Action |
| :--- | :--- |
| `itms` | Launch Web Operator Console in default browser |
| `itms --tui` | Launch Textual Terminal UI |
| `itms status` | Diagnostic health check (database, AI models, OCR) |
| `itms update` | Check for and apply latest updates from GitHub |
| `python manage.py sync_stock_kits` | Crawl and sync all ITMS installation kits |
| `python manage.py reconcile_shift --date today` | Audit today's shift and export bond ledger CSV |
| `python itms_cli.py scan-camera` | Launch Mobile Camera Barcode Scanner CLI |

---

## 🔒 Upgrade & Migration Instructions

Upgrading from v1.0.5 to v1.0.6 preserves all local data, SQLite/PostgreSQL databases, and media vaults:

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
* `core.tests.test_stock_monitoring`: 25 passed (`OK`)
* `core.tests.test_tui`: 6 passed (`OK`)
* `core.tests.test_vault_lifecycle`: passed (`OK`)
* `core.tests.test_order_sync_and_prior`: passed (`OK`)
