#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1

echo "===================================================================="
echo "  ITMS VERIFICATION COPILOT - 1-CLICK WEB CONSOLE LAUNCHER (macOS)"
echo "===================================================================="
echo ""

# 1. Detect Python Executable
PYTHON_EXE=""
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 python; do
    if command -v "$p" >/dev/null 2>&1; then
        PYTHON_EXE="$p"
        break
    fi
done

if [ -z "$PYTHON_EXE" ]; then
    echo "[x] Python 3.10+ was not found on your system."
    echo "To install Python on macOS, run:"
    echo "    brew install python@3.11"
    echo "Or download from: https://www.python.org/downloads/"
    echo ""
    read -r -p "Press Enter to exit..."
    exit 1
fi

# 2. Virtual Environment
if [ ! -f ".venv/bin/activate" ]; then
    echo "[>] Creating isolated virtual environment in .venv..."
    "$PYTHON_EXE" -m venv .venv
    if [ $? -ne 0 ]; then
        echo "[x] Failed to create .venv. Check permissions."
        exit 1
    fi
    echo "[+] Virtual environment initialized."
fi

source .venv/bin/activate

# 3. Fast Dependency Check via Hash
REQS_FILE="requirements.txt"
HASH_FILE=".venv/.reqs_hash"
CURRENT_HASH=""

if [ -f "$REQS_FILE" ]; then
    if command -v shasum >/dev/null 2>&1; then
        CURRENT_HASH=$(shasum -a 256 "$REQS_FILE" | awk '{print $1}')
    elif command -v sha256sum >/dev/null 2>&1; then
        CURRENT_HASH=$(sha256sum "$REQS_FILE" | awk '{print $1}')
    fi
fi

PREV_HASH=""
if [ -f "$HASH_FILE" ]; then
    PREV_HASH=$(cat "$HASH_FILE")
fi

if [ "$CURRENT_HASH" != "$PREV_HASH" ]; then
    echo "[>] Installing or updating system dependencies..."
    pip install --upgrade pip >/dev/null 2>&1
    pip install -r requirements.txt
    if [ $? -eq 0 ]; then
        echo "$CURRENT_HASH" > "$HASH_FILE"
        echo "[+] Dependencies verified."
    else
        echo "[!] Warning: Some dependencies had issues. Continuing..."
    fi
fi

# 4. Automated Resource Provisioning & Health Check
echo "[>] Provisioning models, directories, and database..."
python3 scripts/bootstrap.py

# 5. Launch Web Operator Console & Auto-Open Browser
echo ""
echo "===================================================================="
echo "  [+] Starting Web Operator Console (http://127.0.0.1:8000)"
echo "  (Your default web browser will open automatically in a moment)"
echo "  Press Ctrl+C to stop the application."
echo "===================================================================="
echo ""
python3 manage.py run_web
