"""Adapters from MCP tool descriptors to Aria tool manifests."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from packages.aria_core import PermissionLevel
from packages.aria_tools import ToolSpec


def _capabilities_for_mcp_tool(name: str, description: str = "") -> List[str]:
    text = f"{name} {description}".lower()
    capabilities: List[str] = []
    if any(word in text for word in ("quote", "market", "ohlc", "price")):
        capabilities.append("market.data")
    if any(word in text for word in ("backtest", "strategy", "simulation")):
        capabilities.append("strategy.backtest")
    if any(word in text for word in ("risk", "var", "cvar", "drawdown")):
        capabilities.append("risk")
    if any(word in text for word in ("factor", "alpha", "feature")):
        capabilities.append("factors")
    if any(word in text for word in ("signal", "predict", "regime")):
        capabilities.append("signals")
    if any(word in text for word in ("news", "filing", "web", "search")):
        capabilities.append("research")
    if any(word in text for word in ("trade", "order", "execution")):
        capabilities.append("broker")
    return capabilities or ["mcp.tool"]


def _permissions_for_mcp_tool(name: str, description: str = "") -> List[PermissionLevel]:
    text = f"{name} {description}".lower()
    if any(word in text for word in ("trade", "order", "execution")):
        return [PermissionLevel.NETWORK, PermissionLevel.BROKER_TRADE]
    if any(word in text for word in ("write", "export", "report", "backtest")):
        return [PermissionLevel.NETWORK, PermissionLevel.WORKSPACE_WRITE]
    return [PermissionLevel.NETWORK]


def mcp_tool_to_spec(tool: Dict[str, Any], server_name: str) -> ToolSpec:
    """Convert one MCP tool descriptor into an Aria ToolSpec."""

    short_name = str(tool.get("name") or "unknown")
    qualified_name = str(tool.get("qualified_name") or f"{server_name}/{short_name}")
    description = str(tool.get("description") or f"MCP tool from {server_name}")
    schema = tool.get("inputSchema") or tool.get("parameters") or {}
    return ToolSpec(
        name=qualified_name,
        handler=None,
        description=description,
        schema=schema if isinstance(schema, dict) else {},
        permissions=_permissions_for_mcp_tool(short_name, description),
        capabilities=_capabilities_for_mcp_tool(short_name, description),
    )


def mcp_tools_to_specs(tools: Iterable[Dict[str, Any]], server_name: str) -> List[ToolSpec]:
    return sorted(
        [mcp_tool_to_spec(tool, server_name) for tool in tools],
        key=lambda item: item.name,
    )
