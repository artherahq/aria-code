"""Aria Code package facade layer.

This namespace is intentionally thin. It exposes stable package boundaries
without forcing the existing single-file CLI to move all implementation code at
once.
"""

import pathlib as _pathlib

# Extend __path__ to include Arthera/packages/ so that
# `from packages.quant_engine.*` resolves transparently.
_arthera_pkgs = _pathlib.Path(__file__).parents[2] / "Arthera" / "packages"
if _arthera_pkgs.exists() and str(_arthera_pkgs) not in __path__:
    __path__.append(str(_arthera_pkgs))

__all__ = [
    "aria_agents",
    "aria_core",
    "aria_infra",
    "aria_mcp",
    "aria_skills",
    "aria_tools",
]
