# Release Notes: CLOSE Copilot v1.1.1

**Release Date:** November 19, 2026
**Milestone:** v1.1.1
**Baseline:** Production server state (`dff751b`) + fine-tuned Ugandan character reader
**Target Environments:** Ubuntu 22.04 LTS (AWS EC2 Production) & Windows 10/11 (Dispatch Workstations)

---

## Executive Summary

v1.1.1 formalises what is already running in production as a versioned release and adds one
non-breaking improvement: a **fine-tuned Ugandan character reader** shipped with the app.

The release deliberately **excludes** the unfinished spoof-proof IP/MAC/device access-control
hardening (`c8c73ae`) and the model-training tooling (`training/`). That work stays on `main` and will ship in a later release once complete.
Device/IP access control behaves exactly as it does on the server today.

---

## What's New

### 1. Fine-tuned Ugandan Character Reader (ALPR)
- New model `models/plate_char_reader_ug.onnx` (YOLO, 36 classes `0-9A-Z`, 320x320 input) trained on
  Ugandan plates, replacing the generic Indonesian fallback model downloaded from Hugging Face.
- Shipped **inside the repository/packages**: `git pull` and the zip/tar.gz installers carry it,
  no network access or tokens required.
- **Non-breaking resolution order** (`core/vision/character_recognizer.py`):
  1. If `vision.character_model_weights` is the default (`models/plate_char_reader.onnx|.pt`),
     the shipped Ugandan model is used when present.
  2. Custom weight paths configured by an operator are never overridden.
  3. If the shipped file is missing, the previous lookup + Hugging Face download + PaddleOCR /
     Tesseract fallback chain is unchanged.

### 2. Production changes since v1.1.0 (already live on the server, now versioned)
- **Queue:** 3-tier date → batch → status filtering, shift-date scoped batch submission, conflict
  enforcement on unlinked pairs, 1:1 order invariant, sibling-plate collision prevention.
- **Stock & Bond:** persisted daily ledger overrides, shortfall carry-forward, dispatch double-count
  fix, auto-registered dispatched kits, Warehouse model + sync, stock report email.
- **ITMS sync:** centralised live sync/refresh, vehicle/archive/kit inspection details, kit crawl bounds.
- **Mobile / Scanner:** IndexedDB offline storage, date-scoped daily batches, safe U-turn, in-place photo replacement.
- **Reports & History:** order-stage metrics, scanned-vs-skipped tracking, scoped exports, audit filters with thumbnails.
- **Access control (as deployed):** IP/MAC/device firewall, QR pairing trust, time-bound email grants, TUI expiry lockout.
- **Security & quality:** DOM XSS remediation, SonarQube reliability fixes, LF line-ending enforcement for shell scripts.
- **Branding:** app renamed to **CLOSE Copilot** across UI, email and release titles.

---

## Database Migrations
No new migrations beyond those already applied on the server (`0026`–`0030`).

---

## Upgrade Runbook

### Production EC2 Server
```bash
sudo -u www-data git -C /opt/itms pull origin release/linux
sudo systemctl restart itms
sudo systemctl is-active itms
itms status   # should report v1.1.1
```
Confirm the model arrived: `ls -la /opt/itms/models/plate_char_reader_ug.onnx` (≈10 MB).

### Windows Workstations
```powershell
git pull origin release/windows
itms restart
```

### Rolling Back to v1.1.0
- **Via TUI:** Settings → Developer Mode → Card 2G → Rollback → select `v1.1.0`.
- **Via CLI:** `sudo itms rollback v1.1.0`

To keep v1.1.1 but revert only the character model, set `vision.character_model_weights` to a
custom path, or disable `vision.use_direct_character_reader` (falls back to PaddleOCR/Tesseract).
