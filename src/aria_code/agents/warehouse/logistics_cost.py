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



    async def _execute_tool(self, tool_name: str, tool_args: Dict[str, Any]) -> str:
        if tool_name == "analyze_logistics_data":
            if self.on_tool_start:
                self.on_tool_start(tool_name, tool_args)
                
            from aria_code.tools.logistics_tools import tool_analyze_logistics_data
            
            try:
                res = tool_analyze_logistics_data(tool_args)
                result_str = str(res)
            except Exception as e:
                result_str = f"Error: {e}"
                
            if self.on_tool_end:
                self.on_tool_end(tool_name, result_str)
            return result_str
            
        return await super()._execute_tool(tool_name, tool_args)

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:

        """
        Analyze logistics waybill records and billing discrepancies using LLM and tools.
        """
        request_text = data.get("request", "分析物流承运商及异常运单数据")
        
        system_prompt = (
            "你是一个高级企业物流与供应链成本优化专家。\n"
            "你的任务是通过调用 `analyze_logistics_data` 工具审计真实的物流运单数据，分析各承运商的准时率、运费成本，以及可能存在的包裹计费重量异常（如抛货异常）。\n"
            "你可以直接调用该工具，不需要传入参数，它会自动连接并查询本地的物流真实数据库。\n"
            "工具调用格式：\n"
            '{"type": "tool_call", "name": "analyze_logistics_data", "args": {}}\n'
            "在收到数据后，请输出一份结构清晰的Markdown财务审计报告，指出哪些承运商存在问题，以及预计可节省的成本。"
        )
        
        user_prompt = f"请开始执行任务：{request_text}"
        
        analysis = await self._call_llm(system_prompt, user_prompt, max_tokens=800)
        
        return AgentResult(
            agent=self.name,
            symbol=symbol,
            analysis=analysis,
            confidence=0.9,
            signal="CONCERN",
            key_points=[
                "Logistics Audit Completed via Local DB",
                "Anomaly Analysis Executed"
            ],
            data_used={},
            provenance=["logistics-billing-audit", "carrier-performance-matrix"],
        )
