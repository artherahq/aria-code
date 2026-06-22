"""P0 — tool-augmented deepening for material/uncertain findings.

The flat agents summarise pre-fetched data in a single pass. This layer runs a
small *gap-driven tool loop*: it looks at where the analysis is thin (no risk
angle, momentum undecided, no catalyst coverage) and calls finance tools to pull
the missing evidence — the same move Claude Code's agent loop makes when it needs
more before concluding.

v1 selects tools deterministically from the gaps (testable, no LLM). The selector
is isolated in ``_plan_steps`` so an LLM-driven planner can drop in later without
touching the execution loop. The tool runner is injectable for tests.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from .models import Provenance, QuantEvidence, ThemeGroup


def _default_runner(tool: str, params: Dict) -> Optional[Dict]:
    try:
        import local_finance_tools as lft
    except Exception:
        return None
    fn = getattr(lft, tool, None)
    if not fn:
        return None
    try:
        return fn(params)
    except Exception:
        return None


def _plan_steps(themes: List[ThemeGroup], quant: Optional[QuantEvidence]) -> List[Tuple[str, str]]:
    """Decide which tools to call based on coverage gaps. Returns [(tool, label)]."""
    by_theme = {t.theme: t for t in themes}
    plan: List[Tuple[str, str]] = []

    risk = next((t for k, t in by_theme.items() if k.startswith("风险")), None)
    if risk is None or risk.confidence < 0.5:
        plan.append(("_get_risk_metrics", "下行风险"))

    mom = next((t for k, t in by_theme.items() if k.startswith("动量")), None)
    if mom is not None and mom.signal == "HOLD":
        plan.append(("_calculate_factors", "动量/因子"))

    cat = next((t for k, t in by_theme.items() if k.startswith("催化")), None)
    if cat is None:
        plan.append(("_analyze_news", "催化/消息"))

    # quant undecided → a backtest gives an independent, data-grounded read
    if quant is None or not quant.available or quant.verdict() == "NEUTRAL":
        plan.append(("_backtest_strategy", "策略回测"))

    return plan


def _summarize(tool: str, res: Dict) -> str:
    if tool == "_get_risk_metrics":
        bits = []
        if "var_daily" in res:     bits.append(f"日VaR {res['var_daily']:.1%}")
        for k in ("sharpe", "sharpe_ratio"):
            if isinstance(res.get(k), (int, float)): bits.append(f"Sharpe {res[k]:.2f}"); break
        for k in ("max_drawdown", "max_dd"):
            if isinstance(res.get(k), (int, float)): bits.append(f"MaxDD {res[k]:.1%}"); break
        return "，".join(bits)
    if tool == "_calculate_factors":
        bits = [f"{k}={v:.3f}" for k, v in res.items()
                if isinstance(v, (int, float)) and k in
                ("momentum", "volatility", "beta", "rsi", "ic")][:4]
        return "，".join(bits)
    if tool == "_analyze_news":
        s = res.get("sentiment") or res.get("score")
        return f"新闻情绪 {s}" if s is not None else (res.get("summary", "")[:80])
    if tool == "_backtest_strategy":
        bits = []
        for k in ("total_return", "return", "cagr"):
            if isinstance(res.get(k), (int, float)): bits.append(f"收益 {res[k]:+.1%}"); break
        for k in ("sharpe", "sharpe_ratio"):
            if isinstance(res.get(k), (int, float)): bits.append(f"Sharpe {res[k]:.2f}"); break
        return "，".join(bits)
    return ""


def deepen(
    symbol: str,
    themes: List[ThemeGroup],
    quant: Optional[QuantEvidence] = None,
    tool_runner: Optional[Callable[[str, Dict], Optional[Dict]]] = None,
    max_steps: int = 3,
) -> Tuple[List[str], List[Provenance]]:
    """Deterministic gap-driven tool loop. Returns (deepening_notes, provenance)."""
    runner = tool_runner or _default_runner
    notes: List[str] = []
    prov: List[Provenance] = []
    for tool, label in _plan_steps(themes, quant)[:max_steps]:
        res = None
        try:
            res = runner(tool, {"symbol": symbol})
        except Exception:
            res = None
        if isinstance(res, dict) and res.get("success"):
            line = _summarize(tool, res)
            if line:
                notes.append(f"[{label}] {line}")
                prov.append(Provenance(label, f"deepen:{tool}"))
    return notes, prov


# ── P0 agentic: LLM-driven tool loop (plan → act → observe → re-plan) ──────────
_TOOL_MENU = {
    "_get_risk_metrics":      "下行风险:VaR / Sharpe / 最大回撤",
    "_calculate_factors":     "量化因子:动量 / 波动 / Beta / IC",
    "_analyze_news":          "新闻情绪与催化事件",
    "_backtest_strategy":     "对该标的做策略回测(收益/Sharpe)",
    "_get_sector_performance":"所属板块近期表现",
    "_get_northbound_flow":   "北向资金流向(A股)",
}


async def _collect_llm(llm, system: str, user: str, max_tokens: int = 120) -> str:
    try:
        from providers.llm.base import Message
    except Exception:
        return ""
    msgs = [Message(role="system", content=system), Message(role="user", content=user)]
    out = ""
    try:
        async for ev in llm.stream(msgs, max_tokens=max_tokens):
            if ev.get("type") == "token":
                out += ev.get("text", "")
            elif ev.get("type") == "error":
                break
    except Exception:
        return ""
    return out.strip()


async def _llm_pick_tool(llm, symbol: str, context: str, used: set) -> str:
    menu = "\n".join(f"- {t}: {d}" for t, d in _TOOL_MENU.items() if t not in used)
    if not menu:
        return "DONE"
    system = ("你是量化研究的工具调度器。看已知信息的缺口，从工具清单里挑【最该补的一个】"
              "来补证据。只输出工具名(如 _get_risk_metrics)；证据已够就只输出 DONE。不要解释。")
    user = f"标的: {symbol}\n已知:\n{context}\n\n可用工具:\n{menu}\n\n选一个工具名或 DONE:"
    resp = (await _collect_llm(llm, system, user)).strip()
    for t in _TOOL_MENU:
        if t in resp and t not in used:
            return t
    return "DONE"


def _gap_summary(themes: List[ThemeGroup], quant: Optional[QuantEvidence]) -> str:
    bits = [t.summary for t in themes]
    if quant and quant.available:
        bits.append(f"量化: {quant.verdict()}")
    return "；".join(bits) or "(无)"


async def deepen_agentic(
    symbol: str,
    themes: List[ThemeGroup],
    quant: Optional[QuantEvidence] = None,
    llm=None,
    tool_runner: Optional[Callable[[str, Dict], Optional[Dict]]] = None,
    max_steps: int = 3,
) -> Tuple[List[str], List[Provenance]]:
    """LLM-driven deepening loop. Falls back to the deterministic planner if no LLM."""
    if llm is None:
        return deepen(symbol, themes, quant, tool_runner, max_steps)
    runner = tool_runner or _default_runner
    notes: List[str] = []
    prov: List[Provenance] = []
    used: set = set()
    context = _gap_summary(themes, quant)
    for _ in range(max_steps):
        tool = await _llm_pick_tool(llm, symbol, context, used)
        if not tool or tool == "DONE":
            break
        used.add(tool)
        res = None
        try:
            res = runner(tool, {"symbol": symbol})
        except Exception:
            res = None
        if isinstance(res, dict) and res.get("success"):
            line = _summarize(tool, res) or "已查询"
            label = _TOOL_MENU.get(tool, tool).split(":")[0][:8]
            notes.append(f"[{label}] {line}")
            prov.append(Provenance(label, f"deepen:{tool}"))
            context += f"\n已补({label}): {line}"
    return notes, prov
