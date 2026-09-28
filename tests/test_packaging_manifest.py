"""Keep the package discovery settings aligned with the src/aria_code tree."""

from __future__ import annotations

import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPO_ROOT / "src" / "aria_code"


def test_package_discovery_includes_all_source_subpackages():
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)["tool"]["setuptools"]

    assert config["package-dir"] == {"": "src"}
    assert config["packages"]["find"]["where"] == ["src"]
    assert "aria_code*" in config["packages"]["find"]["include"]

    for relative in (
        "apps/cli/main.py",
        "agents/__init__.py",
        "clients/__init__.py",
        "domain/__init__.py",
        "packages/__init__.py",
        "runtime/__init__.py",
        "tools/__init__.py",
    ):
        assert (PACKAGE_ROOT / relative).is_file(), relative
