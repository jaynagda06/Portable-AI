#requires -Version 5.1
<#
.SYNOPSIS
  Download the two GGUF models this build expects.

.DESCRIPTION
  Rebuilds models\ (chat model) and models\embeddings\ (embedding model) from
  official Hugging Face URLs. Verifies byte size and SHA-256 unless -SkipHashCheck.
  Downloads resume with curl.exe when available.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\fetch-model.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\fetch-model.ps1 -Force -SkipHashCheck
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$Force,
    [switch]$SkipHashCheck
)

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $Root) { throw 'Cannot determine the project folder. Pass -Root explicitly.' }

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'Continue'

$models = @(
    [pscustomobject]@{
        Name = 'Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
        Dir  = 'models'
        Size = 2497281120
        Sha  = '3605803b982cb64aead44f6c1b2ae36e3acdb41d8e46c8a94c6533bc4c67e597'
        Url  = 'https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
    },
    [pscustomobject]@{
        Name = 'nomic-embed-text-v1.5.Q4_K_M.gguf'
        Dir  = 'models\embeddings'
        Size = 84106624
        Sha  = 'd4e388894e09cf3816e8b0896d81d265b55e7a9fff9ab03fe8bf4ef5e11295ac'
        Url  = 'https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q4_K_M.gguf'
    }
)

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Test-Model([string]$Path, $M) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $len = (Get-Item -LiteralPath $Path).Length
    if ($len -ne $M.Size) { return $false }
    if ($SkipHashCheck) { return $true }
    return (Get-Sha256 $Path) -eq $M.Sha
}

Write-Host ''
Write-Host 'Portable AI - model download' -ForegroundColor Cyan
Write-Host ("Target: {0}" -f $Root)

$need = @()
foreach ($m in $models) {
    $path = Join-Path (Join-Path $Root $m.Dir) $m.Name
    if (-not $Force -and (Test-Model $path $m)) {
        Write-Host ("  [ok]      {0}" -f $m.Name) -ForegroundColor Green
    }
    else {
        $need += $m
    }
}

if ($need.Count -eq 0) {
    Write-Host ''
    Write-Host 'All models are present. Nothing to do.' -ForegroundColor Green
    exit 0
}

$needBytes = ($need | Measure-Object -Property Size -Sum).Sum
$srcDrive = (Get-Item -LiteralPath $Root).PSDrive
$free = (Get-PSDrive -Name $srcDrive.Name).Free
if ($free -lt ($needBytes + 200MB)) {
    Write-Host ("Not enough free space: need {0:N1} GB, have {1:N1} GB." -f (($needBytes + 200MB) / 1GB), ($free / 1GB)) -ForegroundColor Red
    exit 1
}

$curl = Get-Command curl.exe -ErrorAction SilentlyContinue
if (-not $curl) {
    Write-Warning 'curl.exe not found; falling back to Invoke-WebRequest (no resume). Windows 10 1803+ normally includes curl.exe.'
}

foreach ($m in $need) {
    $dir = Join-Path $Root $m.Dir
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $final = Join-Path $dir $m.Name
    $part = $final + '.part'

    Write-Host ''
    Write-Host ("Downloading {0}  ({1:N0} MB)" -f $m.Name, ($m.Size / 1MB)) -ForegroundColor Cyan
    Write-Host ("  from {0}" -f $m.Url)

    if (Test-Path -LiteralPath $part) {
        Write-Host '  resuming a partial download'
    }

    if ($curl) {
        & $curl.Source -L --fail --retry 3 --retry-delay 3 -C - -o $part $m.Url
        if ($LASTEXITCODE -ne 0) {
            # 33 = server does not support resume; start over once
            if ($LASTEXITCODE -eq 33) {
                Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
                & $curl.Source -L --fail --retry 3 --retry-delay 3 -o $part $m.Url
            }
            if ($LASTEXITCODE -ne 0) {
                throw "curl failed with exit code $LASTEXITCODE"
            }
        }
    }
    else {
        $old = $ProgressPreference
        Invoke-WebRequest -Uri $m.Url -OutFile $part -UseBasicParsing -TimeoutSec 3600
        $ProgressPreference = $old
    }

    if (-not (Test-Path -LiteralPath $part)) { throw 'download produced no file' }
    $len = (Get-Item -LiteralPath $part).Length
    if ($len -ne $m.Size) {
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
        throw ("size mismatch for {0}: expected {1} bytes, got {2}" -f $m.Name, $m.Size, $len)
    }

    if (-not $SkipHashCheck) {
        Write-Host '  verifying SHA-256...'
        $sha = Get-Sha256 $part
        if ($sha -ne $m.Sha) {
            Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
            throw ("SHA-256 mismatch for {0}`n  expected {1}`n  got      {2}`nRe-run with -SkipHashCheck only if you trust the source." -f $m.Name, $m.Sha, $sha)
        }
    }

    Move-Item -LiteralPath $part -Destination $final -Force
    Write-Host ("  saved {0}" -f $final) -ForegroundColor Green
}

Write-Host ''
Write-Host 'Model download complete. Next: powershell -ExecutionPolicy Bypass -File tools\verify-install.ps1' -ForegroundColor Green
