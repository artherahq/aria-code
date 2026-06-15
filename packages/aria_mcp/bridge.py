"""Describe how Aria packages should be exposed over MCP."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class MCPExposure:
    name: str
    target: str
    description: str
    read_only: bool = True

    def to_tool_descriptor(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "target": self.target,
            "read_only": self.read_only,
        }


def default_exposures() -> List[MCPExposure]:
    """Initial MCP server surface for Aria Code.

    These names are stable contracts. The server implementation can bind them
    to ToolRegistry/AgentRegistry without changing client-facing tool names.
    """

    return [
        MCPExposure("aria.market.quote", "tool:get_market_data", "Fetch quote and technical market snapshot."),
        MCPExposure("aria.agent.team", "agent:team", "Run multi-agent financial research."),
        MCPExposure("aria.report.generate", "skill:financial-research", "Generate a local research artifact.", read_only=False),
        MCPExposure("aria.backtest.run", "skill:strategy-backtest", "Run a historical strategy simulation.", read_only=False),
        MCPExposure("aria.artifacts.list", "infra:artifacts", "List local generated artifacts."),
    ]
