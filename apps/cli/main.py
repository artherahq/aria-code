"""Installed console entrypoint for Aria Code.

The heavy terminal implementation still lives in ``aria_cli`` during migration.
Keeping this shim lets packaging and future app-specific startup logic move
under ``apps/cli`` without breaking the legacy ``python aria_cli.py`` path.
"""

from __future__ import annotations

from aria_cli import main


__all__ = ["main"]

