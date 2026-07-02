"use strict";
/**
 * venv drift detection (pure) — npm-install twin of doctor.py's
 * analyze_python_drift and install.sh --rebuild.
 *
 * Real failure this catches: Homebrew (or apt) upgrades the system Python and
 * removes the version the venv was built against; the venv still exists but
 * its interpreter is a dangling symlink. `npm run repair` used to say
 * "venv exists" and reuse the corpse. These helpers let postinstall.js detect
 * the drift and rebuild with the current Python instead.
 *
 * Pure functions over strings/booleans — no fs, no child_process — so they
 * are unit-testable without touching a real venv.
 */

/** Parse pyvenv.cfg "key = value" lines → { version, home } (lowercased keys). */
function parsePyvenvCfg(text) {
  const out = { version: "", home: "" };
  for (const line of String(text || "").split(/\r?\n/)) {
    const idx = line.indexOf("=");
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim().toLowerCase();
    const value = line.slice(idx + 1).trim();
    if (key === "version" || key === "version_info") out.version = out.version || value;
    else if (key === "home") out.home = value;
  }
  return out;
}

/** major.minor tuple from "3.12.4" → "3.12" (empty string when unparseable). */
function majorMinor(version) {
  const m = String(version || "").match(/^(\d+)\.(\d+)/);
  return m ? `${m[1]}.${m[2]}` : "";
}

/**
 * Decide whether an existing venv must be rebuilt.
 *
 * @param {object} p
 * @param {string}      p.cfgText        pyvenv.cfg contents ("" → not a venv, never rebuild)
 * @param {boolean}     p.homeExists     whether the recorded base interpreter still exists
 * @param {string|null} p.runningVersion venv python's reported version, null if it won't run
 * @returns {string|null} human-readable rebuild reason, or null when healthy
 */
function venvDriftReason({ cfgText, homeExists, runningVersion }) {
  if (!cfgText) return null; // no pyvenv.cfg → not a venv; refuse to judge (or touch) it
  const { version: recorded } = parsePyvenvCfg(cfgText);
  if (!homeExists) {
    return `venv base interpreter is gone (built with ${recorded || "unknown"})`;
  }
  if (runningVersion === null) {
    return "venv python does not run (broken interpreter link)";
  }
  const rec = majorMinor(recorded);
  const run = majorMinor(runningVersion);
  if (rec && run && rec !== run) {
    return `venv was created with Python ${recorded} but ${runningVersion} is running`;
  }
  return null; // healthy (patch-level differences are routine, not drift)
}

module.exports = { parsePyvenvCfg, majorMinor, venvDriftReason };
