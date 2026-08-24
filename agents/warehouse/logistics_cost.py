"""
agents/warehouse/logistics_cost.py — Logistics Cost Optimizer Agent
===================================================================
Analyzes shipping bills, freight rate tiers, surcharges, and billing discrepancies.
Recommends optimal carrier allocation to minimize logistics spend while meeting SLAs.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from ..base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class LogisticsCostOptimizerAgent(BaseAgent):
    name = "warehouse_logistics_cost"
    description = "物流运费优化智能体 — 负责运费账单审计、异常加价识别与承运商分流优化"

    def __init__(
        self,
        llm_provider=None,
        data_router=None,
        on_token: Optional[Callable[[str], None]] = None,
        on_thought: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, Dict], None]] = None,
        on_tool_end: Optional[Callable[[str, Any], None]] = None,
        lang: str = "zh",
    ) -> None:
        super().__init__(
            llm_provider=llm_provider,
            data_router=data_router,
            on_token=on_token,
            on_thought=on_thought,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            lang=lang,
        )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        """
        Analyze logistics waybill records and billing discrepancies.
        """
        waybills = data.get("waybills", data.get("shipments", []))

        # Try to use Arthera LogisticsAnalyticsService if available
        try:
            from packages.quant_engine.services.logistics_analytics_service import LogisticsAnalyticsService
            service = LogisticsAnalyticsService()
            report = service.analyze_shipping_data(waybills)
            summary_dict = report.to_dict()
        except Exception as exc:
            logger.debug(f"Arthera analytics service not imported, using local engine: {exc}")
            summary_dict = self._local_analyze(waybills)

        total_spend = summary_dict.get("total_freight_spend", 0.0)
        total_wb = summary_dict.get("total_waybills", len(waybills))
        anomalies = summary_dict.get("billing_anomalies", [])
        recs = summary_dict.get("cost_saving_recommendations", [])
        carrier_metrics = summary_dict.get("carrier_metrics", [])

        carrier_rows = []
        for c in carrier_metrics:
            c_name = c.get("carrier", "")
            c_cnt = c.get("total_shipments", 0)
            c_cost = c.get("avg_cost_per_kg", 0.0)
            c_otd = c.get("on_time_delivery_rate", 0.0)
            c_anom = c.get("billing_anomaly_count", 0)
            carrier_rows.append(f"| {c_name} | {c_cnt} | ¥{c_cost}/kg | {c_otd}% | {c_anom} 笔 |")

        table_str = "\n".join(carrier_rows) if carrier_rows else "| 暂无承运商细分数据 | - | - | - | - |"

        analysis_text = (
            f"### 🚚 物流运费与承运商审计报告\n\n"
            f"• **审计包裹总量**: `{total_wb}` 单\n"
            f"• **运费总支出**: `¥{total_spend:,.2f}`\n"
            f"• **异常计费发现**: `{len(anomalies)}` 笔异常加价\n\n"
            f"| 承运商 | 发货量 | 单公斤成本 | 准时交付率(OTD) | 计费异常 |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"{table_str}\n\n"
            f"**💡 降本优化建议**:\n"
            + ("\n".join(f"- {r}" for r in recs) if recs else "- 各承运商计费与费率水平整体受控，建议保持常规对账。")
        )

        signal = "SEVERE" if len(anomalies) > 5 else ("CONCERN" if anomalies else "GOOD")

        return AgentResult(
            agent=self.name,
            symbol=symbol,
            analysis=analysis_text,
            confidence=0.92,
            signal=signal,
            key_points=[
                f"Total Freight Spend: ¥{total_spend:,.2f}",
                f"Billing Anomalies Detected: {len(anomalies)}",
                f"Carriers Analyzed: {len(carrier_metrics)}",
            ],
            data_used=summary_dict,
            provenance=["logistics-billing-audit", "carrier-performance-matrix"],
        )

    def _local_analyze(self, waybills: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_spend = sum(float(w.get("total_cost", w.get("cost", 0.0))) for w in waybills)
        anomalies = []
        for w in waybills:
            act = float(w.get("actual_weight_kg", w.get("weight_kg", 1.0)))
            billed = float(w.get("billed_weight_kg", act))
            if act > 0 and (billed - act) / act > 0.15:
                anomalies.append({
                    "waybill_no": w.get("waybill_no", "WB"),
                    "carrier": w.get("carrier", "Carrier"),
                    "anomaly_type": "WEIGHT_OVERCHARGE",
                    "estimated_overcharge": (billed - act) * 10.0,
                })
        return {
            "total_waybills": len(waybills),
            "total_freight_spend": total_spend,
            "billing_anomalies": anomalies,
            "carrier_metrics": [],
            "cost_saving_recommendations": [f"发现 {len(anomalies)} 笔重量异常计费"] if anomalies else [],
        }
