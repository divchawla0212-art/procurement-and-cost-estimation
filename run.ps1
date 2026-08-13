#Requires -Version 7.0
<#
.SYNOPSIS
    Run the app — FastAPI backend and the React dev server — from one terminal.

.DESCRIPTION
    Collapses step 7 of docs/SETUP.md ("two terminals, both in the repo root")
    into a single command, with the preflight checks that turn the three usual
    startup failures into a sentence instead of a stack trace: no virtualenv,
    no web dependencies, and a port already held by something else.

    Both servers stream into this console. Ctrl+C stops both — including the
    uvicorn reloader's worker and npm's node child, which a plain Stop-Process
    would orphan and leave holding the ports.

.PARAMETER ApiPort
    Port for the FastAPI backend. Default 8000. See the note on -ApiPort below
    before changing it.

.PARAMETER WebPort
    Port for the Vite dev server. Default 5173.

.PARAMETER NoBrowser
    Don't open a browser once the API reports healthy.

.PARAMETER NoReload
    Run uvicorn without --reload. Slightly faster startup, and it takes the
    reloader's child process out of the picture; use it if you are not editing
    Python.

.PARAMETER SkipWebInstall
    Don't offer to run `npm --prefix web install` when web/node_modules is
    missing — fail instead. For unattended runs.

.EXAMPLE
    .\run.ps1

.EXAMPLE
    .\run.ps1 -WebPort 5174 -NoBrowser

.NOTES
    If PowerShell refuses to run this with an execution-policy error, allow
    local scripts for your account once:

        Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$WebPort = 5173,
    [switch]$NoBrowser,
    [switch]$NoReload,
    [switch]$SkipWebInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Root = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$WebDir = Join-Path $Root 'web'
$ApiUrl = "http://127.0.0.1:$ApiPort"
$WebUrl = "http://localhost:$WebPort"

function Write-Step($Message) { Write-Host "  $Message" -ForegroundColor Cyan }
function Write-Ok($Message) { Write-Host "  $Message" -ForegroundColor Green }
function Write-Warn($Message) { Write-Host "  $Message" -ForegroundColor Yellow }
function Write-Bad($Message) { Write-Host "  $Message" -ForegroundColor Red }

function Get-PortHolder([int]$Port) {
    # Returns the owning process, or $null when the port is free.
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
    } catch {
        return $null   # no listener on that port
    }
    if (-not $conn) { return $null }
    try {
        return Get-Process -Id $conn.OwningProcess -ErrorAction Stop
    } catch {
        # A listener we cannot resolve to a process is still a listener.
        return [pscustomobject]@{ Id = $conn.OwningProcess; ProcessName = '<unknown>' }
    }
}

function Assert-PortFree([int]$Port, [string]$Label) {
    $holder = Get-PortHolder $Port
    if (-not $holder) { return }

    Write-Bad "Port $Port ($Label) is already in use by $($holder.ProcessName) (PID $($holder.Id))."
    Write-Host ""
    Write-Host "  Either stop it:" -ForegroundColor DarkGray
    Write-Host "      Stop-Process -Id $($holder.Id)" -ForegroundColor DarkGray
    Write-Host "  or run this script on a different port:" -ForegroundColor DarkGray
    if ($Label -eq 'web') {
        Write-Host "      .\run.ps1 -WebPort $($Port + 1)" -ForegroundColor DarkGray
    } else {
        Write-Host "      .\run.ps1 -ApiPort $($Port + 1)   # see the -ApiPort note in Get-Help .\run.ps1" -ForegroundColor DarkGray
    }
    Write-Host ""
    throw "Port $Port is not free."
}

# -- preflight ---------------------------------------------------------------

Write-Host ""
Write-Host "Tender Eval - starting API + web" -ForegroundColor White
Write-Host ""

