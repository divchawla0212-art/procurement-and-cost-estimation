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

    PORTS. Both ports are made available before anything is launched, rather
    than merely checked. A port held by this repo's own leftovers — a uvicorn or
    vite that outlived its terminal, or a Ctrl+C that missed a child — is
    reclaimed automatically, because restarting a dev server this script started
    loses nothing. A port held by anything else is reported and refused, not
    killed: 8000 is a popular default, and silently taskkilling whatever answers
    on it eventually takes out a database or a colleague's service. Pass -Force
    to take those too.

    Reclaiming kills the outermost process of the holder's tree rather than the
    listener itself, since uvicorn --reload and npm run dev are supervisors that
    would otherwise respawn a replacement onto the same port.

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

.PARAMETER NoAuth
    Start with authentication disabled (AUTH_DISABLED=1), so the front end
    opens without a sign-in. Every request is served as the first administrator
    in auth.json, or as DEV_USER_EMAIL if that is set. Development only -- do
    not use it anywhere another person can reach the port.

.PARAMETER SkipWebInstall
    Don't offer to run `npm --prefix web install` when web/node_modules is
    missing — fail instead. For unattended runs.

.PARAMETER Force
    Also reclaim a port held by a process this script did not start. Without it
    such a port is reported, with its command line, and the run stops. With it
    the holder's process tree is killed. Read the command line the refusal
    prints before reaching for this.

.EXAMPLE
    .\run.ps1

.EXAMPLE
    .\run.ps1 -WebPort 5174 -NoBrowser

.EXAMPLE
    # A previous run left uvicorn holding 8000: reclaimed automatically.
    .\run.ps1

.EXAMPLE
    # Something else holds 8000 and you have read what it is.
    .\run.ps1 -Force

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
    [switch]$NoAuth,
    [switch]$SkipWebInstall,
    [switch]$Force
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

# -- ports -------------------------------------------------------------------
#
# The common startup failure is this script's *own* leftovers: a previous run
# whose uvicorn or vite outlived its terminal, or a Ctrl+C that missed a child.
# Those are reclaimed automatically — nothing is lost by restarting a dev server
# this repo started.
#
# Anything else is refused rather than killed. Port 8000 is a popular default,
# and a script that silently taskkills whatever answers on it will one day take
# out a database, a colleague's service, or an unrelated container. `-Force`
# says "yes, that one too", deliberately, per run.

function Get-ProcessInfo([int]$TargetId) {
    try {
        return Get-CimInstance Win32_Process -Filter "ProcessId = $TargetId" -ErrorAction Stop
    } catch {
        return $null
    }
}

function Get-SelfAncestry {
    # This process and every ancestor of it. Nothing here may ever be killed:
    # the walk in Get-ReclaimTarget climbs parent links, and the terminal running
    # this script has the repo path on its own command line too — without this
    # guard a stale-port cleanup could close the window it is printing into.
    $ids = @($PID)
    $current = $PID
    for ($hop = 0; $hop -lt 12; $hop++) {
        $info = Get-ProcessInfo $current
        if (-not $info) { break }
        $parentId = [int]$info.ParentProcessId
        if ($parentId -le 0 -or $ids -contains $parentId) { break }
        $ids += $parentId
        $current = $parentId
    }
    return $ids
}

function Test-OursCommandLine([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $false }

    # The API is matched on its ASGI target alone, and that is not laziness:
    # a venv's python.exe reports its *base* interpreter on the command line
    # (anaconda, pyenv, a symlinked venv), so the observed line is
    #
    #     "C:\...\anaconda3\python.exe" -m uvicorn api.main:app --port 8000 --reload
    #
    # with the repo path nowhere in it. Requiring the path here would classify
    # this script's own leftover API as a stranger and refuse to reclaim it,
    # which is the single case this whole section exists for.
    #
    # `api.main:app` is specific enough to carry that on its own. The one thing
    # it also matches is the same application served from a *different worktree*
    # of this repo — which is deliberate: two checkouts cannot share a port
    # anyway, and it is still this app rather than someone else's service.
    if ($CommandLine -match 'api\.main:app') { return $true }

    # node and npm are generic, so the web server has to prove it is this
    # checkout — and unlike python it always can, because vite is resolved
    # through web/node_modules and the path is right there on the line.
    if (-not $CommandLine.ToLowerInvariant().Contains($Root.ToLowerInvariant())) { return $false }
    return $CommandLine -match 'vite|npm'
}

