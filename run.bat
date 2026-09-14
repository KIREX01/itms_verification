@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title ITMS Verification Copilot - Web Console

echo ====================================================================
echo   ITMS VERIFICATION COPILOT - 1-CLICK WEB CONSOLE LAUNCHER
echo ====================================================================
echo.

REM 1. Detect Python Executable
set "PYTHON_EXE="

REM 1a. Fast Path: If virtual environment already exists, use it directly
if exist ".venv\Scripts\python.exe" (
    set "PYTHON_EXE=.venv\Scripts\python.exe"
    goto :python_ready
)

REM 1b. Check local portable Python in tools\python\
if exist "tools\python\python.exe" (
    set "PYTHON_EXE=tools\python\python.exe"
    goto :python_ready
)

REM 1c. Check standard PATH (python, py, python3)
for %%P in (python py python3) do (
    if not defined PYTHON_EXE (
        %%P --version >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            set "PYTHON_EXE=%%P"
        )
    )
)

REM 1d. Scan standard installation locations (in case Python was installed without checking "Add to PATH")
if not defined PYTHON_EXE (
    for %%D in (
        "%LocalAppData%\Programs\Python\Python314\python.exe"
        "%LocalAppData%\Programs\Python\Python312\python.exe"
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "%LocalAppData%\Programs\Python\Python310\python.exe"
        "C:\Python314\python.exe"
        "C:\Python312\python.exe"
        "C:\Python311\python.exe"
        "C:\Python310\python.exe"
        "%ProgramFiles%\Python312\python.exe"
        "%ProgramFiles%\Python311\python.exe"
        "%ProgramFiles%\Python310\python.exe"
        "%ProgramFiles(x86)%\Python311\python.exe"
    ) do (
        if not defined PYTHON_EXE (
            if exist %%D (
                set "PYTHON_EXE=%%D"
            )
        )
    )
)

REM 1e. Automated Provisioning: When Python is NOT installed and NOT in PATH
if not defined PYTHON_EXE (
    echo ====================================================================
    echo   [!] Python 3.10+ was not detected on your system.
    echo   Beginning automated zero-configuration Python provisioning...
    echo ====================================================================
    echo.

    REM Attempt 1: Try winget if present
    set "WINGET_OK=0"
    where.exe winget >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        echo [>] Attempting installation via Windows Package Manager (winget)...
        winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
        if !ERRORLEVEL! EQU 0 (
            set "WINGET_OK=1"
        )
    )

    REM Check if winget successfully installed Python
    for %%D in (
        "%LocalAppData%\Programs\Python\Python311\python.exe"
        "%LocalAppData%\Programs\Python\Python312\python.exe"
    ) do (
        if not defined PYTHON_EXE (
            if exist %%D (
                set "PYTHON_EXE=%%D"
            )
        )
    )

    REM Attempt 2: If winget was absent or failed, download official installer from python.org
    if not defined PYTHON_EXE (
        echo [!] winget not available or failed. Falling back to direct python.org installer...
        echo [>] Downloading official Python 3.11 64-bit installer...
        
        set "PY_INSTALLER=%TEMP%\python-3.11.9-amd64.exe"
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
            "$ProgressPreference = 'SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; (New-Object Net.WebClient).DownloadFile('https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe', '!PY_INSTALLER!')"
        
        if exist "!PY_INSTALLER!" (
            echo [>] Installing Python silently (user-level, no admin privileges required)...
            "!PY_INSTALLER!" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_launcher=1
            
            REM Wait a moment for registration and verify location
            timeout /t 4 >nul 2>&1
            if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
                set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python311\python.exe"
                set "PATH=%LocalAppData%\Programs\Python\Python311;%LocalAppData%\Programs\Python\Python311\Scripts;!PATH!"
                echo [+] Python 3.11 successfully installed and activated!
            )
            del /f /q "!PY_INSTALLER!" >nul 2>&1
        )
    )

    REM Attempt 3: If still not resolved (e.g. completely air-gapped machine)
    if not defined PYTHON_EXE (
        echo.
        echo [x] Automated Python installation could not complete.
        echo.
        echo Offline / Manual Setup Options:
        echo   1. If connected to internet: Download and run https://www.python.org/downloads/
        echo      (Crucial: check "Add python.exe to PATH" during installation)
        echo   2. If on an air-gapped / offline machine:
        echo      Copy a portable Python folder into 'tools\python\' inside this project,
        echo      or install Python from an offline installer USB.
        echo.
        echo Opening Python download page in your browser...
        start https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

:python_ready

REM 2. Create or Activate Virtual Environment (.venv)
if not exist ".venv\Scripts\python.exe" (
    echo [>] Creating isolated Python environment in .venv...
    %PYTHON_EXE% -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo [x] Failed to create virtual environment. Check Python permissions.
        pause
        exit /b 1
    )
    echo [+] Virtual environment initialized.
)

REM Activate virtual environment
call .venv\Scripts\activate.bat

REM 3. Fast Dependency Check via Hash
set "REQS_FILE=requirements.txt"
set "HASH_FILE=.venv\.reqs_hash"
set "CURRENT_HASH="

if exist "%REQS_FILE%" (
    for /f "skip=1 delims=" %%H in ('certutil -hashfile "%REQS_FILE%" SHA256 2^>nul') do (
        if not defined CURRENT_HASH (
            set "CURRENT_HASH=%%H"
            set "CURRENT_HASH=!CURRENT_HASH: =!"
        )
    )
)

set "PREV_HASH="
if exist "%HASH_FILE%" (
    set /p PREV_HASH=<"%HASH_FILE%"
)

if not "%CURRENT_HASH%"=="%PREV_HASH%" (
    echo [>] Installing or updating system dependencies...
    python -m pip install --upgrade pip >nul 2>&1
    python -m pip install -r requirements.txt
    if %ERRORLEVEL% EQU 0 (
        echo !CURRENT_HASH!>"%HASH_FILE%"
        echo [+] Dependencies verified.
    ) else (
        echo [!] Warning: Some dependencies had issues. Continuing with bootstrap...
    )
)

REM 4. Automated Resource Provisioning & Health Check
echo [>] Provisioning models, directories, and database...
python scripts\bootstrap.py
if %ERRORLEVEL% NEQ 0 (
    echo [!] Bootstrap reported an issue. Starting Web Console anyway...
)

REM 5. Launch Web Operator Console & Auto-Open Browser (Default UX)
echo.
echo ====================================================================
echo   [+] Starting Web Operator Console (http://127.0.0.1:8000)
echo   (Your default web browser will open automatically in a moment)
echo   Press Ctrl+C to stop the application.
echo ====================================================================
echo.
python manage.py run_web

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Server exited with an error. Press any key to close.
    pause >nul
)
