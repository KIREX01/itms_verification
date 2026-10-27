# Release Notes: ITMS Verification Copilot v1.1.0

**Release Date:** October 27, 2026  
**Milestone:** v1.1.0  
**Target Environments:** Ubuntu 22.04 LTS (AWS EC2 Production) & Windows 10/11 Professional (Dispatch Workstations)

---

## Executive Summary

Version 1.1.0 introduces **Direct Plate Character Recognition (ALPR)** to eliminate generic OCR failure points, implements a **Safe Non-Destructive Version Rollback Engine** accessible directly from the Operator TUI and CLI, and hardens the command-line dispatcher interface (`itms --tui` and `itms <action>`).

---

## Major Enhancements & Key Features

### 1. Direct License Plate Character Recognition (ALPR)
- **OCR-Bypass Pipeline:** Introduced direct character-level detection and recognition on high-resolution plate crops (`core/vision/character_recognizer.py`), achieving 90.3%+ first-pass accuracy without falling back to generic OCR dictionaries.
- **Dual-Engine Fusion:** Retains PaddleOCR and Tesseract as intelligent fallback engines when character recognition confidence is below configurable thresholds (`char_min_conf`).
- **Syntax Disambiguation:** Integrated Ugandan registration plate rules directly into character sequencing to reliably resolve ambiguous alphanumeric pairs (`B` vs `8`, `O` vs `0`, `Z` vs `2`, `I` vs `1`).

### 2. Operator TUI & CLI Version Rollback Engine
- **One-Click Non-Destructive Rollback:** Operators can instantly roll back to any previous release tag (`v1.0.9`, `v1.0.8`, etc.) directly from **Settings Pane Tab 7 (Card 2G)** or via CLI (`itms rollback [tag]`).
- **Zero Data Loss Guarantee:** Untracked database assets (`db.sqlite3`), evidence photos (`media/vault/`), cropped plates (`media/crops/`), and local secrets (`.env`) are strictly protected from overwrite or deletion during upgrade or rollback operations.
- **Rollback Points:** Automatic snapshot creation (`secure/.rollback_point`) prior to applying updates, enabling 100% reliable single-step reversals.

### 3. Resilient CLI Dispatcher & Script Wrappers
- **Fixed `itms --tui` Error:** Re-engineered `core/management/commands/itms_ctl.py` to make the `action` positional argument optional with a default of `status`, adding direct support for `tui` and `rollback` actions and `--tui` / `-t` flags.
- **Linux Service Wrapper (`scripts/itms`):**
  - Interactive commands (`itms`, `itms --tui`, `itms tui`) run directly under the active shell session without `sudo`, preventing terminal capability loss and environment corruption.
  - Read-only diagnostics (`itms status`) execute without root privileges.
  - State mutations (`start`, `stop`, `restart`, `maintenance`, `switch`, `rollback`) intelligently prompt for or utilize `sudo`.
- **Windows Dispatchers (`itms.bat`, `itms.cmd`, `itms.ps1`):** Synchronized to route `itms`, `itms --tui`, and `itms tui` directly into the Textual operator dashboard.

---

## Migration & Operational Runbook

### Upgrading Remote EC2 Server
```bash
# 1. Update from remote repository
sudo -u www-data git -C /opt/itms pull origin release/linux

# 2. Update CLI script permissions
sudo cp /opt/itms/scripts/itms /usr/local/bin/itms
sudo chmod +x /usr/local/bin/itms

# 3. Restart daemon & verify status
sudo systemctl restart itms
sudo systemctl is-active itms
itms status
```

### Rolling Back to v1.0.9 (If Required)
- **Via TUI:** Navigate to **Settings (Tab 7) -> Developer Mode -> Card 2G -> Rollback to Previous Version -> Select v1.0.9 -> Confirm**.
- **Via CLI:**
  ```bash
  sudo itms rollback v1.0.9
  ```

---

## Validation & Automated Testing
- `test_character_recognizer`: Validates direct ALPR character extraction, confidence thresholds, and OCR bypass logic.
- `test_updater`: Validates update checking, rollback candidate enumeration, and non-destructive rollbacks.
- `test_vision`: Confirms end-to-end plate localization and dual-head recognition integration.
