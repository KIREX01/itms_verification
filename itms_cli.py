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
import shutil
import signal
import sys
from pathlib import Path

# Always execute with working directory set to the project root
PROJECT_ROOT = Path(__file__).resolve().parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "itms_project.settings")

# Configure UTF-8 encoding on Windows console if available
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass


def get_python_exe(windowless: bool = False) -> str:
    local_venv = PROJECT_ROOT / ".venv"
    if sys.platform == "win32":
        if windowless:
            venv_pyw = local_venv / "Scripts" / "pythonw.exe"
            if venv_pyw.is_file():
                return str(venv_pyw)
        venv_py = local_venv / "Scripts" / "python.exe"
    else:
        venv_py = local_venv / "bin" / "python"
    if venv_py.is_file():
        return str(venv_py)
    return sys.executable


def is_port_in_use(port: int = 8000, host: str = "127.0.0.1") -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) == 0


def show_help():
    print("""
======================================================================
  ITMS VERIFICATION COPILOT - SYSTEM COMMAND INTERFACE
======================================================================

Usage:
  itms                Launch Web Operator Console in browser (default)
  itms start, --bg    Start Web Console in BACKGROUND (daemon mode)
  itms stop           Stop running background Web Console
  itms restart        Restart background Web Console
  itms --tui, -t      Launch Textual Terminal UI (keyboard-driven TUI)
  itms --web, -w      Launch Web Operator Console in foreground
  itms --ssl, -s      Launch Web Console with HTTPS primary (port 8443)
  itms --port <port>  Launch Web Console on custom port (e.g. 8080)
  itms clear data     Clean up evidence images, batches, and vaulted files
  itms status         Display system health, active database & diagnostics
  itms bootstrap      Verify AI plate models, dependencies & run migrations
  itms update         Check for and pull latest software updates
  itms uninstall      Completely uninstall app, shortcuts, and global commands
  itms manage <cmd>   Execute any Django management command
  itms --version, -v  Display installed version
  itms --help, -h     Show this command guide

Examples:
  itms                # Opens Web Console at http://127.0.0.1:8000
  itms start          # Starts server in background so terminal can be closed
  itms stop           # Stops the background server cleanly
  itms clear data     # Cleans temporary photos and resets batches
  itms --tui          # Launches keyboard-driven terminal dashboard
  itms status         # Verifies SQLite database, plate models & OCR
""")


def launch_web(extra_args=None, port=443):
    """Launches the Django Web Operator Console with automatic browser pop-up."""
    if extra_args:
        for i, a in enumerate(extra_args):
            if a in ("--port", "-p") and i + 1 < len(extra_args):
                try:
                    port = int(extra_args[i + 1])
                except ValueError:
                    pass

    if is_port_in_use(port):
        url = f"https://127.0.0.1:{port}/"
        print(f"[✓] ITMS Web Console is already running at {url}")
        print("[*] Opening browser...")
        import webbrowser
        webbrowser.open(url)
        return

    try:
        import django
        django.setup()
        from django.core.management import execute_from_command_line
        from core.services.ssl_service import ensure_ssl_certificates

        cert_file, key_file = ensure_ssl_certificates()
        cmd = [
            "manage.py", "runserver_plus", f"0.0.0.0:{port}",
            "--cert-file", str(cert_file),
            "--key-file", str(key_file)
        ]
        if extra_args:
            # Filter out custom args that runserver_plus doesn't understand natively,
            # or map them appropriately if needed. For now just extend.
            cmd.extend([a for a in extra_args if a not in ("--ssl", "-s") and not a.startswith("--port")])
        execute_from_command_line(cmd)
    except KeyboardInterrupt:
        print("\n[+] Web server stopped cleanly.")
        sys.exit(0)


def is_service_installed(service_name="ITMSVerificationService"):
    if sys.platform != "win32":
        return False
    import subprocess
    try:
        res = subprocess.run(["sc", "query", service_name], capture_output=True, text=True, creationflags=0x08000000)
        return "SERVICE_NAME" in res.stdout
    except Exception:
        return False


