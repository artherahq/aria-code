"""
agents/financial/synthesis.py — 综合汇总 Agent
===============================================
汇总所有 agent 结果，输出可操作的投资建议。
加权规则：置信度平方作为权重，高置信度信号影响力更大。
"""
from __future__ import annotations
from typing import Any, Dict, List
from ..base import BaseAgent, AgentResult

# 每个 agent 的基础重要性权重（反映信息价值，可在 .ariarc 中覆盖）
_AGENT_WEIGHTS: Dict[str, float] = {
    "technical":   1.2,   # 短期动量信息量最高
    "fundamental": 1.1,   # 价值锚
    "risk":        1.0,
    "macro":       0.9,   # 宏观慢变量，不应过度主导短期判断
    "news":        1.0,
    "catalyst":    1.1,   # 催化剂改变时机，提高权重
    "earnings":    1.1,
    "sector":      0.8,
    "debate":      0.5,   # 调解结果已融入其他 agent，避免重复计入
}

_SIGNAL_SCORE: Dict[str, float] = {
    "STRONG_BUY":  2.0,
    "BUY":         1.0,
    "HOLD":        0.0,
    "SELL":        -1.0,
    "STRONG_SELL": -2.0,
}


class SynthesisAgent(BaseAgent):
    name        = "synthesis"
    description = "综合汇总：整合多 Agent 结论，输出操作建议（置信度加权投票）"

    _SYSTEM = (
        "You are the chief investment strategist. Synthesize analyses from "
        "macro, fundamental, technical, and risk agents into a clear, "
        "actionable investment recommendation. Be concise and direct. "
        "End with: FINAL: BUY / HOLD / SELL (with target price and stop loss IF real data is available)."
    )

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        agent_results: List[Dict] = data.get("agent_results", [])

        # Check if any agent had real price data
        _quote = data.get("quote", {})
        _price = _quote.get("price", 0) if _quote else 0
        _data_available = bool(_price and float(_price) > 0)

        summary_parts = []
        for r in agent_results:
            if not r.get("error"):
                agent_name = r["agent"].upper()
                signal     = r.get("signal", "HOLD")
                conf       = r.get("confidence", 0.5)
                points     = r.get("key_points", [])
                pts_str    = "; ".join(points[:2]) if points else r.get("analysis", "")[:80]
                summary_parts.append(
                    f"[{agent_name}] Signal={signal} ({conf:.0%}): {pts_str}"
                )

        summary = "\n".join(summary_parts) if summary_parts else "No agent data"

        if _data_available:
            _price_instruction = (
                "3. Entry strategy (timing and specific price level)\n"
                "4. Exit strategy (specific target price and stop loss)\n"
                "FINAL: BUY/HOLD/SELL | Target: $X | Stop: $Y"
            )
        else:
            _price_instruction = (
                "3. Entry strategy (qualitative timing only — NO specific prices, real data unavailable)\n"
                "4. Key risks (NO stop-loss or target prices — data unavailable)\n"
                "FINAL: BUY/HOLD/SELL | Target: N/A (no real data) | Stop: N/A"
            )

        prompt = (
            f"Symbol: {symbol}\n"
            f"Real price data available: {'YES — price=' + str(_price) if _data_available else 'NO — do not invent prices'}\n\n"
            f"Agent Analyses:\n{summary}\n\n"
            "Provide final recommendation:\n"
            "1. Overall investment thesis (2-3 sentences)\n"
            "2. Key risks to monitor\n"
            + _price_instruction
        )

        analysis  = await self._call_llm(self._SYSTEM, prompt, max_tokens=600, quote=_quote)
        if not analysis:
            analysis = _template_synthesis(symbol, agent_results)

        signal     = _extract_final_signal(analysis)
        confidence = _calc_weighted_confidence(agent_results)
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


def _calc_weighted_confidence(results: List[Dict]) -> float:
    """
    Confidence = weighted average where weight = agent_base_weight × confidence².
    High-confidence agents dominate; low-confidence agents contribute minimally.
    """
    valid = [r for r in results if not r.get("error")]
    if not valid:
        return 0.5
    total_w, weighted_sum = 0.0, 0.0
    for r in valid:
        conf      = max(0.0, min(1.0, float(r.get("confidence", 0.5))))
        base_w    = _AGENT_WEIGHTS.get(r.get("agent", ""), 1.0)
        w         = base_w * (conf ** 2)          # confidence² amplifies strong signals
        weighted_sum += conf * w
        total_w      += w
    return round(weighted_sum / total_w, 2) if total_w else 0.5


def _calc_weighted_signal_score(results: List[Dict]) -> float:
    """Weighted signal score used by the template fallback."""
    valid = [r for r in results if not r.get("error")]
    if not valid:
        return 0.0
    total_w, score_sum = 0.0, 0.0
    for r in valid:
        conf   = max(0.0, min(1.0, float(r.get("confidence", 0.5))))
        base_w = _AGENT_WEIGHTS.get(r.get("agent", ""), 1.0)
        w      = base_w * (conf ** 2)
        score  = _SIGNAL_SCORE.get(r.get("signal", "HOLD"), 0.0)
        score_sum += score * w
        total_w   += w
    return score_sum / total_w if total_w else 0.0


def _extract_key_points(text: str) -> List[str]:
    points = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith(("1.", "2.", "3.", "4.", "•", "-", "·")) and len(line) > 8:
            points.append(line.lstrip("1234567890.-•· "))
    return points[:4]


def _template_synthesis(symbol: str, results: List[Dict]) -> str:
    avg_s  = _calc_weighted_signal_score(results)
    final  = "BUY" if avg_s > 0.4 else ("SELL" if avg_s < -0.4 else "HOLD")
    conf   = _calc_weighted_confidence(results)

    agent_lines = "\n".join(
        f"  • {r['agent'].upper()} ({r.get('confidence', 0.5):.0%}): "
        f"{r.get('signal','?')} — " + "; ".join(r.get("key_points", [])[:1])
        for r in results if not r.get("error")
    )
    return (
        f"{symbol} 综合分析（加权汇总）:\n"
        f"{agent_lines}\n\n"
        f"加权信号得分: {avg_s:+.2f}  综合置信度: {conf:.0%}\n"
        f"FINAL: {final} | 请结合个人风险承受能力制定仓位"
    )
