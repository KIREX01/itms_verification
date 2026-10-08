import os
import sys
import time
import win32serviceutil
import win32service
import win32event
import servicemanager
import subprocess

class ITMSDjangoService(win32serviceutil.ServiceFramework):
    _svc_name_ = 'ITMSVerificationService'
    _svc_display_name_ = 'ITMS Verification Copilot (Django)'
    _svc_description_ = 'Runs the ITMS Verification Django server locally on HTTPS.'

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self.process = None

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        if self.process:
            self.process.terminate()

    def SvcDoRun(self):
        servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                              servicemanager.PYS_SERVICE_STARTED,
                              (self._svc_name_, ''))
        self.main()

    def main(self):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        venv_python = os.path.join(base_dir, '.venv', 'Scripts', 'python.exe')
        manage_py = os.path.join(base_dir, 'manage.py')
        
        # Using runserver_plus for HTTPS (requires werkzeug, pyOpenSSL, django-extensions)
        # Port 443 needs admin/system privileges, which services have.
        command = [
            venv_python, manage_py, 'runserver_plus',
            '0.0.0.0:443',
            '--cert-file', 'cert.crt'
        ]

        log_dir = os.path.join(base_dir, 'logs')
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        log_file = open(os.path.join(log_dir, 'service.log'), 'a')

        self.process = subprocess.Popen(
            command,
            cwd=base_dir,
            stdout=log_file,
            stderr=subprocess.STDOUT
        )
        # Wait for service stop signal
        win32event.WaitForSingleObject(self.hWaitStop, win32event.INFINITE)

if __name__ == '__main__':
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(ITMSDjangoService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(ITMSDjangoService)
