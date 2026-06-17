#!/usr/bin/env bash
# ============================================================
#  Aria Code — One-command installer
#  Usage:
#    bash install.sh             # full install (recommended)
#    bash install.sh --core      # core + data only (no file parsers)
#    bash install.sh --dev       # full + dev/test tools
#    bash install.sh --upgrade   # upgrade all installed packages
#    bash install.sh --no-wizard # skip setup wizard at the end
# ============================================================
set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'
info()  { echo -e "${CYAN}[aria]${NC} $*"; }
ok()    { echo -e "${GREEN}[aria] ✓${NC} $*"; }
warn()  { echo -e "${YELLOW}[aria] ⚠${NC} $*"; }
err()   { echo -e "${RED}[aria] ✗${NC} $*" >&2; }
step()  { echo -e "\n${BOLD}$*${NC}"; }
dim()   { echo -e "${DIM}$*${NC}"; }

# ── Banner ────────────────────────────────────────────────────
echo -e "
${CYAN}    _         _
   / \\  _ __ (_) __ _
  / _ \\| '__|| |/ _\` |
 / ___ \\  |  | | (_| |
/_/   \\_\\_|  |_|\\__,_|${NC}

  ${BOLD}Aria Code${NC} ${DIM}— AI Financial Terminal${NC}
"

CLI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${ARIA_VENV:-$CLI_DIR/.venv}"
BIN_DIR="${ARIA_BIN_DIR:-$HOME/.local/bin}"
LINK_PATH="$BIN_DIR/aria-code"
PYTHON="${ARIA_PYTHON:-python3}"

# ── Pre-flight: ensure git & python exist before anything else ─
_OS="$(uname -s)"
_preflight_ok=1

if ! command -v git &>/dev/null; then
    err "git not found."
    if [[ "$_OS" == "Darwin" ]]; then
        echo
        echo -e "  Run ${CYAN}xcode-select --install${NC} and try again."
        echo -e "  Or run our bootstrap instead:"
        echo -e "  ${CYAN}bash bootstrap.sh${NC}"
    else
        echo -e "  Run: sudo apt-get install git  (Debian/Ubuntu)"
        echo -e "       sudo yum install git       (RHEL/CentOS)"
    fi
    _preflight_ok=0
fi

if ! command -v python3 &>/dev/null && ! command -v python &>/dev/null; then
    err "Python 3 not found."
    if [[ "$_OS" == "Darwin" ]]; then
        echo
        if command -v brew &>/dev/null; then
            echo -e "  Run: ${CYAN}brew install python@3.12${NC}"
        else
            echo -e "  Run our bootstrap (handles everything automatically):"
            echo -e "  ${CYAN}bash bootstrap.sh${NC}"
            echo
            echo -e "  Or install manually:"
            echo -e "    1. xcode-select --install"
            echo -e "    2. /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
            echo -e "    3. brew install python@3.12"
        fi
    else
        echo -e "  Run: sudo apt-get install python3.12 python3.12-venv"
    fi
    _preflight_ok=0
fi

if [[ "$_preflight_ok" -eq 0 ]]; then
    echo
    err "Pre-flight checks failed. Fix the issues above and re-run."
    exit 1
fi

# ── Parse flags ───────────────────────────────────────────────
MODE="full"
UPGRADE=0
NO_WIZARD=0
for arg in "$@"; do
    case "$arg" in
        --core)    MODE="core" ;;
        --dev)     MODE="dev" ;;
        --upgrade) UPGRADE=1 ;;
        --no-wizard) NO_WIZARD=1 ;;
        --help|-h)
            echo "Usage: bash install.sh [--core|--dev|--upgrade]"
            echo "  (no flag)    Full install: core + file parsers + data analysis"
            echo "  --core       Minimal: CLI + market data only"
            echo "  --dev        Full + pytest/dev tools"
            echo "  --upgrade    Upgrade all existing packages"
            echo "  --no-wizard  Skip interactive setup wizard"
            exit 0 ;;
        *) warn "Unknown flag: $arg (ignored)" ;;
    esac
done

# ── Python version check ──────────────────────────────────────
step "1 / 7  Checking Python"
if ! command -v "$PYTHON" &>/dev/null; then
    err "Python 3 not found. Install from https://python.org and re-run."
    exit 1
fi
PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$("$PYTHON" -c "import sys; print(sys.version_info.major)")
PY_MINOR=$("$PYTHON" -c "import sys; print(sys.version_info.minor)")
if [[ "$PY_MAJOR" -lt 3 ]] || [[ "$PY_MAJOR" -eq 3 && "$PY_MINOR" -lt 10 ]]; then
    err "Python $PY_VER found, but Aria Code requires Python 3.10+."
    exit 1
fi
ok "Python $PY_VER"

# ── Virtual environment ───────────────────────────────────────
step "2 / 7  Setting up virtual environment"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating venv at $VENV_DIR ..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment created"
else
    ok "Virtual environment exists: $VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"
VENV_PY="$VENV_DIR/bin/python"

info "Upgrading pip..."
"$PIP" install --quiet --upgrade pip

