#!/usr/bin/env node
/**
 * aria-code — global CLI launcher
 *
 * Reads ~/.aria-code/.npm-install-info.json (written by postinstall.js)
 * to find the correct Python venv and aria_cli.py path, then delegates.
 *
 * Fallback chain:
 *   1. venv python from install-info
 *   2. system python3 / python in PATH
 *   3. friendly error with repair instructions
 */

"use strict";

const { spawnSync } = require("child_process");
const fs   = require("fs");
const os   = require("os");
const path = require("path");

const PLATFORM   = os.platform();
const INFO_FILE  = path.join(os.homedir(), ".aria-code", ".npm-install-info.json");
const INSTALL_DIR = path.join(os.homedir(), ".aria-code");

const C = {
  reset: "\x1b[0m", bold: "\x1b[1m", dim: "\x1b[2m",
  red: "\x1b[31m", green: "\x1b[32m", yellow: "\x1b[33m", cyan: "\x1b[36m",
};

// ── Read install info ─────────────────────────────────────────────────────────

function readInstallInfo() {
  try {
    if (fs.existsSync(INFO_FILE)) {
      return JSON.parse(fs.readFileSync(INFO_FILE, "utf8"));
    }
  } catch (_) {}
  return null;
}

// ── Find python executable ────────────────────────────────────────────────────

function findPython(info) {
  // 1. Use venv python from install
  if (info && info.venvPy && fs.existsSync(info.venvPy)) {
    return info.venvPy;
  }
  // 2. System python
  for (const cmd of ["python3", "python"]) {
    const r = spawnSync(PLATFORM === "win32" ? "where" : "which", [cmd],
      { encoding: "utf8", stdio: "pipe" });
    if (r.status === 0) return r.stdout.trim().split("\n")[0];
  }
  return null;
}

// ── Find aria_cli.py ──────────────────────────────────────────────────────────

function findAriaCli(info) {
  const candidates = [
    info && info.ariaCli,
    path.join(INSTALL_DIR, "aria_cli.py"),
    // bundled alongside this script (dev/test only)
    path.join(__dirname, "..", "..", "aria_cli.py"),
  ].filter(Boolean);

  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  return null;
}

// ── Main ─────────────────────────────────────────────────────────────────────

const info    = readInstallInfo();
const python  = findPython(info);
const ariaCli = findAriaCli(info);
const args    = process.argv.slice(2);

if (!python) {
  process.stderr.write(`
${C.red}  aria-code: Python not found.${C.reset}

  Run the installer to set up Python automatically:
    ${C.cyan}npm install -g aria-code${C.reset}

  Or repair the installation:
    ${C.cyan}node $(npm root -g)/aria-code/scripts/postinstall.js${C.reset}

`);
  process.exit(1);
}

if (!ariaCli) {
  process.stderr.write(`
${C.red}  aria-code: aria_cli.py not found at ${INSTALL_DIR}${C.reset}

  Repair the installation:
    ${C.cyan}node $(npm root -g)/aria-code/scripts/postinstall.js${C.reset}

`);
  process.exit(1);
}

const result = spawnSync(python, [ariaCli, ...args], {
  stdio: "inherit",
  env: {
    ...process.env,
    // Ensure the venv's site-packages are used
    VIRTUAL_ENV: info && info.venvDir ? info.venvDir : undefined,
    PYTHONPATH:  path.dirname(ariaCli),
  },
  windowsHide: true,
});

process.exit(result.status ?? 1);
