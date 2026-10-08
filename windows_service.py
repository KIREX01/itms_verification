"""
ITMS Verification Copilot - Production Windows Service.
Runs ITMS Verification Django Web & Mobile Server 24/7 as a background Windows Service.

Features:
- Survives user logout and background execution.
- Keeps system and network alive during sleep / lid-close using Away Mode (ES_AWAYMODE_REQUIRED).
- Auto-restarts on unexpected process termination via Service Control Manager recovery.
- Starts MorningKitSyncDaemon (3-hour background warehouse kit sync).
- Performs clean process tree termination (taskkill /F /T) on service stop to prevent port 443 locking.
"""
import ctypes
import os
import subprocess
import sys
import time
from pathlib import Path

import servicemanager
import win32event
import win32service
import win32serviceutil

# Windows Power Management Execution State Flags
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040


def enable_keep_awake() -> bool:
    """
    Prevents Windows from suspending CPU and network when the laptop lid is closed
    or the display sleeps (Away Mode).
    """
    try:
        flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        res = ctypes.windll.kernel32.SetThreadExecutionState(flags)
        return bool(res)
    except Exception:
        return False


def disable_keep_awake() -> None:
    """Restores default Windows power management state."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
    except Exception:
        pass


class ITMSDjangoService(win32serviceutil.ServiceFramework):
    _svc_name_ = 'ITMSVerificationService'
    _svc_display_name_ = 'ITMS Verification Copilot (Django)'
    _svc_description_ = 'Runs the ITMS Verification Django server locally on HTTPS with 3-hour stock synchronizer.'

    def __init__(self, args):
        super().__init__(args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.process = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        servicemanager.LogInfoMsg(f"{self._svc_display_name_} stopping...")
        win32event.SetEvent(self.hWaitStop)
        disable_keep_awake()
        if self.process:
            try:
                # Terminate entire process tree cleanly to release port 443 immediately
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(self.process.pid)],
                    capture_output=True,
                    timeout=10,
                )
            except Exception:
                try:
                    self.process.terminate()
                except Exception:
                    pass

    def SvcDoRun(self):
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ''),
        )
        self.main()

    def main(self):
        base_dir = Path(__file__).resolve().parent
        os.chdir(str(base_dir))
        venv_python = base_dir / ".venv" / "Scripts" / "python.exe"
        if not venv_python.is_file():
            venv_python = Path(sys.executable)
        manage_py = base_dir / "manage.py"

        # 1. Enable Windows Away Mode to keep server & background sync running during sleep
        enable_keep_awake()

        # 2. Pre-ensure HTTPS SSL certificates exist before spawning server
        try:
            sys.path.insert(0, str(base_dir))
            from core.services.ssl_service import ensure_ssl_certificates
            ensure_ssl_certificates()
        except Exception as ssl_err:
            servicemanager.LogWarningMsg(f"[SSL] Notice pre-generating certificates: {ssl_err}")

        # 3. Command to launch full web server and 3-hour background stock crawler
        command = [
            str(venv_python),
            "-u",
            str(manage_py),
            "run_web",
            "--noreload",
            "--no-browser",
            "--host", "0.0.0.0",
            "--port", "443",
            "--ssl-port", "443",
        ]

        log_dir = base_dir / "media"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "itms_service.log"
        log_fp = open(log_file, "a", encoding="utf-8")

        servicemanager.LogInfoMsg(f"Starting ITMS Web Console via {command}")

        self.process = subprocess.Popen(
            command,
            cwd=str(base_dir),
            stdout=log_fp,
            stderr=subprocess.STDOUT,
        )

        # 4. Process watchdog loop: wait for SCM stop signal while monitoring child process health
        while True:
            rc = win32event.WaitForSingleObject(self.hWaitStop, 1000)
            if rc == win32event.WAIT_OBJECT_0:
                # SCM issued stop command
                break

            # If child Django process died unexpectedly, log and exit so SCM auto-restart kicks in
            if self.process.poll() is not None:
                exit_code = self.process.returncode
                servicemanager.LogErrorMsg(
                    f"ITMS Django process (PID {self.process.pid}) terminated unexpectedly with exit code {exit_code}. SCM will restart."
                )
                break

        disable_keep_awake()


if __name__ == '__main__':
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(ITMSDjangoService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(ITMSDjangoService)
