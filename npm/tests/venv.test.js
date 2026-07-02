"use strict";

const assert = require("assert");

const { parsePyvenvCfg, majorMinor, venvDriftReason } = require("../lib/venv");

function test(name, fn) {
  try {
    fn();
    process.stdout.write(`✓ ${name}\n`);
  } catch (err) {
    process.stderr.write(`✗ ${name}\n${err.stack || err}\n`);
    process.exitCode = 1;
  }
}

const CFG = [
  "home = /opt/homebrew/opt/python@3.12/bin",
  "include-system-site-packages = false",
  "version = 3.12.4",
].join("\n");

test("parsePyvenvCfg extracts home and version", () => {
  const parsed = parsePyvenvCfg(CFG);
  assert.strictEqual(parsed.home, "/opt/homebrew/opt/python@3.12/bin");
  assert.strictEqual(parsed.version, "3.12.4");
});

test("majorMinor truncates patch and tolerates junk", () => {
  assert.strictEqual(majorMinor("3.12.4"), "3.12");
  assert.strictEqual(majorMinor("garbage"), "");
});

test("healthy venv → no drift", () => {
  assert.strictEqual(
    venvDriftReason({ cfgText: CFG, homeExists: true, runningVersion: "3.12.9" }),
    null,
  );
});

test("patch-level difference is routine, not drift", () => {
  assert.strictEqual(
    venvDriftReason({ cfgText: CFG, homeExists: true, runningVersion: "3.12.1" }),
    null,
  );
});

test("missing base interpreter → rebuild (Homebrew upgrade breakage)", () => {
  const reason = venvDriftReason({ cfgText: CFG, homeExists: false, runningVersion: "3.12.4" });
  assert.ok(reason && reason.includes("base interpreter is gone"));
  assert.ok(reason.includes("3.12.4"));
});

test("venv python that will not run → rebuild", () => {
  const reason = venvDriftReason({ cfgText: CFG, homeExists: true, runningVersion: null });
  assert.ok(reason && reason.includes("does not run"));
});

test("minor version mismatch → rebuild with both versions named", () => {
  const reason = venvDriftReason({ cfgText: CFG, homeExists: true, runningVersion: "3.14.6" });
  assert.ok(reason && reason.includes("3.12.4") && reason.includes("3.14.6"));
});

test("no pyvenv.cfg → never judge or touch (safety parity with install.sh)", () => {
  assert.strictEqual(
    venvDriftReason({ cfgText: "", homeExists: false, runningVersion: null }),
    null,
  );
});
