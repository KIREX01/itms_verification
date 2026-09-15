[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

# Determine Application Directory
$AppDir = $env:ITMS_HOME
if (-not $AppDir) {
    try {
        $AppDir = [Environment]::GetEnvironmentVariable("ITMS_HOME", "User")
    } catch {}
}
if (-not $AppDir -or -not (Test-Path (Join-Path $AppDir "itms_cli.py"))) {
    $AppDir = $PSScriptRoot
}

# Determine Python Executable
$venvPy = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    $venvPy = "python"
}

$cliScript = Join-Path $AppDir "itms_cli.py"
& $venvPy $cliScript @CliArgs
exit $LASTEXITCODE
