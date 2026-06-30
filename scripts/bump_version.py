#!/usr/bin/env python3
"""Bump the aria-code version in every place that hard-codes it, atomically.

The version lives in three files that must stay in lock-step, because each is a
separate publish source:

  pyproject.toml   version = "X"       -> PyPI  (uv publish reads this)
  npm/package.json "version": "X"      -> npm   (npm publish reads this)
  aria_cli.py      __version__ = "X"   -> `aria-code --version`

`publish.yml` triggers on a `vX.Y.Z` tag and publishes whatever these files say,
so a mismatch ships a wrong/duplicate version or makes --version lie. This script
is the single entry point: it sets all three, verifies they agree, and prints the
tag command. The companion check (`--check`) is what CI runs to refuse a release
whose files disagree with the tag.

Usage:
  python scripts/bump_version.py 4.1.5     # set all three to 4.1.5
  python scripts/bump_version.py --check            # assert all three already agree
  python scripts/bump_version.py --check 4.1.5      # assert all three == 4.1.5 (CI: pass the tag)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_JSON = ROOT / "npm" / "package.json"
ARIA_CLI = ROOT / "aria_cli.py"

SEMVER = re.compile(r"^\d+\.\d+\.\d+([.-][0-9A-Za-z.]+)?$")


def read_versions() -> dict[str, str]:
    """Current version as recorded in each source. Anchored patterns so a
    dependency pin like `foo>=4.4.0` is never mistaken for the project version."""
    out: dict[str, str] = {}

    m = re.search(r'(?m)^version = "([^"]+)"', PYPROJECT.read_text())
    out["pyproject.toml"] = m.group(1) if m else "<missing>"

    m = re.search(r'(?m)^__version__ = "([^"]+)"', ARIA_CLI.read_text())
    out["aria_cli.py"] = m.group(1) if m else "<missing>"

    out["npm/package.json"] = json.loads(PACKAGE_JSON.read_text()).get("version", "<missing>")
    return out


def write_version(new: str) -> None:
    PYPROJECT.write_text(
        re.sub(r'(?m)^version = "[^"]+"', f'version = "{new}"', PYPROJECT.read_text(), count=1)
    )
    ARIA_CLI.write_text(
        re.sub(r'(?m)^__version__ = "[^"]+"', f'__version__ = "{new}"', ARIA_CLI.read_text(), count=1)
    )
    # Swap only the top-level "version" value in package.json — a regex line edit,
    # not a JSON round-trip, so the file's hand-formatting (compact arrays, inline
    # objects) is preserved. count=1 hits the first (top-level) version key.
    PACKAGE_JSON.write_text(
        re.sub(r'(?m)^(\s*"version":\s*")[^"]+(")', rf"\g<1>{new}\g<2>",
               PACKAGE_JSON.read_text(), count=1)
    )


def cmd_check(expected: str | None) -> int:
    versions = read_versions()
    distinct = set(versions.values())
    ok = len(distinct) == 1 and "<missing>" not in distinct
    if expected is not None:
        ok = ok and distinct == {expected.lstrip("v")}

    for f, v in versions.items():
        print(f"  {v:<12} {f}")
    if expected is not None:
        print(f"  expected: {expected.lstrip('v')}")

    if ok:
        print("✓ versions aligned")
        return 0
    print("✗ version mismatch — run: python scripts/bump_version.py <version>", file=sys.stderr)
    return 1


def cmd_bump(new: str) -> int:
    new = new.lstrip("v")
    if not SEMVER.match(new):
        print(f"✗ not a valid version: {new!r} (expected X.Y.Z)", file=sys.stderr)
        return 2
    before = read_versions()
    write_version(new)
    after = read_versions()
    for f in after:
        print(f"  {before[f]:>10} → {after[f]:<10} {f}")
    print(f"\n✓ bumped to {new}. Next:")
    print(f"    git commit -am 'release: v{new}'")
    print(f"    git tag v{new} && git push --tags    # triggers publish.yml")
    return 0


def main(argv: list[str]) -> int:
    args = argv[1:]
    if args and args[0] == "--check":
        return cmd_check(args[1] if len(args) > 1 else None)
    if len(args) == 1:
        return cmd_bump(args[0])
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
