param(
    [string]$PythonPath = "",
    [string]$PytestTargets = "",
    [switch]$Bootstrap,
    [switch]$RuntimeOnly,
    [switch]$SkipBenchmark
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot

function Resolve-BasePython {
    if ($PythonPath) {
        if (-not (Test-Path -LiteralPath $PythonPath)) {
            throw "The requested Python interpreter does not exist: $PythonPath"
        }
        return (Resolve-Path -LiteralPath $PythonPath).Path
    }
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return $venvPython
    }
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw "Python was not found. Install Python 3.9+ or pass -PythonPath."
}

$python = Resolve-BasePython
if ($Bootstrap) {
    $venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host "Creating isolated development environment in .venv..."
        & $python -m venv (Join-Path $repoRoot ".venv")
        if ($LASTEXITCODE -ne 0) {
            throw "Python could not create the development environment."
        }
    }
    $python = $venvPython
    Write-Host "Installing project and development dependencies..."
    & $python -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "pip could not be upgraded. Check network/proxy access and retry."
    }
    & $python -m pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        throw "Development dependencies could not be installed. Check network/proxy access and retry."
    }
}

$runtimeProbe = @'
import importlib.util
import json
import sys

required = ('pytest', 'ruff', 'streamlit', 'pypdf')
missing = [name for name in required if importlib.util.find_spec(name) is None]
optional = {'keyring': bool(importlib.util.find_spec('keyring'))}
print(json.dumps({
    'python': sys.executable,
    'version': '.'.join(map(str, sys.version_info[:3])),
    'missing': missing,
    'optional': optional,
}))
raise SystemExit(0 if sys.version_info >= (3, 9) and not missing else 3)
'@

Write-Host "Verifying the selected test runtime..."
& $python -c $runtimeProbe
if ($LASTEXITCODE -ne 0) {
    Write-Host @"
The selected runtime is missing Python 3.9+ or required development packages.
Run this command again with -Bootstrap, or pass the interpreter used by your
working Streamlit installation with -PythonPath.
"@
    exit 3
}

if ($RuntimeOnly) {
    Write-Host "The IC Copilot development runtime is ready."
    exit 0
}

Write-Host "Compiling all Python entrypoints..."
& $python -m compileall -q app.py server.py equity_research scripts
if ($LASTEXITCODE -ne 0) {
    throw "Python compilation failed."
}

Write-Host "Running static checks..."
& $python -m ruff check app.py server.py equity_research scripts tests
if ($LASTEXITCODE -ne 0) {
    throw "Static checks failed."
}

if ($PytestTargets) {
    Write-Host "Running the selected pytest and Streamlit AppTest targets..."
} else {
    Write-Host "Running the complete pytest and Streamlit AppTest suite..."
}
$pytestTempRoot = Join-Path (
    Join-Path $repoRoot ".pytest_cache"
) ("quality-" + [guid]::NewGuid().ToString("N"))
$pytestArgs = @(
    "-m",
    "pytest",
    "-q",
    "--basetemp=$pytestTempRoot"
)
if ($PytestTargets) {
    $pytestArgs += @(
        $PytestTargets.Split(",") |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ }
    )
    Write-Host "Running focused pytest targets: $PytestTargets"
}
& $python @pytestArgs
$pytestExitCode = $LASTEXITCODE
$resolvedCacheRoot = [IO.Path]::GetFullPath(
    (Join-Path $repoRoot ".pytest_cache")
).TrimEnd([IO.Path]::DirectorySeparatorChar)
$resolvedPytestTemp = [IO.Path]::GetFullPath($pytestTempRoot)
if (
    (Test-Path -LiteralPath $resolvedPytestTemp) -and
    $resolvedPytestTemp.StartsWith(
        $resolvedCacheRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase
    )
) {
    Remove-Item -LiteralPath $resolvedPytestTemp -Recurse -Force
}
if ($pytestExitCode -ne 0) {
    throw "The test suite failed."
}

if (-not $SkipBenchmark) {
    Write-Host "Running the offline research-quality benchmark..."
    & $python scripts\run_benchmark.py
    if ($LASTEXITCODE -ne 0) {
        throw "The offline benchmark failed."
    }
}

Write-Host "All IC Copilot quality checks passed."
