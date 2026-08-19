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
from .signal_scheme import SignalScheme, FINANCIAL_SCHEME

logger = logging.getLogger(__name__)

# 默认 team 构成（金融场景；其他领域调用方应显式传 agents= 而不是依赖这个）
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
        on_thought: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, Dict], None]] = None,
        on_tool_end: Optional[Callable[[str, Any], None]] = None,
        on_agent_done: Optional[Callable[[str, AgentResult], None]] = None,
        on_synthesis_start: Optional[Callable[[List["AgentResult"]], None]] = None,
        timeout_per_agent: float = 60.0,
        synthesis_timeout: float | None = None,
        lang: str = "zh",
        signal_scheme: SignalScheme = FINANCIAL_SCHEME,
    ):
        self.llm                = llm_provider
        self.data               = data_router
        self.on_token           = on_token
        self.on_thought         = on_thought
        self.on_tool_start      = on_tool_start
        self.on_tool_end        = on_tool_end
        self.on_agent_done      = on_agent_done
        self.on_synthesis_start = on_synthesis_start
        self.timeout            = timeout_per_agent
        self.synthesis_timeout  = (
            float(synthesis_timeout)
            if synthesis_timeout is not None
            else min(float(timeout_per_agent), 30.0)
        )
        self.lang               = lang
        # 领域可插拔的信号词汇表/表决规则；默认金融词汇，对现有调用方零行为
        # 变化。realty/sports 等领域应传各自的 SignalScheme（见 signal_scheme.py），
        # 而不是让自己的 agent 借用金融的 BUY/SELL 语义表达完全不同的含义。
        self.signal_scheme      = signal_scheme

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
            on_thought=self.on_thought,
            on_tool_start=self.on_tool_start,
            on_tool_end=self.on_tool_end,
            lang=self.lang,
        )

    @staticmethod
    def _available_default_team() -> List[str]:
        """Return only default agents that are registered in this installation."""
        registry = get_registry()
        return [name for name in DEFAULT_TEAM if registry.get(name)]

    def _emit_agent_done(self, name: str, result: AgentResult) -> None:
        """Notify UI adapters without allowing rendering errors to fail agents."""
        if not self.on_agent_done:
            return
        try:
            self.on_agent_done(name, result)
        except Exception as exc:
            logger.debug("[%s] completion callback failed: %s", name, exc)

    @staticmethod
    def _fallback_data_is_usable(agent_name: str, data: Dict[str, Any]) -> bool:
        if agent_name == "technical":
            return bool(data.get("quote", {}).get("price") and data.get("history"))
        if agent_name == "fundamental":
            fundamentals = data.get("fundamentals") or {}
            return any(
                fundamentals.get(key) not in (None, "", 0)
                for key in ("pe_ttm", "pe_ratio", "pb", "pb_ratio", "roe", "revenue_growth")
            )
        if agent_name == "risk":
            return bool(data.get("risk_metrics"))
        return False

    async def _deterministic_fallback(
        self,
        agent: BaseAgent,
        symbol: str,
        data: Optional[Dict[str, Any]],
        reason: str,
    ) -> Optional[AgentResult]:
        if not data or not self._fallback_data_is_usable(agent.name, data):
            return None
        try:
            fallback_agent = agent.__class__(
                llm_provider=None,
                data_router=None,
                on_token=None,
                lang=self.lang,
            )
            result = await fallback_agent.analyze(symbol, data)
            result.degraded = True
            result.confidence = min(float(result.confidence or 0), 0.45)
            result.provenance = list(dict.fromkeys([
                *result.provenance,
                "prefetched_market_bundle",
                "deterministic_template",
            ]))
            result.limitations = list(dict.fromkeys([
                *result.limitations,
                f"LLM agent unavailable: {reason}",
            ]))
            result.data_used = dict(result.data_used or {})
            result.data_used["fallback_reason"] = reason
            return result
        except Exception as exc:
            logger.warning("[%s] deterministic fallback failed: %s", agent.name, exc)
            return None

    async def _run_one(
        self,
        agent: BaseAgent,
        symbol: str,
        prefetched_data: Optional[Dict[str, Any]] = None,
    ) -> AgentResult:
        try:
            agent._current_data = prefetched_data or {}
            operation = (
                agent.analyze(symbol, prefetched_data)
                if prefetched_data is not None
                else agent.run(symbol)
            )
            result = await asyncio.wait_for(
                operation, timeout=self.timeout
            )
            self._emit_agent_done(agent.name, result)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"[{agent.name}] 超时 ({self.timeout}s)")
            fallback = await self._deterministic_fallback(
                agent, symbol, prefetched_data, "timeout"
            )
            if fallback is not None:
                self._emit_agent_done(agent.name, fallback)
                return fallback
            _timeout_result = AgentResult(
                agent=agent.name, symbol=symbol,
                analysis="", confidence=0.0, error="timeout",
            )
            # Still emit the leaf so the streaming tree shows ⎿ ⏺ <agent> 超时
            self._emit_agent_done(agent.name, _timeout_result)
            return _timeout_result
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            logger.warning("[%s] failed: %s", agent.name, reason)
            fallback = await self._deterministic_fallback(
                agent, symbol, prefetched_data, reason
            )
            if fallback is not None:
                self._emit_agent_done(agent.name, fallback)
                return fallback
            failed = AgentResult(
                agent=agent.name,
                symbol=symbol,
                analysis="",
                confidence=0.0,
                error=reason,
            )
            self._emit_agent_done(agent.name, failed)
            return failed

    async def run(
        self,
        symbol: str,
        agents: Optional[List[str]] = None,
        market_context: Optional[Dict[str, Any]] = None,
        agent_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> TeamResult:
        """并行运行所有 agent，等待全部完成后汇总。"""
        names_to_run = agents
        t0 = time.time()

        if not names_to_run or names_to_run == ["auto"]:
            supervisor = self._build_agent("supervisor")
            if supervisor:
                try:
                    sup_result = await asyncio.wait_for(
                        supervisor.analyze(symbol, market_context or {}),
                        timeout=self.timeout
                    )
                    import json
                    resolved = json.loads(sup_result.analysis)
                    names_to_run = (
                        resolved
                        if isinstance(resolved, list) and resolved
                        else self._available_default_team()
                    )
                    logger.info(f"[supervisor] dynamically selected agents: {names_to_run}")
                except Exception as e:
                    logger.warning(f"[supervisor] failed to resolve dynamic agents: {e}, falling back to default.")
                    names_to_run = self._available_default_team()
            else:
                names_to_run = self._available_default_team()

        # 过滤掉 synthesis 和 debate（各自在并行批次后单独运行）
        regular = [n for n in names_to_run if n not in ("synthesis", "debate")]
        agent_objects = [a for n in regular if (a := self._build_agent(n))]

        if not agent_objects:
            return TeamResult(
                symbol=symbol, agents_run=[], results=[],
                error="no_agents_available"
            )

        # 并行执行 — return_exceptions=True 确保单个 agent 异常不取消其余 agent
        prefetched = agent_data or {}
        tasks = [self._run_one(a, symbol, prefetched.get(a.name)) for a in agent_objects]
        _raw    = await asyncio.gather(*tasks, return_exceptions=True)
        results: List[AgentResult] = []
        for _item, _agent in zip(_raw, agent_objects):
            if isinstance(_item, BaseException):
                logger.warning("[%s] 意外异常: %s", _agent.name, _item)
                results.append(AgentResult(
                    agent=_agent.name, symbol=symbol,
                    analysis="", confidence=0.0,
                    error=f"exception: {type(_item).__name__}: {_item}",
                ))
            else:
                results.append(_item)

        # DebateAgent — 显式请求 OR 信号冲突时自动触发
        explicit_debate = "debate" in names_to_run
        if explicit_debate or self.signal_scheme.needs_debate(results):
            debate_agent = self._build_agent("debate")
            if debate_agent:
                debate_data = {
                    "conflicting": [r.to_dict() for r in results if r.success],
                    # 领域词汇表要一起传：DebateAgent 默认说金融的 BUY/HOLD/SELL，
                    # 不告诉它当前领域用什么词，它产出的信号会被 vote() 过滤掉
                    # （不在 scores 里），等于裁判意见完全不参与最终结论。
                    "signal_scheme": self.signal_scheme,
                }
                try:
                    debate_result = await asyncio.wait_for(
                        debate_agent.analyze(symbol, debate_data),
                        timeout=self.timeout,
                    )
                    results.append(debate_result)
                    logger.info("[debate] %s 信号冲突已调解", symbol)
                except Exception as e:
                    logger.warning("[debate] 调解失败: %s", e)

        # Fire on_synthesis_start callback so callers can print the agent table
        # before synthesis begins streaming tokens.
        if self.on_synthesis_start:
            try:
                self.on_synthesis_start(list(results))
            except Exception:
                pass

        final_signal, confidence = self.signal_scheme.vote(results)

        # synthesis — 把 agent 结果打包进 data，直接调 analyze() 而非 run()
        synthesis_text = ""
        successful_results = [result for result in results if result.success]
        should_synthesize = bool(successful_results) and (
            "synthesis" in names_to_run or len(successful_results) >= 2
        )
        if should_synthesize:
            synth_cls = get_registry().get("synthesis")
            if synth_cls:
                synth_agent = synth_cls(
                    llm_provider=self.llm,
                    data_router=self.data,
                    on_token=self.on_token,
                )
                synth_data = {
                    "agent_results": [r.to_dict() for r in results],
                    "consensus_signal": final_signal,
                    "consensus_confidence": confidence,
                }
                if market_context:
                    synth_data.update(market_context)
                try:
                    synth_result = await asyncio.wait_for(
                        synth_agent.analyze(symbol, synth_data),
                        timeout=self.synthesis_timeout,
                    )
                    synthesis_text = synth_result.analysis
                except Exception as e:
                    logger.warning(f"[synthesis] 失败: {e}")
                    synthesis_text = _template_synthesis(results, self.signal_scheme)
            else:
                synthesis_text = _template_synthesis(results, self.signal_scheme)
        else:
            synthesis_text = _template_synthesis(results, self.signal_scheme)

        return TeamResult(
            symbol       = symbol,
            agents_run   = [a.name for a in agent_objects],
            results      = list(results),
            synthesis    = synthesis_text,
            final_signal = final_signal,
            confidence   = confidence,
            elapsed_sec  = round(time.time() - t0, 1),
        )

    async def run_sequential(
        self,
        symbol: str,
        agents: List[str],
        market_context: Optional[Dict[str, Any]] = None,
        agent_data: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> TeamResult:
        """
        DAG / Pipeline 顺序工作流。
        按给定顺序依次执行 Agent，上一个 Agent 的核心结论（key_points）会自动
        注入到下一个 Agent 的上下文中。适合需要因果推理的场景（如 Macro -> Fundamental）。
        """
        t0 = time.time()
        results: List[AgentResult] = []
        accumulated_context = dict(market_context or {})

        agent_objects = [a for n in agents if (a := self._build_agent(n))]
        if not agent_objects:
            return TeamResult(symbol=symbol, agents_run=[], results=[], error="no_agents_available")

        prefetched = agent_data or {}

        for agent in agent_objects:
            # 注入前面累积的上下文（如果有 prefetched_data 则混合进去）
            current_data = dict(prefetched.get(agent.name, {}))
            current_data["upstream_context"] = accumulated_context.get("upstream_insights", [])

            result = await self._run_one(agent, symbol, current_data)
            results.append(result)

            # 将当前结论提取，传递给下一个 Agent
            if result.success and result.key_points:
                insights = accumulated_context.get("upstream_insights", [])
                insights.extend([f"[{agent.name.upper()}]: {pt}" for pt in result.key_points])
                accumulated_context["upstream_insights"] = insights

        # 执行最终的信号表决与综合
        final_signal, confidence = self.signal_scheme.vote(results)
        synthesis_text = _template_synthesis(results, self.signal_scheme)

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
    on_thought: Optional[Callable] = None,
    on_tool_start: Optional[Callable] = None,
    on_tool_end: Optional[Callable] = None,
    on_agent_done: Optional[Callable] = None,
    on_synthesis_start: Optional[Callable] = None,
    lang: str = "zh",
    market_context: Optional[Dict[str, Any]] = None,
    agent_data: Optional[Dict[str, Dict[str, Any]]] = None,
    timeout_per_agent: float = 60.0,
    synthesis_timeout: float | None = None,
    use_pipeline: bool = False,
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
        on_thought=on_thought,
        on_tool_start=on_tool_start,
        on_tool_end=on_tool_end,
        on_agent_done=on_agent_done,
        on_synthesis_start=on_synthesis_start,
        timeout_per_agent=timeout_per_agent,
        synthesis_timeout=synthesis_timeout,
        lang=lang,
    )
    if use_pipeline:
        return await team.run_sequential(
            symbol,
            agents=agents,
            market_context=market_context,
            agent_data=agent_data,
        )
    else:
        return await team.run(
            symbol,
            agents=agents,
            market_context=market_context,
            agent_data=agent_data,
        )


# ── 内部工具 ──────────────────────────────────────────────────────────────────

def _needs_debate(results: List[AgentResult], scheme: SignalScheme = FINANCIAL_SCHEME) -> bool:
    """当出现真实的正/负信号分歧时返回 True（默认金融 BUY/SELL 词汇，向后兼容）。"""
    return scheme.needs_debate(results)


def _vote_signal(results: List[AgentResult], scheme: SignalScheme = FINANCIAL_SCHEME) -> tuple:
    """按置信度加权表决最终信号（默认金融词汇，向后兼容 agents/deep/pipeline.py 的直接调用）。"""
    return scheme.vote(results)


def _template_synthesis(results: List[AgentResult], scheme: SignalScheme = FINANCIAL_SCHEME) -> str:
    """无 synthesis agent 时的模板汇总"""
    if not results:
        return "分析完成，无结果。"
    lines = ["## 团队分析汇总\n"]
    failed_count = sum(1 for r in results if not r.success)
    if failed_count:
        lines.append(f"> ⚠️ {failed_count}/{len(results)} 个 agent 未能完成分析"
                     f"（超时或 LLM 不可用），以下结论仅基于成功的 agent。\n")
    for r in results:
        if r.success:
            lines.append(f"**{r.agent.upper()}** ({r.signal}, 置信度 {r.confidence:.0%})")
            for pt in (r.key_points or [])[:3]:
                lines.append(f"  • {pt}")
        else:
            err_label = "超时" if r.error == "timeout" else (r.error or "分析失败")
            lines.append(f"**{r.agent.upper()}** ⚠️ {err_label}")
    signal, conf = scheme.vote(results)
    lines.append(f"\n**综合结论**: {signal}（置信度 {conf:.0%}）")
    if failed_count == len(results):
        lines.append("\n> ⚠️ 所有 agent 均未成功，此结论仅为默认值，不具参考意义。请确认 LLM 服务正常后重试。")
    return "\n".join(lines)
