<#
.SYNOPSIS
    1-Click PowerShell Installer for ITMS Verification Copilot.
.DESCRIPTION
    Installs ITMS Verification Copilot into the user's local application directory,
    creates an isolated Python virtual environment, installs all Python project
    dependencies (requirements.txt), provisions AI models and database,
    creates Desktop and Start Menu shortcuts, and launches the Web Operator Console.
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

# ------------------------------------------------------------------
# 1. Prepare Target Directory & Fetch Codebase
# ------------------------------------------------------------------
if (Test-Path "$InstallDir") {
    if (Test-Path "$InstallDir\.git") {
        Write-Host "[*] Existing installation detected. Updating via git pull..." -ForegroundColor Yellow
        Push-Location "$InstallDir"
        try {
            git pull --rebase origin main
            Write-Host "[+] Updated codebase to latest version." -ForegroundColor Green
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

if (-not (Test-Path "$InstallDir\requirements.txt")) {
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

    if (-not $hasGit -or -not (Test-Path "$InstallDir\requirements.txt")) {
        Write-Host "[>] Downloading official package from GitHub..." -ForegroundColor Cyan
        $tempZip = Join-Path $env:TEMP "itms_verification_$(Get-Random).zip"
        $tempExtract = Join-Path $env:TEMP "itms_extract_$(Get-Random)"

        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-RestMethod -Uri $zipUrl -OutFile $tempZip
            Write-Host "[>] Extracting application archive..." -ForegroundColor Gray
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

# ------------------------------------------------------------------
# 2. Detect / Provision Python 3.10+ Executable
# ------------------------------------------------------------------
Write-Host "[>] Verifying Python runtime environment..." -ForegroundColor Cyan
$pythonExe = $null

# Priority 1: Check existing .venv
$venvPy = Join-Path $InstallDir ".venv\Scripts\python.exe"
if (Test-Path $venvPy) {
    $pythonExe = $venvPy
}

# Priority 2: Check portable tools\python
if (-not $pythonExe) {
    $portablePy = Join-Path $InstallDir "tools\python\python.exe"
    if (Test-Path $portablePy) { $pythonExe = $portablePy }
}

# Priority 3: Scan PATH (python, py, python3)
if (-not $pythonExe) {
    foreach ($cmd in @("python", "py", "python3")) {
        try {
            $cmdObj = Get-Command $cmd -ErrorAction SilentlyContinue
            if ($cmdObj) {
                $verOutput = & $cmd --version 2>&1
                if ($verOutput -match "Python (\d+)\.(\d+)") {
                    $major = [int]$matches[1]
                    $minor = [int]$matches[2]
                    if ($major -eq 3 -and $minor -ge 10) {
                        $pythonExe = $cmdObj.Source
                        Write-Host "    Found Python in PATH: $pythonExe ($verOutput)" -ForegroundColor Gray
                        break
                    }
                }
            }
        } catch {}
    }
}

# Priority 4: Scan standard Windows install folders
if (-not $pythonExe) {
    $standardPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python314\python.exe",
        "C:\Python311\python.exe",
        "C:\Python312\python.exe",
        "C:\Python310\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe"
    )
    foreach ($p in $standardPaths) {
        if (Test-Path $p) {
            $pythonExe = $p
            Write-Host "    Found installed Python: $p" -ForegroundColor Gray
            break
        }
    }
}

# Priority 5: Automated Silent Python Installation (when Python is absent)
if (-not $pythonExe) {
    Write-Host "[!] Python 3.10+ was not detected on this machine." -ForegroundColor Yellow
    Write-Host "[>] Auto-provisioning official Python 3.11 (user-level, no admin required)..." -ForegroundColor Cyan

    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        Write-Host "    Installing Python 3.11 via Windows Package Manager..." -ForegroundColor Gray
        winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    }

    $targetPy = "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    if (-not (Test-Path $targetPy)) {
        Write-Host "    Downloading official Python 3.11 64-bit installer from python.org..." -ForegroundColor Gray
        $pyInstaller = Join-Path $env:TEMP "python-3.11.9-amd64.exe"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-RestMethod -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe" -OutFile $pyInstaller
            Write-Host "    Running silent user-level installer..." -ForegroundColor Gray
            Start-Process -FilePath $pyInstaller -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1", "Include_launcher=1" -Wait
            Start-Sleep -Seconds 3
        } catch {
            Write-Warning "Direct installer download failed: $_"
        } finally {
            if (Test-Path $pyInstaller) { Remove-Item -Path $pyInstaller -Force -ErrorAction SilentlyContinue }
        }
    }

    if (Test-Path $targetPy) {
        $pythonExe = $targetPy
        Write-Host "[+] Python 3.11 provisioned successfully!" -ForegroundColor Green
    }
}

if (-not $pythonExe) {
    Write-Error "Python 3.10+ is required. Please install from https://www.python.org/downloads/ (check 'Add python.exe to PATH')."
    return
}

# ------------------------------------------------------------------
# 3. Create Virtual Environment (.venv)
# ------------------------------------------------------------------
if (-not (Test-Path $venvPy)) {
    Write-Host "[>] Creating isolated virtual environment (.venv)..." -ForegroundColor Cyan
    & $pythonExe -m venv (Join-Path $InstallDir ".venv")
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
        Write-Error "Failed to initialize .venv in $InstallDir."
        return
    }
    Write-Host "[+] Virtual environment initialized." -ForegroundColor Green
}

