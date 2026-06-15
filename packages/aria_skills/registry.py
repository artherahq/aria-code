"""Skill manifests for model-facing workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from packages.aria_core import CapabilityManifest, PermissionLevel, ServiceKind


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    tools: List[str] = field(default_factory=list)
    agents: List[str] = field(default_factory=list)
    permissions: List[PermissionLevel] = field(default_factory=list)

    def manifest(self) -> CapabilityManifest:
        return CapabilityManifest(
            name=self.name,
            kind=ServiceKind.SKILL,
            description=self.description,
            capabilities=[f"tool:{name}" for name in self.tools] + [f"agent:{name}" for name in self.agents],
            permissions=self.permissions,
            required_services=self.tools + self.agents,
            tags=["skill"],
        )


def builtin_skill_specs() -> List[SkillSpec]:
    return [
        SkillSpec(
            name="financial-research",
            description="Multi-source quote, fundamentals, technical analysis, and narrative report generation.",
            tools=["get_market_data"],
            agents=["technical", "fundamental", "macro", "risk", "synthesis"],
            permissions=[PermissionLevel.NETWORK],
        ),
        SkillSpec(
            name="strategy-backtest",
            description="Generate and validate trading strategies with historical simulation and artifacts.",
            tools=["get_market_data"],
            agents=["technical", "risk"],
            permissions=[PermissionLevel.NETWORK, PermissionLevel.WORKSPACE_WRITE],
        ),
        SkillSpec(
            name="workspace-coding",
            description="Read, edit, and verify local project files with explicit workspace permissions.",
            tools=["read_file", "list_files", "search_code", "edit_file", "write_file", "run_command"],
            permissions=[PermissionLevel.READ_ONLY, PermissionLevel.WORKSPACE_WRITE],
        ),
        SkillSpec(
            name="broker-portfolio",
            description="Read broker account state and propose guarded orders with user approval.",
            tools=["broker_query", "broker_order"],
            permissions=[PermissionLevel.BROKER_READ, PermissionLevel.BROKER_TRADE],
        ),
    ]
