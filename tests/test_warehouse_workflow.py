import asyncio

from agents.warehouse.workflow import run_warehouse_analysis


class FakeWarehouseClient:
    async def fetch_snapshot(self, warehouse_id):
        return {
            "warehouse_id": warehouse_id,
            "connectors": [{"name": "DHL", "delay_minutes": 0, "failed_jobs": 0}],
            "inbounds": [],
            "skus": [{"sku": "SKU-1", "available": 20, "safety_stock": 10}],
            "locations": [{"code": "A-01", "utilization": 0.5}],
        }


def test_workflow_uses_the_warehouse_signal_scheme_and_snapshot_for_all_agents():
    result, snapshot = asyncio.run(run_warehouse_analysis("WH-CN-01", client=FakeWarehouseClient()))
    assert result.final_signal == "GOOD"
    assert result.confidence > 0
    assert result.agents_run == [
        "warehouse_logistics_sync",
        "warehouse_inbound_exceptions",
        "warehouse_inventory_health",
    ]
    assert snapshot["warehouse_id"] == "WH-CN-01"