# ------------------------------------------------------------------
# 4. Install Project Python Dependencies (requirements.txt)
# ------------------------------------------------------------------
Write-Host ""
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "   INSTALLING PROJECT PYTHON DEPENDENCIES (requirements.txt)" -ForegroundColor Yellow
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "Packages: Django, Ultralytics YOLOv8, OpenCV, Pillow, Textual, RapidFuzz..." -ForegroundColor Gray
Write-Host ""

$reqFile = Join-Path $InstallDir "requirements.txt"
$hashFile = Join-Path $InstallDir ".venv\.reqs_hash"

# Upgrade pip first
Write-Host "[>] Ensuring pip is up to date..." -ForegroundColor Gray
& $venvPy -m pip install --upgrade pip --quiet

# Install requirements
Write-Host "[>] Running pip install -r requirements.txt..." -ForegroundColor Cyan
& $venvPy -m pip install -r $reqFile

if ($LASTEXITCODE -eq 0) {
    Write-Host "[+] All project Python dependencies successfully installed!" -ForegroundColor Green
    try {
        $reqHash = (Get-FileHash -Path $reqFile -Algorithm SHA256).Hash
        Set-Content -Path $hashFile -Value $reqHash -Force
    } catch {}
} else {
    Write-Warning "pip reported a non-zero exit code during package installation."
}

# ------------------------------------------------------------------
# 5. Automated Resource Provisioning (AI Models, DB & Config)
# ------------------------------------------------------------------
Write-Host ""
Write-Host "[>] Running automated system bootstrap (AI plate models & database)..." -ForegroundColor Cyan
$bootstrapPy = Join-Path $InstallDir "scripts\bootstrap.py"
& $venvPy $bootstrapPy

# ------------------------------------------------------------------
# 6. Create Windows Shortcuts (Desktop & Start Menu)
# ------------------------------------------------------------------
Write-Host ""
Write-Host "[>] Registering Windows shortcuts..." -ForegroundColor Cyan
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

# ------------------------------------------------------------------
# 6b. Register Global System-Wide 'itms' Command (PATH & WindowsApps)
# ------------------------------------------------------------------
Write-Host ""
Write-Host "[>] Configuring global system-wide 'itms' command..." -ForegroundColor Cyan
try {
    # Set persistent ITMS_HOME environment variable
    [Environment]::SetEnvironmentVariable("ITMS_HOME", $InstallDir, "User")
    $env:ITMS_HOME = $InstallDir

    # Add InstallDir to User PATH
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $pathParts = if ($userPath) { ($userPath -split ";") | Where-Object { $_ -ne "" } } else { @() }
    if ($pathParts -notcontains $InstallDir) {
        $newUserPath = if ($userPath) { "$InstallDir;$userPath" } else { $InstallDir }
        [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
        Write-Host "    [+] Added to User PATH: $InstallDir" -ForegroundColor Green
    }

    # Shim itms.cmd and itms.ps1 into WindowsApps if present (instant PATH for Windows 10/11)
    $winAppsDir = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
    if (Test-Path $winAppsDir) {
        Copy-Item -Path (Join-Path $InstallDir "itms.cmd") -Destination (Join-Path $winAppsDir "itms.cmd") -Force -ErrorAction SilentlyContinue
        Copy-Item -Path (Join-Path $InstallDir "itms.ps1") -Destination (Join-Path $winAppsDir "itms.ps1") -Force -ErrorAction SilentlyContinue
        Write-Host "    [+] Registered 'itms' launcher in $winAppsDir" -ForegroundColor Green
    }

    # Update current session PATH so 'itms' works immediately in this terminal!
    if (($env:PATH -split ";") -notcontains $InstallDir) {
        $env:PATH = "$InstallDir;" + $env:PATH
    }
    Write-Host "    [+] Global command active: Type 'itms' or 'itms --tui' anywhere" -ForegroundColor Green
} catch {
    Write-Warning "Could not register global command: $_"
}

Write-Host ""
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "   [+] ITMS VERIFICATION COPILOT READY FOR USE!" -ForegroundColor Green
Write-Host "====================================================================" -ForegroundColor Green
Write-Host "Location: $InstallDir" -ForegroundColor White
Write-Host "Launcher: Double-click 'ITMS Verification Copilot' on your Desktop" -ForegroundColor Yellow
Write-Host "Commands: 'itms'       -> Launches Web Operator Console (Default)" -ForegroundColor Cyan
Write-Host "          'itms --tui' -> Launches Terminal User Interface (TUI)" -ForegroundColor Cyan
Write-Host "          'itms status'-> Check system health & database" -ForegroundColor Gray
Write-Host "Database: SQLite (db.sqlite3)" -ForegroundColor White
Write-Host "Login:    admin / admin" -ForegroundColor White
Write-Host "====================================================================" -ForegroundColor Green
Write-Host ""

# ------------------------------------------------------------------
# 7. Launch Application
# ------------------------------------------------------------------
if (-not $NoLaunch) {
    Write-Host "[>] Starting ITMS Verification Copilot..." -ForegroundColor Cyan
    Write-Host "    Opening Web Console in your default browser..." -ForegroundColor Gray
    Start-Process -FilePath "$InstallDir\run.bat" -WorkingDirectory "$InstallDir"
}
