#!/usr/bin/env node
/**
 * aria-code.js — thin shim that delegates to the Python CLI.
 *
 * Search order for the Python entry point:
 *   1. `aria-code`  in PATH  (installed by pip as a console_script)
 *   2. `python3 -m aria_cli` (fallback for git-clone installs)
 *   3. `python  -m aria_cli`
 */

"use strict";

const { spawnSync } = require("child_process");
const os = require("os");

const args = process.argv.slice(2);
const isWin = os.platform() === "win32";

function run(cmd, cmdArgs) {
  const r = spawnSync(cmd, cmdArgs, {
    stdio: "inherit",
    shell: isWin,
    windowsHide: true,
  });
  return r;
}

// 1. Try the pip-installed console script
{
  const r = run(isWin ? "aria-code.exe" : "aria-code-py", args);
  if (r.status !== null && r.error === undefined) {
    process.exit(r.status);
  }
}

// 2. Try python3 -m aria_cli
for (const py of ["python3", "python"]) {
  const r = run(py, ["-m", "aria_cli", ...args]);
  if (r.status !== null && r.error === undefined) {
    process.exit(r.status);
  }
}

// Nothing worked
process.stderr.write(
  "\n  aria-code: could not find the Python CLI.\n" +
  "  Run: python3 -m pip install --upgrade 'aria-code @ git+https://github.com/Cinsoul/Aria-Code.git'\n\n"
);
process.exit(1);
