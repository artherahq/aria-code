"""Rendering helpers for /team output — table, verdict banner, adaptive widths."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console

# ── Signal → Rich style ──────────────────────────────────────────────────────

SIGNAL_COLORS: dict[str, str] = {
    "BUY":         "green",
    "STRONG_BUY":  "bold green",
    "SELL":        "red",
    "STRONG_SELL": "bold red",
    "HOLD":        "yellow",
}

# ── Verdict banner ────────────────────────────────────────────────────────────

VERDICT_STYLE: dict[str, tuple[str, str]] = {
    "HEALTHY":         ("green",      "✅"),
    "NEEDS_ATTENTION": ("yellow",     "⚠️ "),
    "HIGH_RISK":       ("red",        "🔴"),
    "STRONG_BUY":      ("bold green", "▲▲"),
    "BUY":             ("green",      "▲ "),
    "HOLD":            ("dim",        "─ "),
    "SELL":            ("red",        "▼ "),
    "STRONG_SELL":     ("bold red",   "▼▼"),
}


# ── Streaming agent tree (Claude Code-style nested ⏺ rendering) ────────────────

AGENT_LABELS: dict[str, str] = {
    "fundamental": "基本面", "technical": "技术面", "macro": "宏观",
    "risk": "风险", "news": "新闻", "catalyst": "催化剂",
    "sector": "行业", "sentiment": "情绪", "debate": "分歧调解",
    "valuation": "估值", "quant": "量化", "synthesis": "综合",
}


def agent_label(name: str) -> str:
    return AGENT_LABELS.get((name or "").lower(), name or "?")


def render_agent_tree_root(console, sym: str, n_agents: int, lang: str = "zh") -> None:
    """Print the root of the agent tree: ⏺ 多代理分析 SYM   N 个分析师并行"""
    head = "多代理分析" if lang == "zh" else "Multi-agent analysis"
    sub  = (f"{n_agents} 个分析师并行" if lang == "zh"
            else f"{n_agents} analysts in parallel")
    console.print(f"\n  [#C08050]⏺[/#C08050]  [bold]{head} {sym}[/bold]  [dim]{sub}[/dim]")


def render_agent_node(console, name: str, signal: str | None,
                      key_point: str | None, success: bool = True,
                      error: str | None = None) -> None:
    """Print one completed-agent leaf: ⎿ ⏺ 基本面  BUY  ROE 24%·PE 32 偏高"""
    label = agent_label(name)
    if not success or error:
        _err_label = {
            "timeout": "超时", "rate_limited": "数据源限流",
            "no_data": "无数据", "": "失败",
        }.get((error or "").lower(), error or "失败")
        console.print(f"  [dim]⎿ ⏺ {label}  {_err_label}[/dim]")
        return
    sig   = (signal or "").upper()
    color = SIGNAL_COLORS.get(sig, "dim")
    sig_disp = f"[{color}]{sig}[/{color}]  " if sig else ""
    kp = (key_point or "").strip().replace("\n", " ")
    if len(kp) > 52:
        kp = kp[:52] + "…"
    console.print(
        f"  [dim]⎿[/dim] [#C08050]⏺[/#C08050] [bold]{label}[/bold]  "
        f"{sig_disp}[dim]{kp}[/dim]"
    )


def render_agent_synthesis_leaf(console, signal: str | None,
                                confidence: float | None, elapsed: float | None,
                                lang: str = "zh") -> None:
    """Print the synthesis leaf: ⎿ 综合: ▲ BUY (置信 68%)  耗时 4.2s"""
    sig = (signal or "").upper()
    color, icon = VERDICT_STYLE.get(sig, ("dim", "●"))
    conf = (f"  [dim]置信 {confidence:.0%}[/dim]" if confidence else "")
    el   = (f"  [dim]耗时 {elapsed:.1f}s[/dim]" if elapsed else "")
    lab  = "综合" if lang == "zh" else "Synthesis"
    console.print(
        f"  [dim]⎿[/dim] [bold]{lab}[/bold]  [{color}]{icon} {sig}[/{color}]{conf}{el}"
    )


def build_verdict_body(
    verdict: str,
    subtitle: str = "",
    confidence: float | None = None,
) -> str:
    """Return a Rich markup string suitable for a Panel body."""
    verdict_upper = verdict.upper()
    style, icon = VERDICT_STYLE.get(verdict_upper, ("dim", "●"))
    conf_str = f"  [dim]置信度 {confidence:.0%}[/dim]" if confidence else ""
    body = f"[{style}]{icon}  {verdict_upper}[/{style}]{conf_str}"
    if subtitle:
        body += f"\n[dim]{subtitle}[/dim]"
    return body


def render_verdict_banner(
    verdict: str,
    subtitle: str = "",
    confidence: float | None = None,
    *,
    console: "Console | None" = None,
    has_rich: bool = True,
) -> None:
    """Print a visually prominent verdict/signal result.

    Falls back to a plain print when Rich is unavailable or *console* is None.
    """
    if not verdict:
        return
    verdict_upper = verdict.upper()

    if not has_rich or console is None:
        conf_str = f"  ({confidence:.0%})" if confidence else ""
        sub_str  = f"  {subtitle}" if subtitle else ""
        print(f"\n  {verdict_upper}{conf_str}{sub_str}\n")
        return

    try:
        from rich import box as _rbox
        from rich.panel import Panel
        body = build_verdict_body(verdict_upper, subtitle, confidence)
        console.print(Panel(body, box=_rbox.SIMPLE, padding=(0, 2)))
    except Exception:
        conf_str = f"  ({confidence:.0%})" if confidence else ""
        console.print(f"\n  {verdict_upper}{conf_str}\n")


# ── Team table ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TeamTableRow:
    agent: str
    signal: str
    confidence: str
    key_point: str
    success: bool
    signal_color: str = "dim"
    is_debate: bool = False


# Minimum usable terminal width before we drop the key_point column entirely.
_KEY_POINT_MIN_TERMINAL = 72


def calc_column_widths(terminal_width: int) -> tuple[int, int, int, int]:
    """Return ``(agent_w, signal_w, conf_w, key_w)`` adaptive to *terminal_width*.

    Column budget (chars used by content only, excluding Rich padding/borders):

    +-----------+---------+-----------+--------------------+
    | Agent     | Signal  | Conf      | Key point          |
    +-----------+---------+-----------+--------------------+
    Fixed overhead per row: ~7 chars (borders + padding × 4 cols).

    Width tiers
    -----------
    ≥ 120 cols  full layout   14 | 10 | 7 | 38
    ≥ 100 cols  medium        12 |  9 | 6 | 30
    ≥  80 cols  compact       10 |  8 | 6 | 20
    ≥  72 cols  tight          9 |  8 | 6 | 12
    <  72 cols  no key col     9 |  8 | 6 |  0  (column omitted)
    """
    if terminal_width >= 120:
        return (14, 10, 7, 38)
    if terminal_width >= 100:
        return (12,  9, 6, 30)
    if terminal_width >= 80:
        return (10,  8, 6, 20)
    if terminal_width >= _KEY_POINT_MIN_TERMINAL:
        return ( 9,  8, 6, 12)
    return (9, 8, 6, 0)


def truncate_cell(value: Any, width: int = 36) -> str:
    text = str(value or "").strip().replace("\n", " ")
    if width <= 0:
        return ""
    if width <= 1:
        return text[:width]
    if len(text) <= width:
        return text
    return text[: width - 1].rstrip() + "…"


def team_mode_label(results: list[Any], use_full: bool = False) -> str:
    non_debate = [r for r in results if getattr(r, "agent", None) != "debate"]
    return f"{len(non_debate)}-agent {'完整分析' if use_full else '分析'}"


_AGENT_DISPLAY = {
    "fundamental": "fundmntl",
    "synthesis":   "synthsis",
    "catalyst":    "catalyst",
}


def build_team_table_rows(results: list[Any], key_width: int = 36) -> list[TeamTableRow]:
    rows: list[TeamTableRow] = []
    debate_rows: list[TeamTableRow] = []

    for result in results:
        agent      = str(getattr(result, "agent", "") or "")
        agent      = _AGENT_DISPLAY.get(agent, agent)
        success    = bool(getattr(result, "success", False))
        raw_signal = str(getattr(result, "signal",  "") or "")
        signal     = raw_signal or "N/A"
        conf_val   = float(getattr(result, "confidence", 0.0) or 0.0)
        confidence = f"{conf_val:.0%}" if success else "-"
        color      = SIGNAL_COLORS.get(raw_signal.upper(), "dim")

        if agent == "debate":
            debate_rows.append(TeamTableRow(
                agent="debate",
                signal=raw_signal or "ADJ",
                confidence=confidence,
                key_point="信号分歧调解",
                success=success,
                signal_color="orange1",
                is_debate=True,
            ))
            continue

        if success:
            kpts      = getattr(result, "key_points", []) or []
            key_point = truncate_cell(kpts[0] if kpts else "", key_width)
        else:
            _raw_err = getattr(result, "error", None) or "failed"
            _err_display = {
                "stale_or_conflicting_price": "数据冲突 — 价格与行情不符",
                "timeout":                    "分析超时",
                "no_data":                    "数据不可用",
                "agent_failed":               "Agent 执行失败",
            }.get(_raw_err, _raw_err)
            key_point = truncate_cell(_err_display, key_width)

        rows.append(TeamTableRow(
            agent=agent,
            signal=signal,
            confidence=confidence,
            key_point=key_point,
            success=success,
            signal_color=color,
        ))

    return rows + debate_rows


def render_team_rows_plain(rows: list[TeamTableRow]) -> list[str]:
    """Plain-text fallback (no Rich)."""
    lines: list[str] = []
    for row in rows:
        icon = "OK" if row.success else "WARN"
        lines.append(
            f"  {icon} [{row.agent}] {row.signal} ({row.confidence}) {row.key_point}".rstrip()
        )
    return lines


def render_team_table(
    sym: str,
    rows: list[TeamTableRow],
    use_full: bool = False,
    *,
    console: "Console | None" = None,
    terminal_width: int | None = None,
    has_rich: bool = True,
) -> None:
    """Print the /team results table, adapting column widths to *terminal_width*.

    Falls back to :func:`render_team_rows_plain` when Rich is unavailable.
    """
    if not has_rich or console is None:
        for line in render_team_rows_plain(rows):
            print(line)
        return

    try:
        from rich import box as _rbox
        from rich.table import Table as _Table
    except ImportError:
        for line in render_team_rows_plain(rows):
            console.print(line)
        return

    # Determine terminal width from console if not supplied
    if terminal_width is None:
        terminal_width = getattr(console, "width", None) or shutil.get_terminal_size().columns

    agent_w, signal_w, conf_w, key_w = calc_column_widths(terminal_width)
    mode_label = team_mode_label(rows, use_full)  # rows already built; agent count approximate

    tbl = _Table(
        title=f"[bold]/team {sym}[/bold] · [dim]{mode_label}[/dim]",
        box=_rbox.ROUNDED,
        border_style="dim",
        show_header=True,
        header_style="bold dim",
        padding=(0, 1),
    )
    tbl.add_column("Agent",  width=agent_w)
    tbl.add_column("信号",   width=signal_w)
    tbl.add_column("置信度", justify="right", width=conf_w)
    if key_w > 0:
        tbl.add_column("关键点", width=key_w)

    for row in rows:
        # Re-truncate key_point to the current adaptive width
        kp = truncate_cell(row.key_point, key_w) if key_w > 0 else None

        agent_cell = (
            f"[orange1]{row.agent}[/orange1]"
            if row.is_debate
            else f"[dim]{row.agent}[/dim]"
        )
        key_cell = (
            "[dim][orange1]信号分歧调解[/orange1][/dim]"
            if row.is_debate
            else f"[dim]{kp}[/dim]"
        ) if key_w > 0 else None

        cells = [
            agent_cell,
            f"[{row.signal_color}]{row.signal}[/{row.signal_color}]",
            f"[dim]{row.confidence}[/dim]" if row.success else "[dim]—[/dim]",
        ]
        if key_cell is not None:
            cells.append(key_cell)
        tbl.add_row(*cells)

    console.print()
    console.print(tbl)
