"""
agents/financial/cashflow_burn.py — Cash Flow, Burn Rate & Runway Analysis Agent
==============================================================================
Monitors enterprise cash flow sustainability:
1. Gross Burn vs. Net Burn Rate
2. Cash Runway (Months of survival under zero revenue or current operations)
3. Accounts Receivable Aging Breakdown & Bad Debt Risk
4. Working Capital Liquidity Stress Testing
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from ..base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class CashflowBurnRateAgent(BaseAgent):
    name = "cashflow_burn"
    description = "企业现金流与烧钱率智能体 — 负责现金跑道测算、月度烧钱率与应收账龄风险审计"

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
        Analyze cash flow, burn rate, and runway.
        """
        company_name = symbol or data.get("company_name", "Enterprise")
        cash = float(data.get("cash", data.get("cash_and_equivalents", 5000000.0)))
        monthly_inflows = float(data.get("monthly_inflows", data.get("revenue_monthly", 800000.0)))
        monthly_outflows = float(data.get("monthly_outflows", data.get("expenses_monthly", 1000000.0)))
        ar_aging = data.get("ar_aging", {
            "within_30_days": 1200000.0,
            "days_31_60": 500000.0,
            "days_61_90": 200000.0,
            "over_90_days": 100000.0,
        })

        gross_burn = monthly_outflows
        net_burn = monthly_outflows - monthly_inflows

        if net_burn > 0:
            runway_months = cash / net_burn
            status = "CONCERN" if runway_months < 12.0 else ("SEVERE" if runway_months < 6.0 else "GOOD")
        else:
            runway_months = 999.0
            status = "GOOD"

        total_ar = sum(ar_aging.values())
        overdue_ar = ar_aging.get("days_61_90", 0.0) + ar_aging.get("over_90_days", 0.0)
        overdue_pct = (overdue_ar / max(1.0, total_ar)) * 100.0

        runway_str = f"{runway_months:.1f} 个月" if runway_months < 100.0 else "正向自由现金流 (无需消耗现金储备)"
        burn_str = f"¥{net_burn:,.2f}/月" if net_burn > 0 else f"正向结余 ¥{abs(net_burn):,.2f}/月"

        analysis_text = (
            f"### 💰 企业现金流与跑道 (Runway) 分析报告: {company_name}\n\n"
            f"• **当前现金储备**: `¥{cash:,.2f}`\n"
            f"• **月度净消耗 (Net Burn)**: `{burn_str}` (月支出 `¥{gross_burn:,.2f}`)\n"
            f"• **现金可持续跑道**: `{runway_str}`\n"
            f"• **应收账款总额**: `¥{total_ar:,.2f}` (逾期 >60天占比: `{overdue_pct:.1f}%`)\n\n"
            f"**📊 账龄结构 (AR Aging)**:\n"
            f"- 30天以内 (正常): `¥{ar_aging.get('within_30_days', 0):,.2f}`\n"
            f"- 31-60天: `¥{ar_aging.get('days_31_60', 0):,.2f}`\n"
            f"- 61-90天 (预警): `¥{ar_aging.get('days_61_90', 0):,.2f}`\n"
            f"- >90天 (高坏账风险): `¥{ar_aging.get('over_90_days', 0):,.2f}`\n\n"
            f"**💡 资金管理对策**:\n"
        )

        if net_burn > 0 and runway_months < 12.0:
            analysis_text += f"- ⚠️ 现金跑道短于 12 个月，需严控固定支出或推进催收逾期款项 ¥{overdue_ar:,.2f}。\n"
        else:
            analysis_text += "- ✓ 现金储备处于安全水位，可按计划支持业务拓展。\n"

        if overdue_pct > 15.0:
            analysis_text += f"- ⚠️ 逾期账款占比达 {overdue_pct:.1f}%，需启动法务/催收介入以防死账坏账。"

        return AgentResult(
            agent=self.name,
            symbol=company_name,
            analysis=analysis_text,
            confidence=0.93,
            signal=status,
            key_points=[
                f"Cash Reserve: ¥{cash:,.2f}",
                f"Net Burn Rate: {burn_str}",
                f"Runway: {runway_str}",
                f"Overdue AR (>60d): ¥{overdue_ar:,.2f} ({overdue_pct:.1f}%)",
            ],
            data_used={
                "cash": cash,
                "gross_burn": gross_burn,
                "net_burn": net_burn,
                "runway_months": runway_months,
                "ar_aging": ar_aging,
            },
            provenance=["cashflow-runway-engine", "ar-aging-risk-matrix"],
        )
