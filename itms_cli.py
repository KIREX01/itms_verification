#!/usr/bin/env python3
"""
ITMS Verification Copilot - Universal Command Line Interface.

Usage:
  itms                Launch Web Operator Console in default browser
  itms --tui, -t      Launch Textual Terminal User Interface (TUI)
  itms --web, -w      Launch Web Operator Console (explicit)
  itms --port <port>  Launch Web Operator Console on custom port (e.g. 8080)
  itms status         Run system diagnostics & database health check
  itms bootstrap      Verify AI plate models, dependencies & run migrations
  itms update         Check for and pull latest software updates
  itms manage <args>  Run Django management commands
  itms --version, -v  Display installed software version
  itms --help, -h     Show this command guide
"""
import os
import sys
from pathlib import Path

# Always execute with working directory set to the project root
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "itms_project.settings")


def show_help():
    print("""
======================================================================
  ITMS VERIFICATION COPILOT - SYSTEM COMMAND INTERFACE
======================================================================

Usage:
  itms                Launch Web Operator Console in browser (default)
  itms --tui, -t      Launch Textual Terminal UI (keyboard-driven TUI)
  itms --web, -w      Launch Web Operator Console explicitly
  itms --port <port>  Launch Web Console on custom port (e.g. 8080)
  itms status         Display system health, active database & diagnostics
  itms bootstrap      Verify AI plate models, dependencies & run migrations
  itms update         Check for and pull latest software updates
  itms uninstall      Completely uninstall app, shortcuts, and global commands
  itms manage <cmd>   Execute any Django management command
  itms --version, -v  Display installed version
  itms --help, -h     Show this command guide

Examples:
  itms                # Opens Web Console at http://127.0.0.1:8000
  itms --tui          # Launches keyboard-driven terminal dashboard
  itms --port 8080    # Starts Web Console on port 8080
  itms status         # Verifies SQLite database, plate models & OCR
  itms update         # Pulls latest updates from repository
""")


def launch_web(extra_args=None):
    """Launches the Django Web Operator Console with automatic browser pop-up."""
    try:
        import django
        django.setup()
        from django.core.management import execute_from_command_line

        cmd = ["manage.py", "run_web"]
        if extra_args:
            cmd.extend(extra_args)
        execute_from_command_line(cmd)
    except KeyboardInterrupt:
        print("\n[+] Web server stopped cleanly.")
        sys.exit(0)


def launch_tui(extra_args=None):
    """Launches the Textual Terminal User Interface."""
    try:
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        import django
        django.setup()
        from django.core.management import execute_from_command_line

        cmd = ["manage.py", "run_tui"]
        if extra_args:
            cmd.extend(extra_args)
        execute_from_command_line(cmd)
    except KeyboardInterrupt:
        print("\n[+] TUI closed.")
        sys.exit(0)


def run_status():
    """Runs system diagnostics table."""
    from scripts import bootstrap
    sys.argv = ["bootstrap.py", "--verify-only"]
    bootstrap.main()


def run_bootstrap(extra_args=None):
    """Runs full system bootstrap (models, directories, migrations)."""
    from scripts import bootstrap
    sys.argv = ["bootstrap.py"] + (extra_args or [])
    bootstrap.main()


def run_update():
    """Triggers system update directly via Django check_updates command or git pull."""
    print("====================================================================")
    print("  ITMS VERIFICATION COPILOT - SYSTEM UPDATE MANAGER")
    print("====================================================================")
    print()
    try:
        import django
        django.setup()
        from django.core.management import call_command
        call_command("check_updates", apply=True)
    except Exception as exc:
        print(f"[*] Checking updates via git pull: {exc}")
        import subprocess
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=PROJECT_ROOT, check=False)


def run_uninstall(extra_args=None):
    """Triggers complete application uninstallation."""
    import subprocess
    if sys.platform == "win32":
        uninstall_ps1 = PROJECT_ROOT / "uninstall.ps1"
        if not uninstall_ps1.is_file():
            print("[!] uninstall.ps1 not found.")
            return
        cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(uninstall_ps1)]
        if extra_args:
            cmd.extend(extra_args)
        subprocess.run(cmd)
    else:
        uninstall_sh = PROJECT_ROOT / "uninstall.sh"
        if not uninstall_sh.is_file():
            print("[!] uninstall.sh not found.")
            return
        cmd = ["bash", str(uninstall_sh)]
        if extra_args:
            cmd.extend(extra_args)
        subprocess.run(cmd)


def run_manage(args):
    """Passes commands directly through to manage.py."""
    import django
    django.setup()
    from django.core.management import execute_from_command_line
    execute_from_command_line(["manage.py"] + args)


def main():
    args = sys.argv[1:]

    # Default action: launch Web Console
    if not args:
        launch_web()
        return

    first = args[0].lower().strip()

    if first in ("--help", "-h", "help", "/?"):
        show_help()
        return

    if first in ("--version", "-v", "version"):
        print("ITMS Verification Copilot v1.0.0")
        return

    if first in ("--tui", "-t", "tui"):
        launch_tui(args[1:])
        return

    if first in ("--web", "-w", "web"):
        launch_web(args[1:])
        return

    if first in ("status", "diagnostics", "health", "check"):
        run_status()
        return

    if first in ("bootstrap", "setup", "init"):
        run_bootstrap(args[1:])
        return

    if first in ("update", "upgrade"):
        run_update()
        return

    if first in ("uninstall", "remove"):
        run_uninstall(args[1:])
        return

    if first in ("manage", "django"):
        run_manage(args[1:])
        return

    # If argument starts with --port or -p, pass directly to web launcher
    if first in ("--port", "-p") or first.startswith("-"):
        launch_web(args)
        return

    # Fallback: forward to Django manage.py
    run_manage(args)


if __name__ == "__main__":
    main()