function Get-PortHolder([int]$Port) {
    # Returns the listening process, or $null when the port is free.
    try {
        $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1
    } catch {
        return $null   # no listener on that port
    }
    if (-not $conn) { return $null }

    $holderId = [int]$conn.OwningProcess
    $info = Get-ProcessInfo $holderId
    # A listener we cannot resolve is still a listener, and it is never "ours":
    # with no command line there is no evidence it belongs to this checkout.
    return [pscustomobject]@{
        Id          = $holderId
        ProcessName = if ($info) { $info.Name } else { '<unknown>' }
        CommandLine = if ($info) { $info.CommandLine } else { $null }
        IsOurs      = if ($info) { Test-OursCommandLine $info.CommandLine } else { $false }
    }
}

function Get-ReclaimTarget($Holder, $SelfIds) {
    # Climb to the outermost ancestor that is still one of ours.
    #
    # `uvicorn --reload` is a supervisor with a worker child, and `npm run dev`
    # is cmd.exe -> node -> node. The listener is the innermost of those, and
    # killing only it lets the supervisor respawn a replacement onto the same
    # port — so the port never actually frees. Killing the outermost with
    # taskkill /T takes the whole tree at once.
    $targetId = $Holder.Id
    $current = Get-ProcessInfo $Holder.Id
    for ($hop = 0; $hop -lt 6; $hop++) {
        if (-not $current) { break }
        $parentId = [int]$current.ParentProcessId
        if ($parentId -le 0) { break }
        if ($SelfIds -contains $parentId) { break }     # never cross into our own chain
        $parent = Get-ProcessInfo $parentId
        if (-not $parent) { break }
        if (-not (Test-OursCommandLine $parent.CommandLine)) { break }
        $targetId = $parentId
        $current = $parent
    }
    return $targetId
}

function Wait-PortFree([int]$Port, [int]$TimeoutSeconds = 10) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-PortHolder $Port)) { return $true }
        Start-Sleep -Milliseconds 250
    }
    return $false
}

