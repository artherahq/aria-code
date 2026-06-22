"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const APP_NAME = "Aria Code";
const APP_SLUG = "aria-code";
const LEGACY_DIRNAME = ".aria-code";

function _clean(value) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function expandHome(value) {
  const raw = _clean(value);
  if (!raw) return "";
  if (raw === "~") return os.homedir();
  if (raw.startsWith("~/") || raw.startsWith("~\\")) {
    return path.join(os.homedir(), raw.slice(2));
  }
  return raw;
}

function _abs(value) {
  const expanded = expandHome(value);
  return expanded ? path.resolve(expanded) : "";
}

function _firstEnv(names, env = process.env) {
  for (const name of names) {
    const value = _abs(env[name]);
    if (value) return { value, source: `env:${name}` };
  }
  return { value: "", source: "" };
}

function _npmConfigHome(env = process.env) {
  const fromEnv = _firstEnv([
    "npm_config_aria_code_home",
    "npm_config_aria_home",
    "npm_config_ariacode_home",
  ], env);
  if (fromEnv.value) return fromEnv;

  try {
    const result = spawnSync("npm", ["config", "get", "aria-code:home"], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
      shell: os.platform() === "win32",
      timeout: 1500,
    });
    if (result.status === 0) {
      const value = _abs(result.stdout);
      if (value && value !== path.resolve("undefined") && value !== path.resolve("null")) {
        return { value, source: "npm-config:aria-code:home" };
      }
    }
  } catch (_) {}
  return { value: "", source: "" };
}

function platformDataDir(platform = os.platform(), env = process.env) {
  if (platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", APP_NAME);
  }
  if (platform === "win32") {
    return path.join(env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "AriaCode");
  }
  return path.join(env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), APP_SLUG);
}

function platformConfigDir(platform = os.platform(), env = process.env) {
  if (platform === "darwin") {
    return path.join(os.homedir(), "Library", "Application Support", APP_NAME);
  }
  if (platform === "win32") {
    return path.join(env.APPDATA || path.join(os.homedir(), "AppData", "Roaming"), "AriaCode");
  }
  return path.join(env.XDG_CONFIG_HOME || path.join(os.homedir(), ".config"), APP_SLUG);
}

function platformCacheDir(platform = os.platform(), env = process.env) {
  if (platform === "darwin") {
    return path.join(os.homedir(), "Library", "Caches", APP_NAME);
  }
  if (platform === "win32") {
    return path.join(env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "AriaCode", "Cache");
  }
  return path.join(env.XDG_CACHE_HOME || path.join(os.homedir(), ".cache"), APP_SLUG);
}

function resolveAriaPaths(options = {}) {
  const env = options.env || process.env;
  const platform = options.platform || os.platform();
  const legacyInstallDir = path.join(os.homedir(), LEGACY_DIRNAME);

  let selected = _firstEnv(["ARIA_HOME", "ARIA_CODE_HOME"], env);
  if (!selected.value && options.skipNpmConfig !== true) {
    selected = _npmConfigHome(env);
  }
  if (
    !selected.value
    && options.preferExistingLegacy !== false
    && fs.existsSync(legacyInstallDir)
  ) {
    selected = { value: legacyInstallDir, source: "legacy-existing" };
  }
  if (!selected.value) {
    selected = { value: platformDataDir(platform, env), source: "platform-default" };
  }

  const installDir = selected.value;
  const configDir = _abs(env.ARIA_CONFIG_DIR) || platformConfigDir(platform, env);
  const cacheDir = _abs(env.ARIA_CACHE_DIR) || platformCacheDir(platform, env);
  const venvDir = path.join(installDir, ".venv");
  const infoFile = path.join(installDir, ".npm-install-info.json");
  const configInfoFile = path.join(configDir, "install.json");
  const legacyInfoFile = path.join(legacyInstallDir, ".npm-install-info.json");

  const infoCandidates = [
    infoFile,
    configInfoFile,
    legacyInfoFile,
  ].filter((item, index, arr) => item && arr.indexOf(item) === index);

  return {
    appName: APP_NAME,
    appSlug: APP_SLUG,
    installDir,
    installDirSource: selected.source,
    legacyInstallDir,
    venvDir,
    infoFile,
    configDir,
    configInfoFile,
    cacheDir,
    legacyInfoFile,
    infoCandidates,
  };
}

module.exports = {
  APP_NAME,
  APP_SLUG,
  LEGACY_DIRNAME,
  expandHome,
  platformDataDir,
  platformConfigDir,
  platformCacheDir,
  resolveAriaPaths,
};
