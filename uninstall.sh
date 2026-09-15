#!/usr/bin/env bash
#
# Complete Uninstaller for ITMS Verification Copilot (macOS / Linux)
# Usage: curl -fsSL https://raw.githubusercontent.com/KIREX01/itms_verification/main/uninstall.sh | bash
#

set -e

INSTALL_DIR="${INSTALL_DIR:-${ITMS_HOME:-$HOME/.local/share/itms-verification}}"
FORCE=0

for arg in "$@"; do
    case $arg in
        -f|--force|-y|--yes)
            FORCE=1
            ;;
    esac
done

echo "===================================================================="
echo "   ITMS VERIFICATION COPILOT - COMPLETE UNINSTALLER (macOS / Linux)"
echo "===================================================================="
echo ""

if [ ! -d "$INSTALL_DIR" ]; then
    echo "[!] No installation found at $INSTALL_DIR."
    exit 0
fi

echo "Target Installation: $INSTALL_DIR"
echo ""

if [ "$FORCE" -ne 1 ]; then
    echo "This will completely remove:"
    echo "  - All application files, AI models, and virtual environment"
    echo "  - SQLite database and photographic evidence vault"
    echo "  - Global 'itms' command from ~/.local/bin and /usr/local/bin"
    echo ""
    read -r -p "Are you sure you want to completely uninstall ITMS? (type 'yes' to proceed): " answer
    if [ "$answer" != "yes" ]; then
        echo "Uninstall canceled by user."
        exit 0
    fi
    echo ""
fi

# 1. Remove Global Commands
echo "[>] Removing global 'itms' command..."
rm -f "$HOME/.local/bin/itms"
if [ -w "/usr/local/bin/itms" ] || [ "$(id -u)" -eq 0 ]; then
    rm -f "/usr/local/bin/itms" 2>/dev/null || true
fi
echo "    [+] Removed global CLI binaries."

# 2. Kill Running ITMS Processes
echo "[>] Stopping any running ITMS processes..."
pkill -f "run_web" 2>/dev/null || true
pkill -f "run_tui" 2>/dev/null || true

# 3. Delete Installation Directory
echo "[>] Deleting installation directory: $INSTALL_DIR..."
cd "$HOME"
rm -rf "$INSTALL_DIR"
echo "    [+] Installation directory completely removed."

echo ""
echo "===================================================================="
echo "   [✓] ITMS VERIFICATION COPILOT UNINSTALLED SUCCESSFULLY"
echo "===================================================================="
echo "The application and all associated data have been completely removed."
echo ""
