#requires -Version 5.1
<#
.SYNOPSIS
  Rebuild the portable Python runtime, Python packages and llama.cpp binaries.

.DESCRIPTION
  Pinned to the versions this build was tested with:
    Python 3.13.5 (embeddable, x64)
    llama.cpp b11871 (Vulkan and/or CPU, x64)
    pefile 2024.8.26, pypdf 6.21.2, capstone 5.0.9

  Needs internet access once. Existing models, config and data are not touched.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\fetch-runtime.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\fetch-runtime.ps1 -Backend cpu -Force
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [ValidateSet('both', 'vulkan', 'cpu')]
    [string]$Backend = 'both',
    [switch]$Force,
    [switch]$NoCheck
)

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
if (-not $Root) { throw 'Cannot determine the project folder. Pass -Root explicitly.' }

$ErrorActionPreference = 'Stop'
$pyVersion = '3.13.5'
$llamaTag = 'b11871'
$pkgs = @(
    [pscustomobject]@{ Name = 'pefile'; Version = '2024.8.26' },
    [pscustomobject]@{ Name = 'pypdf'; Version = '6.21.2' },
    [pscustomobject]@{ Name = 'capstone'; Version = '5.0.9' }
)

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host $Text -ForegroundColor Cyan
}

function Get-File([string]$Url, [string]$OutFile) {
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    Write-Host ("  {0}" -f $Url)
    if ($curl) {
        & $curl.Source -L --fail --retry 3 --retry-delay 3 -o $OutFile $Url
        if ($LASTEXITCODE -ne 0) { throw "download failed (curl exit $LASTEXITCODE): $Url" }
    }
    else {
        Invoke-WebRequest -Uri $Url -OutFile $OutFile -UseBasicParsing -TimeoutSec 3600
    }
    if (-not (Test-Path -LiteralPath $OutFile)) { throw "download produced no file: $Url" }
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("pai-runtime-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$runtime = Join-Path $Root 'runtime'
$pyDir = Join-Path $runtime 'python'
$spDir = Join-Path $runtime 'site-packages'
$llamaDir = Join-Path $runtime 'llama'

try {
    Write-Host ''
    Write-Host 'Portable AI - runtime rebuild' -ForegroundColor Cyan
    Write-Host ("Target: {0}" -f $runtime)

    # ---------------- Python ----------------
    $pyExe = Join-Path $pyDir 'python.exe'
    if ($Force -or -not (Test-Path -LiteralPath $pyExe)) {
        Write-Step "Python $pyVersion (embeddable x64)"
        $zip = Join-Path $tmp 'python-embed.zip'
        Get-File "https://www.python.org/ftp/python/$pyVersion/python-$pyVersion-embed-amd64.zip" $zip
        if (Test-Path -LiteralPath $pyDir) { Remove-Item -LiteralPath $pyDir -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $pyDir -Force

        # Make site-packages and the app importable from the portable layout.
        $pth = @(
            'python313.zip',
            '.',
            '..\site-packages',
            '..\..\app',
            '',
            '# Uncomment to run site.main() automatically',
            'import site'
        )
        Set-Content -LiteralPath (Join-Path $pyDir 'python313._pth') -Value $pth -Encoding ASCII
        Write-Host ("  installed {0}" -f $pyExe) -ForegroundColor Green
    }
    else {
        Write-Host ("  [ok] Python already present: {0}" -f $pyExe) -ForegroundColor Green
    }

    # ---------------- Python packages ----------------
    Write-Step 'Python packages (wheels)'
    if ($Force -and (Test-Path -LiteralPath $spDir)) {
        Remove-Item -LiteralPath $spDir -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $spDir | Out-Null

    foreach ($p in $pkgs) {
        if (-not $Force) {
            $existing = Join-Path $spDir ($p.Name + '-' + $p.Version + '.dist-info')
            if (Test-Path -LiteralPath $existing) {
                Write-Host ("  [ok] {0} {1} already installed" -f $p.Name, $p.Version) -ForegroundColor Green
                continue
            }
        }
        $meta = Invoke-RestMethod -Uri "https://pypi.org/pypi/$($p.Name)/$($p.Version)/json" -TimeoutSec 60
        $wheel = $meta.urls |
            Where-Object { $_.packagetype -eq 'bdist_wheel' -and $_.filename -match 'win_amd64' } |
            Select-Object -First 1
        if (-not $wheel) {
            $wheel = $meta.urls |
                Where-Object { $_.packagetype -eq 'bdist_wheel' -and $_.filename -match 'py3-none-any' } |
                Select-Object -First 1
        }
        if (-not $wheel) { throw "no suitable wheel for $($p.Name) $($p.Version)" }

        $whl = Join-Path $tmp $wheel.filename
        Get-File $wheel.url $whl
        $zipCopy = Join-Path $tmp ($wheel.filename + '.zip')
        Copy-Item -LiteralPath $whl -Destination $zipCopy -Force
        Expand-Archive -LiteralPath $zipCopy -DestinationPath $spDir -Force
        Write-Host ("  installed {0} {1}" -f $p.Name, $p.Version) -ForegroundColor Green
    }

    # ---------------- llama.cpp ----------------
    $targets = @()
    if ($Backend -in @('both', 'vulkan')) {
        $targets += [pscustomobject]@{
            Name = 'vulkan'
            Url  = "https://github.com/ggml-org/llama.cpp/releases/download/$llamaTag/llama-$llamaTag-bin-win-vulkan-x64.zip"
        }
    }
    if ($Backend -in @('both', 'cpu')) {
        $targets += [pscustomobject]@{
            Name = 'cpu'
            Url  = "https://github.com/ggml-org/llama.cpp/releases/download/$llamaTag/llama-$llamaTag-bin-win-cpu-x64.zip"
        }
    }

    foreach ($t in $targets) {
        Write-Step ("llama.cpp {0} ({1})" -f $llamaTag, $t.Name)
        $dest = Join-Path $llamaDir $t.Name
        if (-not $Force -and (Test-Path -LiteralPath (Join-Path $dest 'llama-server.exe'))) {
            Write-Host ("  [ok] already present: {0}" -f $dest) -ForegroundColor Green
            continue
        }
        $zip = Join-Path $tmp ("llama-" + $t.Name + ".zip")
        Get-File $t.Url $zip
        if (Test-Path -LiteralPath $dest) { Remove-Item -LiteralPath $dest -Recurse -Force }
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $dest -Force
        if (-not (Test-Path -LiteralPath (Join-Path $dest 'llama-server.exe'))) {
            throw "llama-server.exe missing after extracting $($t.Name) build"
        }
        Write-Host ("  installed {0}" -f $dest) -ForegroundColor Green
    }

    # ---------------- app-local VC++ runtime ----------------
    # llama.cpp is built against the MSVC runtime. Windows 10/11 usually has it,
    # but not always on a clean install, so keep the three redistributable DLLs
    # next to the engine to make the drive self-contained.
    $vcDlls = @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
    foreach ($t in $targets) {
        $dest = Join-Path $llamaDir $t.Name
        if (-not (Test-Path -LiteralPath $dest)) { continue }
        foreach ($d in $vcDlls) {
            $from = Join-Path $env:SystemRoot "System32\$d"
            if (Test-Path -LiteralPath $from) {
                Copy-Item -LiteralPath $from -Destination (Join-Path $dest $d) -Force
            }
            else {
                Write-Warning ("{0} not found in System32; if the engine fails to start on another PC, install the Microsoft Visual C++ 2015-2022 x64 Redistributable, then re-run this script with -Force." -f $d)
            }
        }
        Write-Host ("  app-local VC++ runtime ready in {0}" -f $dest) -ForegroundColor Green
    }

    # ---------------- check ----------------
    if (-not $NoCheck) {
        Write-Step 'Installation check'
        & (Join-Path $pyDir 'python.exe') (Join-Path $Root 'app\server.py') --check
        if ($LASTEXITCODE -ne 0) {
            Write-Warning 'The installation check reported problems. See the output above.'
        }
    }

    Write-Host ''
    Write-Host 'Runtime rebuild complete.' -ForegroundColor Green
}
finally {
    Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
