@echo off
setlocal
echo ====================================================================
echo   ITMS VERIFICATION COPILOT - LOCAL HTTPS LAUNCHER
echo ====================================================================
echo.

set "script_path=%~dp0"
set "python_exe=%script_path%.venv\Scripts\python.exe"
if not exist "%python_exe%" set "python_exe=python"

"%python_exe%" "%script_path%manage.py" run_web --ssl --port 443
if %errorLevel% neq 0 (
    echo.
    echo [!] Server exited with code %errorLevel%.
    pause
)
