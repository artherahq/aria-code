"use strict";
/**
 * The dispatcher has one job: hand off to the right prebuilt binary, or say
 * clearly why it cannot. Both halves matter — the install it replaces failed in
 * 651 lines of bootstrap, and a launcher that fails vaguely is not an
 * improvement on that.
 */

const assert = require("assert");
const P = require("../lib/platform");

let failures = 0;
function test(name, fn) {
  try { fn(); console.log(`✓ ${name}`); }
  catch (e) { failures++; console.error(`✗ ${name}\n    ${e.message}`); }
}

test("this machine's platform resolves to a key", () => {
  // If the host running the tests is unsupported the rest is untestable, so
  // assert the shape rather than a literal.
  const key = P.platformKey(process);
  if (key !== null) assert.ok(P.PLATFORM_KEYS.includes(key), key);
});

test("each supported pair maps to its own key", () => {
  const seen = new Set();
  for (const key of P.PLATFORM_KEYS) {
    const [platform, arch] = key.split("-");
    const got = P.platformKey({ platform, arch });
    assert.strictEqual(got, key);
    assert.ok(!seen.has(got), `duplicate key ${got}`);
    seen.add(got);
  }
});

test("an unsupported pair is null, not a guess", () => {
  for (const proc of [
    { platform: "freebsd", arch: "x64" },
    { platform: "darwin", arch: "ia32" },
    { platform: "linux", arch: "ppc64" },
    { platform: "win32", arch: "arm64" },   // no build yet; must not silently map
    {}, null, undefined,
  ]) {
    assert.strictEqual(P.platformKey(proc), null, JSON.stringify(proc));
  }
});

test("package names are scoped and derived from the key", () => {
  assert.strictEqual(P.packageNameFor("darwin-arm64"), "@artheras/aria-code-darwin-arm64");
  assert.strictEqual(P.packageNameFor("win32-x64"), "@artheras/aria-code-win32-x64");
  assert.strictEqual(P.packageNameFor("linux-arm64", "aria-code-mcp-bin"), "@artheras/aria-code-mcp-linux-arm64");
});

test("only Windows gets .exe", () => {
  assert.strictEqual(P.binaryName("win32"), "aria-code-bin.exe");
  for (const p of ["darwin", "linux"]) {
    assert.strictEqual(P.binaryName(p), "aria-code-bin");
  }
});

test("the mcp binary follows the same rule", () => {
  assert.strictEqual(P.binaryName("win32", "aria-code-mcp-bin"), "aria-code-mcp-bin.exe");
  assert.strictEqual(P.binaryName("linux", "aria-code-mcp-bin"), "aria-code-mcp-bin");
});

test("the require path points inside the platform package", () => {
  assert.strictEqual(
    P.binaryRequestFor("darwin-arm64"),
    "@artheras/aria-code-darwin-arm64/bin/aria-code-bin/aria-code-bin"
  );
  assert.strictEqual(
    P.binaryRequestFor("win32-x64"),
    "@artheras/aria-code-win32-x64/bin/aria-code-bin/aria-code-bin.exe"
  );
  assert.strictEqual(
    P.binaryRequestFor("linux-arm64", "aria-code-mcp-bin"),
    "@artheras/aria-code-mcp-linux-arm64/bin/aria-code-mcp-bin/aria-code-mcp-bin"
  );
});

test("dispatcher pins both binaries for every platform", () => {
  const manifest = require("../package.json");
  assert.strictEqual(Object.keys(manifest.optionalDependencies).length, P.PLATFORM_KEYS.length * 2);
  for (const key of P.PLATFORM_KEYS) {
    for (const binary of ["aria-code-bin", "aria-code-mcp-bin"]) {
      const name = P.packageNameFor(key, binary);
      assert.strictEqual(manifest.optionalDependencies[name], manifest.version, name);
    }
  }
});

test("the unsupported message names the platform and a real alternative", () => {
  const m = P.unsupportedMessage({ platform: "freebsd", arch: "x64" });
  assert.ok(m.includes("freebsd-x64"), "does not say what it saw");
  assert.ok(m.includes("pip install aria-code"), "offers no way forward");
  assert.ok(!/npm install -g/.test(m), "tells them to retry an install that cannot work");
});

test("the missing-package message explains --no-optional rather than blaming the user", () => {
  const m = P.missingPackageMessage("linux-x64");
  assert.ok(m.includes("@artheras/aria-code-linux-x64"), "does not name the package");
  assert.ok(m.includes("--no-optional"), "does not name the likely cause");
  assert.ok(m.includes("npm install -g"), "does not say how to fix it");
});

test("every supported key produces a usable triple", () => {
  for (const key of P.PLATFORM_KEYS) {
    assert.ok(P.packageNameFor(key).startsWith("@artheras/"));
    assert.ok(P.binaryRequestFor(key).includes("/bin/"));
    assert.ok(P.missingPackageMessage(key).includes(key));
  }
});

test("the first-launch notice is per build, and says it is a one-off", () => {
  const a = P.firstLaunchMarker("/home/u", "/x/aria-code-bin", 1);
  assert.strictEqual(a, P.firstLaunchMarker("/home/u", "/x/aria-code-bin", 1));
  assert.notStrictEqual(a, P.firstLaunchMarker("/home/u", "/x/aria-code-bin", 2),
                        "an upgraded build would not be announced");
  assert.ok(a.startsWith("/home/u/.aria-code/"), a);
  assert.ok(/once/.test(P.FIRST_LAUNCH_NOTICE));
});

if (failures) { console.error(`\n${failures} failing`); process.exit(1); }
console.log("\nall passing");
