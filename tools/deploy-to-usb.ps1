#requires -Version 5.1
<#
.SYNOPSIS
  Copy a clean, ready-to-run Portable AI tree to a USB drive or folder.

.DESCRIPTION
  Copies the app, models, runtime, docs and launchers. By default it does NOT copy
  your reports, library database, logs or config.json, so the destination starts
  fresh (and generates its own LAN token). Use -IncludeData to migrate them too.

  The destination must be writable. FAT32 is detected and warned about.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\deploy-to-usb.ps1 -Destination E:\PortableAI

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\deploy-to-usb.ps1 -Destination E:\ -IncludeData
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination,
    [string]$Root = '',
    [switch]$IncludeData,
    [switch]$NoVerify
)

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $Root) { throw 'Cannot determine the project folder. Pass -Root explicitly.' }

$ErrorActionPreference = 'Stop'

function Get-NormPath([string]$Path) {
    $p = [System.IO.Path]::GetFullPath($Path)
    if ($p.Length -gt 3) { $p = $p.TrimEnd('\') }
    return $p
}

$src = Get-NormPath $Root
$dst = Get-NormPath $Destination

if ($src -eq $dst) { throw 'Destination is the source folder.' }
if ($dst.StartsWith($src + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Destination cannot be inside the source folder.'
}
if ($src.StartsWith($dst + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Source cannot be inside the destination.'
}
if (-not (Test-Path -LiteralPath (Join-Path $src 'app\server.py'))) {
    throw "This does not look like a Portable AI folder: $src"
}

if (-not (Test-Path -LiteralPath $dst)) {
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Write-Host ("Created {0}" -f $dst)
}

# ---- free space -------------------------------------------------------------
$fat = $null
$driveLetter = $null
if ($dst -match '^([A-Za-z]):') { $driveLetter = $Matches[1] }
if ($driveLetter) {
    try {
        $vol = Get-Volume -DriveLetter $driveLetter -ErrorAction Stop
        $fat = $vol.FileSystem
        $free = $vol.SizeRemaining
    }
    catch { $free = (Get-PSDrive -Name $driveLetter).Free }
}
else {
    $free = (Get-PSDrive -Name (Get-Item -LiteralPath $dst).PSDrive.Name).Free
}

$folders = @('app', 'runtime', 'models', 'docs', 'tools')
if ($IncludeData) { $folders += @('data', 'logs', 'config') }

$bytes = 0
foreach ($f in $folders) {
    $p = Join-Path $src $f
    if (Test-Path -LiteralPath $p) {
        $bytes += (Get-ChildItem -LiteralPath $p -Recurse -File -Force -ErrorAction SilentlyContinue |
                   Measure-Object -Property Length -Sum).Sum
    }
}
$rootFiles = Get-ChildItem -LiteralPath $src -File | Where-Object { $_.Extension -in @('.cmd', '.md', '.txt') }
$bytes += ($rootFiles | Measure-Object -Property Length -Sum).Sum

Write-Host ''
Write-Host 'Portable AI - deploy' -ForegroundColor Cyan
Write-Host ("  from : {0}" -f $src)
Write-Host ("  to   : {0}" -f $dst)
Write-Host ("  data : {0}" -f $(if ($IncludeData) { 'included' } else { 'fresh start (excluded)' }))
Write-Host ("  size : {0:N2} GB   free: {1:N1} GB" -f ($bytes / 1GB), ($free / 1GB))
if ($fat -and $fat -eq 'FAT32') {
    Write-Host '  [warn] destination is FAT32: single files are limited to 4 GB.' -ForegroundColor Yellow
}
if ($free -lt ($bytes * 1.02 + 100MB)) {
    throw ("Not enough free space at {0}. Need about {1:N1} GB." -f $dst, (($bytes * 1.02 + 100MB) / 1GB))
}

# ---- copy -------------------------------------------------------------------
$xd = @(
    (Join-Path $src 'build'),
    (Join-Path $src '.git'),
    '__pycache__'
)
$xf = @('*.pyc', 'Thumbs.db', '.DS_Store')
if (-not $IncludeData) {
    $xd += @((Join-Path $src 'data'), (Join-Path $src 'logs'))
    $xf += (Join-Path $src 'config\config.json')
}

$roboArgs = @($src, $dst, '/E', '/R:2', '/W:2', '/NFL', '/NDL', '/NJH', '/NJS',
              '/XD') + $xd + @('/XF') + $xf

Write-Host ''
Write-Host 'Copying...'
& robocopy @roboArgs | Out-Host
$rc = $LASTEXITCODE
# 0-7 are success codes for robocopy; >=8 is a failure
if ($rc -ge 8) { throw "robocopy failed with exit code $rc" }

# ---- verify -----------------------------------------------------------------
$required = @(
    'Start-AI.cmd',
    'Stop-AI.cmd',
    'app\server.py',
    'app\web\index.html',
    'runtime\python\python.exe',
    'models\Qwen3-4B-Instruct-2507-Q4_K_M.gguf',
    'models\embeddings\nomic-embed-text-v1.5.Q4_K_M.gguf'
)
foreach ($r in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $dst $r))) {
        throw "copy incomplete, missing: $r"
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $dst 'runtime\llama\vulkan\llama-server.exe')) -and
    -not (Test-Path -LiteralPath (Join-Path $dst 'runtime\llama\cpu\llama-server.exe'))) {
    throw 'copy incomplete: no llama.cpp engine in runtime\llama'
}

$dstBytes = (Get-ChildItem -LiteralPath $dst -Recurse -File -Force -ErrorAction SilentlyContinue |
             Measure-Object -Property Length -Sum).Sum
Write-Host ''
Write-Host ("Copied {0:N2} GB to {1}" -f ($dstBytes / 1GB), $dst) -ForegroundColor Green

if (-not $NoVerify) {
    Write-Host ''
    Write-Host 'Running the destination self-check (starts no model)...'
    & (Join-Path $dst 'runtime\python\python.exe') (Join-Path $dst 'app\server.py') --check
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Destination self-check reported problems (see above).' -ForegroundColor Yellow
        exit 1
    }
}

Write-Host ''
Write-Host 'Deploy complete.' -ForegroundColor Green
Write-Host ("Next: open {0}\Start-AI.cmd on the target PC." -f $dst)
Write-Host 'If you want a new LAN token, delete config\config.json on the destination.'
