#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1

echo "===================================================================="
echo "  ITMS VERIFICATION COPILOT - OPERATOR WEB CONSOLE"
echo "===================================================================="

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

python3 manage.py run_web
