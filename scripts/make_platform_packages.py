#!/usr/bin/env python3
"""Build the per-platform npm packages that carry the prebuilt binaries.

The dispatcher has separate CLI and MCP optional packages for each platform.
Combining both PyInstaller binaries exceeded the npm registry's payload limit
on Linux arm64 (307.8 MB at v0.53.0). Each install downloads only the two
packages matching its platform and runs no install-time code.

Each input is the release archive of a PyInstaller --onedir build (see
scripts/package_onedir.py for why it is not --onefile). The package carries the
unpacked directory with every symlink replaced by a copy, because an npm
tarball cannot be relied on to carry symlinks:

    bin/aria-code-bin/aria-code-bin        (.exe on Windows)
    bin/aria-code-bin/_internal/...

Usage:
  python scripts/make_platform_packages.py --version 4.4.3 \\
      --binary darwin-arm64=built/aria-code-macos-arm64/aria-code-bin.tar.gz \\
      --mcp    darwin-arm64=built/aria-code-mcp-macos-arm64/aria-code-mcp-bin.tar.gz \\
      --out npm/platforms

Each --binary and --mcp is <platform-key>=<archive>. A release requires both
binaries for every platform pinned by the dispatcher.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import shutil
import sys

_spec = importlib.util.spec_from_file_location(
    "package_onedir", pathlib.Path(__file__).with_name("package_onedir.py"))
package_onedir = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(package_onedir)

SCOPE = "@artheras"
BASE = "aria-code"
# Kept in step with npm/lib/platform.js PLATFORM_KEYS; the test asserts it.
PLATFORM_KEYS = ("darwin-arm64", "darwin-x64", "linux-x64", "linux-arm64", "win32-x64")

# npm's own fields for refusing to install a package on the wrong machine. Worth
# setting even though the dispatcher checks too: this way npm skips the download
# rather than fetching a binary it cannot run.
OS_FOR = {"darwin": "darwin", "linux": "linux", "win32": "win32"}


def binary_name(key: str, name: str = "aria-code-bin") -> str:
    return f"{name}.exe" if key.startswith("win32") else name


def _pairs(values: list[str], flag: str) -> dict[str, pathlib.Path]:
    out: dict[str, pathlib.Path] = {}
    for raw in values or ():
        if "=" not in raw:
            raise SystemExit(f"{flag} expects <platform-key>=<path>, got {raw!r}")
        key, path = raw.split("=", 1)
        if key not in PLATFORM_KEYS:
            raise SystemExit(f"unknown platform key {key!r}; known: {', '.join(PLATFORM_KEYS)}")
        resolved = pathlib.Path(path).expanduser()
        if not resolved.is_file():
            raise SystemExit(f"{flag} {key}: no such file: {resolved}")
        out[key] = resolved
    return out


def build_one(key: str, version: str, source: pathlib.Path,
              out_root: pathlib.Path, *, mcp: bool = False) -> pathlib.Path:
    platform = key.split("-")[0]
    arch = key.split("-", 1)[1]
    suffix = f"mcp-{key}" if mcp else key
    name = "aria-code-mcp-bin" if mcp else "aria-code-bin"
    pkg_dir = out_root / f"{BASE}-{suffix}"
    bin_dir = pkg_dir / "bin"
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    bin_dir.mkdir(parents=True)

    app_dir = package_onedir.unpack(source, bin_dir, dereference=True)
    if app_dir.name != name:
        raise SystemExit(f"{source.name} unpacks to {app_dir.name}/, expected {name}/")
    target = app_dir / binary_name(key, name)
    if not target.is_file():
        raise SystemExit(f"{source.name} has no {target.name} at its top level")
    # npm preserves the mode it finds. A binary shipped without +x installs
    # fine and then fails with EACCES on first run.
    package_onedir.ensure_executable(target)

    (pkg_dir / "package.json").write_text(json.dumps({
        "name": f"{SCOPE}/{BASE}-{suffix}",
        "version": version,
        "description": f"aria-code {'MCP server' if mcp else 'CLI'} binary for {key}",
        "license": "Apache-2.0",
        "os": [OS_FOR[platform]],
        "cpu": [arch],
        "files": ["bin/"],
    }, indent=2) + "\n", encoding="utf-8")
    return pkg_dir


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", required=True)
    ap.add_argument("--binary", action="append", default=[],
                    help="<platform-key>=<archive of the aria-code-bin directory>")
    ap.add_argument("--mcp", action="append", default=[],
                    help="<platform-key>=<archive of the aria-code-mcp-bin directory>")
    ap.add_argument("--out", default="npm/platforms")
    args = ap.parse_args(argv[1:])

    main_bins = _pairs(args.binary, "--binary")
    mcp_bins = _pairs(args.mcp, "--mcp")
    if not main_bins:
        raise SystemExit("no --binary given; nothing to package")

    if set(main_bins) != set(mcp_bins):
        raise SystemExit("CLI and MCP platform sets must match: "
                         f"CLI={sorted(main_bins)}, MCP={sorted(mcp_bins)}")

    out_root = pathlib.Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    built = []
    for key, path in sorted(main_bins.items()):
        built.append(build_one(key, args.version, path, out_root))
        built.append(build_one(key, args.version, mcp_bins[key], out_root, mcp=True))

    print(f"Built {len(built)} platform package(s) in {out_root}:")
    for pkg in built:
        files = sum(1 for p in (pkg / "bin").rglob("*") if p.is_file())
        print(f"  {pkg.name:34} {files} files")
    missing = [k for k in PLATFORM_KEYS if k not in main_bins]
    if missing:
        print(f"Not built this run: {', '.join(missing)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
