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
status=$?

echo ""
if [ $status -eq 0 ]; then
    echo "===================================================================="
    echo "  [+] Update completed successfully!"
    echo "  Run ./run.command to start the updated Web Console."
    echo "===================================================================="
else
    echo "[!] Update process encountered an issue. See logs above."
fi

echo ""
read -r -p "Press Enter to exit..."
