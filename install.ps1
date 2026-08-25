# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Dmitriy Dobrovolskiy dima@dobrovolskiy.com

<#
.SYNOPSIS
  Installs (or removes) the FreeGAD addon for FreeCAD and stores the API key (Anthropic or OpenAI-compatible).

.DESCRIPTION
  Copies the addon to %APPDATA%\FreeCAD\Mod\FreeGAD (FreeCAD's per-user addon folder, scanned at
  startup by every FreeCAD 1.x install). Then prompts for an API key, verifies it against the API,
  and saves it encrypted (Windows DPAPI, current user only) in %APPDATA%\FreeGAD\config.json.
  Two providers share that file: Anthropic (apiKeyEnc) and any OpenAI-compatible API (openaiApiKeyEnc,
  openaiBaseUrl - OpenAI, OpenRouter, ...); "provider" selects the active one.

.EXAMPLE
  pwsh -File install.ps1                     # install, prompt for the key if none is stored
  pwsh -File install.ps1 -SetKey             # only change the stored API key (no install)
  pwsh -File install.ps1 -ApiKey sk-ant-...  # non-interactive Anthropic key
  pwsh -File install.ps1 -SetKey -Provider openai -BaseUrl https://openrouter.ai/api/v1 -ApiKey sk-or-...
  pwsh -File install.ps1 -SkipKey            # install without touching the key
  pwsh -File install.ps1 -Uninstall          # remove the addon (keeps config + memory unless -Purge)
#>
[CmdletBinding()]
param(
    [switch]$Uninstall,
    [switch]$SetKey,
    [switch]$SkipKey,
    [string]$ApiKey,
    [ValidateSet('anthropic', 'openai')]
    [string]$Provider,        # which API the key is for (default: the configured one, else anthropic)
    [string]$BaseUrl,         # OpenAI-compatible base URL, e.g. https://openrouter.ai/api/v1
    [switch]$RemoveKey,
    [switch]$Purge            # with -Uninstall: also delete %APPDATA%\FreeGAD (config, key, memory)
)

$ErrorActionPreference = 'Stop'

$root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$modDir     = Join-Path $env:APPDATA 'FreeCAD\Mod\FreeGAD'
$appDir     = Join-Path $env:APPDATA 'FreeGAD'
$configPath = Join-Path $appDir 'config.json'

