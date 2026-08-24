"""
src/aria_code/utils/vscode_uri.py — VS Code / Cursor / Windsurf URI Protocol Generator
=====================================================================================
Generates standard deep links to open files directly in local IDEs.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def get_ide_uri(
    file_path: str,
    line: int = 1,
    column: int = 1,
    editor: str = "vscode",
) -> str:
    """
    Generate deep link URI for opening a local file in VS Code, Cursor, or Windsurf.
    
    Supported editors:
    - "vscode" (default) -> vscode://file/{path}:{line}:{column}
    - "cursor"           -> cursor://file/{path}:{line}:{column}
    - "windsurf"         -> windsurf://file/{path}:{line}:{column}
    - "vscodium"         -> vscodium://file/{path}:{line}:{column}
    """
    abs_path = os.path.abspath(os.path.expanduser(file_path))
    scheme_map = {
        "vscode": "vscode",
        "cursor": "cursor",
        "windsurf": "windsurf",
        "vscodium": "vscodium",
    }
    scheme = scheme_map.get(editor.lower(), "vscode")
    return f"{scheme}://file/{abs_path}:{line}:{column}"
