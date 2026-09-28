"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "aria-launcher-test-"));
try {
  const cli = path.join(root, "src", "aria_code", "aria_cli.py");
  const python = path.join(root, ".venv", "bin", "python");
  fs.mkdirSync(path.dirname(cli), { recursive: true });
  fs.mkdirSync(path.dirname(python), { recursive: true });
  fs.writeFileSync(cli, "# test entrypoint\n");
  fs.writeFileSync(python, "#!/bin/sh\nprintf '%s\\n' \"$1\"\n", { mode: 0o755 });

  const result = spawnSync(process.execPath, [path.join(__dirname, "..", "bin", "aria-code.js"), "--version"], {
    encoding: "utf8",
    env: { ...process.env, ARIA_HOME: root },
  });
  assert.strictEqual(result.status, 0, result.stderr);
  assert.strictEqual(result.stdout.trim(), cli);
  process.stdout.write("✓ launcher finds the src-layout CLI\n");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}
