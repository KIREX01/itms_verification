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
chmod +x run.sh update.sh run.command update.command scripts/bootstrap.py 2>/dev/null || true

echo ""
echo "===================================================================="
echo "   [✓] ITMS VERIFICATION COPILOT INSTALLED SUCCESSFULLY!"
echo "===================================================================="
echo "Location: $INSTALL_DIR"
echo "To start: cd \"$INSTALL_DIR\" && ./run.sh"
echo ""

# Launch if running in an interactive session
if [ -t 0 ] || [ -n "$DISPLAY" ] || [ "$(uname)" = "Darwin" ]; then
    echo "[>] Starting ITMS Verification Copilot..."
    ./run.sh
fi
