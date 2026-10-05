# Start Docker Desktop reliably on Windows hosts affected by stale-socket crashes.
#
# Why: on some Windows machines, Windows refuses to open/delete the Unix-socket files Docker
# leaves in AppData\Local\Docker\run and AppData\Local\docker-secrets-engine (error 1920).
# Docker then crashes on startup trying to remove them, sometimes even sockets it created
# moments earlier during a slow start. Moving the folders aside (nothing is deleted) lets
# Docker create fresh ones; if a start still crashes, this script notices from Docker's log
# and retries (up to 3 attempts).
#
# Usage (PowerShell):   powershell -ExecutionPolicy Bypass -File scripts\start_docker_windows.ps1
# (scripts/showcase.sh runs this automatically when Docker isn't answering.)

$ErrorActionPreference = "Continue"
$backendLog = "$env:LOCALAPPDATA\Docker\log\host\com.docker.backend.exe.log"
$maxAttempts = 3

function Test-DockerReady {
    $v = docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { return $v }
    return $null
}

function Test-CrashedSince([datetime]$since) {
    if (-not (Test-Path $backendLog)) { return $false }
    $lines = Select-String -Path $backendLog -Pattern "backend crashed" -ErrorAction SilentlyContinue
    foreach ($l in $lines) {
        if ($l.Line -match '^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})') {
            $ts = [datetime]::Parse($Matches[1]).ToLocalTime()   # log is in UTC
            if ($ts -ge $since) { return $true }
        }
    }
    return $false
}

$ready = Test-DockerReady
if ($ready) { Write-Host "Docker is already running (engine $ready)." -ForegroundColor Green; exit 0 }

for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    Write-Host "Attempt $attempt of ${maxAttempts}: stopping any half-started Docker Desktop..."
    Get-Process | Where-Object { $_.Name -in @("Docker Desktop", "com.docker.backend", "com.docker.build", "docker-agent", "docker-sandbox") } |
        Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    foreach ($dir in @("$env:LOCALAPPDATA\Docker\run", "$env:LOCALAPPDATA\docker-secrets-engine")) {
        if (Test-Path $dir) {
            try {
                Rename-Item -LiteralPath $dir -NewName ((Split-Path $dir -Leaf) + ".stale-$stamp") -ErrorAction Stop
                Write-Host "  moved aside: $dir"
            } catch {
                Write-Host "  could not move $dir : $($_.Exception.Message)"
            }
        }
    }

    Write-Host "Starting Docker Desktop..."
    $started = (Get-Date).AddSeconds(-2)
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    for ($i = 0; $i -lt 60; $i++) {          # up to ~5 minutes per attempt
        Start-Sleep -Seconds 5
        $ready = Test-DockerReady
        if ($ready) {
            Write-Host "Docker is ready (engine $ready)." -ForegroundColor Green
            exit 0
        }
        if (Test-CrashedSince $started) {
            Write-Host "Docker crashed while starting (stuck socket file); retrying..." -ForegroundColor Yellow
            break
        }
    }
}
Write-Host "Docker did not start after $maxAttempts attempts. Restart the PC and run this again." -ForegroundColor Red
exit 1
