# Do not use [CmdletBinding()] to prevent PowerShell from consuming -v as -Verbose

# Determine Application Directory
if (Test-Path (Join-Path (Get-Location) "itms_cli.py")) {
    $AppDir = (Get-Location).Path
} elseif (Test-Path (Join-Path $PSScriptRoot "itms_cli.py")) {
    $AppDir = $PSScriptRoot
} elseif ($env:ITMS_HOME -and (Test-Path (Join-Path $env:ITMS_HOME "itms_cli.py"))) {
    $AppDir = $env:ITMS_HOME
} else {
    try {
        $regHome = [Environment]::GetEnvironmentVariable("ITMS_HOME", "User")
        if ($regHome -and (Test-Path (Join-Path $regHome "itms_cli.py"))) {
            $AppDir = $regHome
        }
    } catch {}
}
if (-not $AppDir) {
    $AppDir = $PSScriptRoot
}

# Determine Python Executable
$venvPy = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $venvPy = "python"
}

$cliScript = Join-Path $AppDir "itms_cli.py"
& $venvPy $cliScript @args
exit $LASTEXITCODE
