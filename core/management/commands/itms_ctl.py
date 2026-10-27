"""
ITMS Verification Copilot Process & Lifecycle Management Command.

Unified command-line controller for application lifecycle:
- start: Launches web server in background and clears maintenance flag.
- stop: Completely shuts down application services and enables browser maintenance page.
- restart: Gracefully restarts the application server.
- status: Reports system health, active PIDs, port bindings, maintenance mode, and ITMS master switch.
- maintenance [on|off|status]: Controls the web application maintenance page.
- switch [on|off|status]: Controls the ITMS upstream master connection switch.
"""
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from django.core.management.base import BaseCommand, CommandError
from core.services import config_service


def is_port_in_use(port: int = 8000, host: str = "127.0.0.1") -> bool:
    """Checks whether the specified TCP port is listening."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def has_systemd_service(service_name: str = "itms") -> bool:
    """Checks if a systemd service exists and is loaded."""
    if platform.system() != "Linux":
        return False
    try:
        res = subprocess.run(
            ["systemctl", "status", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
        return res.returncode != 4  # 4 = no such unit
    except Exception:
        return False


def run_cmd(cmd_list: list, timeout: int = 15) -> subprocess.CompletedProcess:
    """Runs a shell command with sudo fallback if needed on Linux."""
    try:
        return subprocess.run(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    except Exception as exc:
        raise CommandError(f"Command execution failed: {cmd_list} -> {exc}")


class Command(BaseCommand):
    help = "Controls the ITMS Verification Copilot application lifecycle and maintenance modes."

    def print_help(self, prog_name, subcommand):
        self.handle_help()

    def add_arguments(self, parser):
        parser.add_argument(
            "action",
            nargs="?",
            default="status",
            choices=[
                "start",
                "stop",
                "restart",
                "status",
                "maintenance",
                "switch",
                "tui",
                "web",
                "rollback",
                "update",
                "bootstrap",
                "clear",
                "reset",
                "prune",
                "audit",
                "fetch",
                "reconcile",
                "uninstall",
                "manage",
                "version",
                "help",
            ],
            help="Command action to perform (default: status)",
        )
        parser.add_argument(
            "subargs",
            nargs="*",
            default=[],
            help="Subaction or target parameters (e.g. maintenance on, rollback v1.0.9, manage migrate)",
        )
        parser.add_argument(
            "--tui",
            "-t",
            action="store_true",
            help="Launch the interactive Textual TUI interface directly",
        )
        parser.add_argument(
            "--web",
            "-w",
            action="store_true",
            help="Launch the Web Operator Console directly",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8000,
            help="Application port (default: 8000)",
        )
        parser.add_argument(
            "--host",
            type=str,
            default="127.0.0.1",
            help="Application host binding (default: 127.0.0.1)",
        )

    def _restart_services_if_present(self):
        self.stdout.write("Restarting application services...")
        is_linux = platform.system() == "Linux"
        if is_linux and has_systemd_service("itms"):
            run_cmd(["sudo", "systemctl", "restart", "itms"])
            self.stdout.write(self.style.SUCCESS("[+] systemd unit (itms.service) restarted successfully."))
        elif is_linux and has_systemd_service("itms-web"):
            run_cmd(["sudo", "systemctl", "restart", "itms-web"])
            self.stdout.write(self.style.SUCCESS("[+] systemd unit (itms-web.service) restarted successfully."))

    def handle(self, *args, **options):
        if options.get("tui") or options.get("action") == "tui":
            from django.core.management import call_command
            call_command("run_tui")
            return

        if options.get("web") or options.get("action") == "web":
            from django.core.management import call_command
            host = options.get("host") or "127.0.0.1"
            port = options.get("port") or 8000
            subargs = options.get("subargs") or []
            call_command("run_web", "--host", host, "--port", str(port), *subargs)
            return

        action = (options.get("action") or "status").lower()
        subargs = options.get("subargs") or []
        subaction = subargs[0] if subargs else ""
        port = options["port"]
        host = options["host"]

        if action in ("help", "-h", "--help"):
            self.handle_help()
        elif action in ("version", "-v", "--version"):
            self.handle_version()
        elif action == "start":
            self.handle_start(host, port)
        elif action == "stop":
            self.handle_stop(port)
        elif action == "restart":
            self.handle_restart(host, port)
        elif action == "status":
            self.handle_status(host, port)
        elif action == "maintenance":
            self.handle_maintenance(subaction.lower())
        elif action == "switch":
            self.handle_switch(subaction.lower())
        elif action == "rollback":
            self.handle_rollback(subaction)
        elif action == "update":
            self.handle_update(subargs)
        elif action == "bootstrap":
            self.handle_bootstrap(subargs)
        elif action == "clear":
            self.handle_clear(subargs)
        elif action in ("reset", "factory-reset"):
            self.handle_reset(subargs)
        elif action == "prune":
            self.handle_prune(subargs)
        elif action == "audit":
            self.handle_audit(subargs)
        elif action == "fetch":
            self.handle_fetch(subargs)
        elif action == "reconcile":
            self.handle_reconcile(subargs)
        elif action == "uninstall":
            self.handle_uninstall(subargs)
        elif action == "manage":
            self.handle_manage(subargs)
        else:
            self.handle_help()

    def handle_help(self):
        from core.version import __version__
        guide = f"""
