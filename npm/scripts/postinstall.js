#!/usr/bin/env node
/**
 * postinstall.js — runs automatically after `npm install -g @artheras/aria-code`
 *
 * Handles the full bootstrap chain on a fresh machine:
 *   macOS  : Xcode CLT → Homebrew → Python 3.10+ → git clone → venv → pip
 *   Linux  : git + python3 check → git clone → venv → pip
 *   Windows: Python check → git clone → venv → pip
 *
 * Install target is resolved by ../lib/paths.js:
 *   ARIA_HOME / npm config > existing ~/.aria-code > platform data directory.
 */

"use strict";

const { spawnSync } = require("child_process");
const fs       = require("fs");
const path     = require("path");
const readline = require("readline");
const { resolveAriaPaths } = require("../lib/paths");

// ── Colours ──────────────────────────────────────────────────────────────────
const C = {
  reset:  "\x1b[0m",
  bold:   "\x1b[1m",
  dim:    "\x1b[2m",
  red:    "\x1b[31m",
  green:  "\x1b[32m",
  yellow: "\x1b[33m",
  cyan:   "\x1b[36m",
};

const log   = (m) => process.stdout.write(`  ${m}\n`);
const ok    = (m) => log(`${C.green}✓${C.reset}  ${m}`);
const warn  = (m) => log(`${C.yellow}⚠${C.reset}  ${m}`);
const err   = (m) => log(`${C.red}✗${C.reset}  ${m}`);
const info  = (m) => log(`${C.cyan}▸${C.reset}  ${m}`);
const step  = (n, t) => process.stdout.write(`\n${C.bold}── Step ${n}: ${t}${C.reset}\n`);
const hr    = () => log(`${C.dim}${"─".repeat(44)}${C.reset}`);

const PLATFORM = process.platform;   // darwin | linux | win32
const REPO_URL  = "https://github.com/artherahq/aria-code.git";
const PATHS = resolveAriaPaths();
const INSTALL_DIR = PATHS.installDir;
const INFO_FILE   = PATHS.infoFile;
const MIN_PY = [3, 10];

// ── Helpers ───────────────────────────────────────────────────────────────────

function run(cmd, args = [], opts = {}) {
  return spawnSync(cmd, args, {
    stdio: opts.silent ? "pipe" : "inherit",
    shell: PLATFORM === "win32",
    ...opts,
  });
}

function runOk(cmd, args = [], opts = {}) {
  const r = run(cmd, args, opts);
  return r.status === 0 && !r.error;
}

function capture(cmd, args = []) {
  const r = spawnSync(cmd, args, { encoding: "utf8", shell: PLATFORM === "win32" });
  return r.status === 0 ? (r.stdout || "").trim() : null;
}

function which(cmd) {
  try {
    const r = spawnSync(PLATFORM === "win32" ? "where" : "which", [cmd],
      { encoding: "utf8", stdio: "pipe" });
    return r.status === 0 ? r.stdout.trim().split("\n")[0] : null;
  } catch (_) { return null; }
}

function ask(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(`\n  ${question} `, (ans) => { rl.close(); resolve(ans.trim()); });
  });
}

function ensureDir(p) {
  if (!fs.existsSync(p)) fs.mkdirSync(p, { recursive: true });
}

function writeJson(file, data) {
  ensureDir(path.dirname(file));
  fs.writeFileSync(file, JSON.stringify(data, null, 2));
}

// ── Step 1: Xcode Command Line Tools (macOS only) ─────────────────────────────

async function ensureXcodeCLT() {
  if (PLATFORM !== "darwin") return;
  step(1, "Xcode Command Line Tools");

  const installed = capture("xcode-select", ["-p"]);
  if (installed) {
    ok(`Already installed → ${installed}`);
    return;
  }

  warn("Xcode Command Line Tools not found (provides git, make, compilers).");
  info("Requesting installation — a dialog box will appear…");
  run("xcode-select", ["--install"], { stdio: "ignore" });

  process.stdout.write(`
  ${C.yellow}A system dialog has appeared asking to install developer tools.${C.reset}
  ${C.yellow}Click "Install" and wait for completion (~5 min).${C.reset}
`);
  await ask("Press ENTER once the Xcode installation is complete:");

  const check = capture("xcode-select", ["-p"]);
  if (!check) {
    err("Xcode CLT still not detected. Install manually: xcode-select --install");
    process.exit(1);
  }
  ok("Xcode Command Line Tools installed");
}

// ── Step 2: Homebrew (macOS only) ────────────────────────────────────────────

