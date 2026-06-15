#!/usr/bin/env node
/**
 * postinstall.js — runs after `npm install -g aria-code`
 *
 * Installs the Python package from GitHub so the `aria-code` CLI is available.
 * Tries pip3 first, falls back to pip, then gives a clear error.
 */

"use strict";

const { execSync, spawnSync } = require("child_process");
const os = require("os");
const path = require("path");

const REPO = "git+https://github.com/Cinsoul/Aria-Code.git";
const MIN_PYTHON = [3, 10];

const CYAN   = "\x1b[36m";
const GREEN  = "\x1b[32m";
const YELLOW = "\x1b[33m";
const RED    = "\x1b[31m";
const RESET  = "\x1b[0m";
const BOLD   = "\x1b[1m";

function log(msg)  { process.stdout.write(`  ${msg}\n`); }
function ok(msg)   { log(`${GREEN}✓${RESET}  ${msg}`); }
function warn(msg) { log(`${YELLOW}⚠${RESET}  ${msg}`); }
function err(msg)  { log(`${RED}✗${RESET}  ${msg}`); }
function info(msg) { log(`${CYAN}▸${RESET}  ${msg}`); }

// ── Detect Python ────────────────────────────────────────────────────────────

function findPython() {
  const candidates = os.platform() === "win32"
    ? ["python", "python3"]
    : ["python3", "python"];

  for (const cmd of candidates) {
    try {
      const out = execSync(`${cmd} --version 2>&1`, { encoding: "utf8" }).trim();
      const m = out.match(/Python (\d+)\.(\d+)/);
      if (!m) continue;
      const [major, minor] = [parseInt(m[1]), parseInt(m[2])];
      if (major > MIN_PYTHON[0] || (major === MIN_PYTHON[0] && minor >= MIN_PYTHON[1])) {
        return { cmd, version: `${major}.${minor}` };
      }
      warn(`Found ${cmd} ${major}.${minor} but Aria Code requires Python ≥ ${MIN_PYTHON.join(".")}`);
    } catch (_) {
      // not found
    }
  }
  return null;
}

// ── Install pip package ──────────────────────────────────────────────────────

function pipInstall(pythonCmd) {
  const pipCmds = [`${pythonCmd} -m pip`, "pip3", "pip"];

  for (const pip of pipCmds) {
    info(`Trying: ${pip} install "aria-code @ ${REPO}"`);
    const r = spawnSync(
      pip,
      ["install", "--upgrade", `aria-code @ ${REPO}`],
      { stdio: "inherit", shell: true }
    );
    if (r.status === 0) return true;
  }
  return false;
}

// ── Main ─────────────────────────────────────────────────────────────────────

function main() {
  process.stdout.write(`\n${BOLD}  Aria Code — Python package setup${RESET}\n\n`);

  const python = findPython();
  if (!python) {
    err("Python 3.10+ not found.");
    err("Install from https://python.org then re-run: npm install -g aria-code");
    process.exit(1);
  }
  ok(`Python ${python.version} found (${python.cmd})`);

  info("Installing Aria Code Python package from GitHub …");
  const success = pipInstall(python.cmd);

  if (!success) {
    err("pip install failed. Try manually:");
    err(`  ${python.cmd} -m pip install "aria-code @ ${REPO}"`);
    process.exit(1);
  }

  ok("Aria Code installed successfully.");
  process.stdout.write(`\n  ${CYAN}Run:${RESET} ${BOLD}aria-code${RESET}\n\n`);
}

main();