======================================================================
  ITMS VERIFICATION COPILOT v{__version__} — SYSTEM COMMAND INTERFACE
======================================================================

USAGE:
  itms [command] [options]

LAUNCH & USER INTERFACES:
  itms                     Launch Web Operator Console in browser (default)
  itms --web, -w [options] Launch Web Operator Console in foreground
  itms --tui, -t           Launch Textual Terminal UI (keyboard-driven TUI)
  itms --port <port>       Launch Web Console on custom port (e.g. 8080)
  itms --ssl, -s           Launch Web Console with HTTPS primary (port 443)

SERVICE & LIFECYCLE MANAGEMENT:
  itms start, --bg         Start Web Console / services in background (daemon)
  itms stop                Stop running background service safely
  itms restart             Restart background service gracefully
  itms status              Display system health, active database & diagnostics
  itms maintenance [mode]  Web maintenance page: on (HTTP 503) | off | status
  itms switch [mode]       ITMS upstream connection switch: on | off | status
  itms service [action]    Manage Windows Service / systemd (status|install|start|stop)

DATA CLEANING & REPOSITORY RESET:
  itms clear [target]      Clean temporary data (data | crops | vault | exports)
  itms reset               Reset active batches and unassigned queue records
  itms reset --factory     Factory reset: wipe orders, kits, ledgers (preserves superuser)
  itms prune [days]        Prune vault evidence photos older than N days (default: 30)
  itms audit               Verify SHA-256 cryptographic integrity of vaulted evidence

OPERATIONS & ITMS UPSTREAM SYNC:
  itms fetch orders        Pull & sync vehicle installation orders from ITMS portal
  itms fetch stock         Synchronize safe-room warehouse kits with ITMS stock ledger
  itms fetch bonds         Synchronize bonded facilities (AGM, NAGURU, CFAO, etc.)
  itms reconcile [date]    Run shift reconciliation and export discrepancy CSV reports

SYSTEM MAINTENANCE & RELEASES:
  itms bootstrap           Verify AI models, directory structure, and run migrations
  itms update              Check for and apply latest software update from GitHub
  itms rollback [tag]      Roll back code non-destructively to previous release (e.g. v1.0.9)
  itms uninstall           Completely uninstall app, background service, and global commands
  itms manage <command>    Run any Django management command (e.g. itms manage showmigrations)
  itms version, -v         Display installed software version
  itms help, -h            Show this command reference

EXAMPLES:
  itms                     # Opens Web Console at http://127.0.0.1:8000 (or https://127.0.0.1:443)
  itms --tui               # Launches keyboard-driven terminal dashboard
  itms status              # Verifies SQLite/PostgreSQL, plate models & OCR health
  itms clear data          # Purges temporary evidence and unallocated pairs
  itms fetch orders        # Queries ITMS portal for new installation work orders
  itms rollback v1.0.9     # Reverts code safely while preserving operator database
  itms uninstall           # Removes Windows service or systemd unit and shortcuts
