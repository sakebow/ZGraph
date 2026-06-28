# test_me.ps1
#
# PowerShell equivalent of `make integration`: run all tests marked
# @pytest.mark.integration with verbose output.
#
# Usage:
#   .\test_me.ps1                     # run directly
#   .\test_me.ps1 -LogFile run.log    # also tee full output to run.log
#
# Equivalent to Makefile target:
#   PYTHONPATH=. python -m pytest tests/ -v -m integration --tb=short

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

# ------------------------------------------------------------
# Parse arguments
# ------------------------------------------------------------
$LogFile = $null
for ($i = 0; $i -lt $args.Count; $i++) {
    if ($args[$i] -eq "-LogFile" -and ($i + 1) -lt $args.Count) {
        $LogFile = $args[$i + 1]
        break
    }
    if ($args[$i].StartsWith("-LogFile=")) {
        $LogFile = $args[$i].Substring("-LogFile=".Length)
        break
    }
}

# ------------------------------------------------------------
# Pick a python interpreter
# ------------------------------------------------------------
$PythonExe = $null
foreach ($candidate in @("python", "py", "python3")) {
    try {
        & $candidate --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $PythonExe = $candidate
            break
        }
    } catch {
        # ignore and try next
    }
}
if (-not $PythonExe) {
    Write-Host "ERROR: python interpreter not found on PATH" -ForegroundColor Red
    Write-Host "Hint   : install Python 3.11+ or activate your conda/miniconda env." -ForegroundColor Yellow
    exit 2
}

# ------------------------------------------------------------
# Set PYTHONPATH
# ------------------------------------------------------------
$env:PYTHONPATH = $ScriptDir

# ------------------------------------------------------------
# Header: environment and version info
# ------------------------------------------------------------
$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Write-Host ""
Write-Host "============================================================"
Write-Host " ZGraph integration test runner"
Write-Host "============================================================"
Write-Host (" Script dir : {0}" -f $ScriptDir)
Write-Host (" Python     : {0}" -f $PythonExe)
Write-Host (" PYTHONPATH : {0}" -f $env:PYTHONPATH)
Write-Host (" Started at : {0}" -f $timestamp)
if ($LogFile) {
    Write-Host (" Log file   : {0}" -f $LogFile) -ForegroundColor Cyan
}
Write-Host ""

# Show pytest version for diagnostics
Write-Host ">> pytest version:"
$pytestVersionOutput = & $PythonExe -m pytest --version 2>&1
foreach ($line in $pytestVersionOutput) {
    Write-Host ("   {0}" -f $line.TrimEnd())
}
Write-Host ""

# ------------------------------------------------------------
# Run tests
# ------------------------------------------------------------
Write-Host ">> Running: $PythonExe -m pytest tests/ -v -m integration --tb=short" -ForegroundColor Yellow
Write-Host ""

$startedAt = Get-Date
$exitCode = 0

# When -LogFile is given, write the same content to file with explicit
# UTF-8 encoding. We use a manual loop instead of Tee-Object because
# Tee-Object on Windows PowerShell 5.1 silently writes UTF-16 LE,
# which produces a mixed-encoding file.
if ($LogFile) {
    $logDir = Split-Path -Parent $LogFile
    if ([string]::IsNullOrEmpty($logDir)) {
        $logDir = "."
    }
    if (-not (Test-Path $logDir)) {
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    }
    $resolvedLogFile = Join-Path $logDir (Split-Path -Leaf $LogFile)

    # Delete any prior file with the same name so we always start clean
    if (Test-Path $resolvedLogFile) {
        Remove-Item -Path $resolvedLogFile -Force
    }

    $logStream = [System.IO.StreamWriter]::new($resolvedLogFile, $true, (New-Object System.Text.UTF8Encoding $false))
    try {
        # Write header to log file
        $headerLines = @(
            "============================================================"
            " ZGraph integration test runner"
            "============================================================"
            (" Script dir : {0}" -f $ScriptDir)
            (" Python     : {0}" -f $PythonExe)
            (" PYTHONPATH : {0}" -f $env:PYTHONPATH)
            (" Started at : {0}" -f $timestamp)
            ""
            ">> pytest version:"
        )
        foreach ($pytestLine in $pytestVersionOutput) {
            $headerLines += ("   {0}" -f $pytestLine.TrimEnd())
        }
        $headerLines += ""
        $headerLines += (">> Running: $PythonExe -m pytest tests/ -v -m integration --tb=short")
        $headerLines += ""
        foreach ($line in $headerLines) {
            $logStream.WriteLine($line)
        }

        # Run pytest line by line: write to console AND to log file with explicit UTF-8
        & $PythonExe -m pytest tests/ -v -m integration --tb=short 2>&1 | ForEach-Object {
            Write-Host $_
            $logStream.WriteLine($_)
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $logStream.Close()
    }
    $LogFile = $resolvedLogFile
} else {
    & $PythonExe -m pytest tests/ -v -m integration --tb=short
    $exitCode = $LASTEXITCODE
}

$elapsed = (Get-Date) - $startedAt

# ------------------------------------------------------------
# Summary
# ------------------------------------------------------------
Write-Host ""
Write-Host "============================================================"
if ($exitCode -eq 0) {
    Write-Host " RESULT: ALL TESTS PASSED" -ForegroundColor Green
} else {
    Write-Host (" RESULT: TESTS FAILED (exit code: {0})" -f $exitCode) -ForegroundColor Red
}
Write-Host (" Elapsed : {0:N1}s" -f $elapsed.TotalSeconds)
if ($LogFile) {
    Write-Host (" Log     : {0}" -f $LogFile) -ForegroundColor Cyan
}
Write-Host "============================================================"
Write-Host ""

exit $exitCode
