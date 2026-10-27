# ITMS Verification Copilot PowerShell CLI Controller
param(
    [Parameter(Position=0)]
    [string]$Action = "",

    [Parameter(Position=1)]
    [string]$SubAction = "",

    [Parameter(ValueFromRemainingArguments=$true)]
    $RemainingArgs
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$PythonExe = Join-Path $ScriptDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

# 1. Detect if user specified version (-v, --version, version, or PowerShell -Verbose switch)
if ($PSBoundParameters.ContainsKey('Verbose') -or $Action -in @("version", "--version", "-v")) {
    & $PythonExe "itms_cli.py" "version"
    exit $LASTEXITCODE
}

# 2. Detect if user specified help (-h, --help, help)
if ($Action -in @("help", "--help", "-h", "/?")) {
    & $PythonExe "itms_cli.py" "help"
    exit $LASTEXITCODE
}

# 3. Explicit TUI request
if ($Action -in @("tui", "--tui", "-t")) {
    $Arguments = @("manage.py", "run_tui")
    if ($RemainingArgs) {
        $Arguments += $RemainingArgs
    }
    & $PythonExe $Arguments
    exit $LASTEXITCODE
}

# 4. Pass-through Django management command
if ($Action -eq "manage") {
    $Arguments = @("manage.py")
    if ($SubAction) { $Arguments += $SubAction }
    if ($RemainingArgs) { $Arguments += $RemainingArgs }
    & $PythonExe $Arguments
    exit $LASTEXITCODE
}

# 5. Default action (no args) or dispatch all other commands to itms_cli.py
$Arguments = @("itms_cli.py")
if ($Action) {
    $Arguments += $Action
}
if ($SubAction) {
    $Arguments += $SubAction
}
if ($RemainingArgs) {
    $Arguments += $RemainingArgs
}

& $PythonExe $Arguments
exit $LASTEXITCODE
