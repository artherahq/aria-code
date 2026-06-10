"""
agents/team.py — 多 Agent 并行执行与结果汇总
=============================================
/team AAPL                          → 运行默认4个内置 agent
/team AAPL --agents macro,technical → 只运行指定 agent
/team AAPL --agents macro,my_agent  → 内置 + 自定义混合
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .base import BaseAgent, AgentResult
from .registry import get_registry

logger = logging.getLogger(__name__)

# 默认 team 构成
DEFAULT_TEAM = ["macro", "fundamental", "technical", "risk"]


@dataclass
class TeamResult:
    symbol:       str
    agents_run:   List[str]
    results:      List[AgentResult]
    synthesis:    str = ""             # 综合结论（synthesis agent 输出）
    final_signal: str = "HOLD"        # 多数表决
    confidence:   float = 0.0
    elapsed_sec:  float = 0.0
    error:        Optional[str] = None


class AgentTeam:
    """
    并行运行多个 Agent，汇总结果。

    用法:
        team   = AgentTeam(llm_provider=provider, data_router=router)
        result = await team.run("NVDA", agents=["macro","technical","risk"])
    """

    def __init__(
        self,
        llm_provider=None,
        data_router=None,
        on_token: Optional[Callable[[str], None]] = None,
        on_agent_done: Optional[Callable[[str, AgentResult], None]] = None,
        timeout_per_agent: float = 60.0,
    ):
        self.llm              = llm_provider
        self.data             = data_router
        self.on_token         = on_token
        self.on_agent_done    = on_agent_done
        self.timeout          = timeout_per_agent

    def _build_agent(self, name: str) -> Optional[BaseAgent]:
        registry = get_registry()
        cls = registry.get(name)
        if not cls:
            logger.warning(f"未知 Agent: {name}，跳过")
            return None
        return cls(
            llm_provider=self.llm,
            data_router=self.data,
            on_token=self.on_token,
        )

    async def _run_one(self, agent: BaseAgent, symbol: str) -> AgentResult:
        try:
            result = await asyncio.wait_for(
                agent.run(symbol), timeout=self.timeout
            )
            if self.on_agent_done:
                self.on_agent_done(agent.name, result)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[{agent.name}] 超时 ({self.timeout}s)")
            return AgentResult(
                agent=agent.name, symbol=symbol,
                analysis="", confidence=0.0, error="timeout",
            )

    async def run(
        self,
        symbol: str,
        agents: Optional[List[str]] = None,
    ) -> TeamResult:
        """并行运行所有 agent，等待全部完成后汇总。"""
        names_to_run = agents or DEFAULT_TEAM
        t0 = time.time()

        # 过滤掉 synthesis（最后单独跑）
        regular = [n for n in names_to_run if n != "synthesis"]
        agent_objects = [a for n in regular if (a := self._build_agent(n))]

        if not agent_objects:
            return TeamResult(
                symbol=symbol, agents_run=[], results=[],
                error="no_agents_available"
            )

        # 并行执行
        tasks   = [self._run_one(a, symbol) for a in agent_objects]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        # synthesis — 把 agent 结果打包进 data，直接调 analyze() 而非 run()
        synthesis_text = ""
        if "synthesis" in names_to_run or len(agent_objects) >= 2:
            synth_cls = get_registry().get("synthesis")
            if synth_cls:
                synth_agent = synth_cls(
                    llm_provider=self.llm,
                    data_router=self.data,
                    on_token=self.on_token,
                )
                synth_data = {"agent_results": [r.to_dict() for r in results]}
                try:
                    synth_result = await asyncio.wait_for(
                        synth_agent.analyze(symbol, synth_data),
                        timeout=self.timeout,
                    )
                    synthesis_text = synth_result.analysis
                except Exception as e:
                    logger.warning(f"[synthesis] 失败: {e}")
                    synthesis_text = _template_synthesis(results)
            else:
                synthesis_text = _template_synthesis(results)
        else:
            synthesis_text = _template_synthesis(results)

        final_signal, confidence = _vote_signal(results)

        return TeamResult(
            symbol       = symbol,
            agents_run   = [a.name for a in agent_objects],
            results      = list(results),
            synthesis    = synthesis_text,
            final_signal = final_signal,
            confidence   = confidence,
            elapsed_sec  = round(time.time() - t0, 1),
        )


# ── 独立函数（兼容旧 financial_agents.py 调用方式）──────────────────────────

async def run_team(
    symbol: str,
    agents: Optional[List[str]] = None,
    llm_provider=None,
    data_router=None,
    on_token: Optional[Callable] = None,
    on_agent_done: Optional[Callable] = None,
) -> TeamResult:
    """
    便捷函数，替代原 financial_agents.run_team_analysis()。

    旧签名兼容:
        result = await run_team_analysis("NVDA", ollama_url, model, on_token)
    新签名:
        result = await run_team("NVDA", llm_provider=provider, on_token=cb)
    """
    team = AgentTeam(
        llm_provider=llm_provider,
        data_router=data_router,
        on_token=on_token,
        on_agent_done=on_agent_done,
    )
    return await team.run(symbol, agents=agents)


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _vote_signal(results: List[AgentResult]) -> tuple:
    """多数表决最终信号"""
    _SCORE = {
        "STRONG_BUY": 2, "BUY": 1, "HOLD": 0, "SELL": -1, "STRONG_SELL": -2
    }
    valid = [r for r in results if r.success and r.signal in _SCORE]
    if not valid:
        return "HOLD", 0.0

    avg_score = sum(_SCORE[r.signal] * r.confidence for r in valid) / len(valid)
    avg_conf  = sum(r.confidence for r in valid) / len(valid)

    if avg_score >= 1.5:
        return "STRONG_BUY", avg_conf
    if avg_score >= 0.5:
        return "BUY", avg_conf
    if avg_score <= -1.5:
        return "STRONG_SELL", avg_conf
    if avg_score <= -0.5:
        return "SELL", avg_conf
    return "HOLD", avg_conf


def _template_synthesis(results: List[AgentResult]) -> str:
    """无 synthesis agent 时的模板汇总"""
    if not results:
        return "分析完成，无结果。"
    lines = ["## 团队分析汇总\n"]
    for r in results:
        if r.success:
            lines.append(f"**{r.agent.upper()}** ({r.signal}, 置信度 {r.confidence:.0%})")
            for pt in (r.key_points or [])[:3]:
                lines.append(f"  • {pt}")
        else:
            lines.append(f"**{r.agent.upper()}** ⚠️ {r.error or '分析失败'}")
    signal, conf = _vote_signal(results)
    lines.append(f"\n**综合结论**: {signal}（置信度 {conf:.0%}）")
    return "\n".join(lines)
