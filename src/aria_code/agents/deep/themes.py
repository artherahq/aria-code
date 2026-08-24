"""P1a — group agent results by theme and build a per-theme sub-synthesis.

Flat synthesis ("here are 8 opinions, here's the average") loses structure. Real
research clusters evidence: what does *valuation* say, what does *momentum* say,
what does *risk* say — then reconciles across clusters. This module does the
clustering and a deterministic per-cluster roll-up (no LLM needed).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from ..base import AgentResult
from .models import ThemeGroup

# Which theme each agent belongs to. Unknown agents fall into "other".
_AGENT_THEME = {
    "fundamental": "valuation",
    "earnings":    "valuation",
    "technical":   "momentum",
    "risk":        "risk",
    "news":        "catalysts",
    "catalyst":    "catalysts",
    "macro":       "macro",
    "sector":      "macro",
    "northbound":  "macro",
    "debate":      "reconciliation",
}

# Display order + human labels.
_THEME_ORDER = ["valuation", "momentum", "macro", "catalysts", "risk",
                "reconciliation", "other"]
_THEME_LABEL = {
    "valuation":      "估值/基本面",
    "momentum":       "动量/技术",
    "macro":          "宏观/板块",
    "catalysts":      "催化/消息",
    "risk":           "风险",
    "reconciliation": "分歧调解",
    "other":          "其他",
}

_SCORE = {"STRONG_BUY": 2, "BUY": 1, "HOLD": 0, "SELL": -1, "STRONG_SELL": -2}


def theme_of(agent_name: str) -> str:
    return _AGENT_THEME.get(agent_name, "other")


def _vote(results: List[AgentResult]) -> Tuple[str, float]:
    """Confidence-weighted majority within a single theme."""
    valid = [r for r in results if r.success and r.signal in _SCORE]
    if not valid:
        return "HOLD", 0.0
    avg_score = sum(_SCORE[r.signal] * r.confidence for r in valid) / len(valid)
    avg_conf = sum(r.confidence for r in valid) / len(valid)
    if avg_score >= 1.5:
        return "STRONG_BUY", avg_conf
    if avg_score >= 0.5:
        return "BUY", avg_conf
    if avg_score <= -1.5:
        return "STRONG_SELL", avg_conf
    if avg_score <= -0.5:
        return "SELL", avg_conf
    return "HOLD", avg_conf


def group_by_theme(results: List[AgentResult]) -> List[ThemeGroup]:
    """Cluster agent results into themes with a per-theme signal + summary."""
    buckets: Dict[str, List[AgentResult]] = {}
    for r in results:
        buckets.setdefault(theme_of(r.agent), []).append(r)

    groups: List[ThemeGroup] = []
    for theme in _THEME_ORDER:
        members = buckets.get(theme)
        if not members:
            continue
        signal, conf = _vote(members)
        points: List[str] = []
        for r in members:
            if r.success:
                points.extend((r.key_points or [])[:2])
        ok = sum(1 for r in members if r.success)
        summary = (f"{_THEME_LABEL[theme]}: {signal}（{ok}/{len(members)} agent 有效，"
                   f"置信度 {conf:.0%}）")
        groups.append(ThemeGroup(
            theme=_THEME_LABEL[theme],
            agents=[r.agent for r in members],
            signal=signal,
            confidence=conf,
            summary=summary,
            key_points=points[:5],
        ))
    return groups
