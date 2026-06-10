#!/usr/bin/env bash
set -euo pipefail

CLI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$CLI_DIR/.venv"
BIN_DIR="${ARIA_CODE_BIN_DIR:-$HOME/.local/bin}"
LINK_PATH="$BIN_DIR/aria-code"

echo "[aria-code] CLI dir: $CLI_DIR"
echo "[aria-code] Preparing virtual environment..."

if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
"$VENV_DIR/bin/pip" install -r "$CLI_DIR/requirements.txt" >/dev/null

mkdir -p "$BIN_DIR"
ln -sf "$CLI_DIR/aria-code" "$LINK_PATH"

echo "[aria-code] Installed launcher: $LINK_PATH"
echo
echo "Next steps:"
echo "  1) Ensure PATH includes $BIN_DIR"
echo "  2) Run: aria-code --help"
