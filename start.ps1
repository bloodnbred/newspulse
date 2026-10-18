<#
.SYNOPSIS
    One command to set up and run NewsPulse Nifty on a fresh machine.

.DESCRIPTION
    Checks what is installed, installs what is missing, builds the data
    warehouse if it has not been built, then starts the API and the dashboard
    and opens a browser.

    Safe to run more than once. Every step checks before it acts, so a second
    run skips the installs and goes straight to starting the servers.

.PARAMETER Rebuild
    Discard the existing warehouse and rebuild it from scratch.

.PARAMETER Sample
    Only process this many rows per dataset. Produces a smaller warehouse in a
    few minutes instead of an hour, and every page still works - the numbers
    are just from less data. Good for a first look.

.PARAMETER NoBrowser
    Do not open a browser. Useful on a remote machine or over SSH.

.EXAMPLE
    .\start.ps1
    .\start.ps1 -Sample 50000
    .\start.ps1 -Rebuild
#>
[CmdletBinding()]
param(
    [switch]$Rebuild,
    [int]$Sample = 0,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

# --- output helpers --------------------------------------------------------
function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor DarkGreen }
function Warn($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "`nFAILED: $msg" -ForegroundColor Red; exit 1 }

$script:VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
$PyArgs = if ($python.Count -gt 1) { $python[1..($python.Count - 1)] } else { @() }

# --- 1. Python -------------------------------------------------------------
Step "Checking Python"
# Ask Python itself whether it is new enough, rather than parsing version
# strings. `python --version` is a plain string, `py -3` needs a launcher, and
# a machine may have several installs - so each candidate is asked to run a
# one-line check and the first that agrees is used.
$python = $null
$candidates = @(
    @{ Exe = 'python';   Args = @() },
    @{ Exe = 'python3';  Args = @() },
    @{ Exe = 'py';       Args = @('-3') }
)
foreach ($c in $candidates) {
    if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    $all = @($c.Exe) + $c.Args
    try {
        & $all @('-c', 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)') 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $python = $all; break }
    } catch { }
}
if (-not $python) {
    Die @"
Python 3.11 or newer is required and was not found.

  Install it from https://www.python.org/downloads/ and tick
  "Add python.exe to PATH" during setup, then run this again.
"@
}
Ok "using $($python -join ' ')"

# --- 2. The virtual environment -------------------------------------------
Step "Preparing the Python environment"
if (-not (Test-Path $script:VenvPy)) {
    Write-Host "    creating .venv (first run only, takes a minute)"
    & $python @PyArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Die "could not create .venv" }
}
Ok ".venv present"

Step "Installing Python packages"
& $script:VenvPy -m pip install --quiet --upgrade pip
& $script:VenvPy -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Die "pip install failed. Scroll up for the error." }
Ok "requirements installed"

# --- 3. Node, for the dashboard -------------------------------------------
Step "Checking Node.js (needed for the dashboard)"
$nodeOk = $false
try { $null = & node --version 2>$null; if ($LASTEXITCODE -eq 0) { $nodeOk = $true } } catch { }
if (-not $nodeOk) {
    Warn "Node.js 18+ was not found on PATH."
    Warn "The dashboard needs it. Install from https://nodejs.org/ then re-run."
    Die "Node.js is required for the dashboard but is not installed."
}
$nodeVersion = (& node --version).TrimStart('v')
Ok "node $nodeVersion"

Step "Installing dashboard packages"
# Three things here are deliberate and each one cost a failed run.
#
# 1. `npm.cmd`, not `npm`. PowerShell resolves `npm` to the `npm.ps1` shim in
#    the Node install directory, and splatting an argument array into that shim
#    makes it invoke `npm config` instead of `npm ci` - so the install fails
#    with a usage error and nothing resembling the real problem.
# 2. An explicit branch, not `@npmArgs`. Same cause: the splat is what breaks.
# 3. Not `--silent`. npm's own error is the only thing that says WHY an install
#    failed, and silencing it means a failed setup prints "npm install failed"
#    with nothing underneath. Quiet on success, the log on failure.
$npmLog = Join-Path $env:TEMP 'newspulse-npm.log'
Push-Location (Join-Path $Root 'web')
try {
    if (Test-Path 'package-lock.json') {
        & npm.cmd ci *> $npmLog
        $npmWhat = 'ci'
    } else {
        & npm.cmd install *> $npmLog
        $npmWhat = 'install'
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    --- last lines of npm output ---" -ForegroundColor Red
        Get-Content $npmLog -Tail 25 | ForEach-Object { Write-Host "    $_" }
        Die "npm $npmWhat failed. The full log is at $npmLog"
    }
} finally { Pop-Location }
Ok "web/node_modules present"

