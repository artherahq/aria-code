"""Tool registry facade over Aria Code's legacy LOCAL_TOOLS shape."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from packages.aria_core import CapabilityManifest, PermissionLevel, ServiceKind


Handler = Callable[[dict], dict]


_PERMISSION_BY_TOOL = {
    "read_file": [PermissionLevel.READ_ONLY],
    "list_files": [PermissionLevel.READ_ONLY],
    "search_code": [PermissionLevel.READ_ONLY],
    "glob": [PermissionLevel.READ_ONLY],
    "write_file": [PermissionLevel.WORKSPACE_WRITE],
    "edit_file": [PermissionLevel.WORKSPACE_WRITE],
    "notebook_edit": [PermissionLevel.WORKSPACE_WRITE],
    "run_command": [PermissionLevel.WORKSPACE_WRITE],
    "web_fetch": [PermissionLevel.NETWORK],
    "github": [PermissionLevel.NETWORK],
    "get_market_data": [PermissionLevel.NETWORK],
    "broker_query": [PermissionLevel.BROKER_READ],
    "broker_order": [PermissionLevel.BROKER_TRADE],
}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: Optional[Handler]
    description: str
    schema: Dict[str, Any] = field(default_factory=dict)
    permissions: List[PermissionLevel] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)

    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            name=self.name,
            kind=ServiceKind.TOOL,
            description=self.description,
            capabilities=self.capabilities,
            permissions=self.permissions,
            input_schema=self.schema,
            tags=["tool"],
        )


class ToolRegistry:
    """Small typed registry that can wrap existing dict-based tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec, *, overwrite: bool = False) -> None:
        if spec.name in self._tools and not overwrite:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list(self) -> List[ToolSpec]:
        return [self._tools[name] for name in sorted(self._tools)]

    def manifests(self) -> List[CapabilityManifest]:
        return [tool.manifest() for tool in self.list()]

    def legacy_tools(self) -> Dict[str, tuple]:
        return {
            name: (spec.handler, spec.description)
            for name, spec in self._tools.items()
            if spec.handler is not None
        }


def _schema_by_name(schemas: Iterable[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for schema in schemas or []:
        fn = schema.get("function") or {}
        name = fn.get("name")
        if name:
            out[name] = fn.get("parameters") or {}
    return out


def _capabilities_for_tool(name: str) -> List[str]:
    if name.startswith("broker_"):
        return ["broker"]
    if name in ("get_market_data",) or "market" in name or "quote" in name:
        return ["market.data"]
    if name in ("read_file", "list_files", "search_code", "glob"):
        return ["workspace.read"]
    if name in ("write_file", "edit_file", "notebook_edit"):
        return ["workspace.write"]
    if name == "run_command":
        return ["shell"]
    if name in ("web_fetch", "github"):
        return ["network"]
    return ["tool"]


def build_registry_from_legacy(
    tools: Mapping[str, tuple],
    schemas: Iterable[dict] = (),
) -> ToolRegistry:
    """Create a typed registry from ``LOCAL_TOOLS`` and tool schemas."""

    registry = ToolRegistry()
    schema_map = _schema_by_name(schemas)
    for name, value in tools.items():
        handler = value[0] if value else None
        description = value[1] if len(value) > 1 else ""
        registry.register(
            ToolSpec(
                name=name,
                handler=handler,
                description=description,
                schema=schema_map.get(name, {}),
                permissions=_PERMISSION_BY_TOOL.get(name, [PermissionLevel.READ_ONLY]),
                capabilities=_capabilities_for_tool(name),
            ),
            overwrite=True,
        )
    return registry
