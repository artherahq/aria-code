"""Logistics cost audit backed by waybill records and traceable calculations."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from ..base import BaseAgent, AgentResult
from ...tools.logistics_tools import tool_analyze_logistics_data


class LogisticsCostOptimizerAgent(BaseAgent):
    name = "warehouse_logistics_cost"
    description = "物流运费优化智能体 — 基于运单核算费用、准时率和计费重量异常"

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

    async def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        if tool_name != "analyze_logistics_data":
            return await super()._execute_tool(tool_name, tool_args)
        if self.on_tool_start:
            self.on_tool_start(tool_name, tool_args)
        result = str(tool_analyze_logistics_data(tool_args))
        if self.on_tool_end:
            self.on_tool_end(tool_name, result)
        return result

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        params: Dict[str, Any] = {key: data[key] for key in ("waybills", "file_path") if key in data}
        audit = tool_analyze_logistics_data(params)
        if not audit["success"]:
            error = audit["error"]
            return AgentResult(
                agent=self.name,
                symbol=symbol,
                analysis=f"无法完成物流成本审计：{error}",
                confidence=0.0,
                signal="HOLD",
                error=error,
                limitations=["未取得可审计的运单记录"],
            )
        result = audit["data"]
        rate = result["overall_on_time_rate"]
        rate_text = f"{rate}%" if rate is not None else "未知（没有准时状态记录）"
        carrier_lines = [
            f"- {item['carrier']}：{item['count']} 单，费用 ¥{item['total_spend']:,.2f}，准时率 "
            + (f"{item['on_time_rate']}%" if item["on_time_rate"] is not None else "未知")
            for item in result["carrier_metrics"]
        ]
        analysis = (
            f"## 物流费用审计\n来源：{audit['source']}；共 {result['total_waybills']} 单。\n"
            f"运费总支出：¥{result['total_freight_spend']:,.2f}；已知准时率：{rate_text}。\n\n"
            "### 承运商表现\n" + "\n".join(carrier_lines) + "\n\n"
            f"### 异常计费发现\n{len(result['billing_anomalies'])} 单计费重量高于实重 20% 以上，"
            "需核对体积重及合同条款后才能确认多收费用。"
        )
        return AgentResult(
            agent=self.name,
            symbol=symbol,
            analysis=analysis,
            confidence=1.0,
            signal="CONCERN" if result["billing_anomalies"] else "HOLD",
            key_points=[f"运费总支出 ¥{result['total_freight_spend']:,.2f}", f"待核实重量异常 {len(result['billing_anomalies'])} 单"],
            data_used=result,
            provenance=[audit["source"]],
            limitations=["重量差异仅是审计线索；无法据此推断可节省金额"],
        )
