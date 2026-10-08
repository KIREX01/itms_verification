@echo off
:: Batch script to stop and remove the Windows Service with Administrator privileges
:: Prompts with UAC if not already running as Admin.

set "script_path=%~dp0"
set "python_exe=%script_path%.venv\Scripts\python.exe"
if not exist "%python_exe%" set "python_exe=python"
set "service_script=%script_path%windows_service.py"

:: Check for admin rights
net session >nul 2>&1
if %errorLevel% == 0 (
    echo ====================================================================
    echo    ITMS VERIFICATION COPILOT - WINDOWS SERVICE UNINSTALLER
    echo ====================================================================
    echo.
    echo Administrator privileges confirmed.
    echo Stopping ITMS Verification Service...
    "%python_exe%" "%service_script%" stop >nul 2>&1
    net stop ITMSVerificationService >nul 2>&1

    echo Removing ITMS Verification Service...
    "%python_exe%" "%service_script%" remove

    echo.
    echo ====================================================================
    echo    [+] SERVICE SUCCESSFULLY STOPPED AND REMOVED.
    echo ====================================================================
    echo.
    if not "%1"=="/nopause" if not "%1"=="--no-pause" pause
) else (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -ArgumentList '%*' -Verb RunAs"
)
