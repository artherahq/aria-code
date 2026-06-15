<#
.SYNOPSIS
    Aria Code Windows 安装脚本
.DESCRIPTION
    自动化安装 Aria Code 所需环境：Python 虚拟环境、依赖包、Ollama（可选）
    并运行首次配置向导。

.PARAMETER Core
    仅安装核心依赖（不安装可选包）

.PARAMETER Dev
    同时安装开发依赖（pytest 等）

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
$MIN_PYTHON = [version]"3.10"

# ── 颜色输出 ──────────────────────────────────────────────────────────────────

function Write-Step   { param($msg) Write-Host "  [*] $msg" -ForegroundColor Cyan }
function Write-OK     { param($msg) Write-Host "  [✓] $msg" -ForegroundColor Green }
function Write-Warn   { param($msg) Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Fail   { param($msg) Write-Host "  [✗] $msg" -ForegroundColor Red; exit 1 }

# ── Banner ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║       Aria Code — Windows 安装程序       ║" -ForegroundColor Cyan
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ── Step 1: Python check ──────────────────────────────────────────────────────

Write-Step "检查 Python 版本..."

$python = $null
foreach ($cmd in @("python3", "python", "py")) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match "Python (\d+\.\d+)") {
            $found = [version]$Matches[1]
            if ($found -ge $MIN_PYTHON) {
                $python = $cmd
                Write-OK "找到 Python $found ($cmd)"
                break
            }
        }
    } catch {}
}

if (-not $python) {
    Write-Warn "未找到 Python $MIN_PYTHON+"
    Write-Host "  正在打开 Python 下载页面..." -ForegroundColor Yellow
    Start-Process "https://www.python.org/downloads/"
    Write-Fail "请安装 Python $MIN_PYTHON+ 后重新运行此脚本"
}

# ── Step 2: Virtual environment ───────────────────────────────────────────────

Write-Step "创建虚拟环境 (.venv)..."

if (-not (Test-Path $VENV_DIR)) {
    & $python -m venv $VENV_DIR
    Write-OK "虚拟环境创建完成"
} else {
    Write-OK "虚拟环境已存在，跳过"
}

$pip = Join-Path $VENV_DIR "Scripts\pip.exe"
$py  = Join-Path $VENV_DIR "Scripts\python.exe"

# ── Step 3: Upgrade pip ───────────────────────────────────────────────────────

Write-Step "升级 pip..."
& $pip install --quiet --upgrade pip

# ── Step 4: Core packages ─────────────────────────────────────────────────────

Write-Step "安装核心依赖..."

$core_pkgs = @(
    "aiohttp>=3.9.0",
    "rich>=13.7.0",
    "prompt_toolkit>=3.0.43",
    "PyYAML>=6.0.2",
    "yfinance>=0.2.55",
    "akshare>=1.14.68",
    "numpy>=1.26.0",
    "pandas>=2.2.0",
    "pandas_ta>=0.3.14b",
    "requests>=2.32.0",
    "httpx[http2]>=0.27.0",
    "PyJWT>=2.8.0",
    "apscheduler>=3.10.0",
    "aiofiles>=23.2.0",
    "websockets>=12.0"
)

$upgrade_flag = if ($Upgrade) { "--upgrade" } else { "" }
foreach ($pkg in $core_pkgs) {
    if ($upgrade_flag) {
        & $pip install --quiet --upgrade $pkg
    } else {
        & $pip install --quiet $pkg
    }
}
Write-OK "核心依赖安装完成"

# ── Step 5: File analysis packages ───────────────────────────────────────────

if (-not $Core) {
    Write-Step "安装文件解析依赖..."
    $file_pkgs = @(
        "pdfplumber>=0.11.0",
        "pypdf>=4.3.0",
        "python-docx>=1.1.2",
        "openpyxl>=3.1.5",
        "beautifulsoup4>=4.12.3",
        "Pillow>=10.4.0",
        "duckdb>=0.10.3",
        "mplfinance>=0.12.9"
    )
    foreach ($pkg in $file_pkgs) {
        & $pip install --quiet $pkg
    }
    Write-OK "文件解析依赖安装完成"
}

# ── Step 6: Dev packages ──────────────────────────────────────────────────────

if ($Dev) {
    Write-Step "安装开发依赖..."
    & $pip install --quiet pytest>=8.2.0 pytest-asyncio>=0.23.7
    Write-OK "开发依赖安装完成"
}

# ── Step 7: Launcher script ───────────────────────────────────────────────────

Write-Step "创建启动脚本..."

$launcherDir  = Join-Path $env:USERPROFILE ".local\bin"
$launcherPath = Join-Path $launcherDir "aria-code.bat"

if (-not (Test-Path $launcherDir)) {
    New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null
}

$launcherContent = @"
@echo off
"$py" "$ARIA_DIR\aria_cli.py" %*
"@
$launcherContent | Out-File -FilePath $launcherPath -Encoding ASCII
Write-OK "启动脚本: $launcherPath"

# Add to PATH if not already there
$userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($userPath -notlike "*$launcherDir*") {
    [Environment]::SetEnvironmentVariable("PATH", "$userPath;$launcherDir", "User")
    Write-OK "已将 $launcherDir 添加到用户 PATH（重启终端生效）"
}

# ── Step 8: Ollama (optional) ─────────────────────────────────────────────────

Write-Step "检查 Ollama..."
$ollamaInstalled = $false
try {
    $null = & ollama --version 2>&1
    $ollamaInstalled = $true
    Write-OK "Ollama 已安装"
} catch {
    Write-Warn "未检测到 Ollama"
    $install = Read-Host "  是否打开 Ollama 下载页面？[Y/n]"
    if ($install -ne "n" -and $install -ne "N") {
        Start-Process "https://ollama.com/download"
        Write-Host "  请下载并安装 Ollama，然后重新运行 setup_wizard.py" -ForegroundColor Yellow
    }
}

# ── Step 9: Config directory ──────────────────────────────────────────────────

$ariaConfigDir = Join-Path $env:USERPROFILE ".aria"
if (-not (Test-Path $ariaConfigDir)) {
    New-Item -ItemType Directory -Path $ariaConfigDir -Force | Out-Null
    Write-OK "配置目录: $ariaConfigDir"
}

# ── Summary ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "  ╔══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║          安装完成！                      ║" -ForegroundColor Green
Write-Host "  ╠══════════════════════════════════════════╣" -ForegroundColor Green
Write-Host "  ║  启动向导:  python setup_wizard.py       ║" -ForegroundColor Green
Write-Host "  ║  启动 CLI:  aria-code  (或 python aria_cli.py) ║" -ForegroundColor Green
Write-Host "  ║  启动守护:  python aria_daemon.py        ║" -ForegroundColor Green
Write-Host "  ╚══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

# ── Step 10: Run wizard ───────────────────────────────────────────────────────

if (-not $NoWizard) {
    $runWizard = Read-Host "  是否现在运行首次配置向导？[Y/n]"
    if ($runWizard -ne "n" -and $runWizard -ne "N") {
        & $py (Join-Path $ARIA_DIR "setup_wizard.py")
    }
}
