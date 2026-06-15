"""Shared manifest types for agents, tools, skills, and package bridges."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class PermissionLevel(str, Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    NETWORK = "network"
    BROKER_READ = "broker_read"
    BROKER_TRADE = "broker_trade"
    FULL_ACCESS = "full_access"


class ServiceKind(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    SKILL = "skill"
    MCP = "mcp"
    SERVICE = "service"
    DATA = "data"
    INFRA = "infra"


@dataclass(frozen=True)
class PackageLink:
    """Optional link to an implementation package or external service."""

    package: str
    module: str = ""
    object_name: str = ""
    transport: str = "python"
    optional: bool = True
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CapabilityManifest:
    """Stable capability description used by registries and MCP exposure."""

    name: str
    kind: ServiceKind
    description: str
    capabilities: List[str] = field(default_factory=list)
    permissions: List[PermissionLevel] = field(default_factory=list)
    required_services: List[str] = field(default_factory=list)
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    package_link: Optional[PackageLink] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["permissions"] = [p.value for p in self.permissions]
        if self.package_link:
            data["package_link"] = self.package_link.to_dict()
        return data
