"""Turn-prompt assembly DECISIONS for send_message (pure, testable).

Extracted from ``aria_cli.send_message``. These functions decide what text is
actually sent to the model for a turn — the precedence between analysis-
commentary mode, a decomposition plan, and the raw message, plus whether a
file-tool hint or a fetched ML-signal block gets prepended. All of it is pure
string assembly: the upstream decisions (whether analysis was requested,
what the decomposition plan says, what the ML signal text is) are computed
elsewhere and passed in here as plain values, so none of this needs I/O,
``self`` state, or mocking to test.
"""

from __future__ import annotations

ANALYSIS_COMMENTARY_PROMPT_ZH = (
    "请基于上方已获取的实时行情数据提供深度分析（约300字）："
    " 当前价格位置与短期趋势判断、多空力量对比"
    "（若涉及多标的则对比两者强弱）、关键支撑/阻力位、操作建议（附风险提示）。"
    " 直接开始分析，不要重复表格数据。"
)


def build_base_message(
    message: str,
    *,
    wants_analysis_commentary: bool,
    decomposition_plan: str = "",
) -> str:
    """Pick the effective prompt body for this turn.

    Precedence: analysis-commentary mode wins outright (the snapshot data was
    already shown, so the model just needs the fixed commentary instruction —
    a decomposition plan would be redundant). Otherwise a non-empty
    decomposition plan is prefixed onto the raw message. Otherwise the raw
    message is sent unchanged.
    """
    if wants_analysis_commentary:
        return ANALYSIS_COMMENTARY_PROMPT_ZH
    if decomposition_plan:
        return f"[执行计划]\n{decomposition_plan}\n\n[用户请求]\n{message}"
    return message


def should_prepend_file_tool_hint(
    wants_analysis_commentary: bool,
    reference_context: str,
) -> bool:
    """A file-tool hint is only useful when the model doesn't already have
    situational grounding — skip it in analysis-commentary mode (grounded by
    the snapshot) and whenever ``@``-reference context is present (grounded
    by the referenced files)."""
    return not wants_analysis_commentary and not reference_context


def with_ml_signal_prefix(current_message: str, ml_signal_text: str) -> str:
    """Prepend a fetched ML-signal reference block, if one was fetched."""
    if not ml_signal_text:
        return current_message
    return f"[ML信号参考 — 仅供分析参考，非投资建议]\n{ml_signal_text}\n\n{current_message}"