def start_daemon(extra_args=None, port=443):
    """Starts the Web Console as an independent background daemon."""
    if is_service_installed():
        import subprocess
        print("[*] Starting ITMSVerificationService Windows Service...")
        res = subprocess.run(["net", "start", "ITMSVerificationService"], capture_output=True, text=True)
        if res.returncode == 0 or "has already been started" in res.stdout or "has already been started" in res.stderr:
            print("[+] ITMSVerificationService is running.")
            return
        elif "Access is denied" in res.stderr or "Access is denied" in res.stdout or "System error 5" in res.stderr or "System error 5" in res.stdout:
            print("[!] Access denied. Elevating privileges to start service...")
            try:
                import ctypes
                ctypes.windll.shell32.ShellExecuteW(None, "runas", "net", "start ITMSVerificationService", None, 1)
                print("[+] UAC prompt launched to start the service.")
                return
            except Exception as e:
                print(f"[!] Failed to elevate: {e}")
        else:
            print(f"[!] Failed to start service:\n{res.stdout}\n{res.stderr}")
            return

    if extra_args:
        for i, a in enumerate(extra_args):
            if a in ("--port", "-p") and i + 1 < len(extra_args):
                try:
                    port = int(extra_args[i + 1])
                except ValueError:
                    pass

    protocol = "https" if port == 443 else "http"
    if is_port_in_use(port):
        url = f"{protocol}://127.0.0.1:{port}/"
        print(f"[✓] ITMS Web Console is already running at {url}")
        print("[*] Opening browser...")
        import webbrowser
        webbrowser.open(url)
        return

    py_exe = get_python_exe(windowless=False)
    log_dir = PROJECT_ROOT / "media"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "itms_web.log"

    cmd = [py_exe, "-u", str(PROJECT_ROOT / "manage.py"), "run_web", "--noreload"]
    if extra_args:
        cmd.extend(extra_args)

    print(f"[*] Starting ITMS Web Console in background on port {port}...")
    import subprocess
    log_fp = open(log_file, "a", encoding="utf-8")

    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        flags = CREATE_NEW_PROCESS_GROUP
        if not py_exe.lower().endswith("pythonw.exe"):
            flags |= 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    pid_file = PROJECT_ROOT / ".itms_web.pid"
    pid_file.write_text(str(proc.pid))

    import time
    time.sleep(2.0)

    # Verify that background process did not immediately terminate/crash
    if proc.poll() is not None:
        log_content = ""
        try:
            log_content = log_file.read_text(encoding="utf-8", errors="replace")[-600:]
        except OSError:
            pass
        print(f"[x] Error: Background Web Console process failed to start (exit code {proc.returncode}).")
        if log_content:
            print(f"Log excerpt:\n{log_content.strip()}")
        if pid_file.is_file():
            pid_file.unlink(missing_ok=True)
        return

    url = f"{protocol}://127.0.0.1:{port}/"
    print(f"[+] ITMS Web Console active in background (PID: {proc.pid}).")
    print(f"[i] URL: {url}")
    print(f"[i] Output logs: {log_file}")
    print("[i] The terminal window can now be closed safely without stopping the server.")
    print("[i] To stop the server at any time, run: itms stop\n")

    if not ("--no-browser" in (extra_args or [])):
        import webbrowser
        webbrowser.open(url)


def stop_server(extra_args=None):
    """Stops any running background Web Console server."""
    if is_service_installed():
        import subprocess
        print("[*] Stopping ITMSVerificationService Windows Service...")
        res = subprocess.run(["net", "stop", "ITMSVerificationService"], capture_output=True, text=True)
        if res.returncode == 0 or "is not started" in res.stdout or "is not started" in res.stderr:
            print("[+] ITMSVerificationService stopped.")
        elif "Access is denied" in res.stderr or "Access is denied" in res.stdout or "System error 5" in res.stderr or "System error 5" in res.stdout:
            print("[!] Access denied. Elevating privileges to stop service...")
            try:
                import ctypes
                ctypes.windll.shell32.ShellExecuteW(None, "runas", "net", "stop ITMSVerificationService", None, 1)
                print("[+] UAC prompt launched to stop the service.")
            except Exception as e:
                print(f"[!] Failed to elevate: {e}")
        else:
            print(f"[!] Failed to stop service:\n{res.stdout}\n{res.stderr}")

    pid_file = PROJECT_ROOT / ".itms_web.pid"
    stopped = False

    port = 8000
    if extra_args:
        for idx, a in enumerate(extra_args):
            if a in ("--port", "-p") and idx + 1 < len(extra_args):
                try:
                    port = int(extra_args[idx + 1])
                except ValueError:
                    pass
            elif a.startswith("--port="):
                try:
                    port = int(a.split("=")[1])
                except ValueError:
                    pass

    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text().strip())
            if sys.platform == "win32":
                import subprocess
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
            else:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            stopped = True
            print(f"[+] Terminated ITMS Web server process (PID {pid}).")
        except Exception as e:
            print(f"[*] Note: Could not terminate PID from file: {e}")
        finally:
            if pid_file.is_file():
                pid_file.unlink(missing_ok=True)

    # Also terminate any running run_web processes on Windows to prevent orphan processes
    if sys.platform == "win32":
        import subprocess
        try:
            ps_args = [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run_web*' } | Select-Object -ExpandProperty ProcessId"
            ]
            ps_res = subprocess.run(ps_args, capture_output=True, text=True, check=False)
            for line in ps_res.stdout.splitlines():
                line = line.strip()
                if line.isdigit() and int(line) != os.getpid():
                    subprocess.run(["taskkill", "/F", "/T", "/PID", line], capture_output=True, check=False)
                    stopped = True
        except (subprocess.SubprocessError, OSError):
            pass

    # Verify if target port is still listening
    try:
        clean_port = int(port)
        if not (1 <= clean_port <= 65535):
            clean_port = 8000
    except (ValueError, TypeError):
        clean_port = 8000

    if is_port_in_use(clean_port):
        print(f"[*] Releasing port {clean_port}...")
        if sys.platform == "win32":
            import subprocess
            try:
                res = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
                target_suffix = f":{clean_port}"
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 5 and "LISTENING" in parts:
                        local_addr = parts[1]
                        if local_addr.endswith(target_suffix):
                            proc_pid = parts[-1]
                            if proc_pid.isdigit() and int(proc_pid) != os.getpid():
                                subprocess.run(["taskkill", "/F", "/T", "/PID", proc_pid], capture_output=True, check=False)
                                stopped = True
            except (subprocess.SubprocessError, OSError):
                pass
        print(f"[+] Port {clean_port} released.")
    elif stopped:
        print("[+] ITMS Web server stopped cleanly.")
    else:
        print("[i] No running ITMS Web server found.")


