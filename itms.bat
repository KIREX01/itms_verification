@echo off
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

REM 1. Default action (no arguments): Launch Web Console in browser
if "%~1"=="" (
    "%PY%" itms_cli.py
    goto :eof
)

REM 2. Explicit TUI request
if /i "%~1"=="--tui" (
    shift
    "%PY%" manage.py run_tui %1 %2 %3 %4 %5 %6 %7 %8 %9
    goto :eof
)
if /i "%~1"=="-t" (
    shift
    "%PY%" manage.py run_tui %1 %2 %3 %4 %5 %6 %7 %8 %9
    goto :eof
)
if /i "%~1"=="tui" (
    shift
    "%PY%" manage.py run_tui %1 %2 %3 %4 %5 %6 %7 %8 %9
    goto :eof
)

REM 3. Django manage pass-through
if /i "%~1"=="manage" (
    shift
    "%PY%" manage.py %1 %2 %3 %4 %5 %6 %7 %8 %9
    goto :eof
)

REM 4. Dispatch all other commands to itms_cli.py
"%PY%" itms_cli.py %*
endlocal
