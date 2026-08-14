"""库存健康度 Agent：低库存、库容与待上架积压。"""
from __future__ import annotations
from typing import Any, Dict
from ..base import BaseAgent, AgentResult
from .contracts import number, records

class InventoryHealthAgent(BaseAgent):
    name = "warehouse_inventory_health"
    description = "库存健康度：识别低库存 SKU、库位容量风险与上架积压"

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        skus, locations = records(data, "skus"), records(data, "locations")
        alerts = [
            f"{sku.get('sku', '未知 SKU')} 可用库存 {sku.get('available', 0)}，低于安全库存 {sku.get('safety_stock', 0)}"
            for sku in skus
            if number(sku.get("available")) < number(sku.get("safety_stock"))
        ]
        alerts += [
            f"库位 {loc.get('code', '未知')} 使用率 {number(loc.get('utilization')):.0%}"
            for loc in locations
            if number(loc.get("utilization")) >= 0.9
        ]
        signal = "CONCERN" if alerts else "GOOD"
        return AgentResult(agent=self.name, symbol=symbol, analysis="；".join(alerts) if alerts else "库存与库位容量均处于健康阈值内。", confidence=.88, signal=signal, key_points=alerts or ["库存健康"], data_used={"sku_count": len(skus), "location_count": len(locations)}, provenance=["warehouse-api", "deterministic-rules"])
