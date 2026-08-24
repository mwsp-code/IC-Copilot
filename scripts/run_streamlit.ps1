param(
    [int]$Port = 8501,
    [switch]$Headless,
    [string]$PythonPath = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

function Test-AppPython([string]$Candidate) {
    if (-not $Candidate -or -not (Test-Path -LiteralPath $Candidate)) {
        return $false
    }
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $Candidate -c "import sys; import streamlit, pypdf; raise SystemExit(0 if sys.version_info >= (3, 9) else 4)" *> $null
        return $LASTEXITCODE -eq 0
    }
    finally {
        $ErrorActionPreference = $previousErrorPreference
    }
}

$pythonCandidates = @()
if ($PythonPath) {
    $pythonCandidates += $PythonPath
}
$pythonCandidates += @(
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    (Join-Path $repoRoot "venv\Scripts\python.exe")
)
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $pythonCandidates += $pythonCommand.Source
}
$python = $pythonCandidates |
    Select-Object -Unique |
    Where-Object { Test-AppPython $_ } |
    Select-Object -First 1
if (-not $python) {
    Write-Host @"
No usable IC Copilot Python runtime was found. Python 3.9+ with Streamlit and
pypdf is required. OS-keychain support is optional at launch. From this
repository, run:

  .\scripts\quality.cmd -Bootstrap

Or pass the interpreter already used by Streamlit:

  .\scripts\start_ic_copilot.cmd -PythonPath C:\path\to\python.exe
"@
    exit 3
}

Write-Host "Using Python runtime: $python"
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
