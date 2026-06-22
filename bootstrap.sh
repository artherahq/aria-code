#!/usr/bin/env bash
# ============================================================
#  Aria Code — Zero-dependency Bootstrap
#  Works on a completely fresh Mac with nothing installed.
#
#  Usage (one-liner):
#    curl -fsSL https://raw.githubusercontent.com/artherahq/aria-code/main/bootstrap.sh | bash
#
#  Or after cloning:
#    bash bootstrap.sh
# ============================================================

set -uo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; NC='\033[0m'

info()  { echo -e "${CYAN}[aria]${NC} $*"; }
ok()    { echo -e "${GREEN}[aria] ✓${NC} $*"; }
warn()  { echo -e "${YELLOW}[aria] ⚠${NC} $*"; }
err()   { echo -e "${RED}[aria] ✗${NC} $*" >&2; }
step()  { echo -e "\n${BOLD}── $* ${NC}"; }
hr()    { echo -e "${DIM}────────────────────────────────────────${NC}"; }

echo -e "
${CYAN}    _         _
   / \\  _ __ (_) __ _
  / _ \\| '__|| |/ _\` |
 / ___ \\  |  | | (_| |
/_/   \\_\\_|  |_|\\__,_|${NC}

  ${BOLD}Aria Code${NC} ${DIM}— Bootstrap Installer${NC}
  ${DIM}Fresh Mac? No problem. We handle everything.${NC}
"
hr

OS="$(uname -s)"
if [[ "$OS" != "Darwin" && "$OS" != "Linux" ]]; then
    err "Unsupported OS: $OS"
    err "Windows users: run  .\\install.ps1  in PowerShell"
    exit 1
fi

# ── Step 1: Xcode Command Line Tools (macOS only) ─────────────
if [[ "$OS" == "Darwin" ]]; then
    step "1 / 5  Xcode Command Line Tools"
    if xcode-select -p &>/dev/null 2>&1; then
        ok "Already installed: $(xcode-select -p)"
    else
        warn "Not installed — this provides git, make, and compiler tools."
        info "Starting installer (a dialog box will appear)…"
        xcode-select --install 2>/dev/null || true

        echo
        echo -e "  ${YELLOW}A system dialog has appeared asking to install developer tools.${NC}"
        echo -e "  ${YELLOW}Click \"Install\" and wait for it to finish (~5 min on fast internet).${NC}"
        echo
        read -r -p "  Press ENTER once the Xcode installation is complete: "

        if ! xcode-select -p &>/dev/null 2>&1; then
            err "Xcode CLI tools still not detected."
            err "Please install manually: xcode-select --install"
            exit 1
        fi
        ok "Xcode Command Line Tools installed"
    fi
else
    step "1 / 5  System tools (Linux)"
    if ! command -v git &>/dev/null; then
        info "Installing git via apt/yum…"
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq && sudo apt-get install -y git curl
        elif command -v yum &>/dev/null; then
            sudo yum install -y git curl
        fi
    fi
    ok "git $(git --version 2>/dev/null | awk '{print $3}')"
fi

# ── Step 2: Homebrew (macOS) ──────────────────────────────────
if [[ "$OS" == "Darwin" ]]; then
    step "2 / 5  Homebrew"
    if command -v brew &>/dev/null; then
        ok "Homebrew already installed: $(brew --version | head -1)"
    else
        info "Installing Homebrew (this may take 2–3 minutes)…"
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

        # Activate brew for the current shell session
        if [[ -f /opt/homebrew/bin/brew ]]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
            echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        elif [[ -f /usr/local/bin/brew ]]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi

        if command -v brew &>/dev/null; then
            ok "Homebrew installed"
        else
            err "Homebrew installation failed — install manually from https://brew.sh"
            exit 1
        fi
    fi
else
    step "2 / 5  Skipping Homebrew (Linux)"
    ok "Not needed on Linux"
fi

# ── Step 3: Python 3.10+ ──────────────────────────────────────
step "3 / 5  Python 3.10+"

PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        MAJOR=$(echo "$VER" | cut -d. -f1)
        MINOR=$(echo "$VER" | cut -d. -f2)
        if [[ "$MAJOR" -ge 3 && "$MINOR" -ge 10 ]]; then
            PYTHON="$cmd"
            ok "Found Python $VER → $cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "Python 3.10+ not found — installing now…"
    if [[ "$OS" == "Darwin" ]]; then
        brew install python@3.12
        eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || true)"
        PYTHON="$(brew --prefix python@3.12)/bin/python3.12"
        if ! command -v "$PYTHON" &>/dev/null; then
            PYTHON="python3"
        fi
    else
        if command -v apt-get &>/dev/null; then
            sudo apt-get update -qq && sudo apt-get install -y python3.12 python3.12-venv python3-pip
            PYTHON="python3.12"
        elif command -v yum &>/dev/null; then
            sudo yum install -y python3.12
            PYTHON="python3.12"
        fi
    fi
    VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "unknown")
    ok "Python $VER installed"
fi

# ── Step 4: Clone or locate repo ─────────────────────────────
step "4 / 5  Aria Code repository"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
INSTALL_SH="$SCRIPT_DIR/install.sh"

if [[ -f "$INSTALL_SH" ]]; then
    # Already inside the repo (user ran: bash bootstrap.sh)
    ok "Running from repo directory: $SCRIPT_DIR"
    REPO_DIR="$SCRIPT_DIR"
else
    # Piped from curl — need to clone
    REPO_DIR="$HOME/aria-code"
    if [[ -d "$REPO_DIR/.git" ]]; then
        info "Repo exists at $REPO_DIR — pulling latest…"
        git -C "$REPO_DIR" pull --ff-only 2>/dev/null || true
        ok "Repository up to date"
    else
        info "Cloning Aria Code into $REPO_DIR …"
        git clone https://github.com/artherahq/aria-code.git "$REPO_DIR"
        ok "Cloned to $REPO_DIR"
    fi
    INSTALL_SH="$REPO_DIR/install.sh"
fi

# ── Step 5: Hand off to install.sh ───────────────────────────
step "5 / 5  Running installer"
hr
echo

if [[ ! -f "$INSTALL_SH" ]]; then
    err "install.sh not found at $INSTALL_SH"
    exit 1
fi

chmod +x "$INSTALL_SH"
export ARIA_PYTHON="$PYTHON"
cd "$REPO_DIR"
bash "$INSTALL_SH" "$@"
