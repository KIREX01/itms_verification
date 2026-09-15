<#
.SYNOPSIS
    1-Click PowerShell Installer for ITMS Verification Copilot.
.DESCRIPTION
    Installs ITMS Verification Copilot into the user's local application directory,
    creates Desktop and Start Menu shortcuts, sets up the environment, and launches
    the Web Operator Console.
.EXAMPLE
    irm https://raw.githubusercontent.com/KIREX01/itms_verification/main/install.ps1 | iex
#>

[CmdletBinding()]
param (
    [string]$InstallDir = "$env:LOCALAPPDATA\Programs\ITMS-Verification",
    [switch]$NoLaunch,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "   ITMS VERIFICATION COPILOT - 1-CLICK POWERSHELL INSTALLER" -ForegroundColor Yellow
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""

$repoUrl = "https://github.com/KIREX01/itms_verification.git"
$zipUrl = "https://github.com/KIREX01/itms_verification/archive/refs/heads/main.zip"

Write-Host "[>] Target Installation Directory:" -ForegroundColor Gray
Write-Host "    $InstallDir" -ForegroundColor White
Write-Host ""

# 1. Prepare Target Directory
if (Test-Path "$InstallDir") {
    if (Test-Path "$InstallDir\.git") {
        Write-Host "[*] Existing installation detected. Updating via git pull..." -ForegroundColor Yellow
        Push-Location "$InstallDir"
        try {
            git pull --rebase origin main
            Write-Host "[+] Updated to latest version successfully." -ForegroundColor Green
        } catch {
            Write-Warning "Failed to git pull. Continuing with existing files."
        }
        Pop-Location
    } elseif ($Force) {
        Write-Host "[*] Force flag supplied. Re-installing into $InstallDir..." -ForegroundColor Yellow
    } else {
        Write-Host "[!] Directory already exists. Re-verifying installation files..." -ForegroundColor Yellow
    }
} else {
    New-Item -ItemType Directory -Path "$InstallDir" -Force | Out-Null
}

# 2. Fetch Codebase (Git Clone or ZIP Download)
if (-not (Test-Path "$InstallDir\run.bat")) {
    $hasGit = $null
    try {
        $hasGit = Get-Command git -ErrorAction SilentlyContinue
    } catch {}

    if ($hasGit) {
        Write-Host "[>] Git detected on system. Cloning official repository..." -ForegroundColor Cyan
        git clone --depth 1 $repoUrl "$InstallDir"
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Git clone failed. Falling back to direct ZIP download..."
            $hasGit = $null
        }
    }

    if (-not $hasGit -or -not (Test-Path "$InstallDir\run.bat")) {
        Write-Host "[>] Downloading official package from GitHub..." -ForegroundColor Cyan
        $tempZip = Join-Path $env:TEMP "itms_verification_$(Get-Random).zip"
        $tempExtract = Join-Path $env:TEMP "itms_extract_$(Get-Random)"

        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-RestMethod -Uri $zipUrl -OutFile $tempZip
            Write-Host "[>] Extracting archive..." -ForegroundColor Gray
            Expand-Archive -Path $tempZip -DestinationPath $tempExtract -Force

            $extractedRoot = Get-ChildItem -Path $tempExtract -Directory | Select-Object -First 1
            if ($extractedRoot) {
                Copy-Item -Path "$($extractedRoot.FullName)\*" -Destination "$InstallDir" -Recurse -Force
            }
        } finally {
            if (Test-Path $tempZip) { Remove-Item -Path $tempZip -Force -ErrorAction SilentlyContinue }
            if (Test-Path $tempExtract) { Remove-Item -Path $tempExtract -Recurse -Force -ErrorAction SilentlyContinue }
        }
    }
}

# 3. Create Windows Shortcuts (Desktop & Start Menu)
Write-Host "[>] Creating Windows shortcuts..." -ForegroundColor Cyan
try {
    $wsh = New-Object -ComObject WScript.Shell
    $iconPath = Join-Path $InstallDir "assets\logos\favicon.ico"
    $runBat = Join-Path $InstallDir "run.bat"

    # Desktop Shortcut
    $desktopPath = [Environment]::GetFolderPath('Desktop')
    $desktopLnk = Join-Path $desktopPath "ITMS Verification Copilot.lnk"
    $shortcut = $wsh.CreateShortcut($desktopLnk)
    $shortcut.TargetPath = $runBat
    $shortcut.WorkingDirectory = $InstallDir
    if (Test-Path $iconPath) {
        $shortcut.IconLocation = $iconPath
    }
    $shortcut.Description = "Launch ITMS Verification Copilot Web Operator Console"
    $shortcut.Save()
    Write-Host "    [+] Desktop: $desktopLnk" -ForegroundColor Green

    # Start Menu Shortcut
    $startMenuPath = [Environment]::GetFolderPath('StartMenu')
    $programsPath = Join-Path $startMenuPath "Programs"
    if (Test-Path $programsPath) {
        $startMenuLnk = Join-Path $programsPath "ITMS Verification Copilot.lnk"
        $shortcut2 = $wsh.CreateShortcut($startMenuLnk)
        $shortcut2.TargetPath = $runBat
        $shortcut2.WorkingDirectory = $InstallDir
        if (Test-Path $iconPath) {
            $shortcut2.IconLocation = $iconPath
        }
        $shortcut2.Description = "Launch ITMS Verification Copilot Web Operator Console"
        $shortcut2.Save()
        Write-Host "    [+] Start Menu: $startMenuLnk" -ForegroundColor Green
    }
} catch {
    Write-Warning "Could not register system shortcuts: $_"
}

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "   [+] ITMS VERIFICATION COPILOT INSTALLED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "Location: $InstallDir" -ForegroundColor White
Write-Host "Launcher: Double-click 'ITMS Verification Copilot' on your Desktop" -ForegroundColor Yellow
Write-Host ""

# 4. Launch Application
if (-not $NoLaunch) {
    Write-Host "[>] Launching ITMS Verification Copilot..." -ForegroundColor Cyan
    Write-Host "    (The system will auto-provision dependencies and open your browser)" -ForegroundColor Gray
    Start-Process -FilePath "$InstallDir\run.bat" -WorkingDirectory "$InstallDir"
}
