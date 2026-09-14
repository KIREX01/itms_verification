# ITMS Verification Copilot - Distribution, Installation & Update Guide

Welcome to the **ITMS Verification Copilot** distribution guide. This document provides step-by-step instructions for operators, technicians, and developers to install, execute, maintain, and upgrade the application on **Windows**, **macOS** (both Apple Silicon M-series and Intel), and **Linux** workstations with zero manual configuration.

---

## 1. Architectural Highlights

* **Zero-Configuration 1-Click Startup**: Launch the complete application stack with a single double-click. Environment setup, dependencies, AI weights, directory structures, and database migrations are self-healing and fully automated.
* **Default Web Console Experience**: The application automatically starts the local HTTP server and opens your system's default web browser to the modern Uganda-themed Operator Console (`http://127.0.0.1:8000/`).
* **Automated Resource Provisioning (`scripts/bootstrap.py`)**: Checks and provisions all required runtime dependencies, fine-tuned license plate detection weights (`license-plate-finetune-v1n.pt`, `license-plate-finetune-v1s.pt`), OCR engines, `.env` files with secure keys, and default operator credentials.
* **In-Place Non-Destructive Updates**: Seamlessly pulls semantic updates from GitHub Releases (`https://github.com/KIREX01/itms_verification/releases`) with 100% data protection guarantees for SQLite databases, encrypted credentials, and evidence photo vaults.

---

## 2. System Prerequisites

| Component | Minimum Specification | Recommended Specification |
| :--- | :--- | :--- |
| **Operating System** | Windows 10/11 (64-bit)<br>macOS 12+ (Intel or Apple Silicon M1-M4)<br>Ubuntu 20.04+ / Debian 11+ | Windows 11 (64-bit)<br>macOS 14+ (Apple Silicon M-series)<br>Ubuntu 22.04 LTS |
| **Python** | Python 3.10, 3.11, or 3.12 | Python 3.11 or 3.12 |
| **RAM** | 4 GB | 8 GB or more (for rapid YOLOv8 & OCR batch inference) |
| **Disk Space** | 2.5 GB free space | 10 GB+ free space (accommodates evidence vault growth) |
| **Network** | Offline supported after initial bootstrap | Broadband connection for initial setup and updates |

---

## 3. 60-Second Quick Start

