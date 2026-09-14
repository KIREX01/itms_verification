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
for %%P in (python py python3) do (
    if not defined PYTHON_EXE (
        %%P --version >nul 2>&1
        if !ERRORLEVEL! EQU 0 (
            set "PYTHON_EXE=%%P"
        )
    )
)

if not defined PYTHON_EXE (
    echo [x] Python 3.10+ was not found on your system PATH.
    echo.
    echo To install Python automatically on Windows, run:
    echo     winget install Python.Python.3.11
    echo Or download from: https://www.python.org/downloads/
    echo (Make sure to check "Add python.exe to PATH" during setup)
    echo.
    pause
    exit /b 1
)

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
