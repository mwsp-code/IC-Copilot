param(
    [int]$Port = 8501,
    [switch]$Headless
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

$pythonCandidates = @(
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    (Join-Path $repoRoot "venv\Scripts\python.exe")
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $python) {
    $pythonCommand = Get-Command python -ErrorAction Stop
    $python = $pythonCommand.Source
}

Write-Host "Checking Python entrypoints..."
& $python -m compileall -q app.py server.py equity_research scripts
if ($LASTEXITCODE -ne 0) {
    throw "Preflight failed. Fix the syntax error shown above before starting Streamlit."
}

$streamlitArgs = @(
    "-m",
    "streamlit",
    "run",
    "app.py",
    "--server.port=$Port"
)
if ($Headless) {
    $streamlitArgs += "--server.headless=true"
}

Write-Host "Starting IC Copilot at http://localhost:$Port"
& $python @streamlitArgs
exit $LASTEXITCODE
