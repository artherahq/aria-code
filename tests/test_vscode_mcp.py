"""
tests/test_vscode_mcp.py — Unit tests for VS Code URI and MCP installer
"""

import json
from pathlib import Path
from aria_code.utils.vscode_uri import get_ide_uri
from aria_code.packages.aria_mcp.installer import (
    generate_mcp_entry,
    get_ide_config_paths,
    install_mcp_to_target,
)


def test_get_ide_uri():
    uri = get_ide_uri("/workspace/strategy.py", line=42, column=5, editor="vscode")
    assert uri.startswith("vscode://file/")
    assert uri.endswith(":42:5")
    assert "strategy.py" in uri

    cursor_uri = get_ide_uri("/workspace/strategy.py", line=10, editor="cursor")
    assert cursor_uri.startswith("cursor://file/")


def test_generate_mcp_entry():
    entry = generate_mcp_entry()
    assert entry["command"] == "aria-code"
    assert entry["args"] == ["mcp"]


def test_install_mcp_to_custom_path(tmp_path: Path):
    target_json = tmp_path / "settings.json"
    target_json.write_text('{"editor.fontSize": 14}', encoding="utf-8")

    success, msg = install_mcp_to_target(target="vscode", custom_path=target_json)
    assert success is True
    assert "Successfully registered" in msg

    content = json.loads(target_json.read_text(encoding="utf-8"))
    assert content["editor.fontSize"] == 14
    assert "mcpServers" in content
    assert "aria-code" in content["mcpServers"]
    assert content["mcpServers"]["aria-code"]["args"] == ["mcp"]