# --- 4. The data ----------------------------------------------------------
$warehouse = Join-Path $Root 'warehouse\dwm.duckdb'
Step "Checking the data warehouse"
if ($Rebuild) {
    Warn "-Rebuild was given; deleting the existing warehouse"
    Remove-Item $warehouse -Force -ErrorAction SilentlyContinue
}
if (Test-Path $warehouse) {
    $sizeMb = [math]::Round((Get-Item $warehouse).Length / 1MB, 1)
    Ok "warehouse present ($sizeMb MB)"
} else {
    Warn "No warehouse yet. Building it now."
    Warn "This downloads news and price data and can take 30-60 minutes."
    Warn "  For a fast first look, stop and run:  .\start.ps1 -Sample 50000"
    Write-Host ""
    $args_ = @('-m', 'dwm', 'all')
    if ($Sample -gt 0) { $args_ += @('--sample', "$Sample") }
    & $script:VenvPy @args_
    if ($LASTEXITCODE -ne 0) { Die "the data build failed. Scroll up for the error." }
    Ok "warehouse built"
}

# --- 5. Run ----------------------------------------------------------------
Step "Starting the API and the dashboard"

# The API opens a snapshot copy of the warehouse rather than the live file, so
# the tests can open the original while the server is running.
$serve = Join-Path $Root 'warehouse\dwm.serve.duckdb'
if (Test-Path $warehouse) { Copy-Item $warehouse $serve -Force }

$api = Start-Process -FilePath $script:VenvPy `
    -ArgumentList @('-m', 'dwm', 'serve') `
    -WorkingDirectory $Root -PassThru `
    -RedirectStandardOutput (Join-Path $env:TEMP 'newspulse-api.log') `
    -RedirectStandardError  (Join-Path $env:TEMP 'newspulse-api.err')

$web = Start-Process -FilePath 'npm.cmd' `
    -ArgumentList @('run', 'dev') `
    -WorkingDirectory (Join-Path $Root 'web') -PassThru `
    -RedirectStandardOutput (Join-Path $env:TEMP 'newspulse-web.log') `
    -RedirectStandardError  (Join-Path $env:TEMP 'newspulse-web.err')

Write-Host "    waiting for both to answer..."
$apiUp = $false; $webUp = $false
for ($i = 0; $i -lt 60 -and (-not $apiUp -or -not $webUp); $i++) {
    Start-Sleep -Seconds 1
    if (-not $apiUp) {
        try { $null = Invoke-WebRequest 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 3; $apiUp = $true } catch { }
    }
    if (-not $webUp) {
        try { $null = Invoke-WebRequest 'http://localhost:5173/' -UseBasicParsing -TimeoutSec 3; $webUp = $true } catch { }
    }
}

Write-Host ""
if ($apiUp -and $webUp) {
    Ok "API      http://127.0.0.1:8000   (add /docs for the endpoint list)"
    Ok "Dashboard http://localhost:5173"
} elseif (-not $apiUp) {
    Warn "The API did not come up. See $env:TEMP\newspulse-api.err"
} else {
    Warn "The dashboard did not come up. See $env:TEMP\newspulse-web.err"
}

if (-not $NoBrowser -and $webUp) { Start-Process 'http://localhost:5173/' }

Write-Host ""
Write-Host "  Press Ctrl+C to stop both." -ForegroundColor Yellow
Write-Host ""

try {
    while ($true) {
        Start-Sleep -Seconds 2
        foreach ($p in @($api, $web)) {
            if ($p -and $p.HasExited) {
                Write-Host "A process exited. Stopping the other." -ForegroundColor Yellow
                foreach ($q in @($api, $web)) { if ($q -and -not $q.HasExited) { $q.Kill() } }
                exit 1
            }
        }
    }
} finally {
    foreach ($p in @($api, $web)) {
        if ($p -and -not $p.HasExited) {
            try { $p.Kill() } catch { }
        }
    }
}