<#
.SYNOPSIS
    Aria Code Windows 安装脚本 (uv-powered)
.DESCRIPTION
    用 uv 自动安装 Aria Code：创建虚拟环境（必要时自动下载 Python）、
    从 pyproject.toml 安装依赖、注册 aria-code 命令，并运行首次配置向导。

    依赖来源是 pyproject.toml（唯一真源）：
      (默认)    →  .[full]   核心 + cn + crypto + charts + data + files
      -Core     →  .         仅精简核心
      -Dev      →  .[all]    full + 券商 + 回测 + 开发工具

.PARAMETER Core
    仅安装精简核心（不含可选数据源/文件解析/图表）

.PARAMETER Dev
    安装全部（含券商、回测、pytest 等）

.PARAMETER Upgrade
    升级所有已安装的包

.PARAMETER NoWizard
    跳过首次配置向导

.EXAMPLE
    .\install.ps1
    .\install.ps1 -Core
    .\install.ps1 -Upgrade
#>

[CmdletBinding()]
param(
    [switch]$Core,
    [switch]$Dev,
    [switch]$Upgrade,
    [switch]$NoWizard
)

$ErrorActionPreference = "Stop"
$ARIA_DIR = $PSScriptRoot
$VENV_DIR = Join-Path $ARIA_DIR ".venv"

# ── 颜色输出 ──────────────────────────────────────────────────────────────────