async function ensureHomebrew() {
  if (PLATFORM !== "darwin") { step(2, "Homebrew — skipped (not macOS)"); return; }
  step(2, "Homebrew");

  if (which("brew")) {
    ok(`Homebrew found: ${capture("brew", ["--version"]).split("\n")[0]}`);
    return;
  }

  warn("Homebrew not found. Installing now (this may take 2–3 minutes)…");
  const installCmd = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"';
  const r = spawnSync("/bin/bash", ["-c", installCmd], { stdio: "inherit" });
  if (r.status !== 0) {
    err("Homebrew installation failed. Install manually from https://brew.sh");
    process.exit(1);
  }

  // Activate brew in current process env
  for (const prefix of ["/opt/homebrew", "/usr/local"]) {
    const shellenv = capture(`${prefix}/bin/brew`, ["shellenv"]);
    if (shellenv) {
      shellenv.split("\n").forEach((line) => {
        const m = line.match(/^export (\w+)="(.+)"$/);
        if (m) process.env[m[1]] = m[2];
      });
      break;
    }
  }

  ok("Homebrew installed");
}

// ── Step 3: Python 3.10+ ─────────────────────────────────────────────────────

function detectPython() {
  const candidates = PLATFORM === "win32"
    ? ["python", "python3", "py"]
    : ["python3.13", "python3.12", "python3.11", "python3.10", "python3", "python"];

  for (const cmd of candidates) {
    const out = capture(cmd, ["--version"]);
    if (!out) continue;
    const m = out.match(/Python (\d+)\.(\d+)/);
    if (!m) continue;
    const [major, minor] = [+m[1], +m[2]];
    if (major > MIN_PY[0] || (major === MIN_PY[0] && minor >= MIN_PY[1])) {
      return { cmd, version: `${major}.${minor}`, path: which(cmd) || cmd };
    }
  }
  return null;
}

async function ensurePython() {
  step(3, "Python 3.10+");

  let python = detectPython();
  if (python) {
    ok(`Python ${python.version} → ${python.path}`);
    return python;
  }

  warn(`Python ${MIN_PY.join(".")}+ not found. Installing…`);

  if (PLATFORM === "darwin") {
    const brewOk = runOk("brew", ["install", "python@3.12"]);
    if (!brewOk) {
      err("brew install python@3.12 failed. Install from https://python.org");
      process.exit(1);
    }
    // Rehash after brew install
    python = detectPython();
    if (!python) {
      // Try the explicit brew path
      const brewPrefix = capture("brew", ["--prefix", "python@3.12"]);
      if (brewPrefix) {
        const explicit = path.join(brewPrefix, "bin", "python3.12");
        if (fs.existsSync(explicit)) python = { cmd: explicit, version: "3.12", path: explicit };
      }
    }
  } else if (PLATFORM === "linux") {
    const apt = which("apt-get");
    const yum = which("yum");
    if (apt) runOk("sudo", ["apt-get", "install", "-y", "python3.12", "python3.12-venv", "python3-pip"]);
    else if (yum) runOk("sudo", ["yum", "install", "-y", "python3.12"]);
    python = detectPython();
  } else {
    err("Python 3.10+ required. Download from https://python.org/downloads/");
    process.exit(1);
  }

  if (!python) {
    err("Python installation failed. Install manually from https://python.org");
    process.exit(1);
  }
  ok(`Python ${python.version} installed`);
  return python;
}

// ── Step 4: git ───────────────────────────────────────────────────────────────

function ensureGit() {
  step(4, "git");
  if (which("git")) {
    ok(`git ${capture("git", ["--version"]).replace("git version ", "")}`);
    return;
  }
  err("git not found.");
  if (PLATFORM === "darwin")
    err("Run: xcode-select --install  then re-run: npm install -g @artheras/aria-code");
  else
    err("Run: sudo apt-get install git  then re-run: npm install -g @artheras/aria-code");
  process.exit(1);
}

// ── Step 5: Clone or update repo ──────────────────────────────────────────────

function ensureRepo() {
  step(5, "Aria Code repository");
  ensureDir(INSTALL_DIR);
  info(`Runtime path: ${INSTALL_DIR} (${PATHS.installDirSource})`);

  const gitDir = path.join(INSTALL_DIR, ".git");
  if (fs.existsSync(gitDir)) {
    info(`Updating existing repo at ${INSTALL_DIR} …`);
    const r = run("git", ["-C", INSTALL_DIR, "pull", "--ff-only"]);
    if (r.status !== 0) warn("git pull failed — using existing version");
    else ok("Repository up to date");
  } else {
    info(`Cloning Aria Code into ${INSTALL_DIR} …`);
    const r = run("git", ["clone", "--depth=1", REPO_URL, INSTALL_DIR]);
    if (r.status !== 0) {
      err(`git clone failed. Try manually:\n  git clone ${REPO_URL} ${INSTALL_DIR}`);
      process.exit(1);
    }
    ok(`Cloned to ${INSTALL_DIR}`);
  }
}

// ── Step 6: Virtual environment + pip ────────────────────────────────────────

