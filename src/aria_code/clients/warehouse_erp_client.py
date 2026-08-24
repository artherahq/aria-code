"""Read-only client for the warehouse ERP agent snapshot endpoint.

The client intentionally exposes one narrow endpoint instead of a generic ERP
proxy.  That keeps the agent boundary auditable: the CLI can only retrieve the
small operational snapshot required for analysis and has no write capability.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlparse

import httpx


class WarehouseERPConfigurationError(ValueError):
    """Raised when the local ERP integration configuration is invalid."""


class WarehouseERPRequestError(RuntimeError):
    """Raised with a safe, user-facing ERP request error."""


_DEFAULT_SNAPSHOT_PATH = "/api/v1/warehouses/{warehouse_id}/agent-snapshot"
_RECORD_LISTS = ("connectors", "inbounds", "skus", "locations")


class WarehouseERPClient:
    """Fetch a minimal, read-only warehouse snapshot over HTTPS or HTTP."""

    def __init__(
        self,
        base_url: str,
        token: str = "",
        snapshot_path: str = _DEFAULT_SNAPSHOT_PATH,
        timeout_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlparse(str(base_url or "").strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise WarehouseERPConfigurationError("ARIA_WAREHOUSE_ERP_URL 必须是完整的 http(s) URL")
        if not snapshot_path.startswith("/") or "{warehouse_id}" not in snapshot_path:
            raise WarehouseERPConfigurationError(
                "ARIA_WAREHOUSE_ERP_SNAPSHOT_PATH 必须是包含 {warehouse_id} 的相对路径"
            )
        self.base_url = str(base_url).rstrip("/")
        self.token = str(token or "").strip()
        self.snapshot_path = snapshot_path
        self.timeout_seconds = float(timeout_seconds)
        self.transport = transport

    @classmethod
    def from_env(cls) -> "WarehouseERPClient":
        return cls(
            base_url=os.environ.get("ARIA_WAREHOUSE_ERP_URL", ""),
            token=os.environ.get("ARIA_WAREHOUSE_ERP_TOKEN", ""),
            snapshot_path=os.environ.get("ARIA_WAREHOUSE_ERP_SNAPSHOT_PATH", _DEFAULT_SNAPSHOT_PATH),
        )

    def _snapshot_url(self, warehouse_id: str) -> str:
        cleaned = str(warehouse_id or "").strip()
        if not cleaned:
            raise WarehouseERPConfigurationError("仓库编号不能为空")
        return self.base_url + self.snapshot_path.format(warehouse_id=quote(cleaned, safe=""))

    async def fetch_snapshot(self, warehouse_id: str) -> dict[str, Any]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_seconds,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.get(self._snapshot_url(warehouse_id), headers=headers)
        except httpx.TimeoutException as exc:
            raise WarehouseERPRequestError("ERP 请求超时，请稍后重试。") from exc
        except httpx.HTTPError as exc:
            raise WarehouseERPRequestError("无法连接 ERP 服务，请检查网络或服务状态。") from exc

        if response.status_code != 200:
            raise WarehouseERPRequestError(f"ERP 服务返回 HTTP {response.status_code}。")
        try:
            payload = response.json()
        except ValueError as exc:
            raise WarehouseERPRequestError("ERP 服务返回的不是有效 JSON。") from exc
        return self._normalise_snapshot(warehouse_id, payload)

    @staticmethod
    def _normalise_snapshot(warehouse_id: str, payload: Any) -> dict[str, Any]:
        root = payload.get("data", payload) if isinstance(payload, Mapping) else None
        if not isinstance(root, Mapping):
            raise WarehouseERPRequestError("ERP 快照必须是 JSON 对象。")

        snapshot: dict[str, Any] = {
            "warehouse_id": str(root.get("warehouse_id") or warehouse_id),
        }
        for key in _RECORD_LISTS:
            value = root.get(key, [])
            snapshot[key] = [dict(item) for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []
        return snapshot
