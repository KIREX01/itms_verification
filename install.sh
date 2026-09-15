#!/usr/bin/env bash
#
# 1-Click Shell Installer for ITMS Verification Copilot (macOS / Linux)
# Usage: curl -fsSL https://raw.githubusercontent.com/KIREX01/itms_verification/main/install.sh | bash
#

set -e

INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/itms-verification}"
REPO_URL="https://github.com/KIREX01/itms_verification.git"
ZIP_URL="https://github.com/KIREX01/itms_verification/archive/refs/heads/main.zip"

echo "===================================================================="
echo "   ITMS VERIFICATION COPILOT - 1-CLICK INSTALLER (macOS / Linux)"
echo "===================================================================="
echo ""
echo "[>] Target Installation Directory: $INSTALL_DIR"

mkdir -p "$INSTALL_DIR"

# 1. Fetch Codebase
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "[*] Existing git repository detected. Updating..."
    cd "$INSTALL_DIR"
    git pull --rebase origin main || true
elif command -v git >/dev/null 2>&1; then
    echo "[>] Cloning repository via git..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
else
    echo "[>] Git not found. Downloading release archive..."
    TEMP_ZIP="/tmp/itms_verification_$$.zip"
    TEMP_DIR="/tmp/itms_extract_$$"
    curl -fsSL "$ZIP_URL" -o "$TEMP_ZIP"
    unzip -q "$TEMP_ZIP" -d "$TEMP_DIR"
    EXTRACTED_DIR=$(find "$TEMP_DIR" -mindepth 1 -maxdepth 1 -type d | head -n 1)
    cp -R "$EXTRACTED_DIR"/* "$INSTALL_DIR"/
    rm -rf "$TEMP_ZIP" "$TEMP_DIR"
fi

cd "$INSTALL_DIR"
chmod +x run.sh update.sh run.command update.command itms scripts/bootstrap.py 2>/dev/null || true

# 2. Python Environment & Virtualenv
echo "[>] Verifying Python 3 environment..."
PYTHON_BIN=""
for cmd in python3 python; do
    if command -v "$cmd" >/dev/null 2>&1; then
        if "$cmd" -c 'import sys; exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
            PYTHON_BIN="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "[x] Python 3.10+ is required. Please install Python 3.11 or 3.12."
    exit 1
fi

if [ ! -f ".venv/bin/python" ]; then
    echo "[>] Creating isolated virtual environment (.venv)..."
    "$PYTHON_BIN" -m venv .venv
fi

# 3. Install Dependencies
echo ""
echo "===================================================================="
echo "   INSTALLING PROJECT PYTHON DEPENDENCIES (requirements.txt)"
echo "===================================================================="
.venv/bin/pip install --upgrade pip --quiet
.venv/bin/pip install -r requirements.txt

# 4. Bootstrap Resources
echo ""
echo "[>] Running system bootstrap (AI plate models & migrations)..."
.venv/bin/python scripts/bootstrap.py

# 5. Global CLI Command Configuration
echo ""
echo "[>] Configuring global system-wide 'itms' command..."
LOCAL_BIN="$HOME/.local/bin"
mkdir -p "$LOCAL_BIN"
ln -sf "$INSTALL_DIR/itms" "$LOCAL_BIN/itms"
chmod +x "$INSTALL_DIR/itms" "$LOCAL_BIN/itms"

if [ -w "/usr/local/bin" ]; then
    ln -sf "$INSTALL_DIR/itms" "/usr/local/bin/itms" 2>/dev/null || true
    echo "    [+] Registered /usr/local/bin/itms"
else
    echo "    [+] Registered $LOCAL_BIN/itms"
fi

export PATH="$LOCAL_BIN:$PATH"
export ITMS_HOME="$INSTALL_DIR"

echo ""
echo "===================================================================="
echo "   [✓] ITMS VERIFICATION COPILOT READY FOR USE!"
echo "===================================================================="
echo "Location: $INSTALL_DIR"
echo "Commands: 'itms'       -> Launches Web Operator Console (Default)"
echo "          'itms --tui' -> Launches Terminal User Interface (TUI)"
echo "          'itms status'-> Check system health & database"
echo "Database: SQLite (db.sqlite3)"
echo "Login:    admin / admin"
echo "===================================================================="
echo ""

# 6. Launch if running interactively
if [ -t 0 ] || [ -n "$DISPLAY" ] || [ "$(uname)" = "Darwin" ]; then
    echo "[>] Starting ITMS Verification Copilot..."
    ./run.sh
fi