// ── uv helpers (fast Python package manager) ─────────────────────────────────

function uvLocalPath() {
  const home = process.env.HOME || process.env.USERPROFILE || "";
  return path.join(home, ".local", "bin", PLATFORM === "win32" ? "uv.exe" : "uv");
}

function findUv() {
  if (which("uv")) return "uv";
  const local = uvLocalPath();
  if (fs.existsSync(local)) return local;
  return null;
}

function installUv() {
  info("Installing uv (fast Python package manager)…");
  let r;
  if (PLATFORM === "win32") {
    r = spawnSync("powershell", ["-ExecutionPolicy", "ByPass", "-c",
      "irm https://astral.sh/uv/install.ps1 | iex"], { stdio: "inherit" });
  } else {
    r = spawnSync("/bin/bash", ["-c",
      "curl -LsSf https://astral.sh/uv/install.sh | sh"], { stdio: "inherit" });
  }
  if (r && r.status === 0) {
    const uv = findUv();
    if (uv) ok("uv installed");
    return uv;
  }
  warn("uv install failed — falling back to python venv + pip");
  return null;
}

// ── China mirror support (avoids GitHub / PyPI timeouts) ─────────────────────
// Opt in with ARIA_CN=1 (or ARIA_MIRROR=cn). Also auto-applied as a retry when a
// download times out, so users behind the Great Firewall still get a clean
// install. Honors any UV_*/PIP_* values the user already set.
const CN_MIRROR  = process.env.ARIA_CN === "1" || process.env.ARIA_MIRROR === "cn";
const CN_PYPI    = "https://pypi.tuna.tsinghua.edu.cn/simple";
const CN_PY_REPO = "https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download";

function mirrorEnv(base) {
  return {
    ...base,
    UV_DEFAULT_INDEX:         base.UV_DEFAULT_INDEX         || CN_PYPI,
    UV_PYTHON_INSTALL_MIRROR: base.UV_PYTHON_INSTALL_MIRROR || CN_PY_REPO,
    PIP_INDEX_URL:            base.PIP_INDEX_URL            || CN_PYPI,
  };
}

function ensureVenv(python, uv) {
  step(6, "Python environment + dependencies");

  const venvDir  = path.join(INSTALL_DIR, ".venv");
  const venvPy   = PLATFORM === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");
  const venvPip  = PLATFORM === "win32"
    ? path.join(venvDir, "Scripts", "pip.exe")
    : path.join(venvDir, "bin", "pip");

  // Editable install of the cloned repo with the "full" feature set; deps come
  // from pyproject.toml (single source of truth) rather than requirements.txt.
  const fullSpec = `${INSTALL_DIR}[full]`;

  let env = CN_MIRROR ? mirrorEnv(process.env) : { ...process.env };
  if (CN_MIRROR) info("Using China mirrors (PyPI Tsinghua + Python build mirror)");

  const venvPyVersion = () =>
    capture(venvPy, ["-c", "import sys;print(f'{sys.version_info.major}.{sys.version_info.minor}')"])
    || (python && python.version) || "3.12";

  if (uv) {
    if (!fs.existsSync(venvPy)) {
      info("Creating virtual environment (uv downloads Python if needed)…");
      let r = run(uv, ["venv", venvDir, "--python", "3.12", "--seed"], { env });
      if (r.status !== 0 && !CN_MIRROR) {
        warn("Python download failed — retrying via China mirror…");
        env = mirrorEnv(env);
        r = run(uv, ["venv", venvDir, "--python", "3.12", "--seed"], { env });
      }
      if (r.status !== 0) r = run(uv, ["venv", venvDir, "--seed"], { env });
      if (r.status !== 0) { err("uv venv failed."); process.exit(1); }
      ok(`venv created at ${venvDir}`);
    } else {
      ok(`venv exists: ${venvDir}`);
    }

    info("Installing dependencies (uv, from pyproject.toml)…");
    let r = run(uv, ["pip", "install", "--python", venvPy, "-e", fullSpec], { env });
    if (r.status !== 0 && !CN_MIRROR) {
      warn("Install failed — retrying via China mirror (PyPI Tsinghua)…");
      env = mirrorEnv(env);
      r = run(uv, ["pip", "install", "--python", venvPy, "-e", fullSpec], { env });
    }
    if (r.status !== 0) {
      warn("Full install failed — retrying with slim core so the CLI still works…");
      r = run(uv, ["pip", "install", "--python", venvPy, "-e", INSTALL_DIR], { env });
      if (r.status === 0) ok("Core installed (optional features via /install later)");
      else warn("Some packages failed — basic features may still work.");
    } else {
      ok("All dependencies installed (uv)");
    }
    return { venvPy, venvPip, venvDir, pythonVersion: venvPyVersion() };
  }

  // ── pip fallback (no uv available) ──────────────────────────────────────────
  if (!fs.existsSync(venvPy)) {
    info("Creating virtual environment…");
    const r = run(python.cmd, ["-m", "venv", venvDir]);
    if (r.status !== 0) {
      err("Failed to create venv. Check that python3-venv is installed.");
      process.exit(1);
    }
    ok(`venv created at ${venvDir}`);
  } else {
    ok(`venv exists: ${venvDir}`);
  }

  info("Upgrading pip…");
  run(venvPip, ["install", "--quiet", "--upgrade", "pip"], { env });

  info("Installing dependencies (pip, from pyproject.toml — may take 3–5 min)…");
  let r = run(venvPip, ["install", "-e", fullSpec], { env });
  if (r.status !== 0 && !CN_MIRROR) {
    warn("Install failed — retrying via China mirror (PyPI Tsinghua)…");
    env = mirrorEnv(env);
    r = run(venvPip, ["install", "-e", fullSpec], { env });
  }
  if (r.status !== 0) {
    warn("Full install failed — retrying with slim core…");
    r = run(venvPip, ["install", "-e", INSTALL_DIR], { env });
    if (r.status === 0) ok("Core installed (optional features via /install later)");
    else warn("Some packages failed — basic features may still work.");
  } else {
    ok("All Python dependencies installed");
  }

  return { venvPy, venvPip, venvDir, pythonVersion: (python && python.version) || venvPyVersion() };
}

