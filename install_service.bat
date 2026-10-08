@echo off
:: Batch script to run the Windows Service installer with Administrator privileges
:: It will prompt with UAC if not already running as Admin.

set "script_path=%~dp0"
set "python_exe=%script_path%.venv\Scripts\python.exe"
set "service_script=%script_path%windows_service.py"

:: Check for admin rights
net session >nul 2>&1
if %errorLevel% == 0 (
    echo Administrator privileges confirmed.
    echo Installing ITMS Verification Service...
    "%python_exe%" "%service_script%" install
    echo Configuring service to start automatically...
    "%python_exe%" "%service_script%" update --startup=auto
    echo Starting the service...
    "%python_exe%" "%service_script%" start
    echo.
    echo Service successfully installed and started!
    echo It will now run in the background and survive sleep/logout.
    echo You can access the UI securely at: https://localhost/ (or port 443)
    pause
) else (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
)
