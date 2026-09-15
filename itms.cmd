@echo off
setlocal enabledelayedexpansion

REM ====================================================================
REM   ITMS VERIFICATION COPILOT - SYSTEM COMMAND WRAPPER
REM ====================================================================

REM 1. Determine Application Home Directory
set "APP_DIR="
if defined ITMS_HOME (
    if exist "%ITMS_HOME%\itms_cli.py" set "APP_DIR=%ITMS_HOME%"
)

REM Registry fallback if ITMS_HOME not yet refreshed in current shell session
if not defined APP_DIR (
    for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v ITMS_HOME 2^>nul') do (
        if exist "%%B\itms_cli.py" set "APP_DIR=%%B"
    )
)

REM Standard location fallback (%~dp0)
if not defined APP_DIR (
    set "APP_DIR=%~dp0"
    if "!APP_DIR:~-1!"=="\" set "APP_DIR=!APP_DIR:~0,-1!"
)

REM 2. Prioritize Virtual Environment Python
set "PYTHON_EXE="
if exist "%APP_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%APP_DIR%\.venv\Scripts\python.exe"
) else (
    set "PYTHON_EXE=python"
)

REM 3. Dispatch to CLI Runner
"%PYTHON_EXE%" "%APP_DIR%\itms_cli.py" %*
exit /b %ERRORLEVEL%