function Write-Step($msg) { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Write-Ok  ($msg) { Write-Host "   $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "   $msg" -ForegroundColor Gray }

# ---------------------------------------------------------------- DPAPI helper
# Same blob format the plugin's dpapi.py reads: base64(CryptProtectData(utf8 key)).
Add-Type -Namespace FreeGadSetup -Name Dpapi -MemberDefinition @'
    [StructLayout(LayoutKind.Sequential)]
    private struct DATA_BLOB { public int cbData; public IntPtr pbData; }

    [DllImport("crypt32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    private static extern bool CryptProtectData(ref DATA_BLOB pDataIn, string szDataDescr,
        IntPtr pOptionalEntropy, IntPtr pvReserved, IntPtr pPromptStruct, int dwFlags, out DATA_BLOB pDataOut);

    [DllImport("kernel32.dll")]
    private static extern IntPtr LocalFree(IntPtr hMem);

    public static string Protect(string plainText)
    {
        byte[] input = System.Text.Encoding.UTF8.GetBytes(plainText);
        DATA_BLOB inBlob = new DATA_BLOB();
        inBlob.cbData = input.Length;
        inBlob.pbData = Marshal.AllocHGlobal(input.Length);
        Marshal.Copy(input, 0, inBlob.pbData, input.Length);
        DATA_BLOB outBlob;
        try
        {
            if (!CryptProtectData(ref inBlob, null, IntPtr.Zero, IntPtr.Zero, IntPtr.Zero, 0, out outBlob))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        }
        finally { Marshal.FreeHGlobal(inBlob.pbData); }
        byte[] output = new byte[outBlob.cbData];
        Marshal.Copy(outBlob.pbData, output, 0, outBlob.cbData);
        LocalFree(outBlob.pbData);
        return System.Convert.ToBase64String(output);
    }
'@

# ---------------------------------------------------------------- config helpers
function Read-Config {
    if (Test-Path $configPath) {
        try { return (Get-Content $configPath -Raw | ConvertFrom-Json -AsHashtable) } catch { }
    }
    return @{}
}

function Write-Config($cfg) {
    New-Item -ItemType Directory -Force -Path $appDir | Out-Null
    $defaults = [ordered]@{
        provider = 'anthropic'; apiKeyEnc = ''; model = 'claude-opus-5'
        openaiApiKeyEnc = ''; openaiModel = 'gpt-5'; openaiBaseUrl = 'https://api.openai.com/v1'
        maxTokens = 16000; effort = 'high'; fallbacks = $true; autoApprove = $false
    }
    foreach ($k in $defaults.Keys) { if (-not $cfg.ContainsKey($k)) { $cfg[$k] = $defaults[$k] } }
    $cfg.Remove('apiKey')      # never leave a plain-text key behind
    $cfg | ConvertTo-Json -Depth 5 | Set-Content -Path $configPath -Encoding UTF8
}

function Get-ActiveProvider {
    $cfg = Read-Config
    if ($cfg.ContainsKey('provider') -and $cfg['provider'] -eq 'openai') { 'openai' } else { 'anthropic' }
}

function Get-KeyField($provider) { if ($provider -eq 'openai') { 'openaiApiKeyEnc' } else { 'apiKeyEnc' } }

function Test-KeyStored($provider) {
    $cfg = Read-Config
    $f = Get-KeyField $provider
    return ($cfg.ContainsKey($f) -and $cfg[$f]) -or
           ($provider -eq 'anthropic' -and $cfg.ContainsKey('apiKey') -and $cfg['apiKey'])
}

# Remove one provider's key, or both when no provider is given.
function Remove-StoredKey([string]$provider) {
    $cfg = Read-Config
    if ($cfg.Count -eq 0) { Write-Info "No config file at $configPath"; return }
    if ($provider) { $cfg.Remove((Get-KeyField $provider)) } else { $cfg.Remove('apiKeyEnc'); $cfg.Remove('openaiApiKeyEnc') }
    $cfg.Remove('apiKey')
    Write-Config $cfg
    Write-Ok 'Stored API key removed.'
}

# Cheap key check against GET /models - costs no tokens.
function Test-ApiKey([string]$key, [string]$provider, [string]$baseUrl) {
    if ($provider -eq 'openai') {
        $uri = $baseUrl.Trim().TrimEnd('/') + '/models'
        $headers = @{ 'Authorization' = "Bearer $key" }
    } else {
        $uri = 'https://api.anthropic.com/v1/models?limit=1'
        $headers = @{ 'x-api-key' = $key; 'anthropic-version' = '2023-06-01' }
    }
    try {
        $null = Invoke-RestMethod -Uri $uri -Method Get -TimeoutSec 20 -Headers $headers
        return $true
    } catch {
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch { }
        if ($code -eq 401 -or $code -eq 403) { Write-Warning "API key rejected (HTTP $code)."; return $false }
        Write-Warning "Could not verify the key ($($_.Exception.Message)); saving anyway."
        return $true
    }
}

function Set-StoredKey {
    param([string]$Key)
    if (-not $script:Provider) {
        $cur = Get-ActiveProvider
        $ans = Read-Host "   Provider - [A]nthropic (Claude) or [O]penAI-compatible (OpenAI, OpenRouter, ...) [current: $cur]"
        $script:Provider = if ($ans -match '^[Oo]') { 'openai' } elseif ($ans -match '^[Aa]') { 'anthropic' } else { $cur }
    }
    if ($script:Provider -eq 'openai' -and -not $script:BaseUrl) {
        $cfg = Read-Config
        $def = if ($cfg.ContainsKey('openaiBaseUrl') -and $cfg['openaiBaseUrl']) { $cfg['openaiBaseUrl'] } else { 'https://api.openai.com/v1' }
        $ans = Read-Host "   Base URL (OpenAI: https://api.openai.com/v1, OpenRouter: https://openrouter.ai/api/v1) [$def]"
        $script:BaseUrl = if ($ans) { $ans.Trim() } else { $def }
    }
    if (-not $Key) {
        $secure = Read-Host -Prompt "   $($script:Provider) API key" -AsSecureString
        $Key = [Runtime.InteropServices.Marshal]::PtrToStringUni(
            [Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($secure))
    }
    $Key = $Key.Trim()
    if (-not $Key) { Write-Info 'No key entered; skipped. Use FreeGAD > Set API key inside FreeCAD later.'; return }
    if (-not (Test-ApiKey $Key $script:Provider $script:BaseUrl)) {
        $ans = Read-Host '   Save it anyway? [y/N]'
        if ($ans -notmatch '^[Yy]') { return }
    }
    $cfg = Read-Config
    $cfg[(Get-KeyField $script:Provider)] = [FreeGadSetup.Dpapi]::Protect($Key)
    $cfg['provider'] = $script:Provider
    if ($script:Provider -eq 'openai' -and $script:BaseUrl) { $cfg['openaiBaseUrl'] = $script:BaseUrl.TrimEnd('/') }
    Write-Config $cfg
    Write-Ok "$($script:Provider) API key saved (encrypted) to $configPath"
}

# ---------------------------------------------------------------- uninstall
if ($Uninstall) {
    Write-Step 'Removing FreeGAD'
    if (Test-Path $modDir) { Remove-Item -Recurse -Force $modDir; Write-Ok "Removed $modDir" }
    else { Write-Info 'Addon folder not present.' }
    if ($Purge -or $RemoveKey) {
        if ($Purge) { if (Test-Path $appDir) { Remove-Item -Recurse -Force $appDir; Write-Ok "Removed $appDir" } }
        else { Remove-StoredKey $Provider }
    } else { Write-Info "Kept $appDir (config, keys, memory). Add -Purge to delete it." }
    Write-Host "`nDone. Restart FreeCAD." -ForegroundColor Cyan
    return
}

# ---------------------------------------------------------------- key only
if ($SetKey) {
    Write-Step 'API key'
    if ($RemoveKey) { Remove-StoredKey $Provider } else { Set-StoredKey -Key $ApiKey }
    return
}

# ---------------------------------------------------------------- install
Write-Step 'Installing FreeGAD addon'
New-Item -ItemType Directory -Force -Path $modDir | Out-Null
$items = @('Init.py', 'InitGui.py', 'package.xml', 'LICENSE', 'README.md', 'version.txt', 'freegad', 'resources')
foreach ($it in $items) {
    $src = Join-Path $root $it
    if (-not (Test-Path $src)) { continue }
    $dst = Join-Path $modDir $it
    if (Test-Path $dst) { Remove-Item -Recurse -Force $dst }
    Copy-Item -Recurse -Force $src $dst
}
Get-ChildItem -Path $modDir -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force
Write-Ok "Copied to $modDir"

$fc = Get-ChildItem "$env:ProgramFiles\FreeCAD*" -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name
if ($fc) { Write-Info ("Found FreeCAD: " + ($fc -join ', ')) }
else { Write-Warning 'No FreeCAD found under Program Files; the addon is installed per-user and will load when FreeCAD is present.' }

if (-not $SkipKey) {
    Write-Step 'API key'
    $active = if ($Provider) { $Provider } else { Get-ActiveProvider }
    if ($ApiKey) { Set-StoredKey -Key $ApiKey }
    elseif (Test-KeyStored $active) { Write-Info "A $active key is already stored; run with -SetKey to replace it." }
    elseif (($active -eq 'openai' -and $env:OPENAI_API_KEY) -or ($active -ne 'openai' -and $env:ANTHROPIC_API_KEY)) {
        Write-Info 'An API key environment variable is set; it will be used. Run -SetKey to store one instead.'
    }
    else { Set-StoredKey }
}

Write-Host "`nDone. Start FreeCAD and open the FreeGAD menu (or the FreeGAD workbench)." -ForegroundColor Cyan
