@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" manage.py itms_ctl %*
) else (
    python manage.py itms_ctl %*
)
endlocal
