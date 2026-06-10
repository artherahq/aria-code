"""
agents/financial/synthesis.py — 综合汇总 Agent
===============================================
汇总所有 agent 结果，输出可操作的投资建议。
"""
from __future__ import annotations
from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult


class SynthesisAgent(BaseAgent):
    name        = "synthesis"
    description = "综合汇总：整合多 Agent 结论，输出操作建议"

    _SYSTEM = (
        "You are the chief investment strategist. Synthesize analyses from "
        "macro, fundamental, technical, and risk agents into a clear, "
        "actionable investment recommendation. Be concise and direct. "
        "End with: FINAL: BUY / HOLD / SELL (with target price and stop loss)."
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        agent_results: List[Dict] = data.get("agent_results", [])

        # 汇总各 agent 分析
        summary_parts = []
        for r in agent_results:
            if not r.get("error"):
                agent_name = r["agent"].upper()
                signal     = r.get("signal", "HOLD")
                conf       = r.get("confidence", 0.5)
                points     = r.get("key_points", [])
                pts_str    = "; ".join(points[:2]) if points else r.get("analysis","")[:80]
                summary_parts.append(
                    f"[{agent_name}] Signal={signal} ({conf:.0%}): {pts_str}"
                )

        summary = "\n".join(summary_parts) if summary_parts else "No agent data"

        prompt = (
            f"Symbol: {symbol}\n\n"
            f"Agent Analyses:\n{summary}\n\n"
            "Provide final recommendation:\n"
            "1. Overall investment thesis (2-3 sentences)\n"
            "2. Key risks to monitor\n"
            "3. Entry strategy (timing and price level)\n"
            "4. Exit strategy (target and stop loss)\n"
            "FINAL: BUY/HOLD/SELL | Target: X | Stop: Y"
        )

        analysis  = await self._call_llm(self._SYSTEM, prompt, max_tokens=600)
        if not analysis:
            analysis = _template_synthesis(symbol, agent_results)

        signal     = _extract_final_signal(analysis)
        confidence = _calc_avg_confidence(agent_results)
        key_points = _extract_key_points(analysis)

        return AgentResult(
            agent=self.name, symbol=symbol,
            analysis=analysis, confidence=confidence,
            signal=signal, key_points=key_points,
        )


def _extract_final_signal(text: str) -> str:
    import re
    m = re.search(r"FINAL[:\s]+([A-Z_]+)", text.upper())
    if m:
        raw = m.group(1)
        if "STRONG_BUY" in raw:  return "STRONG_BUY"
        if "BUY" in raw:         return "BUY"
        if "STRONG_SELL" in raw: return "STRONG_SELL"
        if "SELL" in raw:        return "SELL"
    return "HOLD"


def _calc_avg_confidence(results: List[Dict]) -> float:
    valid = [r for r in results if not r.get("error")]
    if not valid: return 0.5
    return round(sum(r.get("confidence", 0.5) for r in valid) / len(valid), 2)


def _extract_key_points(text: str) -> List[str]:
    points = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith(("1.", "2.", "3.", "4.", "•", "-", "·")) and len(line) > 8:
            points.append(line.lstrip("1234567890.-•· "))
    return points[:4]


def _template_synthesis(symbol: str, results: List[Dict]) -> str:
    _SCORE = {"STRONG_BUY": 2, "BUY": 1, "HOLD": 0, "SELL": -1, "STRONG_SELL": -2}
    signals = [r.get("signal", "HOLD") for r in results if not r.get("error")]
    avg_s   = sum(_SCORE.get(s, 0) for s in signals) / max(len(signals), 1)
    final   = "BUY" if avg_s > 0.5 else ("SELL" if avg_s < -0.5 else "HOLD")

    agent_lines = "\n".join(
        f"  • {r['agent'].upper()}: {r.get('signal','?')} — " +
        "; ".join(r.get("key_points", [])[:1])
        for r in results if not r.get("error")
    )
    return (
        f"{symbol} 综合分析（模板汇总）:\n"
        f"{agent_lines}\n\n"
        f"多 Agent 加权信号评分: {avg_s:.2f}\n"
        f"FINAL: {final} | 请结合个人风险承受能力制定仓位"
    )
