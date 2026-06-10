"""
agents/ — Aria Code 可组合多智能体系统
=======================================
用户可在项目根放置 aria_agents/ 目录，其中的 Agent 类自动被发现。

内置 Agent:
  macro       → 宏观环境、利率、行业周期
  fundamental → 财务指标、估值、竞争壁垒
  technical   → 图形形态、动量、关键价位
  risk        → 风险评分、仓位建议
  synthesis   → 汇总以上，输出可操作建议

自定义 Agent 示例 (aria_agents/northbound_agent.py):
    from agents.base import BaseAgent, AgentResult

    class NorthboundAgent(BaseAgent):
        name        = "northbound"
        description = "北向资金分析专家"

        async def analyze(self, symbol: str, data: dict) -> AgentResult:
            ...
"""

from .base import BaseAgent, AgentResult
from .registry import AgentRegistry, get_registry
from .team import AgentTeam, run_team

__all__ = [
    "BaseAgent", "AgentResult",
    "AgentRegistry", "get_registry",
    "AgentTeam", "run_team",
]