### Windows Installation
1. Extract or clone the `itms_verification` folder to your computer (e.g. `C:\ITMS\itms_verification` or `D:\itms_verification`).
2. **Double-click `run.bat`** (or open PowerShell/Terminal and run `.\run.bat`).
3. The launcher will automatically:
   * Detect your Python installation.
   * Create the local virtual environment (`.venv\`).
   * Install/verify all requirements via pip.
   * Provision missing AI weights, directories, and database tables.
   * Launch the Web Operator Console and open your default browser at `http://127.0.0.1:8000/`.

> **Tip**: You can create a Desktop shortcut to `run.bat` for instant 1-click access.

---

### macOS Installation (Apple Silicon & Intel)
1. Extract or clone the project folder on your Mac.
2. In Finder, navigate to the folder and **double-click `run.command`**.
   * *If macOS flags the script on first run*: Right-click (Control-click) `run.command` -> select **Open** -> click **Open**.
   * *Terminal Alternative*:
     ```bash
     chmod +x run.command run.sh update.command update.sh
     ./run.command
     ```
3. The script automatically sets up the environment, prepares AI models, starts the server, and launches Safari/Chrome at `http://127.0.0.1:8000/`.

---

### Linux Installation (Ubuntu / Debian / RHEL)
1. Open a terminal in the project directory.
2. Ensure execution permissions and run:
   ```bash
   chmod +x run.sh update.sh
   ./run.sh
   ```
3. The launcher provisions the environment and opens the Web Console in your default browser.

---

## 4. What Happens During Bootstrap?

When you run the 1-click launcher, `scripts/bootstrap.py` executes an automated health check:

```
======================================================================
  ITMS Verification Copilot - System Environment Diagnostic & Bootstrap
======================================================================
[OK] Environment: Windows-10-10.0.26100-SP0 (AMD64) | Python 3.14.0
[OK] Directory verified: media\vault
[OK] Directory verified: media\crops
[OK] Directory verified: secure\auth
[OK] Directory verified: exports
[OK] Directory verified: models
[OK] Secure configuration file .env verified.
[OK] Fine-tuned plate models verified: models\license-plate-finetune-v1n.pt, models\license-plate-finetune-v1s.pt
[OK] OCR Engine verified: Tesseract OCR (C:\Program Files\Tesseract-OCR\tesseract.exe)
[OK] Database schema up to date.
[OK] Default operator credentials verified.
======================================================================
[SUCCESS] System bootstrap complete. ITMS Verification Copilot is ready!
======================================================================
```

To run a verification check at any time without starting the server:
```bash
python scripts/bootstrap.py --verify-only
```

---

## 5. Version Control & Automated Updates (GitHub Releases)

The ITMS Verification Copilot includes a built-in semantic update engine linked to the official GitHub repository (`https://github.com/KIREX01/itms_verification`).

### Method 1: In-App Web Updates (Recommended)
1. Open the Web Console (`http://127.0.0.1:8000/`).
2. Click on the **Settings & System (Tab 6)** tab in the top navigation bar.
3. Locate the **Version Control & System Updates** card.
4. Click **Check for Updates**:
   * The system contacts GitHub Releases (or uses local 12-hour cache).
   * Displays the latest version number, release name, and detailed changelog notes.
5. If an update is available, click **Update System Now**.
6. The system downloads the release archive, updates application files, applies database migrations, and prompts you to restart.

---

### Method 2: 1-Click Update Scripts
When the application is closed, you can update with a single click:
* **Windows**: Double-click `update.bat`
* **macOS**: Double-click `update.command`
* **Linux**: Run `./update.sh`

These scripts run:
```bash
python manage.py check_updates --force --apply
```

---

### Method 3: Command Line Updates
Inspect and apply updates via Django management commands:
```bash
# Check status without applying:
python manage.py check_updates

# Bypass 12-hour cache and check live:
python manage.py check_updates --force

# Check and apply immediately:
python manage.py check_updates --apply
```

---

## 6. Operator Data Protection Guarantees

During any update (via Git pull or ZIP release unpacking), the update engine strictly protects and preserves all operator data:

| Protected Path | Contents & Guarantee |
| :--- | :--- |
| `db.sqlite3` | SQLite production database. Contains all batches, evidence links, vehicle pairs, audit logs, and operator accounts. **Never overwritten or deleted.** |
| `.env` | Environment configuration, cryptographic secret keys, and passwords. **Preserved across all updates.** |
| `config.json` | Operator customization settings and confidence thresholds. **Preserved.** |
| `secure/` | Encrypted session tokens, ITMS login cookies, and offline caches. **Preserved.** |
| `media/vault/` | Vault containing ingested front and rear vehicle photos organized by date. **Untouched.** |
| `media/crops/` | Cached AI plate crops and vehicle detections. **Untouched.** |
| `exports/` | Exported verification spreadsheets and audit files. **Untouched.** |
| `.venv/` | Python virtual environment. Preserved and incrementally updated. |

---

## 7. Directory Structure Guide

```text
itms_verification/
│
├── run.bat                   # 1-Click Launcher for Windows (Web UI default)
├── run.command               # 1-Click Launcher for macOS Finder
├── run.sh                    # Terminal Launcher for Linux / macOS
│
├── update.bat                # 1-Click Updater for Windows
├── update.command            # 1-Click Updater for macOS Finder
├── update.sh                 # Shell Updater for Linux / macOS
│
├── manage.py                 # Django command-line utility
├── requirements.txt          # Python dependencies
│
├── core/                     # Application source code
│   ├── models.py             # Database models (Evidence, Orders, Batches, Audits)
│   ├── views.py              # REST API & Web view controllers
│   ├── urls.py               # Application routing endpoints
│   ├── version.py            # Version tracking & GitHub metadata
│   ├── services/             # Core business logic services
│   │   ├── config_service.py # System configuration manager
│   │   ├── vault_service.py  # Image vault storage & file hasher
│   │   └── update_service.py # GitHub Releases update engine
│   ├── vision/               # Computer vision & OCR pipelines
│   ├── static/core/          # Stylesheets (CSS), icons, and JavaScript
│   └── templates/core/       # HTML5 Jinja/Django templates
│
├── scripts/
│   └── bootstrap.py          # Automated resource provisioning & diagnostic tool
│
├── models/                   # Fine-tuned YOLO plate detector weights
│   ├── license-plate-finetune-v1n.pt # Nano plate detector weights (fastest)
│   └── license-plate-finetune-v1s.pt # Small plate detector weights (high accuracy)
│
├── media/                    # Operator evidence vault
│   ├── vault/                # Timestamped immutable original images
│   └── crops/                # Cropped vehicle and plate detections
│
├── secure/                   # Secure storage
│   ├── auth/                 # Encrypted session credentials
│   └── .update_cache.json    # 12-hour GitHub release cache
│
└── exports/                  # Audit reports and CSV export outputs
```

---

## 8. Zero-Configuration Provisioning: When Python, Tesseract, or winget Fail

In real-world field environments (e.g. fresh Windows workstations, restricted enterprise machines without Windows Package Manager / Microsoft Store, or machines without administrator privileges), the system executes an automated, self-healing provisioning ladder:

### What Happens if Python is Not Installed and `winget` Fails?
1. **Local & Standard Path Detection**: `run.bat` checks for existing virtual environments (`.venv`), local portable Python (`tools\python\python.exe`), and standard un-indexed locations (`%LocalAppData%\Programs\Python\Python311`, `C:\Python311`, etc.) in case Python was previously installed without adding to PATH.
2. **Winget Attempt**: If not found, it attempts installation via `winget install Python.Python.3.11`.
3. **Direct Fallback (When winget Fails)**: If `winget` is missing, blocked, or errors out, `run.bat` automatically:
   * Downloads the official 64-bit Python installer (`python-3.11.9-amd64.exe`) directly from `python.org` using built-in Windows PowerShell.
   * Runs a silent user-space installation (`/quiet InstallAllUsers=0 PrependPath=1 Include_pip=1`). Because it installs to user space, **no Administrator/UAC elevation is required**.
   * Injects the new Python path into the current session and immediately continues initializing `.venv` and dependencies.

### What Happens if Tesseract-OCR is Not Installed and `winget` Fails?
1. **Local & System Detection**: `scripts/bootstrap.py` checks `tools/tesseract/tesseract.exe`, `C:\Program Files\Tesseract-OCR\tesseract.exe`, and PATH.
2. **Winget Attempt**: If missing, it attempts `winget install UB-Mannheim.TesseractOCR`.
3. **Direct Local Provisioning (When winget Fails)**: If `winget` fails or is absent, `bootstrap.py`:
   * Downloads the official UB-Mannheim Tesseract 5.4 installer (`https://digi.bib.uni-mannheim.de/tesseract/...`) directly.
   * Executes a silent local unpack directly into the project's `tools\tesseract\` folder:
     ```cmd
     tesseract-setup.exe /S /D=<PROJECT_DIR>\tools\tesseract
     ```
   * Because it installs into the project folder rather than `C:\Program Files\`, **no Administrator privileges are required**.
   * `core/vision/ocr_engine.py` automatically detects `tools/tesseract/tesseract.exe` and configures `TESSDATA_PREFIX` for immediate character recognition.

---

## 9. Offline & Air-Gapped Workstations

For inspection stations or remote checkpoints without any internet connectivity:

1. **One-Time USB Preparation**: Run `run.bat` once on any internet-connected computer. This downloads:
   * Python virtual environment (`.venv/`)
   * Fine-tuned plate detection models (`models/license-plate-finetune-v1n.pt` and `v1s.pt`)
   * Tesseract OCR (`tools/tesseract/`)
2. **Transfer to Offline Machine**: Copy the complete `itms_verification` directory to a USB thumb drive and paste it onto the offline PC.
3. **Offline Execution**:
   * Double-clicking `run.bat` will find `.venv`, `tools/tesseract`, and `models/` locally and launch the Web Console instantly with 0 network calls.
   * Computer vision inference, plate cropping, OCR extraction, and local database operations run 100% offline.

---

## 10. Troubleshooting & FAQ

### Q1: The browser does not open automatically.
If your browser does not launch automatically upon running `run.bat` or `run.command`, simply open Chrome, Edge, Safari, or Firefox and navigate to:
```
http://127.0.0.1:8000/
```

### Q2: What are the default operator credentials?
The automatic bootstrap seeds a default administrator account:
* **Username**: `admin`
* **Password**: `admin123`
*(You can change this password or create new operators in the Django Admin at `http://127.0.0.1:8000/admin/` or via `python manage.py createsuperuser`).*

### Q3: How do I manually configure Tesseract OCR?
If you prefer manual setup rather than automated provisioning:
* **Windows**: Download and install from UB-Mannheim: `https://github.com/UB-Mannheim/tesseract/wiki` or copy files to `tools\tesseract\`.
* **macOS**: `brew install tesseract`
* **Linux**: `sudo apt install tesseract-ocr`

### Q4: Model weights manual download links.
If your network blocks automated Hugging Face downloads, manually download these files to `models/`:
1. `models/license-plate-finetune-v1n.pt`: `https://huggingface.co/morsetechlab/yolov11-license-plate-detection/resolve/main/license-plate-finetune-v1n.pt`
2. `models/license-plate-finetune-v1s.pt`: `https://huggingface.co/morsetechlab/yolov11-license-plate-detection/resolve/main/license-plate-finetune-v1s.pt`
