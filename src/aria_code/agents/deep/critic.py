"""P1b — a self-check pass over the assembled analysis.

Claude Code verifies its own work; the flat pipeline never did. The critic applies
deterministic rules (no LLM, fully testable) to flag the failure modes that quietly
ruin a research note: thin agent coverage, no risk angle, a strong call on weak
confidence, or the quant signal contradicting the qualitative verdict. An optional
LLM pass can add free-text findings on top.
"""

from __future__ import annotations

from typing import List, Optional

from ..base import AgentResult
from .models import Critique, CritiqueIssue, Provenance, QuantEvidence
from .themes import theme_of

_STRONG = ("STRONG_BUY", "STRONG_SELL")


def critique(
    agent_results: List[AgentResult],
    final_signal: str,
    calibrated_confidence: float,
    quant: Optional[QuantEvidence] = None,
    agreement: str = "neutral",
    provenance: Optional[List[Provenance]] = None,
    key_point_count: int = 0,
) -> Critique:
    """Deterministic self-check. Returns a Critique; ``passed`` is False on any
    high-severity issue so the caller can soften an over-confident conclusion."""
    issues: List[CritiqueIssue] = []

    total = len(agent_results)
    ok = sum(1 for r in agent_results if r.success)

    # 1) coverage
    if ok < 2:
        issues.append(CritiqueIssue("high", "thin_coverage",
            f"只有 {ok} 个 agent 成功，结论证据不足，不应作为决策依据。"))
    elif total and ok / total < 0.5:
        issues.append(CritiqueIssue("medium", "thin_coverage",
            f"{total - ok}/{total} 个 agent 失败，覆盖偏薄。"))

    # 2) risk angle present?
    has_risk = any(r.success and theme_of(r.agent) == "risk" for r in agent_results)
    if not has_risk:
        issues.append(CritiqueIssue("medium", "missing_risk",
            "缺少有效的风险维度分析，下行风险可能被低估。"))

    # 3) quant contradicts qualitative verdict
    if quant and quant.available and agreement == "disagree":
        issues.append(CritiqueIssue("high", "conflict",
            f"量化信号（{quant.verdict()}）与定性结论（{final_signal}）相反，置信度已下调；建议人工复核。"))

    # 4) strong call on weak confidence
    if final_signal in _STRONG and calibrated_confidence < 0.5:
        issues.append(CritiqueIssue("medium", "unsupported",
            f"给出强信号 {final_signal} 但校准后置信度仅 {calibrated_confidence:.0%}，"
            "强度与把握不匹配。"))

    # 5) strong call with little supporting evidence
    if final_signal in _STRONG and key_point_count < 3:
        issues.append(CritiqueIssue("medium", "unsupported",
            "强信号但关键论据少于 3 条，论证偏薄。"))

    # 6) stale data
    for p in (provenance or []):
        if p.freshness.endswith("d old"):
            issues.append(CritiqueIssue("low", "stale_data",
                f"{p.field} 数据为 {p.freshness}（来源 {p.source}），注意时效。"))

    passed = not any(i.severity == "high" for i in issues)
    return Critique(issues=issues, passed=passed)


def soften_signal(final_signal: str) -> str:
    """Step a verdict one notch toward HOLD (used when the critic fails)."""
    ladder = {"STRONG_BUY": "BUY", "BUY": "HOLD", "HOLD": "HOLD",
              "SELL": "HOLD", "STRONG_SELL": "SELL"}
    return ladder.get(final_signal, "HOLD")


_SEV = {"高": "high", "中": "medium", "低": "low",
        "high": "high", "medium": "medium", "low": "low"}


def parse_llm_issues(text: str, max_issues: int = 3) -> List[CritiqueIssue]:
    """Parse the LLM reviewer's reply (``高|问题`` per line) into issues. Pure/testable."""
    import re
    issues: List[CritiqueIssue] = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-•*0123456789. ").strip()
        if not line or line.upper().startswith("OK") or line in ("无", "无问题"):
            continue
        parts = re.split(r"[|｜:：]", line, maxsplit=1)
        if len(parts) == 2 and _SEV.get(parts[0].strip().lower()):
            sev, msg = _SEV[parts[0].strip().lower()], parts[1].strip()
        else:
            sev, msg = "medium", line
        if msg:
            issues.append(CritiqueIssue(sev, "llm_review", msg))
        if len(issues) >= max_issues:
            break
    return issues


async def llm_critique(
    symbol: str,
    synthesis: str,
    theme_summaries: str,
    llm,
    max_issues: int = 3,
) -> List[CritiqueIssue]:
    """Optional LLM reviewer — flags unsupported claims / missing risk / contradiction
    / overconfidence in the synthesis. Returns extra issues (empty if no LLM)."""
    if llm is None or not (synthesis or "").strip():
        return []
    from .deepen import _collect_llm
    system = ("你是严格的研究审稿人。只挑【确凿的】问题：无数据支撑的论断、漏掉的下行风险、"
              "自相矛盾、过度自信。每行一个，格式 `高|问题` / `中|问题` / `低|问题`，"
              f"最多 {max_issues} 条。没有问题就只输出 OK。不要多余的话。")
    user = f"标的: {symbol}\n各维度小结: {theme_summaries}\n\n综合结论:\n{synthesis}\n\n审查:"
    resp = await _collect_llm(llm, system, user, max_tokens=300)
    return parse_llm_issues(resp, max_issues)
