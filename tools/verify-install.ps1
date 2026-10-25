#requires -Version 5.1
<#
.SYNOPSIS
  Verify that this copy of Portable AI is complete and runnable.

.DESCRIPTION
  Checks required files, models, engine binaries and free space, then runs the
  app's own offline self-check (app\server.py --check). Starts no model and
  opens no ports.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\verify-install.ps1
#>
[CmdletBinding()]
param(
    [string]$Root = ''
)

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $Root) { throw 'Cannot determine the project folder. Pass -Root explicitly.' }

$ErrorActionPreference = 'Stop'
$fail = 0

function Check-Path([string]$Relative, [string]$Label, [switch]$Directory, [switch]$DirectorySize) {
    $p = Join-Path $Root $Relative
    $ok = if ($Directory) { Test-Path -LiteralPath $p -PathType Container }
          else { Test-Path -LiteralPath $p -PathType Leaf }
    if ($ok) {
        $size = ''
        if ($DirectorySize) {
            $sum = (Get-ChildItem -LiteralPath (Split-Path -Parent $p) -Recurse -File |
                    Measure-Object -Property Length -Sum).Sum
            $size = ' ({0:N1} MB)' -f ($sum / 1MB)
        }
        elseif (-not $Directory) {
            $size = ' ({0:N1} MB)' -f ((Get-Item -LiteralPath $p).Length / 1MB)
        }
        Write-Host ("  [ok]   {0}{1}" -f $Label, $size) -ForegroundColor Green
    }
    else {
        Write-Host ("  [MISS] {0}  ->  {1}" -f $Label, $p) -ForegroundColor Red
        $script:fail++
    }
    return $ok
}

Write-Host ''
Write-Host 'Portable AI - verify install' -ForegroundColor Cyan
Write-Host ("Root: {0}" -f $Root)
Write-Host ''
Write-Host 'Files' -ForegroundColor White

Check-Path 'Start-AI.cmd' 'Start-AI.cmd' | Out-Null
Check-Path 'Stop-AI.cmd' 'Stop-AI.cmd' | Out-Null
Check-Path 'app\server.py' 'app\server.py' | Out-Null
Check-Path 'app\web\index.html' 'app\web\index.html' | Out-Null
Check-Path 'app\web\app.js' 'app\web\app.js' | Out-Null
Check-Path 'app\web\style.css' 'app\web\style.css' | Out-Null
Check-Path 'runtime\python\python.exe' 'portable Python' | Out-Null
Check-Path 'runtime\site-packages' 'Python site-packages' -Directory | Out-Null
Check-Path 'models\Qwen3-4B-Instruct-2507-Q4_K_M.gguf' 'chat model' | Out-Null
Check-Path 'models\embeddings\nomic-embed-text-v1.5.Q4_K_M.gguf' 'embedding model' | Out-Null

$vulkan = Check-Path 'runtime\llama\vulkan\llama-server.exe' 'llama.cpp Vulkan engine' -DirectorySize
$cpu = Check-Path 'runtime\llama\cpu\llama-server.exe' 'llama.cpp CPU engine' -DirectorySize
if (-not $vulkan -and -not $cpu) {
    Write-Host '  [FAIL] no llama.cpp engine found - run tools\fetch-runtime.ps1' -ForegroundColor Red
    $fail++
}

foreach ($backend in @('vulkan', 'cpu')) {
    $dir = Join-Path $Root "runtime\llama\$backend"
    if (-not (Test-Path -LiteralPath (Join-Path $dir 'llama-server.exe'))) { continue }
    $missing = @()
    foreach ($d in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path $dir $d))) { $missing += $d }
    }
    if ($missing.Count -gt 0) {
        Write-Host ("  [warn] {0}: missing app-local {1}" -f $backend, ($missing -join ', ')) -ForegroundColor Yellow
        Write-Host '         The host VC++ runtime may work, but the drive is not fully self-contained.' -ForegroundColor Yellow
    }
    else {
        Write-Host ("  [ok]   {0}: app-local VC++ runtime" -f $backend) -ForegroundColor Green
    }
}

$docs = @('README.md', 'docs\SETUP.md', 'docs\PORTABILITY.md', 'docs\TROUBLESHOOTING.md',
          'docs\SECURITY-AND-SEB.md')
foreach ($d in $docs) {
    if (-not (Test-Path -LiteralPath (Join-Path $Root $d))) {
        Write-Host ("  [warn] missing documentation: {0}" -f $d) -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host 'Size and space' -ForegroundColor White
$bytes = (Get-ChildItem -LiteralPath $Root -Recurse -File -Force -ErrorAction SilentlyContinue |
          Measure-Object -Property Length -Sum).Sum
$free = (Get-PSDrive -Name (Get-Item -LiteralPath $Root).PSDrive.Name).Free
Write-Host ("  folder      : {0:N2} GB" -f ($bytes / 1GB))
Write-Host ("  drive free  : {0:N1} GB" -f ($free / 1GB))
if ($free -lt 1GB) {
    Write-Host '  [warn] less than 1 GB free; uploads and reports need room' -ForegroundColor Yellow
}

Write-Host ''
if ($fail -gt 0) {
    Write-Host 'Result: INCOMPLETE' -ForegroundColor Red
    Write-Host 'Repair with tools\fetch-model.ps1 and/or tools\fetch-runtime.ps1.' -ForegroundColor Yellow
    exit 1
}

$py = Join-Path $Root 'runtime\python\python.exe'
if (Test-Path -LiteralPath $py) {
    & $py (Join-Path $Root 'app\server.py') --check
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        Write-Host ''
        Write-Host 'Result: self-check FAILED (see above)' -ForegroundColor Red
        exit $code
    }
}
else {
    Write-Host 'Result: files present, but the portable Python runtime is missing.' -ForegroundColor Yellow
    exit 1
}

Write-Host ''
Write-Host 'Result: ready. Start with Start-AI.cmd (or Start-AI-LAN.cmd for LAN access).' -ForegroundColor Green
exit 0