if (-not (Test-Path $Python)) {
    Write-Bad "No virtualenv at .venv\Scripts\python.exe."
    Write-Host ""
    Write-Host "  Create it (docs/SETUP.md step 3):" -ForegroundColor DarkGray
    Write-Host "      python -m venv .venv" -ForegroundColor DarkGray
    Write-Host "      .venv\Scripts\python.exe -m pip install -e `".[dev]`"" -ForegroundColor DarkGray
    Write-Host ""
    throw "Virtualenv missing."
}
Write-Ok "virtualenv         .venv"

if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    Write-Bad "npm is not on PATH. Install Node.js LTS, then open a NEW terminal."
    throw "npm missing."
}

if (-not (Test-Path (Join-Path $WebDir 'node_modules'))) {
    if ($SkipWebInstall) {
        Write-Bad "web/node_modules is missing and -SkipWebInstall was given."
        throw "Web dependencies missing."
    }
    Write-Warn "web/node_modules is missing - installing now (one time, ~1 min)."
    & npm --prefix $WebDir install
    if ($LASTEXITCODE -ne 0) { throw "npm install failed with exit code $LASTEXITCODE." }
}
Write-Ok "web deps           web/node_modules"

# .env is genuinely optional: api/main.py calls load_dotenv() itself and the app
# serves every read screen without a provider key. Only ingestion needs one, so
# this is a warning and never a hard stop.
if (-not (Test-Path (Join-Path $Root '.env'))) {
    Write-Warn ".env not found - the UI runs, but ingestion needs a provider key."
    Write-Host "                     Copy-Item .env.example .env, then fill in ANTHROPIC_API_KEY." -ForegroundColor DarkGray
} else {
    Write-Ok ".env               present"
}

if ($ApiPort -ne 8000) {
    # web/vite.config.ts hardcodes proxy target http://127.0.0.1:8000. Moving the
    # API without editing that file gives a UI that loads and then 500s on /api.
    Write-Warn "-ApiPort $ApiPort will not work on its own: web/vite.config.ts proxies"
    Write-Host "                     /api to 127.0.0.1:8000. Edit that target to match." -ForegroundColor DarkGray
}

Assert-PortFree $ApiPort 'api'
Assert-PortFree $WebPort 'web'
Write-Ok "ports              $ApiPort, $WebPort free"
Write-Host ""

# -- launch ------------------------------------------------------------------

$script:Started = @()

function Stop-Tree($Proc) {
    # taskkill /T, not Stop-Process: uvicorn --reload runs a worker child and
    # npm runs node as a child. Killing only the parent leaves the real server
    # alive and still holding the port.
    if ($null -eq $Proc) { return }
    try { if ($Proc.HasExited) { return } } catch { return }
    & taskkill.exe /PID $Proc.Id /T /F *> $null
}

function Stop-All {
    foreach ($p in $script:Started) { Stop-Tree $p }
    $script:Started = @()
}

try {
    $uvicornArgs = @('-m', 'uvicorn', 'api.main:app', '--port', "$ApiPort")
    if (-not $NoReload) { $uvicornArgs += '--reload' }

    Write-Step "api   $ApiUrl"
    $api = Start-Process -FilePath $Python -ArgumentList $uvicornArgs `
        -WorkingDirectory $Root -NoNewWindow -PassThru
    $script:Started += $api

    Write-Step "web   $WebUrl"
    $web = Start-Process -FilePath 'npm.cmd' `
        -ArgumentList @('--prefix', $WebDir, 'run', 'dev', '--', '--port', "$WebPort") `
        -WorkingDirectory $Root -NoNewWindow -PassThru
    $script:Started += $web

    # Wait for the API rather than guessing with a sleep — /api/health is on the
    # middleware's public allowlist, so it answers 200 without a session.
    $deadline = (Get-Date).AddSeconds(45)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
        if ($api.HasExited) { throw "The API exited during startup (code $($api.ExitCode)). See its output above." }
        try {
            $res = Invoke-WebRequest -Uri "$ApiUrl/api/health" -UseBasicParsing -TimeoutSec 2
            if ($res.StatusCode -eq 200) { $healthy = $true; break }
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }

    Write-Host ""
    if ($healthy) {
        Write-Ok "API healthy. Open $WebUrl"
    } else {
        Write-Warn "API did not answer /api/health within 45s - starting anyway, check the output above."
    }
    Write-Host "  Ctrl+C stops both." -ForegroundColor DarkGray
    Write-Host ""

    if ($healthy -and -not $NoBrowser) { Start-Process $WebUrl | Out-Null }

    # Hold the console until either child dies or the user interrupts. Ctrl+C
    # throws out of this loop, which is what gets us into `finally`.
    while ($true) {
        if ($api.HasExited) { Write-Warn "API exited (code $($api.ExitCode))."; break }
        if ($web.HasExited) { Write-Warn "Web dev server exited (code $($web.ExitCode))."; break }
        Start-Sleep -Milliseconds 500
    }
} finally {
    Write-Host ""
    Write-Step "stopping..."
    Stop-All
    Write-Ok "stopped."
    Write-Host ""
}
