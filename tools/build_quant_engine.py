#!/usr/bin/env python3
"""build_quant_engine.py — compile the proprietary quant engine to .so (no source).

This is the IP-protection step for the open-core model: the BSL CLI shell stays
source-available, while ``packages/quant_engine`` (the proprietary math) ships as
compiled extension modules with no recoverable Python source.

Usage:
    python tools/build_quant_engine.py --check     # verify toolchain + show plan
    python tools/build_quant_engine.py --build     # compile to .so into dist_compiled/

Compilation uses Nuitka in --module mode. Install it first:  pip install nuitka

The eventual distribution flow (see CLOSING_SOURCE.md):
  1. Move packages/quant_engine to a PRIVATE repo (package name e.g. arthera_quant).
  2. Build a compiled wheel there with this script's approach.
  3. Publish to a private index; the free shell imports it optionally and degrades
     when absent (packages.quant_engine.is_available()).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = ROOT / "packages" / "quant_engine"
OUT_DIR = ROOT / "dist_compiled"


def _modules() -> list[Path]:
    return sorted(p for p in ENGINE_DIR.rglob("*.py")
                  if "__pycache__" not in p.parts)


def _nuitka_available() -> bool:
    return find_spec("nuitka") is not None


def check() -> int:
    mods = _modules()
    print(f"quant engine dir : {ENGINE_DIR}")
    print(f"python modules   : {len(mods)}")
    for m in mods:
        print(f"  - {m.relative_to(ROOT)}")
    has = _nuitka_available()
    print(f"\nNuitka installed : {'yes' if has else 'NO — run: pip install nuitka'}")
    print(f"C compiler       : {'found' if (shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')) else 'NOT found'}")
    print(f"output dir       : {OUT_DIR}")
    if not ENGINE_DIR.exists():
        print("ERROR: engine dir missing", file=sys.stderr)
        return 1
    return 0 if has else 2


def build() -> int:
    if not _nuitka_available():
        print("Nuitka not installed. Run: pip install nuitka", file=sys.stderr)
        return 2
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "nuitka",
        "--module", "packages/quant_engine",
        "--include-package=packages.quant_engine",
        f"--output-dir={OUT_DIR}",
        "--remove-output",
        "--no-pyi-file",
    ]
    print("running:", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, cwd=ROOT)
    except Exception as exc:  # pragma: no cover - depends on local toolchain
        print(f"build failed: {exc}", file=sys.stderr)
        return 1
    if proc.returncode == 0:
        print(f"\n✓ compiled engine written to {OUT_DIR}")
        print("  Ship these .so/.pyd files instead of the .py sources.")
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Compile quant_engine to .so (open-core IP step)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="verify toolchain and show the plan")
    g.add_argument("--build", action="store_true", help="compile to .so into dist_compiled/")
    args = ap.parse_args(argv)
    if args.build:
        return build()
    return check()  # default: check


if __name__ == "__main__":
    raise SystemExit(main())
