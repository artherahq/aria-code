"""MCP configuration helpers for Aria and Arthera package bridges."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def arthera_quant_engine_server_config(
    arthera_root: Optional[Path] = None,
) -> Dict[str, object]:
    """Return a ready-to-write MCP server entry for Arthera QuantEngine."""

    root = (arthera_root or Path.home() / "Desktop" / "Arthera").expanduser()
    server = root / "packages" / "quant_engine" / "mcp_server.py"
    return {
        "name": "arthera_quant_engine",
        "command": "python3",
        "args": [str(server)],
        "env": {"PYTHONPATH": str(root)},
        "description": "Arthera QuantEngine tools exposed through MCP",
    }


def merge_server_config(existing: Dict[str, object], server: Dict[str, object]) -> Dict[str, object]:
    """Return config with server upserted by name."""

    servers = list(existing.get("servers") or [])
    name = server.get("name")
    out = []
    replaced = False
    for item in servers:
        if isinstance(item, dict) and item.get("name") == name:
            out.append(server)
            replaced = True
        else:
            out.append(item)
    if not replaced:
        out.append(server)
    return {**existing, "servers": out}


def load_mcp_config(path: Path) -> Dict[str, object]:
    if not path.exists():
        return {"servers": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"servers": []}


def write_mcp_config(path: Path, config: Dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def find_server_config(config: Dict[str, object], name: str) -> Optional[Dict[str, object]]:
    for item in config.get("servers") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def mcp_server_status(
    config_path: Path,
    server_name: str = "arthera_quant_engine",
    runtime_status: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Return config/file/runtime status for a configured MCP server."""

    config = load_mcp_config(config_path)
    server = find_server_config(config, server_name)
    runtime = None
    for item in runtime_status or []:
        if item.get("name") == server_name:
            runtime = item
            break

    server_path = None
    if server:
        args = server.get("args") or []
        if args:
            server_path = Path(str(args[0])).expanduser()

    return {
        "name": server_name,
        "config_path": str(config_path),
        "configured": server is not None,
        "server_path": str(server_path) if server_path else "",
        "server_file_exists": bool(server_path and server_path.exists()),
        "running": bool(runtime and runtime.get("running")),
        "tool_count": int(runtime.get("tool_count", 0)) if runtime else 0,
        "tools": list(runtime.get("tools", [])) if runtime else [],
        "description": (server or {}).get("description", ""),
    }
