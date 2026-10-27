@echo off
setlocal
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if "%~1"=="" (
    "%PY%" manage.py run_tui
    goto :eof
)
if /i "%~1"=="-v" (
    "%PY%" manage.py itms_ctl version
    goto :eof
)
if /i "%~1"=="--version" (
    "%PY%" manage.py itms_ctl version
    goto :eof
)
if /i "%~1"=="version" (
    "%PY%" manage.py itms_ctl version
    goto :eof
)
if /i "%~1"=="-h" (
    "%PY%" manage.py itms_ctl help
    goto :eof
)
if /i "%~1"=="--help" (
    "%PY%" manage.py itms_ctl help
    goto :eof
)
if /i "%~1"=="help" (
    "%PY%" manage.py itms_ctl help
    goto :eof
)
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
if /i "%~1"=="manage" (
    shift
    "%PY%" manage.py %1 %2 %3 %4 %5 %6 %7 %8 %9
    goto :eof
)

"%PY%" manage.py itms_ctl %*
endlocal
