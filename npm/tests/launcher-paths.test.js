// The launcher must not resurrect a path from a stale install record.
//
// A global npm install recorded ariaCli as <installDir>/aria_cli.py, a
// root-level file that stopped existing when the package moved under
// src/aria_code/. findAriaCli tries that recorded path, so the install kept
// pointing at a file that was never coming back and failed with
// "aria_cli.py not found" — while 621MB of the temp directory it named sat
// around supporting it.
//
// Run: node npm/tests/launcher-paths.test.js

const assert = require("assert");
const fs = require("fs");
const path = require("path");

const LAUNCHER = path.join(__dirname, "..", "bin", "aria-code.js");
const source = fs.readFileSync(LAUNCHER, "utf8");

let failures = 0;
function check(name, fn) {
  try {
    fn();
    console.log(`  ok  ${name}`);
  } catch (err) {
    failures += 1;
    console.error(`  FAIL ${name}\n       ${err.message}`);
  }
}

check("no candidate points at the pre-restructure root script", () => {
  const stale = source.match(/["'`][^"'`]*\baria_cli\.py["'`]/g) || [];
  assert.deepStrictEqual(
    stale, [],
    `launcher still references a root-level aria_cli.py: ${stale.join(", ")}`
  );
});

check("the entry point it looks for actually exists in this repo", () => {
  const wanted = source.match(/src\/aria_code\/apps\/cli\/main\.py/g) || [];
  assert.ok(wanted.length > 0, "launcher names no entry point at all");
  const target = path.join(__dirname, "..", "..", "src/aria_code/apps/cli/main.py");
  assert.ok(fs.existsSync(target), `${target} does not exist`);
});

check("a recorded path is a candidate, never the only one", () => {
  // info.ariaCli is what a stale install record supplies. If it were the sole
  // candidate, a bad record would be unrecoverable without a manual repair.
  assert.ok(source.includes("info && info.ariaCli"), "recorded path is not consulted");
  const candidates = source.slice(
    source.indexOf("const candidates = ["),
    source.indexOf("].filter(Boolean)")
  );
  const count = (candidates.match(/path\.join\(/g) || []).length;
  assert.ok(count >= 3, `only ${count} fallback candidates; a stale record would strand the install`);
});

check("the error message names the file it actually wants", () => {
  assert.ok(
    source.includes("src/aria_code/apps/cli/main.py not found"),
    "the not-found message names a different file than the launcher looks for"
  );
});

process.exit(failures === 0 ? 0 : 1);
