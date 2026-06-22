"use strict";

const assert = require("assert");
const os = require("os");
const path = require("path");

const {
  expandHome,
  platformDataDir,
  platformConfigDir,
  platformCacheDir,
  resolveAriaPaths,
} = require("../lib/paths");

function test(name, fn) {
  try {
    fn();
    process.stdout.write(`✓ ${name}\n`);
  } catch (err) {
    process.stderr.write(`✗ ${name}\n${err.stack || err}\n`);
    process.exitCode = 1;
  }
}

test("expandHome expands tilde paths", () => {
  assert.strictEqual(expandHome("~/aria-runtime"), path.join(os.homedir(), "aria-runtime"));
});

test("ARIA_HOME wins over npm config and defaults", () => {
  const paths = resolveAriaPaths({
    skipNpmConfig: true,
    preferExistingLegacy: false,
    env: {
      ARIA_HOME: "~/custom-aria",
      ARIA_CONFIG_DIR: "~/custom-config",
      ARIA_CACHE_DIR: "~/custom-cache",
    },
  });

  assert.strictEqual(paths.installDir, path.join(os.homedir(), "custom-aria"));
  assert.strictEqual(paths.installDirSource, "env:ARIA_HOME");
  assert.strictEqual(paths.configDir, path.join(os.homedir(), "custom-config"));
  assert.strictEqual(paths.cacheDir, path.join(os.homedir(), "custom-cache"));
});

test("platform defaults use native directories for new installs", () => {
  const darwin = resolveAriaPaths({
    platform: "darwin",
    skipNpmConfig: true,
    preferExistingLegacy: false,
    env: {},
  });
  assert.strictEqual(
    darwin.installDir,
    path.join(os.homedir(), "Library", "Application Support", "Aria Code"),
  );
  assert.strictEqual(darwin.cacheDir, path.join(os.homedir(), "Library", "Caches", "Aria Code"));

  const linux = resolveAriaPaths({
    platform: "linux",
    skipNpmConfig: true,
    preferExistingLegacy: false,
    env: { XDG_DATA_HOME: "/tmp/data", XDG_CONFIG_HOME: "/tmp/config", XDG_CACHE_HOME: "/tmp/cache" },
  });
  assert.strictEqual(linux.installDir, path.join("/tmp/data", "aria-code"));
  assert.strictEqual(linux.configDir, path.join("/tmp/config", "aria-code"));
  assert.strictEqual(linux.cacheDir, path.join("/tmp/cache", "aria-code"));
});

test("info candidates include runtime, config mirror, and legacy locations", () => {
  const paths = resolveAriaPaths({
    skipNpmConfig: true,
    preferExistingLegacy: false,
    env: { ARIA_HOME: "/tmp/aria-home", ARIA_CONFIG_DIR: "/tmp/aria-config" },
  });

  assert.deepStrictEqual(paths.infoCandidates, [
    path.join("/tmp/aria-home", ".npm-install-info.json"),
    path.join("/tmp/aria-config", "install.json"),
    path.join(os.homedir(), ".aria-code", ".npm-install-info.json"),
  ]);
});
