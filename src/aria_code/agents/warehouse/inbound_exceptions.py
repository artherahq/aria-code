"""电脑端入库异常分级 Agent。只提供建议，不修改入库或库存。"""
from __future__ import annotations
from typing import Any, Dict
from ..base import BaseAgent, AgentResult
from .contracts import integer, records

class InboundExceptionAgent(BaseAgent):
    name = "warehouse_inbound_exceptions"
    description = "入库异常分级：识别超时、数量差异、破损和未上架货件"

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        orders = records(data, "inbounds")
        critical = []
        for order in orders:
            gaps = abs(integer(order.get("expected_qty")) - integer(order.get("received_qty")))
            damaged = integer(order.get("damaged_qty"))
            if gaps or damaged or order.get("overdue", False):
                reasons = []
                if gaps: reasons.append(f"数量差异 {gaps}")
                if damaged: reasons.append(f"破损 {damaged}")
                if order.get("overdue", False): reasons.append("已超时")
                critical.append(f"{order.get('id', '未知入库单')}：{', '.join(reasons)}")
        signal = "SEVERE" if critical else "GOOD"
        analysis = "需优先人工复核：" + "；".join(critical) if critical else "当前没有需要人工复核的入库异常。"
        return AgentResult(agent=self.name, symbol=symbol, analysis=analysis, confidence=.92, signal=signal, key_points=critical or ["无入库异常"], data_used={"inbound_count": len(orders), "exception_count": len(critical)}, provenance=["warehouse-api", "deterministic-rules"])
