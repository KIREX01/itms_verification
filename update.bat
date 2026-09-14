@echo off
setlocal
cd /d "%~dp0"
title ITMS Verification Copilot - System Updater

echo ====================================================================
echo   ITMS VERIFICATION COPILOT - SYSTEM UPDATE WIZARD (GITHUB RELEASES)
echo ====================================================================
echo.

REM Activate virtual environment
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Execute update manager
python manage.py check_updates --apply
if %ERRORLEVEL% EQU 0 (
    echo.
    echo ====================================================================
    echo   [+] Update completed successfully!
    echo   Double-click run.bat to start the updated Web Console.
    echo ====================================================================
) else (
    echo.
    echo [!] Update process encountered an issue. See logs above.
)

echo.
pause