======================================================================
"""
        self.stdout.write(guide.strip())

    def handle_reset(self, subargs: list):
        from itms_cli import run_reset
        run_reset(subargs)

    def handle_prune(self, subargs: list):
        from itms_cli import run_prune
        run_prune(subargs)

    def handle_audit(self, subargs: list):
        from itms_cli import run_audit
        run_audit(subargs)

    def handle_fetch(self, subargs: list):
        from itms_cli import run_fetch
        run_fetch(subargs)

    def handle_reconcile(self, subargs: list):
        from itms_cli import run_reconcile
        run_reconcile(subargs)

    def handle_uninstall(self, subargs: list):
        from itms_cli import run_uninstall
        run_uninstall(subargs)

    def handle_version(self):
        from core.version import __version__
        self.stdout.write(f"ITMS Verification Copilot v{__version__}")

    def handle_update(self, subargs: list):
        self.stdout.write(self.style.NOTICE("Checking for ITMS Verification Copilot updates..."))
        from core.services.update_service import UpdateService
        from core.version import __version__

        info = UpdateService.check_for_updates(force=True)
        if info.get("update_available"):
            target_ver = info.get("latest_version")
            self.stdout.write(self.style.WARNING(f"New update available: v{target_ver} (Current: v{__version__})"))
            self.stdout.write("Applying update non-destructively...")
            res = UpdateService.apply_update()
            if res.get("success"):
                self.stdout.write(self.style.SUCCESS(f"\n[+] {res.get('message', 'Updated successfully.')}"))
                self._restart_services_if_present()
            else:
                self.stdout.write(self.style.ERROR(f"\n[!] Update failed: {res.get('error')}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"[+] System is running the latest release (v{__version__})."))

    def handle_bootstrap(self, subargs: list):
        self.stdout.write(self.style.NOTICE("Running ITMS Verification Copilot bootstrap & self-healing..."))
        old_argv = sys.argv
        try:
            sys.argv = ["bootstrap.py"] + subargs
            from scripts import bootstrap
            bootstrap.main()
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Bootstrap execution failed: {exc}"))
        finally:
            sys.argv = old_argv

    def handle_clear(self, subargs: list):
        self.stdout.write(self.style.NOTICE("Cleaning temporary evidence and cache storage..."))
        from django.core.management import call_command
        cleaned = [a for a in subargs if a.lower() != "data"]
        try:
            call_command("clear_data", *cleaned)
            self.stdout.write(self.style.SUCCESS("[+] Storage cleanup completed."))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f"Cleanup error: {exc}"))

    def handle_manage(self, subargs: list):
        if not subargs:
            self.stdout.write(self.style.WARNING("Usage: itms manage <command> [args...]"))
            return
        from django.core.management import execute_from_command_line
        execute_from_command_line(["manage.py"] + subargs)

    def handle_rollback(self, target_version: Optional[str] = None):
        self.stdout.write(self.style.WARNING("Initiating ITMS Verification Copilot version rollback..."))
        from core.services.update_service import UpdateService

        target = target_version or "v1.0.9"
        self.stdout.write(f"Target rollback version: {target}")
        self.stdout.write("All operator databases, evidence photos, and credentials will be preserved.")

        res = UpdateService.rollback_version(target)
        if res.get("success"):
            self.stdout.write(self.style.SUCCESS(f"\n[+] {res.get('message')}"))
            self._restart_services_if_present()
        else:
            self.stdout.write(self.style.ERROR(f"\n[!] Rollback failed: {res.get('error')}"))

    def handle_start(self, host: str, port: int):
        self.stdout.write(self.style.NOTICE("Initiating ITMS Verification Copilot startup..."))

        # Clear maintenance flag if present
        config_service.set_maintenance_mode(False)

        is_linux = platform.system() == "Linux"
        use_systemd = is_linux and has_systemd_service("itms")

        if use_systemd:
            self.stdout.write("Starting via systemd unit (itms.service)...")
            res = run_cmd(["sudo", "systemctl", "start", "itms"])
            if res.returncode != 0:
                # Try without sudo if already root
                res = run_cmd(["systemctl", "start", "itms"])
            if res.returncode != 0:
                raise CommandError(f"Failed to start itms.service: {res.stderr.strip()}")
        else:
            # Standalone process launch (Windows or Linux without systemd)
            if is_port_in_use(port, host):
                self.stdout.write(self.style.WARNING(f"Service is already listening on {host}:{port}."))
                self.handle_status(host, port)
                return

            self.stdout.write(f"Launching background process on {host}:{port}...")
            python_bin = sys.executable
            manage_py = str(Path(__file__).resolve().parent.parent.parent.parent / "manage.py")
            cmd = [
                python_bin,
                manage_py,
                "run_web",
                "--no-ssl",
                "--host",
                host,
                "--port",
                str(port),
                "--noreload",
            ]

            log_path = Path("media") / "itms_web.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_file = open(log_path, "a", encoding="utf-8")

            if platform.system() == "Windows":
                # DETACHED_PROCESS on Windows
                DETACHED_PROCESS = 0x00000008
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=log_file,
                    creationflags=DETACHED_PROCESS,
                    close_fds=True,
                )
            else:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=log_file,
                    start_new_session=True,
                    close_fds=True,
                )

            pid_file = Path("secure") / "itms_web.pid"
            pid_file.parent.mkdir(parents=True, exist_ok=True)
            pid_file.write_text(str(proc.pid), encoding="utf-8")

        # Wait for service to bind
        self.stdout.write("Waiting for application socket...")
        bound = False
        for _ in range(20):
            time.sleep(0.5)
            if is_port_in_use(port, host):
                bound = True
                break

        if bound:
            self.stdout.write(self.style.SUCCESS(f"\n[OK] ITMS Verification Copilot is ACTIVE on {host}:{port}."))
            self.stdout.write(f"Web Entrypoint: https://close.kirex.online/ (or http://{host}:{port}/)")
        else:
            self.stdout.write(self.style.WARNING(f"\nService started, but port {port} has not responded yet."))

    def handle_stop(self, port: int):
        self.stdout.write(self.style.NOTICE("Initiating ITMS Verification Copilot shutdown..."))

        # 1. Immediately compile maintenance page and set maintenance flag
        # so Nginx serves the maintenance page to operators
        config_service.set_maintenance_mode(
            enabled=True,
            title="ITMS Verification Copilot — Service Offline",
            message="The Verification Copilot application service is currently stopped or undergoing scheduled maintenance. Please standby.",
        )
        self.stdout.write("Maintenance fallback page compiled and active.")

        is_linux = platform.system() == "Linux"
        use_systemd = is_linux and has_systemd_service("itms")

        if use_systemd:
            self.stdout.write("Stopping systemd unit (itms.service)...")
            res = run_cmd(["sudo", "systemctl", "stop", "itms"])
            if res.returncode != 0:
                res = run_cmd(["systemctl", "stop", "itms"])
            if res.returncode != 0:
                raise CommandError(f"Failed to stop itms.service: {res.stderr.strip()}")
        else:
            # Terminate standalone background process
            pid_file = Path("secure") / "itms_web.pid"
            stopped = False
            if pid_file.is_file():
                try:
                    pid = int(pid_file.read_text().strip())
                    self.stdout.write(f"Stopping process PID {pid}...")
                    if platform.system() == "Windows":
                        subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    else:
                        os.kill(pid, 15)  # SIGTERM
                    stopped = True
                except Exception as exc:
                    self.stdout.write(f"PID termination note: {exc}")
                finally:
                    try:
                        pid_file.unlink()
                    except OSError:
                        pass

            # Fallback: scan and kill any manage.py run_web processes
            if not stopped:
                self.stdout.write("Locating and stopping active run_web processes...")
                if platform.system() == "Windows":
                    subprocess.run(
                        ["wmic", "process", "where", "name='python.exe' and CommandLine like '%run_web%'", "call", "terminate"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                else:
                    subprocess.run(["pkill", "-f", "run_web"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        # Confirm socket release
        time.sleep(1.0)
        if not is_port_in_use(port):
            self.stdout.write(self.style.SUCCESS("\n[OK] ITMS Verification Copilot service STOPPED successfully."))
            self.stdout.write(self.style.NOTICE("Browser Behavior: Displays the customized Maintenance & Offline notice."))
            self.stdout.write("To resume service: itms start (or python manage.py itms_ctl start)")
        else:
            self.stdout.write(self.style.WARNING(f"\nWarning: Port {port} appears to still be listening."))

    def handle_restart(self, host: str, port: int):
        self.stdout.write(self.style.NOTICE("Restarting ITMS Verification Copilot..."))
        is_linux = platform.system() == "Linux"
        use_systemd = is_linux and has_systemd_service("itms")

        if use_systemd:
            config_service.set_maintenance_mode(False)
            res = run_cmd(["sudo", "systemctl", "restart", "itms"])
            if res.returncode != 0:
                res = run_cmd(["systemctl", "restart", "itms"])
            if res.returncode != 0:
                raise CommandError(f"Failed to restart itms.service: {res.stderr.strip()}")
            time.sleep(1.0)
            self.handle_status(host, port)
        else:
            self.handle_stop(port)
            time.sleep(1.5)
            self.handle_start(host, port)

    def handle_status(self, host: str, port: int):
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write("  ITMS Verification Copilot — System Status")
        self.stdout.write("=" * 60)

        # 1. Port status
        port_open = is_port_in_use(port, host)
        port_str = self.style.SUCCESS(f"LISTENING (127.0.0.1:{port})") if port_open else self.style.ERROR(f"DOWN (Port {port} closed)")
        self.stdout.write(f"App Web Port:           {port_str}")

        # 2. Service status
        is_linux = platform.system() == "Linux"
        if is_linux and has_systemd_service("itms"):
            res = run_cmd(["systemctl", "is-active", "itms"])
            state = res.stdout.strip()
            state_str = self.style.SUCCESS("ACTIVE (Running)") if state == "active" else self.style.WARNING(f"INACTIVE ({state})")
            self.stdout.write(f"Systemd Service:        {state_str}")
        else:
            pid_file = Path("secure") / "itms_web.pid"
            if pid_file.is_file():
                self.stdout.write(f"Local Process PID:      {pid_file.read_text().strip()}")
            else:
                self.stdout.write(f"Local Process:          {'Running' if port_open else 'Stopped'}")

        # 3. Nginx status
        if is_linux:
            res = run_cmd(["systemctl", "is-active", "nginx"])
            nginx_state = res.stdout.strip()
            n_str = self.style.SUCCESS("ACTIVE (Proxying to 8000)") if nginx_state == "active" else self.style.ERROR(f"INACTIVE ({nginx_state})")
            self.stdout.write(f"Nginx Reverse Proxy:    {n_str}")

        # 4. Maintenance mode
        m_active = config_service.is_maintenance_enabled()
        m_str = self.style.WARNING("ENABLED (Browser serves maintenance page)") if m_active else self.style.SUCCESS("DISABLED (Normal web traffic)")
        self.stdout.write(f"Maintenance Mode:       {m_str}")

        # 5. ITMS upstream master switch
        itms_on = config_service.is_itms_enabled()
        itms_str = self.style.SUCCESS("ONLINE (Upstream pings & pushes active)") if itms_on else self.style.WARNING("PAUSED (Upstream network suspended)")
        self.stdout.write(f"ITMS Master Switch:     {itms_str}")

        # 6. Database reachability
        from django.db import connection
        try:
            connection.ensure_connection()
            db_vendor = connection.vendor.upper()
            self.stdout.write(f"Database Engine:        {self.style.SUCCESS(f'CONNECTED ({db_vendor})')}")
        except Exception as exc:
            self.stdout.write(f"Database Engine:        {self.style.ERROR(f'DISCONNECTED ({exc})')}")

        self.stdout.write("=" * 60 + "\n")

    def handle_maintenance(self, subaction: str):
        if subaction in ("on", "enable", "start"):
            config_service.set_maintenance_mode(True)
            self.stdout.write(self.style.SUCCESS("✅ Maintenance Mode ENABLED. Browser requests now serve the maintenance page."))
        elif subaction in ("off", "disable", "stop"):
            config_service.set_maintenance_mode(False)
            self.stdout.write(self.style.SUCCESS("✅ Maintenance Mode DISABLED. Normal web access restored."))
        else:
            m_cfg = config_service.get_maintenance_config()
            status_text = "ENABLED" if m_cfg.get("enabled") else "DISABLED"
            self.stdout.write(f"Maintenance Mode: {status_text}")
            self.stdout.write(f"Title:            {m_cfg.get('title')}")
            self.stdout.write(f"Message:          {m_cfg.get('message')}")
            self.stdout.write(f"Estimated Window: {m_cfg.get('estimated_duration')}")
            self.stdout.write(f"Support Contact:  {m_cfg.get('contact_support')}")
            self.stdout.write(f"Auto-Refresh:     {m_cfg.get('retry_after_seconds')}s")

    def handle_switch(self, subaction: str):
        if subaction in ("on", "enable", "resume"):
            config_service.set_itms_enabled(True)
            self.stdout.write(self.style.SUCCESS("✅ ITMS Master Switch ONLINE. Upstream synchronization and uploads enabled."))
        elif subaction in ("off", "disable", "pause"):
            config_service.set_itms_enabled(False, paused_by="CLI", reason="Paused via itms CLI")
            self.stdout.write(self.style.WARNING("⏸️ ITMS Master Switch PAUSED. All remote ITMS traffic suspended."))
        else:
            on = config_service.is_itms_enabled()
            self.stdout.write(f"ITMS Master Switch: {'ONLINE' if on else 'PAUSED'}")
