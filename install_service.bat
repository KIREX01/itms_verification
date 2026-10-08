@echo off
:: Batch script to run the Windows Service installer with Administrator privileges
:: It will prompt with UAC if not already running as Admin.

set "script_path=%~dp0"
set "python_exe=%script_path%.venv\Scripts\python.exe"
if not exist "%python_exe%" set "python_exe=python"
set "service_script=%script_path%windows_service.py"

:: Check for admin rights
net session >nul 2>&1
if %errorLevel% == 0 (
    echo ====================================================================
    echo    ITMS VERIFICATION COPILOT - WINDOWS SERVICE INSTALLER
    echo ====================================================================
    echo.
    echo Administrator privileges confirmed.
    echo Installing ITMS Verification Service...
    "%python_exe%" "%service_script%" install

    echo Configuring service to start automatically on system boot...
    "%python_exe%" "%service_script%" update --startup=auto

    echo Configuring SCM automatic failure recovery (restart on crash)...
    sc failure ITMSVerificationService reset= 86400 actions= restart/5000/restart/10000/restart/60000 >nul 2>&1

    echo Setting service description...
    sc description ITMSVerificationService "ITMS Verification Copilot 24/7 background web server and 3-hour stock synchronizer." >nul 2>&1

    echo Starting the service...
    "%python_exe%" "%service_script%" start

    echo.
    echo ====================================================================
    echo    [+] SERVICE SUCCESSFULLY INSTALLED AND STARTED!
    echo ====================================================================
    echo  * Service Name: ITMSVerificationService
    echo  * Access URL:   https://localhost/ (port 443)
    echo  * Status:       Runs 24/7 in background, survives logout.
    echo  * Away Mode:    Active (keeps CPU and network running when screen dims).
    echo.
    echo  [TIP FOR LAPTOPS]:
    echo  To keep running when closing laptop lid, set:
    echo  Control Panel -^> Power Options -^> "Choose what closing the lid does"
    echo  to "Do nothing" (when plugged in).
    echo ====================================================================
    echo.
    if not "%1"=="/nopause" if not "%1"=="--no-pause" pause
) else (
    echo Requesting Administrator privileges...
    powershell -Command "Start-Process '%~f0' -ArgumentList '%*' -Verb RunAs"
)
