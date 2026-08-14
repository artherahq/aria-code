"""Executable read-only workflow for the warehouse ERP agent team."""

from __future__ import annotations

from typing import Any, Callable

from agents.team import AgentTeam, TeamResult
from clients.warehouse_erp_client import WarehouseERPClient

from . import WAREHOUSE_SCHEME, WAREHOUSE_TEAM


async def run_warehouse_analysis(
    warehouse_id: str,
    client: WarehouseERPClient | Any | None = None,
    on_agent_done: Callable[[str, Any], None] | None = None,
) -> tuple[TeamResult, dict[str, Any]]:
    """Fetch one ERP snapshot and analyse it with the warehouse agent team."""
    erp_client = client or WarehouseERPClient.from_env()
    snapshot = await erp_client.fetch_snapshot(warehouse_id)
    team = AgentTeam(
        signal_scheme=WAREHOUSE_SCHEME,
        on_agent_done=on_agent_done,
        timeout_per_agent=20,
    )
    result = await team.run(
        warehouse_id,
        agents=list(WAREHOUSE_TEAM),
        agent_data={agent_name: snapshot for agent_name in WAREHOUSE_TEAM},
    )
    return result, snapshot
