#!/usr/bin/env bash
# scripts/build_native_binary.sh — build, sign, and (optionally) notarize a
# standalone aria-code CLI binary via PyInstaller.
#
# Why this exists: npm install / pip install both fetch or build a separate
# Python runtime on the user's machine, which is what produced the whole
# class of Windows install bugs fixed in 4.1.7 (readline, os.uname(),
# console encoding — all only reachable because the runtime is assembled
# live on the user's box). A self-contained signed+notarized binary sidesteps
# that entire failure class, the same way Claude Code's install.sh does.
#
# Usage:
#   bash scripts/build_native_binary.sh              # build + sign only
#   bash scripts/build_native_binary.sh --notarize    # + submit for notarization
#
# Notarization credentials (only needed with --notarize) — set ONE of:
#   App Store Connect API key (recommended, no password ever typed):
#     APPLE_API_KEY=/absolute/path/AuthKey_XXXXXXXXXX.p8
#     APPLE_API_KEY_ID=XXXXXXXXXX
#     APPLE_API_ISSUER=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
#   or Apple ID + app-specific password, stored once via a keychain profile
#   so the password is never on the command line or in shell history:
#     xcrun notarytool store-credentials "aria-notary" \
#       --apple-id you@example.com --team-id 2HJXDCWWKX --password xxxx-xxxx-xxxx-xxxx
#     export NOTARY_KEYCHAIN_PROFILE=aria-notary

set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script builds the macOS binary. Windows/Linux need their own build job." >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

SIGN_IDENTITY="${SIGN_IDENTITY:-Developer ID Application: Xindi Wang (2HJXDCWWKX)}"
BUILD_DIR="${BUILD_DIR:-$PROJECT_ROOT/dist-native}"
VENV_DIR="$BUILD_DIR/.build-venv"
BIN_NAME="aria-code-bin"
BIN_PATH="$BUILD_DIR/dist/$BIN_NAME"
ENTITLEMENTS="$BUILD_DIR/entitlements.plist"

echo "── Finding a Python within pyproject.toml's requires-python bound ──"
# Bare `python3` isn't safe to assume: it resolves to whatever is first on
# PATH, and pyproject.toml caps at <3.14 (numba, a pandas_ta dependency,
# hard-refuses to build on 3.14 — see the requires-python comment). Search
# newest-first so the freeze picks up current interpreter features.
BUILD_PYTHON=""
for cand in python3.13 python3.12 python3.11 python3.10; do
  if command -v "$cand" >/dev/null 2>&1; then
    BUILD_PYTHON="$cand"
    break
  fi
done
if [[ -z "$BUILD_PYTHON" ]]; then
  echo "No Python 3.10-3.13 found on PATH (pyproject.toml requires-python is >=3.10,<3.14)." >&2
  echo "Install one, e.g.: brew install python@3.13" >&2
  exit 1
fi
echo "  using $BUILD_PYTHON ($("$BUILD_PYTHON" --version))"

echo "── Building isolated venv for the freeze (keeps project .venv untouched) ──"
rm -rf "$VENV_DIR"
"$BUILD_PYTHON" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet -e "$PROJECT_ROOT" pyinstaller

echo "── Running PyInstaller (--onefile) ──"
"$VENV_DIR/bin/pyinstaller" --onefile --name "$BIN_NAME" \
  --distpath "$BUILD_DIR/dist" \
  --workpath "$BUILD_DIR/build" \
  --specpath "$BUILD_DIR" \
  --collect-all rich \
  --collect-all prompt_toolkit \
  aria_cli.py

echo "── Checking for signing identity in keychain ──"
# CI runners (and any machine without the real Developer ID cert imported)
# don't have $SIGN_IDENTITY available — that's expected there, not an error.
# Skip signing/notarization gracefully (produce an unsigned binary + a clear
# note) instead of hard-failing, mirroring how publish.yml skips PyPI
# publish when PYPI_API_TOKEN isn't set rather than failing the whole run.
if ! security find-identity -v -p codesigning 2>/dev/null | grep -qF "$SIGN_IDENTITY"; then
  echo "── Smoke test: the unsigned binary must actually run ──"
  "$BIN_PATH" --version
  echo ""
  echo "Signing identity '$SIGN_IDENTITY' not found in keychain — built unsigned: $BIN_PATH"
  echo "This machine can't sign/notarize (no cert imported). Gatekeeper will reject"
  echo "this binary on end-user machines — only distribute a signed+notarized build"
  echo "(run this script on a machine with the real Developer ID cert imported)."
  exit 0
fi