def run_clear_data(args=None):
    """Executes clear_data management command with argument forwarding."""
    cleaned_args = []
    if args:
        for a in args:
            if a.lower() == "data":
                continue
            cleaned_args.append(a)

    print("====================================================================")
    print("  ITMS VERIFICATION COPILOT - EVIDENCE & VAULT DATA CLEANUP")
    print("====================================================================")
    try:
        import django
        django.setup()
        from django.core.management import execute_from_command_line
        execute_from_command_line(["manage.py", "clear_data"] + cleaned_args)
    except Exception as exc:
        print(f"[!] Error during clear_data: {exc}")


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


def run_update(extra_args=None):
    """Triggers system update directly via Django check_updates command or git pull."""
    print("====================================================================")
    print("  ITMS VERIFICATION COPILOT - SYSTEM UPDATE MANAGER")
    print("====================================================================")
    print()
    url = None
    if extra_args:
        for a in extra_args:
            if a.startswith(("http://", "https://")):
                url = a
                break
    try:
        import django
        django.setup()
        from django.core.management import call_command
        if url:
            call_command("check_updates", apply=True, download_url=url)
        else:
            call_command("check_updates", apply=True)
    except Exception as exc:
        print(f"[*] Checking updates via git pull: {exc}")
        if shutil.which("git"):
            import subprocess
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=PROJECT_ROOT, check=False)
        else:
            print("[*] Git CLI not found in environment; skipped git pull.")

    # Ensure dependencies and bootstrap run in a fresh child process
    py_exe = get_python_exe()
    try:
        req_file = PROJECT_ROOT / "requirements.txt"
        if req_file.is_file():
            import subprocess
            subprocess.run([py_exe, "-m", "pip", "install", "-r", str(req_file)], cwd=PROJECT_ROOT, check=False)
        import subprocess
        subprocess.run([py_exe, str(PROJECT_ROOT / "scripts" / "bootstrap.py")], cwd=PROJECT_ROOT, check=False)
    except Exception as post_exc:
        print(f"[!] Post-update task notice: {post_exc}")


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
        subprocess.run(cmd, check=False)
    else:
        uninstall_sh = PROJECT_ROOT / "uninstall.sh"
        if not uninstall_sh.is_file():
            print("[!] uninstall.sh not found.")
            return
        cmd = ["bash", str(uninstall_sh)]
        if extra_args:
            cmd.extend(extra_args)
        subprocess.run(cmd, check=False)


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
        try:
            from core.version import __version__
            print(f"ITMS Verification Copilot v{__version__}")
        except (ImportError, AttributeError):
            print("ITMS Verification Copilot v1.0.8")
        return

    if first in ("start", "--daemon", "--bg", "-bg", "bg", "daemon"):
        start_daemon(args[1:])
        return

    if first in ("stop", "kill", "shutdown"):
        stop_server(args[1:])
        return

    if first in ("restart", "reboot"):
        stop_server(args[1:])
        import time
        time.sleep(1.0)
        start_daemon(args[1:])
        return

    if first in ("clear-data", "cleardata"):
        run_clear_data(args[1:])
        return

    if first in ("clear", "clean"):
        # itms clear data, itms clear --all, itms clear
        run_clear_data(args[1:])
        return

    if first in ("--tui", "-t", "tui"):
        launch_tui(args[1:])
        return

    if first in ("--web", "-w", "web"):
        launch_web(args[1:])
        return

    if first in ("--ssl", "-s", "ssl"):
        launch_web(["--ssl"] + args[1:])
        return

    if first in ("status", "diagnostics", "health", "check"):
        run_status()
        return

    if first in ("bootstrap", "setup", "init"):
        run_bootstrap(args[1:])
        return

    if first in ("update", "upgrade"):
        run_update(args[1:])
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
