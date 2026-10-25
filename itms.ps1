# ITMS Verification Copilot PowerShell CLI Controller
param(
    [Parameter(Position=0)]
    [string]$Action = "status",

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

$Arguments = @("manage.py", "itms_ctl", $Action)
if ($SubAction) {
    $Arguments += $SubAction
}
if ($RemainingArgs) {
    $Arguments += $RemainingArgs
}

& $PythonExe $Arguments
