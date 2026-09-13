@echo off
setlocal
cd /d "%~dp0"
title ITMS Verification Copilot - Web Console

echo ====================================================================
echo   ITMS VERIFICATION COPILOT - OPERATOR WEB CONSOLE
echo ====================================================================

REM Activate virtual environment if present
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Run web console server and automatically open browser
python manage.py run_web
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Server encountered an error. Press any key to exit.
    pause >nul
)
