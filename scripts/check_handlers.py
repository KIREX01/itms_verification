#!/usr/bin/env python3
"""
ITMS Verification Copilot - Event Handlers & Mobile Connection Diagnostics Checker.

Verifies:
1. All template HTML event handlers (onclick, onchange) map to defined JS functions.
2. Django Web Server status and port 8000 listener on 0.0.0.0.
3. Network interface configuration, IP addresses, and mobile pairing URLs.
4. Windows Firewall & Network Profile (Public vs Private) accessibility for smartphones.
"""
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

def check_html_js_handlers():
    print("=" * 65)
    print("  1. CHECKING TEMPLATE & JAVASCRIPT EVENT HANDLERS")
    print("=" * 65)

    template_file = PROJECT_ROOT / "core" / "templates" / "core" / "mobile_companion.html"
    js_file = PROJECT_ROOT / "core" / "static" / "core" / "js" / "mobile_companion.js"

    if not template_file.is_file() or not js_file.is_file():
        print(f"[!] Missing target files: {template_file.name} or {js_file.name}")
        return False

    html = template_file.read_text(encoding="utf-8", errors="ignore")
    js = js_file.read_text(encoding="utf-8", errors="ignore")

    raw_calls = set(re.findall(r'on(?:click|change)=["\']([a-zA-Z0-9_]+)\(', html))
    calls = {c for c in raw_calls if c not in ("if", "event")}

    missing = []
    found = []

    for fn in sorted(calls):
        # Look for function definition in JS
        pattern = rf'(?:function\s+{fn}\s*\(|{fn}\s*=\s*(?:function|\()|\b{fn}\s*:\s*function)'
        if re.search(pattern, js):
            found.append(fn)
        else:
            missing.append(fn)

    print(f"Total HTML Handlers in Mobile Companion: {len(calls)}")
    for fn in found:
        print(f"  [OK] {fn:<30} -> IMPLEMENTED")

    if missing:
        print("\n[!] MISSING HANDLERS DETECTED:")
        for fn in missing:
            print(f"  [MISSING] {fn:<30} -> NOT DEFINED IN JS")
        return False

    print("\n[OK] All mobile companion HTML event handlers are fully implemented!")
    return True


def check_server_listener(port=8000):
    print("\n" + "=" * 65)
    print(f"  2. CHECKING SERVER LISTENER (PORT {port})")
    print("=" * 65)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        res = s.connect_ex(("127.0.0.1", port))
        if res == 0:
            print(f"[OK] Server is actively listening on port {port} (Localhost: OK)")
            return True
        else:
            print(f"[ERROR] Port {port} is NOT listening! Server is currently stopped.")
            print("    To start server: run 'python manage.py run_web' or 'itms start'")
            return False


def check_mobile_network():
    print("\n" + "=" * 65)
    print("  3. CHECKING MOBILE NETWORK & FIREWALL PROFILE")
    print("=" * 65)

    # Query network interfaces
    try:
        from core.services import network_service
        info = network_service.get_mobile_connection_info(port=8000)
        print(f"Primary Mobile URL : {info.get('primary_url')}")
        print(f"Primary IP         : {info.get('primary_ip')}")
        print(f"Connection Mode    : {info.get('connection_mode')} ({info.get('connection_badge')})")
        print("\nCandidate Mobile Access Endpoints:")
        for c in info.get("candidate_urls", []):
            star = " (★ PRIMARY)" if c.get("is_primary") else ""
            print(f"  - {c.get('url')}{star} [{c.get('badge')}]")
    except Exception as exc:
        print(f"[!] Error querying network_service: {exc}")

    # Check Windows Network Category (Public vs Private)
    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-NetConnectionProfile | Select-Object -Property Name, InterfaceAlias, NetworkCategory | Format-Table -AutoSize"],
                capture_output=True,
                text=True,
                timeout=5
            )
            out = res.stdout.strip()
            print("\nWindows Active Network Profiles:")
            print(out)
            if "Public" in out:
                print("[!] NOTE ON WINDOWS FIREWALL:")
                print("    Your active Wi-Fi profile is set to 'Public'. Windows Firewall blocks")
                print("    inbound connections on Public networks by default.")
                print("    Solutions to allow your phone to connect:")
                print("    1. Switch Wi-Fi to 'Private' in Windows Settings:")
                print("       Settings > Network & internet > Wi-Fi > Click network name > Select 'Private network'")
                print("    2. Or enable Windows Mobile Hotspot on laptop (192.168.137.1)")
                print("    3. Or connect laptop to phone's Personal Hotspot")
        except Exception:
            pass


def main():
    print("""
======================================================================
  ITMS VERIFICATION COPILOT - SYSTEM & EVENT HANDLER DIAGNOSTICS
======================================================================
""")
    h_ok = check_html_js_handlers()
    s_ok = check_server_listener(8000)
    check_mobile_network()

    print("\n" + "=" * 65)
    print("  DIAGNOSTIC SUMMARY")
    print("=" * 65)
    print(f"  Event Handlers Integrity : {'[OK] PASSED' if h_ok else '[FAIL] FAILED'}")
    print(f"  Web Server Listener      : {'[OK] LISTENING (PORT 8000)' if s_ok else '[STOPPED] NOT RUNNING'}")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
