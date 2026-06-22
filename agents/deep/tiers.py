"""P3 — render the deep result at three depths, with data provenance.

  brief    — one glance: signal, calibrated confidence, headline
  standard — + per-theme roll-up + synthesis
  deep     — + quant evidence + critique + provenance (data lineage) + agent points

Pure text/markdown so it renders in the terminal; the HTML report layer can reuse
the same DeepAnalysisResult.
"""

from __future__ import annotations

from .models import DeepAnalysisResult

_SIGNAL_ICON = {
    "STRONG_BUY": "🟢🟢", "BUY": "🟢", "HOLD": "⚪",
    "SELL": "🔴", "STRONG_SELL": "🔴🔴",
}


def _headline(r: DeepAnalysisResult) -> str:
    icon = _SIGNAL_ICON.get(r.final_signal, "⚪")
    conf = f"{r.calibrated_confidence:.0%}"
    note = ""
    if r.raw_confidence and abs(r.calibrated_confidence - r.raw_confidence) >= 0.05:
        note = f"（原始 {r.raw_confidence:.0%} → 校准 {conf}）"
    else:
        note = f"（置信度 {conf}）"
    return f"{icon} **{r.symbol} · {r.final_signal}** {note}"


def render_brief(r: DeepAnalysisResult) -> str:
    lines = [_headline(r)]
    if r.quant and r.quant.available:
        lines.append(f"  量化: {r.quant.verdict()}"
                     + (f" · IC {r.quant.ic:.2f}" if r.quant.ic is not None else "")
                     + (f" · Sharpe {r.quant.sharpe:.2f}" if r.quant.sharpe is not None else ""))
    if r.critique and r.critique.high:
        lines.append(f"  ⚠️ {r.critique.high[0].message}")
    return "\n".join(lines)


def render_standard(r: DeepAnalysisResult) -> str:
    parts = [render_brief(r), ""]
    if r.themes:
        parts.append("### 分主题")
        for t in r.themes:
            parts.append(f"- {t.summary}")
        parts.append("")
    if r.synthesis:
        parts.append("### 综合")
        parts.append(r.synthesis)
    return "\n".join(parts).rstrip()


def render_deep(r: DeepAnalysisResult) -> str:
    parts = [render_standard(r), ""]

    if r.quant and r.quant.available:
        q = r.quant
        parts.append("### 量化地面真值")
        row = []
        if q.ai_signal:           row.append(f"AI信号 {q.ai_signal}")
        if q.ai_score is not None:row.append(f"分值 {q.ai_score:+.2f}")
        if q.ic is not None:      row.append(f"IC {q.ic:.3f}")
        if q.sharpe is not None:  row.append(f"Sharpe {q.sharpe:.2f}")
        if q.max_drawdown is not None: row.append(f"MaxDD {q.max_drawdown:.1%}")
        if q.backtest_return is not None: row.append(f"回测收益 {q.backtest_return:+.1%}")
        parts.append("- " + " · ".join(row) if row else "- (无)")
        parts.append("")

    if r.critique is not None:
        parts.append("### 自检 (Critic)")
        mark = "✅ 通过" if r.critique.passed else "❌ 存在高危问题"
        parts.append(f"结论: {mark}" + ("（无问题）" if not r.critique.issues else ""))
        for i in r.critique.issues:
            sev = {"high": "🔴", "medium": "🟡", "low": "⚪"}.get(i.severity, "·")
            parts.append(f"- {sev} [{i.kind}] {i.message}")
        parts.append("")

    if r.provenance:
        parts.append("### 数据血缘")
        parts.append("| 字段 | 来源 | 时效 | 备注 |")
        parts.append("|------|------|------|------|")
        for p in r.provenance:
            parts.append(f"| {p.field} | {p.source} | {p.freshness} | {p.note} |")
        parts.append("")

    if r.agent_results:
        parts.append("### 各 Agent 要点")
        for a in r.agent_results:
            if a.get("error"):
                parts.append(f"- **{a['agent']}** ⚠️ {a['error']}")
                continue
            pts = a.get("key_points") or []
            head = f"- **{a['agent']}** ({a.get('signal','?')}, {a.get('confidence',0):.0%})"
            parts.append(head)
            for pt in pts[:3]:
                parts.append(f"    • {pt}")

    return "\n".join(parts).rstrip()


def render_tier(r: DeepAnalysisResult, tier: str = "standard") -> str:
    return {"brief": render_brief, "standard": render_standard,
            "deep": render_deep}.get(tier, render_standard)(r)