function Write-Step   { param($msg) Write-Host "  [*] $msg" -ForegroundColor Cyan }
function Write-OK     { param($msg) Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Warn   { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Fail   { param($msg) Write-Host "  [X] $msg" -ForegroundColor Red; exit 1 }

# ── Banner ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  +==========================================+" -ForegroundColor Cyan
Write-Host "  |       Aria Code - Windows Installer       |" -ForegroundColor Cyan
Write-Host "  +==========================================+" -ForegroundColor Cyan
Write-Host ""

# Map flags -> pyproject extra
$extra = "full"
if ($Core) { $extra = "" }
if ($Dev)  { $extra = "all" }

# ── Step 1: package manager (uv) ──────────────────────────────────────────────

Write-Step "Setting up package manager (uv)..."

$useUv = $false
if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-OK "uv found: $(uv --version)"
    $useUv = $true
} else {
    Write-Host "  Installing uv (fast Python package manager)..." -ForegroundColor Yellow
    try {
        powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
        # uv installs to %USERPROFILE%\.local\bin — make it visible this session
        $uvBin = Join-Path $env:USERPROFILE ".local\bin"
        if (Test-Path $uvBin) { $env:Path = "$uvBin;$env:Path" }
    } catch {
        Write-Warn "uv install failed — will try python venv + pip fallback"
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        Write-OK "uv installed: $(uv --version)"
        $useUv = $true
    } else {
        Write-Warn "uv unavailable — falling back to python venv + pip"
    }
}

# ── Step 2: virtual environment ───────────────────────────────────────────────

Write-Step "Creating virtual environment..."

if ($useUv) {
    if (-not (Test-Path $VENV_DIR)) {
        # uv downloads a managed CPython if none >=3.10 is present
        try {
            uv venv $VENV_DIR --python 3.12 --seed
        } catch {
            uv venv $VENV_DIR --seed
        }
        Write-OK "Virtual environment created (uv)"
    } else {
        Write-OK "Virtual environment exists"
    }
} else {
    $python = $null
    foreach ($cmd in @("python3", "python", "py")) {
        try {
            $ver = & $cmd --version 2>&1
            if ($ver -match "Python (\d+\.\d+)") {
                if ([version]$Matches[1] -ge [version]"3.10") { $python = $cmd; break }
            }
        } catch {}
    }
    if (-not $python) {
        Write-Warn "Neither uv nor Python 3.10+ found."
        Write-Host "  Install uv:  irm https://astral.sh/uv/install.ps1 | iex" -ForegroundColor Cyan
        Start-Process "https://www.python.org/downloads/"
        Write-Fail "Install uv or Python 3.10+ and re-run."
    }
    if (-not (Test-Path $VENV_DIR)) {
        & $python -m venv $VENV_DIR
        Write-OK "Virtual environment created (venv)"
    } else {
        Write-OK "Virtual environment exists"
    }
}

$venvPy  = Join-Path $VENV_DIR "Scripts\python.exe"

# ── Step 3: dependencies (from pyproject.toml) ────────────────────────────────

Write-Step "Installing dependencies..."

if ($extra -ne "") {
    $target = "$ARIA_DIR[$extra]"
    Write-Host "  target: aria-code[$extra] (editable)" -ForegroundColor DarkGray
} else {
    $target = "$ARIA_DIR"
    Write-Host "  target: aria-code (slim core, editable)" -ForegroundColor DarkGray
}

function Install-Pkgs($t) {
    if ($useUv) {
        if ($Upgrade) { uv pip install --python $venvPy --upgrade -e $t }
        else          { uv pip install --python $venvPy -e $t }
    } else {
        $venvPip = Join-Path $VENV_DIR "Scripts\pip.exe"
        & $venvPip install --quiet --upgrade pip
        if ($Upgrade) { & $venvPip install --upgrade -e $t }
        else          { & $venvPip install -e $t }
    }
    return $LASTEXITCODE -eq 0
}

if (Install-Pkgs $target) {
    Write-OK "Dependencies installed"
} else {
    Write-Warn "Full install failed — retrying slim core so the CLI still works..."
    if (Install-Pkgs $ARIA_DIR) {
        Write-OK "Core installed (some optional features unavailable — use /install later)"
    } else {
        Write-Fail "Dependency install failed. Try: $venvPy -m pip install -e `"$target`""
    }
}

# ── Step 4: launcher ──────────────────────────────────────────────────────────

Write-Step "Creating launcher..."

$launcherDir  = Join-Path $env:USERPROFILE ".local\bin"
$launcherPath = Join-Path $launcherDir "aria-code.bat"
if (-not (Test-Path $launcherDir)) {
    New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null
}

# Prefer the console-script exe created by the editable install
$entryExe = Join-Path $VENV_DIR "Scripts\aria-code.exe"
if (Test-Path $entryExe) {
    $launcherContent = "@echo off`r`n`"$entryExe`" %*"
} else {
    $launcherContent = "@echo off`r`n`"$venvPy`" `"$ARIA_DIR\aria_cli.py`" %*"
}
$launcherContent | Out-File -FilePath $launcherPath -Encoding ASCII
Write-OK "Launcher: $launcherPath"

$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$launcherDir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$userPath;$launcherDir", "User")
    Write-OK "Added $launcherDir to user PATH (restart terminal to apply)"
}

# ── Step 5: Ollama (optional) ─────────────────────────────────────────────────

Write-Step "Checking Ollama..."
try {
    $null = & ollama --version 2>&1
    Write-OK "Ollama installed"
} catch {
    Write-Warn "Ollama not detected"
    $install = Read-Host "  Open Ollama download page? [Y/n]"
    if ($install -ne "n" -and $install -ne "N") {
        Start-Process "https://ollama.com/download"
        Write-Host "  Install Ollama, then re-run setup_wizard.py" -ForegroundColor Yellow
    }
}

# ── Step 6: Config dir ────────────────────────────────────────────────────────

$ariaConfigDir = Join-Path $env:USERPROFILE ".aria"
if (-not (Test-Path $ariaConfigDir)) {
    New-Item -ItemType Directory -Path $ariaConfigDir -Force | Out-Null
    Write-OK "Config dir: $ariaConfigDir"
}

# ── Summary ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  +==========================================+" -ForegroundColor Green
Write-Host "  |             Install complete!            |" -ForegroundColor Green
Write-Host "  +==========================================+" -ForegroundColor Green
Write-Host ""
Write-Host "  Start CLI:   aria-code" -ForegroundColor Green
Write-Host "  One-shot:    aria-code -p `"AAPL analysis`"" -ForegroundColor Green
Write-Host "  Wizard:      python setup_wizard.py" -ForegroundColor Green
Write-Host ""

# ── Run wizard ────────────────────────────────────────────────────────────────

if (-not $NoWizard) {
    $runWizard = Read-Host "  Run first-time setup wizard now? [Y/n]"
    if ($runWizard -ne "n" -and $runWizard -ne "N") {
        & $venvPy (Join-Path $ARIA_DIR "setup_wizard.py")
    }
}
