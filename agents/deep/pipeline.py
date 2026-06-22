"""Deep analysis orchestrator — ties P0–P3 into one layered pass.

    team (parallel agents) ─▶ group by theme (P1a) ─▶ deepen gaps (P0)
        ─▶ quant fusion (P2) ─▶ vote ─▶ calibrate (P2) ─▶ critic (P1b)
        ─▶ synthesis ─▶ tiered result (P3)

``analyze()`` is the deterministic core (assembles a DeepAnalysisResult from given
agent results); it needs no LLM or network and is fully unit-tested. ``run()`` is
the async convenience that first runs the AgentTeam, then calls ``analyze()``.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, List, Optional

from ..base import AgentResult
from .critic import critique, soften_signal
from .deepen import deepen
from .models import DeepAnalysisResult
from .quant_fusion import CalibrationStore, calibrate_confidence, gather_quant_evidence
from .themes import group_by_theme


def _vote_all(results: List[AgentResult]):
    """Confidence-weighted majority over every successful result."""
    try:
        from ..team import _vote_signal
        return _vote_signal(results)
    except Exception:
        return "HOLD", 0.0


def _build_synthesis(themes, notes, team_synthesis, agree, quant) -> str:
    parts: List[str] = []
    if team_synthesis and team_synthesis.strip():
        parts.append(team_synthesis.strip())
    elif themes:
        parts.append("；".join(t.summary for t in themes))
    if notes:
        parts.append("补充证据（深挖）：" + "；".join(notes))
    if quant and quant.available:
        if agree == "disagree":
            parts.append(f"⚠️ 量化信号为 {quant.verdict()}，与定性方向相反，已下调置信度，建议人工复核。")
        elif agree == "agree":
            parts.append(f"量化信号（{quant.verdict()}）与定性方向一致，置信度已上调。")
    return "\n\n".join(p for p in parts if p)


class DeepAnalysisPipeline:
    def __init__(self, llm_provider=None, data_router=None,
                 store: Optional[CalibrationStore] = None, lang: str = "zh"):
        self.llm = llm_provider
        self.data = data_router
        self.store = store if store is not None else CalibrationStore()
        self.lang = lang

    def analyze(
        self,
        symbol: str,
        agent_results: List[AgentResult],
        *,
        team_synthesis: str = "",
        quant_provider: Optional[Callable[[str], Dict[str, Dict]]] = None,
        tool_runner: Optional[Callable[[str, Dict], Optional[Dict]]] = None,
        deepen_result: Optional[tuple] = None,
    ) -> DeepAnalysisResult:
        """Deterministic assembly of the deep result (no LLM/network required).

        ``deepen_result`` lets ``run()`` inject the LLM-driven (agentic) deepening
        output; when absent the deterministic gap planner runs instead.
        """
        if not agent_results:
            return DeepAnalysisResult(symbol=symbol, error="no_agent_results")

        themes = group_by_theme(agent_results)                       # P1a
        quant, qprov = gather_quant_evidence(symbol, quant_provider)  # P2
        if deepen_result is not None:                                 # P0 (agentic, injected)
            notes, dprov = deepen_result
        else:
            notes, dprov = deepen(symbol, themes, quant, tool_runner)  # P0 (deterministic)

        raw_signal, raw_conf = _vote_all(agent_results)
        cal_conf, agree = calibrate_confidence(raw_conf, raw_signal, quant, self.store)  # P2

        kp_count = sum(len(t.key_points) for t in themes)
        crit = critique(agent_results, raw_signal, cal_conf, quant, agree,  # P1b
                        qprov + dprov, kp_count)

        final_signal = raw_signal if crit.passed else soften_signal(raw_signal)
        synthesis = _build_synthesis(themes, notes, team_synthesis, agree, quant)

        return DeepAnalysisResult(
            symbol=symbol,
            final_signal=final_signal,
            raw_confidence=raw_conf,
            calibrated_confidence=cal_conf,
            themes=themes,
            quant=quant,
            critique=crit,
            provenance=qprov + dprov,
            synthesis=synthesis,
            agent_results=[r.to_dict() for r in agent_results],
        )

    async def run(
        self,
        symbol: str,
        agents: Optional[List[str]] = None,
        quant_provider: Optional[Callable] = None,
        tool_runner: Optional[Callable] = None,
        on_agent_done: Optional[Callable] = None,
    ) -> DeepAnalysisResult:
        t0 = time.time()
        from ..team import AgentTeam
        team = AgentTeam(llm_provider=self.llm, data_router=self.data,
                         on_agent_done=on_agent_done, lang=self.lang)
        tr = await team.run(symbol, agents=agents)

        # P0 agentic deepening (LLM-driven tool loop) when an LLM is available.
        deepen_result = None
        if self.llm and tr.results:
            try:
                from .deepen import deepen_agentic
                from .themes import group_by_theme as _grp
                deepen_result = await deepen_agentic(
                    symbol, _grp(tr.results), None, self.llm, tool_runner)
            except Exception:
                deepen_result = None

        res = self.analyze(symbol, tr.results, team_synthesis=tr.synthesis,
                           quant_provider=quant_provider, tool_runner=tool_runner,
                           deepen_result=deepen_result)

        # P1b LLM self-check — augments the deterministic critic when an LLM is present.
        if self.llm and res.critique is not None and res.synthesis:
            try:
                from .critic import llm_critique, soften_signal
                theme_sum = "；".join(t.summary for t in res.themes)
                extra = await llm_critique(symbol, res.synthesis, theme_sum, self.llm)
                if extra:
                    had_high = bool(res.critique.high)
                    res.critique.issues.extend(extra)
                    new_high = any(i.severity == "high" for i in extra)
                    if new_high:
                        res.critique.passed = False
                        # soften once if the deterministic pass had cleared it
                        if not had_high:
                            res.final_signal = soften_signal(res.final_signal)
            except Exception:
                pass

        res.elapsed_sec = round(time.time() - t0, 1)
        if tr.error and not tr.results:
            res.error = tr.error
        return res


async def run_deep_analysis(symbol: str, llm_provider=None, data_router=None,
                            agents: Optional[List[str]] = None, lang: str = "zh",
                            **kw) -> DeepAnalysisResult:
    """Convenience: run the full team + deep pipeline for ``symbol``."""
    pipe = DeepAnalysisPipeline(llm_provider=llm_provider, data_router=data_router, lang=lang)
    return await pipe.run(symbol, agents=agents, **kw)
