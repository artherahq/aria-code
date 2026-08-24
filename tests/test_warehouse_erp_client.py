import asyncio

import httpx
import pytest

from aria_code.clients.warehouse_erp_client import (
    WarehouseERPClient,
    WarehouseERPConfigurationError,
    WarehouseERPRequestError,
)


def test_client_rejects_missing_or_unsafe_configuration():
    with pytest.raises(WarehouseERPConfigurationError):
        WarehouseERPClient("")
    with pytest.raises(WarehouseERPConfigurationError):
        WarehouseERPClient("https://erp.example.com", snapshot_path="https://other.example/{warehouse_id}")


def test_client_fetches_only_the_snapshot_and_sanitises_records():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["raw_path"] = request.url.raw_path.decode()
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "data": {
                    "warehouse_id": "WH-1",
                    "connectors": [{"name": "DHL"}, "invalid"],
                    "inbounds": "invalid",
                    "skus": [{"sku": "SKU-1"}],
                    "locations": [{"code": "A-01"}],
                    "unrelated_private_field": "not passed to agents",
                }
            },
        )

    client = WarehouseERPClient(
        "https://erp.example.com",
        token="read-only-token",
        transport=httpx.MockTransport(handler),
    )
    snapshot = asyncio.run(client.fetch_snapshot("WH 1"))

    assert seen == {"raw_path": "/api/v1/warehouses/WH%201/agent-snapshot", "auth": "Bearer read-only-token"}
    assert snapshot == {
        "warehouse_id": "WH-1",
        "connectors": [{"name": "DHL"}],
        "inbounds": [],
        "skus": [{"sku": "SKU-1"}],
        "locations": [{"code": "A-01"}],
    }


def test_client_returns_safe_http_failure():
    client = WarehouseERPClient(
        "https://erp.example.com",
        transport=httpx.MockTransport(lambda request: httpx.Response(401)),
    )
    with pytest.raises(WarehouseERPRequestError, match="HTTP 401"):
        asyncio.run(client.fetch_snapshot("WH-1"))
