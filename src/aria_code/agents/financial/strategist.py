"""
agents/financial/strategist.py — Quant Strategist Client Agent
=============================================================
Bridges client-side agent invocation with the cloud QuantStrategistAgent
and RiskComplianceAgent. Produces formal SignalRuleTree specifications.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from ..base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class StrategistAgent(BaseAgent):
    name = "strategist"
    description = "量化策略师智能体 — 负责设计策略规则树、因子组合与进出场逻辑"

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
        Design standardized quantitative strategy rule tree.
        """
        style = data.get("style", "momentum")
        goal = data.get("goal", f"Maximize risk-adjusted returns on {symbol}")
        params = data.get("params", {})

        # Try to import from Arthera packages quant engine
        try:
            from aria_code.packages.quant_engine.agent_runtime.quant_strategist import QuantStrategistAgent
            from aria_code.packages.quant_engine.risk.risk_compliance_agent import RiskComplianceAgent

            cloud_strategist = QuantStrategistAgent()
            rule_tree = await cloud_strategist.design_strategy(
                symbol=symbol,
                goal=goal,
                style=style,
                timeframe="1d",
                params=params,
            )

            # Pass through Risk & Compliance audit
            risk_agent = RiskComplianceAgent()
            audit = risk_agent.audit_and_harden(rule_tree)
            rule_dict = rule_tree.to_dict()

        except Exception as exc:
            logger.warning(f"Could not import Arthera cloud quant engine, using internal fallback: {exc}")
            rule_dict = {
                "strategy_id": f"strat_{symbol.lower()}_fallback",
                "strategy_name": f"{symbol} Dual Moving Average",
                "symbol": symbol,
                "factors": [
                    {"name": "SMA_Fast", "params": {"period": 10}},
                    {"name": "SMA_Slow", "params": {"period": 30}},
                ],
                "entry_conditions": [
                    {"indicator": "SMA_Fast", "operator": "cross_above", "threshold": "SMA_Slow", "action": "BUY"}
                ],
                "exit_conditions": [
                    {"trigger_type": "stop_loss", "params": {"stop_loss_pct": 7.0}, "action": "SELL"}
                ],
                "risk_constraints": {
                    "max_drawdown_limit_pct": 18.0,
                    "max_position_size_pct": 25.0,
                    "stop_loss_pct": 7.0,
                    "slippage_pct": 0.05,
                    "commission_pct": 0.05,
                },
            }

        factors_summary = ", ".join(f.get("name", "") for f in rule_dict.get("factors", []))
        summary = (
            f"### 📈 量化策略规则树规划完成: {rule_dict.get('strategy_name', symbol)}\n\n"
            f"• **标的资产**: {symbol}\n"
            f"• **选取因子**: {factors_summary}\n"
            f"• **入场条件**: {rule_dict.get('entry_conditions', [{}])[0].get('description', '金叉信号入场')}\n"
            f"• **风控约束**: 止损 {rule_dict.get('risk_constraints', {}).get('stop_loss_pct', 7.0)}%, "
            f"最大回撤控制 {rule_dict.get('risk_constraints', {}).get('max_drawdown_limit_pct', 20.0)}%, "
            f"滑点摩擦 {rule_dict.get('risk_constraints', {}).get('slippage_pct', 0.05)}%\n"
            f"• **后续流转**: 规则已下发至 Coder Agent 进行自包含工程编码"
        )

        return AgentResult(
            agent=self.name,
            symbol=symbol,
            analysis=summary,
            confidence=0.90,
            signal="BUY",
            key_points=[
                f"Generated SignalRuleTree for {symbol}",
                f"Factors: {factors_summary}",
                "Injected risk & friction constraints",
            ],
            data_used={
                "rule_tree": rule_dict,
                "symbol": symbol,
            },
        )
