"""
src/aria_code/packages/aria_mcp/installer.py — One-Click MCP Config Installer for VS Code & Cursor
==================================================================================================
Automatically detects and registers aria-code MCP server into:
1. VS Code / VS Code Insiders (`settings.json` or `.vscode/mcp.json`)
2. Cursor IDE (`settings.json`)
3. Claude Desktop (`claude_desktop_config.json`)
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple


def get_ide_config_paths() -> Dict[str, Path]:
    """Return standard config paths by platform."""
    home = Path.home()
    system = platform.system()

    if system == "Darwin":  # macOS
        return {
            "vscode": home / "Library" / "Application Support" / "Code" / "User" / "settings.json",
            "cursor": home / "Library" / "Application Support" / "Cursor" / "User" / "settings.json",
            "claude": home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json",
        }
    elif system == "Windows":
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        return {
            "vscode": appdata / "Code" / "User" / "settings.json",
            "cursor": appdata / "Cursor" / "User" / "settings.json",
            "claude": appdata / "Claude" / "claude_desktop_config.json",
        }
    else:  # Linux
        config_dir = Path(os.environ.get("XDG_CONFIG_HOME", str(home / ".config")))
        return {
            "vscode": config_dir / "Code" / "User" / "settings.json",
            "cursor": config_dir / "Cursor" / "User" / "settings.json",
            "claude": config_dir / "Claude" / "claude_desktop_config.json",
        }


def generate_mcp_entry(server_path: Optional[str] = None) -> Dict[str, Any]:
    """Generate canonical MCP server registration block."""
    if not server_path:
        # Default to invoking aria-code via sys.executable or command
        return {
            "command": "aria-code",
            "args": ["mcp"],
        }
    return {
        "command": sys.executable,
        "args": [server_path],
    }


def install_mcp_to_target(target: str = "vscode", custom_path: Optional[Path] = None) -> Tuple[bool, str]:
    """
    Install aria-code MCP configuration to specified target editor.
    Returns (success, status_message).
    """
    paths = get_ide_config_paths()
    config_file = custom_path or paths.get(target.lower())

    if not config_file:
        return False, f"Unknown target: {target}. Valid targets: vscode, cursor, claude"

    try:
        config_file.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {}
        if config_file.exists():
            try:
                with open(config_file, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        data = json.loads(content)
            except Exception as e:
                return False, f"Failed to parse existing JSON in {config_file}: {e}"

        # Insert or update mcpServers dict
        if "mcpServers" not in data:
            data["mcpServers"] = {}

        data["mcpServers"]["aria-code"] = generate_mcp_entry()

        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return True, f"Successfully registered aria-code MCP server to {config_file}"
    except Exception as e:
        return False, f"Error writing to {config_file}: {e}"