echo "── Signing with $SIGN_IDENTITY ──"
# disable-library-validation is required in --onefile mode: PyInstaller
# extracts its bundled Python.framework to a temp dir at runtime and
# dlopen()s it, and hardened runtime otherwise refuses to load a library
# whose Team ID doesn't match the parent process — which the bundled
# framework's own signature never will, since it isn't signed by us.
# Confirmed empirically: without this entitlement the signed binary fails
# to launch at all (dlopen error, "different Team IDs"), not just a
# notarization-time rejection.
cat > "$ENTITLEMENTS" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>com.apple.security.cs.disable-library-validation</key>
    <true/>
    <key>com.apple.security.cs.allow-jit</key>
    <true/>
</dict>
</plist>
PLIST

codesign --force --sign "$SIGN_IDENTITY" --options runtime --timestamp \
  --entitlements "$ENTITLEMENTS" "$BIN_PATH"

echo "── Verifying signature ──"
codesign --verify --deep --strict --verbose=2 "$BIN_PATH"

echo "── Smoke test: the signed binary must actually run ──"
"$BIN_PATH" --version

if [[ "${1:-}" != "--notarize" ]]; then
  echo ""
  echo "Signed (not notarized): $BIN_PATH"
  echo "Gatekeeper will currently reject it (spctl -a -vvv -t exec \"$BIN_PATH\")."
  echo "Re-run with --notarize once Apple credentials are set (see header of this script)."
  exit 0
fi

has_api_creds() {
  [[ -n "${APPLE_API_KEY:-}" && -n "${APPLE_API_KEY_ID:-}" && -n "${APPLE_API_ISSUER:-}" ]]
}
has_keychain_profile() {
  [[ -n "${NOTARY_KEYCHAIN_PROFILE:-}" ]]
}

if ! has_api_creds && ! has_keychain_profile; then
  cat >&2 <<'EOF'

No notarization credentials found. Set one of:

  App Store Connect API key:
    APPLE_API_KEY=/absolute/path/AuthKey_XXXXXXXXXX.p8
    APPLE_API_KEY_ID=XXXXXXXXXX
    APPLE_API_ISSUER=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

  Apple ID app-specific password, stored once (password never touches the
  command line or shell history again after this one-time setup):
    xcrun notarytool store-credentials "aria-notary" \
      --apple-id you@example.com --team-id 2HJXDCWWKX --password xxxx-xxxx-xxxx-xxxx
    export NOTARY_KEYCHAIN_PROFILE=aria-notary
EOF
  exit 1
fi

echo "── Zipping for submission (notarytool cannot notarize a bare binary directly) ──"
NOTARY_ZIP="$BUILD_DIR/${BIN_NAME}-notarize.zip"
ditto -c -k --keepParent "$BIN_PATH" "$NOTARY_ZIP"

echo "── Submitting to Apple notarization (this polls and can take a few minutes) ──"
if has_api_creds; then
  xcrun notarytool submit "$NOTARY_ZIP" \
    --key "$APPLE_API_KEY" --key-id "$APPLE_API_KEY_ID" --issuer "$APPLE_API_ISSUER" --wait
else
  xcrun notarytool submit "$NOTARY_ZIP" --keychain-profile "$NOTARY_KEYCHAIN_PROFILE" --wait
fi

# Stapling only works on .app/.pkg/.dmg containers — there is no resource
# fork slot on a bare Mach-O executable to attach a ticket to. A notarized
# bare binary relies on Gatekeeper's online check instead, which happens at
# actual process-launch time via syspolicyd — NOT via `spctl -a -t exec`.
# Confirmed empirically: `spctl -a -t exec` on a bare (non-.app) binary
# reliably answers "rejected (the code is valid but does not seem to be an
# app)" or "Unnotarized Developer ID" regardless of notarization status —
# it's simply the wrong assessment type for a raw Mach-O CLI tool, so it is
# NOT used here. The real test is what actually happens to a user's
# downloaded copy: quarantine it (simulating a browser/curl download) and
# execute it directly. A rejected binary throws an unrecoverable
# "cannot be opened because Apple cannot verify..." error at exec time;
# an accepted one just runs.
echo ""
echo "── Verifying Gatekeeper acceptance (real test: execute a quarantined copy, not spctl -t exec) ──"
GATEKEEPER_TEST_COPY="$BUILD_DIR/gatekeeper-test-copy"
cp "$BIN_PATH" "$GATEKEEPER_TEST_COPY"
xattr -w com.apple.quarantine "0181;$(printf '%x' "$(date +%s)");Safari;" "$GATEKEEPER_TEST_COPY"
if ! "$GATEKEEPER_TEST_COPY" --version >/dev/null 2>&1; then
  echo "Gatekeeper rejected the quarantined binary at launch — notarization did not take effect." >&2
  rm -f "$GATEKEEPER_TEST_COPY"
  exit 1
fi
rm -f "$GATEKEEPER_TEST_COPY"
echo "  quarantined copy launched cleanly — Gatekeeper accepts this binary."

echo ""
echo "Signed + notarized: $BIN_PATH"