// ── Step 7: Write install-info ────────────────────────────────────────────────

function writeInstallInfo(python, venv) {
  step(7, "Saving install configuration");

  const info_data = {
    schemaVersion: 2,
    installDir: INSTALL_DIR,
    installDirSource: PATHS.installDirSource,
    venvDir:    venv.venvDir,
    venvPy:     venv.venvPy,
    ariaCli:    path.join(INSTALL_DIR, "aria_cli.py"),
    configDir:  PATHS.configDir,
    cacheDir:   PATHS.cacheDir,
    infoFile:   INFO_FILE,
    configInfoFile: PATHS.configInfoFile,
    legacyInstallDir: PATHS.legacyInstallDir,
    pythonVersion: python.version,
    installedAt:   new Date().toISOString(),
    platform:      PLATFORM,
  };

  writeJson(INFO_FILE, info_data);
  writeJson(PATHS.configInfoFile, info_data);
  ok(`Config saved → ${INFO_FILE}`);
  ok(`Config mirror → ${PATHS.configInfoFile}`);
  return info_data;
}

// ── Summary ───────────────────────────────────────────────────────────────────

function printSummary() {
  process.stdout.write(`
${C.green}╔════════════════════════════════════════════╗
║  ${C.bold}Aria Code installed successfully!${C.reset}${C.green}          ║
╚════════════════════════════════════════════╝${C.reset}

  ${C.bold}Start:${C.reset}       ${C.cyan}aria-code${C.reset}
  ${C.bold}One-shot:${C.reset}    ${C.cyan}aria-code -p "AAPL 分析"${C.reset}
  ${C.bold}Help:${C.reset}        ${C.cyan}aria-code --help${C.reset}

  ${C.bold}Runtime:${C.reset}     ${INSTALL_DIR}
  ${C.bold}Config:${C.reset}      ${PATHS.configDir}
  ${C.bold}Cache:${C.reset}       ${PATHS.cacheDir}

  ${C.dim}Tip: Pull a free local model for offline use:${C.reset}
  ${C.cyan}ollama pull qwen2.5:7b${C.reset}

`);
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  process.stdout.write(`
${C.cyan}    _         _
   / \\  _ __ (_) __ _
  / _ \\| '__|| |/ _\` |
 / ___ \\  |  | | (_| |
/_/   \\_\\_|  |_|\\__,_|${C.reset}

  ${C.bold}Aria Code${C.reset} ${C.dim}— npm installer${C.reset}
`);
  hr();

  try {
    // git is the only hard prerequisite (to clone the repo). On macOS the Xcode
    // CLT provides it; uv supplies Python, so we no longer need Homebrew or a
    // system Python install — that brew step is exactly what times out for many
    // users behind restrictive networks.
    await ensureXcodeCLT();

    const uv = findUv() || installUv();

    let python;
    if (uv) {
      info("uv is available — it will manage Python (skipping Homebrew + system Python)");
      python = { cmd: null, version: "uv-managed", path: "(uv-managed)" };
    } else {
      await ensureHomebrew();
      python = await ensurePython();
    }

    ensureGit();
    ensureRepo();
    const venv = ensureVenv(python, uv);
    if (venv.pythonVersion) python.version = venv.pythonVersion;
    writeInstallInfo(python, venv);
    printSummary();
  } catch (e) {
    err(`Unexpected error: ${e.message}`);
    process.exit(1);
  }
}

main();