# ── Core packages ────────────────────────────────────────────
step "3 / 7  Installing core packages"
dim "  aiohttp · rich · prompt_toolkit · PyYAML · requests · httpx · websockets"

CORE_PKGS=(
    "aiohttp>=3.9.0"
    "rich>=13.7.0"
    "prompt_toolkit>=3.0.43"
    "PyYAML>=6.0.2"
    "requests>=2.32.0"
    "httpx[http2]>=0.27.0"
    "PyJWT>=2.8.0"
    "apscheduler>=3.10.0"
    "aiofiles>=23.2.0"
    "websockets>=12.0"
    "numpy>=1.26.0"
    "pandas>=2.2.0"
    "scipy>=1.13.0"
    "yfinance>=0.2.55"
    "akshare>=1.14.68"
    "ccxt>=4.4.0"
    "pandas_ta>=0.3.14b"
    "mplfinance>=0.12.9"
)

install_group() {
    local label="$1"; shift
    local pkgs=("$@")
    local failed=()
    for pkg in "${pkgs[@]}"; do
        local name="${pkg%%[>=<!]*}"
        printf "  %-36s" "$name"
        if "$PIP" install --quiet ${UPGRADE:+--upgrade} "$pkg" 2>/dev/null; then
            echo -e "${GREEN}✓${NC}"
        else
            echo -e "${YELLOW}⚠ skipped${NC}"
            failed+=("$pkg")
        fi
    done
    if [[ ${#failed[@]} -gt 0 ]]; then
        warn "$label: ${#failed[@]} package(s) skipped — ${failed[*]}"
        warn "Run manually: $PIP install ${failed[*]}"
    fi
}

install_group "core" "${CORE_PKGS[@]}"
ok "Core packages done"

# ── File analysis packages ────────────────────────────────────
if [[ "$MODE" != "core" ]]; then
    step "4 / 7  Installing file analysis packages"
    dim "  PDF · Word · Excel · HTML · Images · DuckDB SQL"

    FILE_PKGS=(
        "pdfplumber>=0.11.0"
        "pypdf>=4.3.0"
        "python-docx>=1.1.2"
        "openpyxl>=3.1.5"
        "beautifulsoup4>=4.12.3"
        "Pillow>=10.4.0"
        "duckdb>=0.10.3"
    )
    install_group "file-analysis" "${FILE_PKGS[@]}"
    ok "File analysis packages done"
else
    step "4 / 7  Skipping file analysis packages (--core mode)"
    dim "  Run 'bash install.sh' without --core to enable /file /data commands"
fi

# ── Dev packages ─────────────────────────────────────────────
if [[ "$MODE" == "dev" ]]; then
    step "5 / 7  Installing dev/test packages"
    DEV_PKGS=(
        "pytest>=8.2.0"
        "pytest-asyncio>=0.23.7"
    )
    install_group "dev" "${DEV_PKGS[@]}"
    ok "Dev packages done"
else
    step "5 / 7  Skipping dev packages (use --dev to include)"
fi

# ── Ollama (local LLM) ────────────────────────────────────────
step "6 / 7  Checking Ollama (local LLM runtime)"
if command -v ollama &>/dev/null; then
    OLLAMA_VER=$(ollama --version 2>/dev/null | head -1 || echo "unknown")
    ok "Ollama installed: $OLLAMA_VER"
    # Show available models
    MODELS=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | tr '\n' '  ' || true)
    if [[ -n "$MODELS" ]]; then
        dim "  Available models: $MODELS"
    else
        warn "No models downloaded yet."
        dim "  Recommended: ollama pull qwen2.5:7b"
    fi
else
    warn "Ollama not found — local LLM features will be unavailable"
    dim "  Install: https://ollama.com/download"
    SYSTEM="$(uname -s)"
    if [[ "$SYSTEM" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            read -r -p "  Install Ollama via Homebrew? [y/N] " INSTALL_OLLAMA
            if [[ "${INSTALL_OLLAMA:-N}" =~ ^[Yy]$ ]]; then
                brew install ollama && ok "Ollama installed" || warn "Brew install failed — install manually"
            fi
        else
            dim "  Run: curl -fsSL https://ollama.com/install.sh | sh"
        fi
    elif [[ "$SYSTEM" == "Linux" ]]; then
        read -r -p "  Install Ollama now? [y/N] " INSTALL_OLLAMA
        if [[ "${INSTALL_OLLAMA:-N}" =~ ^[Yy]$ ]]; then
            curl -fsSL https://ollama.com/install.sh | sh && ok "Ollama installed" || warn "Install failed — try manually"
        fi
    fi
fi

# ── CLI launcher symlink ──────────────────────────────────────
step "7 / 7  Registering aria-code launcher"
mkdir -p "$BIN_DIR"

# Patch the aria-code launcher shebang to point at the venv python
LAUNCHER="$CLI_DIR/aria-code"
if [[ -f "$LAUNCHER" ]]; then
    # Rewrite first line to use venv python
    SHEBANG="#!$VENV_DIR/bin/python"
    if [[ "$(head -1 "$LAUNCHER")" != "$SHEBANG" ]]; then
        # Create patched launcher in venv/bin
        cp "$LAUNCHER" "$VENV_DIR/bin/aria-code-script"
        sed -i.bak "1s|.*|$SHEBANG|" "$VENV_DIR/bin/aria-code-script"
        rm -f "$VENV_DIR/bin/aria-code-script.bak"
        chmod +x "$VENV_DIR/bin/aria-code-script"
    fi
    ln -sf "$VENV_DIR/bin/aria-code-script" "$LINK_PATH" 2>/dev/null || \
    ln -sf "$LAUNCHER" "$LINK_PATH"
else
    # No launcher script — create one
    cat > "$VENV_DIR/bin/aria-code-launcher" <<EOF
#!/bin/bash
exec "$VENV_DIR/bin/python" "$CLI_DIR/aria_cli.py" "\$@"
EOF
    chmod +x "$VENV_DIR/bin/aria-code-launcher"
    ln -sf "$VENV_DIR/bin/aria-code-launcher" "$LINK_PATH"
fi
ok "Launcher → $LINK_PATH"

# ── PATH check ────────────────────────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR is not in your PATH."
    echo
    echo -e "  Add this to your ${BOLD}~/.zshrc${NC} or ${BOLD}~/.bashrc${NC}:"
    echo -e "  ${CYAN}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
    echo
fi

# ── Verify installation ───────────────────────────────────────
echo
info "Verifying installation..."
"$VENV_PY" -c "
import sys
ok = []
fail = []
checks = [
    ('rich',           'import rich'),
    ('aiohttp',        'import aiohttp'),
    ('prompt_toolkit', 'import prompt_toolkit'),
    ('yfinance',       'import yfinance'),
    ('akshare',        'import akshare'),
    ('pandas',         'import pandas'),
    ('numpy',          'import numpy'),
]
if '${MODE}' != 'core':
    checks += [
        ('pdfplumber',    'import pdfplumber'),
        ('pypdf',         'import pypdf'),
        ('python-docx',   'import docx'),
        ('openpyxl',      'import openpyxl'),
        ('beautifulsoup4','from bs4 import BeautifulSoup'),
        ('Pillow',        'from PIL import Image'),
        ('duckdb',        'import duckdb'),
    ]
for name, stmt in checks:
    try:
        exec(stmt)
        ok.append(name)
    except ImportError:
        fail.append(name)

print(f'  ✓ {len(ok)} packages OK' + (f'  ✗ {len(fail)} missing: {fail}' if fail else ''))
sys.exit(1 if fail else 0)
" && VERIFY_OK=1 || VERIFY_OK=0

# ── Summary ───────────────────────────────────────────────────
echo
echo -e "╔════════════════════════════════════════╗"
if [[ "$VERIFY_OK" -eq 1 ]]; then
    echo -e "║  ${GREEN}${BOLD}Aria Code installed successfully!${NC}       ║"
else
    echo -e "║  ${YELLOW}${BOLD}Install complete (some warnings above)${NC}  ║"
fi
echo -e "╚════════════════════════════════════════╝"
echo
echo -e "  ${BOLD}Quick start:${NC}"
echo -e "  ${CYAN}aria-code${NC}                   # interactive REPL"
echo -e "  ${CYAN}aria-code -p \"AAPL 分析\"${NC}    # one-shot query"
echo -e "  ${CYAN}aria-code --help${NC}             # all options"
echo
echo -e "  ${BOLD}File analysis:${NC}"
echo -e "  ${CYAN}/file load ~/report.pdf${NC}      # load any document"
echo -e "  ${CYAN}/file analyze all${NC}            # 4-layer deep analysis"
echo -e "  ${CYAN}/file ask <question>${NC}         # multi-turn Q&A"
echo
echo -e "  ${BOLD}Market data:${NC}"
echo -e "  ${CYAN}/realty market 北京 上海${NC}     # 房价指数"
echo -e "  ${CYAN}/corr AAPL MSFT TSLA SPY${NC}     # 相关性矩阵"
echo -e "  ${CYAN}/ptbt AAPL MSFT GOOG 2y${NC}      # 组合回测"
echo
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo -e "  ${YELLOW}Remember to add $BIN_DIR to your PATH!${NC}"
    echo
fi

# ── Setup wizard ──────────────────────────────────────────────
if [[ "$NO_WIZARD" -eq 0 ]]; then
    echo -e "  ${DIM}(run with --no-wizard to skip)${NC}"
    read -r -p "  Run first-time setup wizard now? [Y/n] " RUN_WIZARD
    if [[ "${RUN_WIZARD:-Y}" =~ ^[Yy]$ ]] || [[ -z "${RUN_WIZARD:-}" ]]; then
        echo
        "$VENV_PY" "$CLI_DIR/setup_wizard.py"
    else
        echo
        dim "  You can run the wizard later: python3 setup_wizard.py"
        dim "  Or set up manually: cp .env.daemon.template ~/.aria/.env && edit it"
    fi
fi
