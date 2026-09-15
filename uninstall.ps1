<#
.SYNOPSIS
    Complete Uninstaller for ITMS Verification Copilot.
.DESCRIPTION
    Completely removes ITMS Verification Copilot from the system:
    - Terminates running ITMS processes
    - Removes Desktop and Start Menu shortcuts
    - Deletes 'itms' shims from WindowsApps
    - Cleans InstallDir from User PATH
    - Clears ITMS_HOME environment variable
    - Completely deletes application files, virtual environment, and database
.PARAMETER Force
    Bypasses the confirmation prompt.
.PARAMETER InstallDir
    Target directory to remove (defaults to detected ITMS_HOME or %LOCALAPPDATA%\Programs\ITMS-Verification).
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [string]$InstallDir = ""
)

Write-Host "====================================================================" -ForegroundColor Red
Write-Host "   ITMS VERIFICATION COPILOT - COMPLETE SYSTEM UNINSTALLER" -ForegroundColor Red
Write-Host "====================================================================" -ForegroundColor Red
Write-Host ""

# 1. Resolve Installation Directory
if (-not $InstallDir) {
    if ($env:ITMS_HOME -and (Test-Path $env:ITMS_HOME)) {
        $InstallDir = $env:ITMS_HOME
    } else {
        try {
            $regHome = [Environment]::GetEnvironmentVariable("ITMS_HOME", "User")
            if ($regHome -and (Test-Path $regHome)) {
                $InstallDir = $regHome
            }
        } catch {}
    }
}
if (-not $InstallDir) {
    $defaultDir = Join-Path $env:LOCALAPPDATA "Programs\ITMS-Verification"
    if (Test-Path $defaultDir) {
        $InstallDir = $defaultDir
    }
}
if (-not $InstallDir -and (Test-Path "$PSScriptRoot\itms_cli.py")) {
    $InstallDir = $PSScriptRoot
}

if (-not $InstallDir -or -not (Test-Path $InstallDir)) {
    Write-Warning "Could not detect an active ITMS Verification Copilot installation."
    exit 0
}

Write-Host "Target Installation to Remove: $InstallDir" -ForegroundColor Yellow
Write-Host ""

# 2. Confirmation Prompt
if (-not $Force) {
    Write-Host "This will completely remove:" -ForegroundColor Yellow
    Write-Host "  - All application files, AI models, and virtual environment" -ForegroundColor White
    Write-Host "  - Local SQLite database (db.sqlite3) and photographic evidence vault" -ForegroundColor White
    Write-Host "  - Desktop and Start Menu shortcuts" -ForegroundColor White
    Write-Host "  - Global 'itms' CLI command from PATH and WindowsApps" -ForegroundColor White
    Write-Host ""
    $reply = Read-Host "Are you sure you want to completely uninstall ITMS? (type 'yes' to proceed)"
    if ($reply.Trim().ToLower() -ne "yes") {
        Write-Host "Uninstall canceled by user." -ForegroundColor Gray
        exit 0
    }
    Write-Host ""
}

# 3. Terminate Any Running ITMS Python / Server Processes
Write-Host "[>] Terminating running ITMS processes..." -ForegroundColor Cyan
try {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and ($_.CommandLine -like "*$InstallDir*" -or $_.CommandLine -like "*run_web*" -or $_.CommandLine -like "*run_tui*")
    } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
} catch {}

# 4. Remove Shortcuts
Write-Host "[>] Removing system shortcuts..." -ForegroundColor Cyan
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) "ITMS Verification Copilot.lnk"
if (Test-Path $desktopLnk) {
    Remove-Item -Path $desktopLnk -Force -ErrorAction SilentlyContinue
    Write-Host "    [+] Removed Desktop shortcut" -ForegroundColor Green
}
$startMenuLnk = Join-Path ([Environment]::GetFolderPath('StartMenu')) "Programs\ITMS Verification Copilot.lnk"
if (Test-Path $startMenuLnk) {
    Remove-Item -Path $startMenuLnk -Force -ErrorAction SilentlyContinue
    Write-Host "    [+] Removed Start Menu shortcut" -ForegroundColor Green
}

# 5. Remove WindowsApps Shims
Write-Host "[>] Removing global 'itms' command shims..." -ForegroundColor Cyan
$winAppsDir = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
foreach ($shim in @("itms.cmd", "itms.ps1")) {
    $shimPath = Join-Path $winAppsDir $shim
    if (Test-Path $shimPath) {
        Remove-Item -Path $shimPath -Force -ErrorAction SilentlyContinue
        Write-Host "    [+] Removed $shim from WindowsApps" -ForegroundColor Green
    }
}

# 6. Clean Environment Variables (ITMS_HOME and PATH)
Write-Host "[>] Cleaning User environment variables..." -ForegroundColor Cyan
try {
    # Remove ITMS_HOME
    [Environment]::SetEnvironmentVariable("ITMS_HOME", $null, "User")
    $env:ITMS_HOME = $null
    Write-Host "    [+] Cleared ITMS_HOME" -ForegroundColor Green

    # Remove InstallDir from User PATH
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($userPath) {
        $parts = $userPath -split ";" | Where-Object { $_ -and $_.Trim().TrimEnd('\') -ne $InstallDir.Trim().TrimEnd('\') }
        $cleanedPath = $parts -join ";"
        [Environment]::SetEnvironmentVariable("Path", $cleanedPath, "User")
        Write-Host "    [+] Removed $InstallDir from User PATH" -ForegroundColor Green
    }
} catch {
    Write-Warning "Could not update environment variables: $_"
}

# 7. Delete Installation Directory
Write-Host "[>] Deleting installation directory: $InstallDir..." -ForegroundColor Cyan
Set-Location $env:TEMP

try {
    Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction Stop
    Write-Host "    [+] Installation directory completely removed." -ForegroundColor Green
} catch {
    Write-Host "    [*] Some files locked. Scheduling background deletion in 2 seconds..." -ForegroundColor Yellow
    Start-Process -FilePath "cmd.exe" -ArgumentList "/c timeout /t 2 >nul & rd /s /q `"$InstallDir`"" -WindowStyle Hidden
}

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "   [+] ITMS VERIFICATION COPILOT UNINSTALLED SUCCESSFULLY" -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "The application and all associated data have been completely removed." -ForegroundColor White
Write-Host ""