function Deny-Port($Holder, [int]$Port, [string]$Label) {
    Write-Bad "Port $Port ($Label) is held by $($Holder.ProcessName) (PID $($Holder.Id)), which this script did not start."
    if ($Holder.CommandLine) {
        Write-Host "      $($Holder.CommandLine)" -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "  Stop it yourself:" -ForegroundColor DarkGray
    Write-Host "      Stop-Process -Id $($Holder.Id)" -ForegroundColor DarkGray
    Write-Host "  or let this script take it (it will be killed, so read the line above first):" -ForegroundColor DarkGray
    Write-Host "      .\run.ps1 -Force" -ForegroundColor DarkGray
    Write-Host "  or run on a different port:" -ForegroundColor DarkGray
    if ($Label -eq 'web') {
        Write-Host "      .\run.ps1 -WebPort $($Port + 1)" -ForegroundColor DarkGray
    } else {
        Write-Host "      .\run.ps1 -ApiPort $($Port + 1)   # see the -ApiPort note in Get-Help .\run.ps1" -ForegroundColor DarkGray
    }
    Write-Host ""
    throw "Port $Port is not free."
}

function Initialize-Port([int]$Port, [string]$Label) {
    # Attempts, not one shot: a supervisor can respawn a worker onto the port
    # between the kill and the re-check, and the second pass then sees the new
    # holder rather than reporting a stale success.
    $selfIds = Get-SelfAncestry

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $holder = Get-PortHolder $Port
        if (-not $holder) {
            if ($attempt -gt 1) { Write-Ok "port $Port ($Label) reclaimed" }
            return
        }

        if ($selfIds -contains $holder.Id) {
            # Only reachable if this script is somehow already serving the port.
            Write-Bad "Port $Port ($Label) is held by this script's own process tree (PID $($holder.Id))."
            throw "Port $Port is not free."
        }

        if (-not ($holder.IsOurs -or $Force)) { Deny-Port $holder $Port $Label }

        $why = if ($holder.IsOurs) { 'left over from a previous run' } else { 'taken by -Force' }
        Write-Warn "port $Port ($Label) held by $($holder.ProcessName) (PID $($holder.Id)) - $why, reclaiming."

        $targetId = Get-ReclaimTarget $holder $selfIds
        if ($targetId -ne $holder.Id) {
            Write-Host "                     killing the supervisor (PID $targetId) so it cannot respawn." -ForegroundColor DarkGray
        }
        & taskkill.exe /PID $targetId /T /F *> $null

        if (Wait-PortFree $Port) {
            Write-Ok "port $Port ($Label) reclaimed"
            return
        }
    }

    $stubborn = Get-PortHolder $Port
    Write-Bad "Port $Port ($Label) is still held after 3 attempts by $($stubborn.ProcessName) (PID $($stubborn.Id))."
    Write-Host "  It may need elevation, or it may be respawning from a supervisor outside this repo." -ForegroundColor DarkGray
    Write-Host ""
    throw "Port $Port could not be freed."
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

Initialize-Port $ApiPort 'api'
Initialize-Port $WebPort 'web'
Write-Ok "ports              $ApiPort, $WebPort free"

# Set for this process, so the uvicorn child inherits it. Not persisted: a new
# terminal is authenticated again, which is the right default for a switch that
# turns authentication off.
if ($NoAuth) {
    $env:AUTH_DISABLED = '1'
    Write-Warn "-NoAuth: authentication is DISABLED. Every request is served as"
    Write-Host "                     the first admin in auth.json. Development only." -ForegroundColor DarkGray
} else {
    # Clear it, so a value left over in this shell cannot silently disable auth
    # on a run that did not ask for it.
    Remove-Item Env:\AUTH_DISABLED -ErrorAction SilentlyContinue
}
Write-Host ""

# -- launch ------------------------------------------------------------------

$script:Started = @()

function Stop-Tree($Proc) {
    # taskkill /T, not Stop-Process: uvicorn --reload runs a worker child and
    # npm runs node as a child. Killing only the parent leaves the real server
    # alive and still holding the port.
    #
    # This only works while the parent is still alive. Once it has exited its
    # PID no longer names a tree to walk, so an already-dead parent is skipped
    # here and its survivors are collected by Stop-LeftoverListeners below.
    if ($null -eq $Proc) { return }
    try { if ($Proc.HasExited) { return } } catch { return }
    & taskkill.exe /PID $Proc.Id /T /F *> $null
}

# The descendants of what we launched, recorded while they are still alive.
#
# A uvicorn reload worker cannot be identified any other way. It is spawned
# through `multiprocessing`, so its command line is a `spawn_main` stub — no
# `api.main:app`, no `--port`, nothing `Test-OursCommandLine` can recognise —
# and it is not the socket's owner either: the reloader creates the listening
# socket and hands it over, so `Get-NetTCPConnection` keeps naming the reloader
# even after the reloader is gone. Walking down from our own PIDs at cleanup
# time does not reach it either, because the reloader in the middle of that
# chain has usually died first, and a dead process is absent from the table
# that the walk reads.
#
# Every one of those failures shares a cause: by the time we look, the
# information is gone. So the tree is recorded as it runs.
$script:LaunchedAt = $null
$script:Tracked = @{}

function Update-TrackedDescendants {
    if ($script:Started.Count -eq 0) { return }

    # Only the process names these two servers actually spawn, so the sweep
    # stays a narrow query rather than an enumeration of every process.
    $procs = @(Get-CimInstance Win32_Process `
        -Filter "Name = 'python.exe' OR Name = 'node.exe' OR Name = 'cmd.exe'" `
        -ErrorAction SilentlyContinue)
    if ($procs.Count -eq 0) { return }

    $known = @{}
    foreach ($p in $script:Started) {
        try { $known[[int]$p.Id] = $true } catch { }
    }
    foreach ($id in $script:Tracked.Keys) { $known[[int]$id] = $true }

    # A PID left by a dead ancestor can be handed to an unrelated process, and
    # that process would look like a child of ours. It cannot predate this run,
    # so the launch time settles it. The second of slack absorbs clock
    # granularity between Get-Date here and CreationDate from CIM.
    $floor = $script:LaunchedAt.AddSeconds(-1)

    # Repeat until nothing new appears: a grandchild is only reachable once its
    # parent is known, and CIM does not return the table in tree order.
    for ($pass = 0; $pass -lt 6; $pass++) {
        $added = $false
        foreach ($proc in $procs) {
            $procId = [int]$proc.ProcessId
            if ($known.ContainsKey($procId)) { continue }
            if (-not $known.ContainsKey([int]$proc.ParentProcessId)) { continue }
            if ($proc.CreationDate -and $proc.CreationDate -lt $floor) { continue }
            $known[$procId] = $true
            $script:Tracked[$procId] = $true
            $added = $true
        }
        if (-not $added) { break }
    }
}

function Stop-LeftoverListeners {
    # The case Stop-Tree cannot cover: a server that outlived the process this
    # script launched.
    #
    # `Start-Process $Python` does not hand us the server directly — a venv's
    # python.exe re-execs its base interpreter, which is the uvicorn reloader,
    # which forks a worker. The thing serving the port is therefore a
    # grandchild. When the launched process exits on its own (the reloader
    # dying with code 1 as WatchFiles churns, rather than a Ctrl+C), Stop-Tree
    # is handed an already-exited process and `taskkill /T` has no tree left to
    # walk, so those descendants survive holding the port. The next run then
    # has to reclaim a port it should never have had to — and because the
    # survivor keeps whatever environment it started with, a stale one can
    # leave the API answering 401 through an entire `-NoAuth` session.
    #
    # Both guards from the startup reclaim still apply, and this is exactly as
    # conservative: a port held by something that is not ours is reported and
    # left alone, and this script's own ancestry is never a target.
    $selfIds = Get-SelfAncestry
    foreach ($entry in @(
        [pscustomobject]@{ Port = $ApiPort; Label = 'api' },
        [pscustomobject]@{ Port = $WebPort; Label = 'web' }
    )) {
        # A server that is merely slow to die is not a leftover. Only what is
        # still listening after a grace period is treated as one.
        if (Wait-PortFree $entry.Port 3) { continue }

        $targets = @()

        # The owner, when it is alive and resolvable — the ordinary case.
        $holder = Get-PortHolder $entry.Port
        if ($holder -and $holder.IsOurs -and $selfIds -notcontains $holder.Id) {
            $targets += Get-ReclaimTarget $holder $selfIds
        }

        $targets = @($targets | Sort-Object -Unique | Where-Object { $selfIds -notcontains $_ })
        if ($targets.Count -eq 0) {
            $who = if ($holder) { "$($holder.ProcessName) (PID $($holder.Id))" } else { 'something' }
            Write-Warn "port $($entry.Port) ($($entry.Label)) is held by $who, which this script did not start - left alone."
            continue
        }

        foreach ($target in $targets) { & taskkill.exe /PID $target /T /F *> $null }
        if (Wait-PortFree $entry.Port 5) {
            Write-Ok "leftover $($entry.Label) (PID $($targets -join ', ')) stopped; port $($entry.Port) free"
        } else {
            Write-Bad "port $($entry.Port) ($($entry.Label)) is still held after stopping PID $($targets -join ', ')."
        }
    }
}

function Stop-All {
    # One last look before anything is killed, so a worker spawned since the
    # previous refresh is still recorded rather than missed.
    Update-TrackedDescendants

    foreach ($p in $script:Started) { Stop-Tree $p }

    # Then everything the tree kill could not reach: the survivors of a parent
    # that had already exited. Killed deepest-PID-first is not meaningful on
    # Windows, so each is taken with /T and failures are ignored — a PID that
    # died with its parent is the expected case here, not an error.
    foreach ($procId in @($script:Tracked.Keys)) {
        & taskkill.exe /PID $procId /T /F *> $null
    }

    $script:Started = @()
    $script:Tracked = @{}

    # After the trees, never before: a live server killed above needs its
    # moment to release the port, and Stop-LeftoverListeners waits for exactly
    # that before deciding anything is a leftover.
    Stop-LeftoverListeners
}

try {
    # Before the first Start-Process: everything born after this instant, below
    # one of our PIDs, is ours. See Update-TrackedDescendants.
    $script:LaunchedAt = Get-Date

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
        Update-TrackedDescendants
        if ($api.HasExited) { throw "The API exited during startup (code $($api.ExitCode)). See its output above." }
        try {
            $res = Invoke-WebRequest -Uri "$ApiUrl/api/health" -UseBasicParsing -TimeoutSec 2
            if ($res.StatusCode -eq 200) { $healthy = $true; break }
        } catch {
            Start-Sleep -Milliseconds 400
        }
    }
    # Startup is when the tree is at its most complete and least likely to have
    # lost a member, so record it once more now that the API is answering.
    Update-TrackedDescendants

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
    # `--reload` replaces the worker on every code change, so the tree is not
    # fixed for the life of the run and one recording at startup would go
    # stale. Re-read it every few seconds rather than every tick: this is a CIM
    # query, and the thing it guards against costs a port, not data.
    $nextScan = (Get-Date).AddSeconds(3)
    while ($true) {
        if ((Get-Date) -ge $nextScan) {
            Update-TrackedDescendants
            $nextScan = (Get-Date).AddSeconds(3)
        }
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
