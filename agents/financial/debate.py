"""
agents/financial/debate.py — 信号争议调解 Agent
================================================
当多个 Agent 出现真实分歧（看涨 vs 看跌）时，
DebateAgent 作为"裁判"对冲突进行分析，输出综合判断。
不独立运行，由 AgentTeam 在检测到分歧时自动触发。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from ..base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class DebateAgent(BaseAgent):

    name        = "debate"
    description = "信号争议调解 — 当多 Agent 信号冲突时自动触发，输出裁判视角"

    _SYSTEM = (
        "You are a senior investment committee chair mediating a dispute between "
        "analysts who have conflicting views on a stock. Your role is to:\n"
        "1. Identify the core disagreement\n"
        "2. Evaluate which side has stronger evidence\n"
        "3. Determine the dominant factor (macro vs technical vs fundamental)\n"
        "4. Provide a nuanced resolution that acknowledges both sides\n"
        "5. Conclude with a clear signal: BUY / HOLD / SELL and the primary reason\n"
        "Be direct. Avoid empty hedging. Make a call."
    )

    async def fetch_data(self, symbol: str) -> Dict[str, Any]:
        return await super().fetch_data(symbol)

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        conflicting: List[Dict] = data.get("conflicting", [])

        if not conflicting:
            return AgentResult(
                agent=self.name, symbol=symbol,
                analysis="无冲突结果可调解。",
                confidence=0.3, signal="HOLD",
                key_points=["无需调解"],
            )

        debate_block = _format_conflict(conflicting)

        prompt = (
            f"Stock: {symbol}\n\n"
            f"Conflicting Analyst Views:\n{debate_block}\n\n"
            "Mediate this dispute. Which view is more compelling and why? "
            "What is the dominant factor driving the stock right now? "
            "End with: Signal: BUY / HOLD / SELL — [primary reason in one line]"
        )

        analysis = await self._call_llm(self._SYSTEM, prompt, max_tokens=600)
        if not analysis:
            analysis = _template_resolution(symbol, conflicting)

        signal     = _extract_signal(analysis)
        confidence = _estimate_confidence(conflicting)
        key_points = _build_key_points(conflicting, analysis)

        return AgentResult(
            agent=self.name, symbol=symbol,
            analysis=analysis,
            confidence=confidence,
            signal=signal,
            key_points=key_points,
            data_used={"conflict_count": len(conflicting)},
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_conflict(results: List[Dict]) -> str:
    lines = []
    for r in results:
        agent   = r.get("agent", "unknown")
        signal  = r.get("signal", "HOLD")
        conf    = r.get("confidence", 0)
        pts     = r.get("key_points", [])
        summary = "; ".join(pts[:3]) if pts else r.get("analysis", "")[:150]
        lines.append(
            f"  [{agent.upper()}] Signal: {signal} (conf {conf:.0%})\n"
            f"    Key points: {summary}"
        )
    return "\n".join(lines)


def _extract_signal(analysis: str) -> str:
    text = analysis.upper()
    for marker in ("SIGNAL: ", "SIGNAL:", "CONCLUSION:", "CONCLUSION: "):
        idx = text.find(marker)
        if idx != -1:
            remainder = text[idx + len(marker):].strip()
            if remainder.startswith("STRONG_BUY"):  return "STRONG_BUY"
            if remainder.startswith("STRONG_SELL"): return "STRONG_SELL"
            if remainder.startswith("BUY"):          return "BUY"
            if remainder.startswith("SELL"):         return "SELL"
    if "BUY" in text and "SELL" not in text:   return "BUY"
    if "SELL" in text and "BUY" not in text:   return "SELL"
    return "HOLD"


def _estimate_confidence(results: List[Dict]) -> float:
    if not results:
        return 0.4
    confs = [r.get("confidence", 0.5) for r in results if r.get("confidence")]
    avg   = sum(confs) / len(confs) if confs else 0.5
    return round(min(avg * 0.9, 0.75), 2)


def _build_key_points(results: List[Dict], analysis: str) -> List[str]:
    bullish = [r["agent"] for r in results if r.get("signal") in ("BUY", "STRONG_BUY")]
    bearish = [r["agent"] for r in results if r.get("signal") in ("SELL", "STRONG_SELL")]
    points  = []
    if bullish:
        points.append(f"看涨方: {', '.join(bullish)}")
    if bearish:
        points.append(f"看跌方: {', '.join(bearish)}")
    points.append("DebateAgent 已介入调解")
    sig = _extract_signal(analysis)
    points.append(f"裁判结论: {sig}")
    return points


def _template_resolution(symbol: str, results: List[Dict]) -> str:
    bullish = [r for r in results if r.get("signal") in ("BUY", "STRONG_BUY")]
    bearish = [r for r in results if r.get("signal") in ("SELL", "STRONG_SELL")]
    if len(bullish) > len(bearish):
        resolution = "BUY — 多数看涨信号占优"
    elif len(bearish) > len(bullish):
        resolution = "SELL — 多数看跌信号占优"
    else:
        resolution = "HOLD — 多空力量均衡，建议观望"
    return (
        f"{symbol} 信号冲突调解报告\n"
        f"看涨方: {', '.join(r['agent'] for r in bullish) or '无'}\n"
        f"看跌方: {', '.join(r['agent'] for r in bearish) or '无'}\n"
        f"裁判结论: Signal: {resolution}"
    )
