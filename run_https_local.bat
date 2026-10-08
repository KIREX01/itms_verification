@echo off
echo Starting ITMS Verification Copilot on HTTPS...
echo.
.venv\Scripts\python.exe manage.py runserver_plus 0.0.0.0:443 --cert-file cert.crt
pause
