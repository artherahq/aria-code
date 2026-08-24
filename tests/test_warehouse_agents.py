import asyncio

from aria_code.agents.registry import get_registry
from aria_code.agents.signal_scheme import WAREHOUSE_SCHEME
from aria_code.agents.warehouse import (
    InboundExceptionAgent,
    InventoryHealthAgent,
    LogisticsSyncAgent,
    WAREHOUSE_TEAM,
)


def test_warehouse_agents_are_registered_with_a_domain_signal_scheme():
    registered = {item["name"] for item in get_registry().list()}
    assert set(WAREHOUSE_TEAM) <= registered
    assert WAREHOUSE_SCHEME.name == "warehouse"


def test_warehouse_agents_flag_operational_risks_and_vote_with_warehouse_terms():
    logistics = asyncio.run(
        LogisticsSyncAgent().analyze(
            "WH-CN-01",
            {"connectors": [{"name": "DHL", "delay_minutes": 42, "failed_jobs": 2}]},
        )
    )
    inventory = asyncio.run(
        InventoryHealthAgent().analyze(
            "WH-CN-01",
            {"skus": [{"sku": "SKU-1", "available": 3, "safety_stock": 10}]},
        )
    )
    inbound = asyncio.run(
        InboundExceptionAgent().analyze(
            "WH-CN-01",
            {"inbounds": [{"id": "IN-1", "expected_qty": 10, "received_qty": 8, "damaged_qty": 1}]},
        )
    )

    assert (logistics.signal, inventory.signal, inbound.signal) == ("SEVERE", "CONCERN", "SEVERE")
    assert WAREHOUSE_SCHEME.vote([logistics, inventory, inbound])[0] == "SEVERE"


def test_warehouse_agents_ignore_malformed_optional_records():
    result = asyncio.run(InventoryHealthAgent().analyze("WH-CN-01", {"skus": "not-a-list"}))
    assert result.signal == "GOOD"
