#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1

echo "===================================================================="
echo "  ITMS VERIFICATION COPILOT - 1-CLICK WEB CONSOLE LAUNCHER (Linux)"
echo "===================================================================="
echo ""

if [ ! -f ".venv/bin/activate" ]; then
    echo "[>] Creating isolated virtual environment in .venv..."
    python3 -m venv .venv
fi

source .venv/bin/activate

# Fast dependency check
if [ ! -f ".venv/.reqs_hash" ]; then
    pip install -r requirements.txt
    touch .venv/.reqs_hash
fi

python3 scripts/bootstrap.py
python3 manage.py run_web
