#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1

echo "===================================================================="
echo "  ITMS VERIFICATION COPILOT - SYSTEM UPDATE WIZARD (GITHUB RELEASES)"
echo "===================================================================="
echo ""

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

python3 manage.py check_updates --apply
