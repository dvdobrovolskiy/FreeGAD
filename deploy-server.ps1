# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

<#
.SYNOPSIS
  Deploys the FreeGAD telemetry backend + dashboard to freecad.dobrovolskiy.com (host "main-server").

.DESCRIPTION
  Ships server/ to /opt/freegad on the VPS, builds the Docker image there (SvelteKit + FastAPI,
  multi-stage), brings the container up on the shared infra_net, installs the Caddy site snippet
  into /opt/infra/Caddyfile.d and reloads the central Caddy (which issues the TLS cert).
  First run creates /opt/freegad/.env with a random SECRET_KEY and prompts for the admin password.

.EXAMPLE
  pwsh -File deploy-server.ps1                  # deploy / update
  pwsh -File deploy-server.ps1 -SetPassword     # change the dashboard password
  pwsh -File deploy-server.ps1 -Logs            # tail container logs
#>
[CmdletBinding()]
param(
    [string]$SshTarget = "main-server",
    [string]$RemoteDir = "/opt/freegad",
    [string]$CaddyDir = "/opt/infra/Caddyfile.d",
    [string]$CaddyContainer = "caddy",
    [string]$Domain = "freecad.dobrovolskiy.com",
    [string]$AdminPassword,      # first deploy: non-interactive password (otherwise prompted)
    [switch]$SetPassword,
    [switch]$Logs
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $root 'server'

function Remote([string]$cmd) { & ssh -o BatchMode=yes $SshTarget -- $cmd; if ($LASTEXITCODE -ne 0) { throw "ssh failed ($LASTEXITCODE): $cmd" } }
function Step($m) { Write-Host "`n== $m" -ForegroundColor Cyan }

if ($Logs) { & ssh $SshTarget -- "docker logs --tail 200 -f freegad"; return }

if ($SetPassword) {
    $sec = Read-Host -Prompt 'New dashboard password' -AsSecureString
    $pw = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($sec))
    Remote "sed -i 's/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=$pw/' $RemoteDir/.env && cd $RemoteDir && docker compose up -d --force-recreate"
    Write-Host "Password updated." -ForegroundColor Green
    return
}

Step "Preparing $RemoteDir on $SshTarget"
$remoteUser = (& ssh -o BatchMode=yes $SshTarget -- 'id -un').Trim()
Remote "sudo mkdir -p $RemoteDir && sudo chown ${remoteUser}:${remoteUser} $RemoteDir"

Step "Uploading server/"
$tar = Join-Path $env:TEMP 'freegad-server.tar'
if (Test-Path $tar) { Remove-Item $tar }
Push-Location $src
try {
    & tar -cf $tar --exclude=web/node_modules --exclude=web/build --exclude=web/.svelte-kit --exclude=__pycache__ .
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
} finally { Pop-Location }
& scp -q $tar "${SshTarget}:/tmp/freegad-server.tar"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }
Remote "tar -xf /tmp/freegad-server.tar -C $RemoteDir && rm /tmp/freegad-server.tar"

Step "Environment (.env)"
$hasEnv = (& ssh -o BatchMode=yes $SshTarget -- "test -f $RemoteDir/.env && echo yes || echo no").Trim()
if ($hasEnv -ne 'yes') {
    $pw = $AdminPassword
    if (-not $pw) {
        $sec = Read-Host -Prompt '   Dashboard admin password (user "admin")' -AsSecureString
        $pw = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($sec))
    }
    if (-not $pw) { throw "password required" }
    $secret = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    $envText = "ADMIN_USER=admin`nADMIN_PASSWORD=$pw`nSECRET_KEY=$secret`nCOOKIE_SECURE=1`n"
    $envLocal = Join-Path $env:TEMP 'freegad.env'
    [IO.File]::WriteAllText($envLocal, $envText)
    & scp -q $envLocal "${SshTarget}:$RemoteDir/.env"
    Remove-Item $envLocal
    Remote "chmod 600 $RemoteDir/.env"
    Write-Host "   created .env" -ForegroundColor Green
} else { Write-Host "   .env exists, kept" -ForegroundColor Gray }

Step "Building image + starting container"
Remote "cd $RemoteDir && docker compose build --pull && docker compose up -d --remove-orphans"

Step "Caddy site"
Remote "sudo cp $RemoteDir/$Domain.caddy $CaddyDir/$Domain.caddy && docker exec $CaddyContainer caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile"

Step "Health"
Start-Sleep -Seconds 3
Remote "docker exec freegad python -c `"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').read().decode())`""
try { $r = Invoke-WebRequest -Uri "https://$Domain/api/v1/health" -TimeoutSec 30; Write-Host "   https://$Domain -> $($r.StatusCode) $($r.Content)" -ForegroundColor Green }
catch { Write-Warning "public check failed (cert may still be issuing): $($_.Exception.Message)" }

Write-Host "`nDone. Dashboard: https://$Domain  (user admin)" -ForegroundColor Cyan
