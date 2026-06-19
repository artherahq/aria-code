"""Market command parsing and top-level routing helpers.

Keep this module independent from the terminal UI and market data providers so
CLI, Feishu, and future gateway adapters can share the same command semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from apps.cli.utils.market_detect import _extract_market_symbol, _extract_market_symbols


TOP_LEVEL_ROUTES: Mapping[str, str] = {
    # quant workflow keywords -> slash command name
    "analyze": "/analyze",
    "analysis": "/analyze",
    "分析": "/analyze",
    "backtest": "/backtest",
    "回测": "/backtest",
    "risk": "/risk",
    "风险": "/risk",
    "report": "/report",
    "报告": "/report",
    "market": "/market",
    "行情": "/market",
    "screen": "/screen",
    "筛选": "/screen",
    "strategy": "/strategy",
    "策略": "/strategy",
    "signal": "/signal",
    "信号": "/signal",
    "chart": "/chart",
    "图表": "/chart",
    "news": "/news",
    "新闻": "/news",
    "predict": "/predict",
    "预测": "/predict",
}

_VISUAL_ROUTE_PATTERNS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("晨报", "日报", "周报", "月报", "看板", "dashboard", "heatmap"), "/dashboard"),
    (("报告", "report", "研报"), "/report"),
    (("图表", "走势图", "k线图", "k线", "k-line", "kline", "candlestick", "chart", "plot"), "/chart"),
)

_DASHBOARD_MODE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("持仓", "portfolio", "仓位", "组合", "资产"), "portfolio"),
    (("市场", "行情", "quote", "prices", "watchlist", "热力图", "heatmap"), "market"),
    (("晨报", "日报", "brief"), "brief"),
)

_CHART_PERIOD_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("近一年", "一年", "1y", "1年"), "1y"),
    (("近三个月", "三个月", "3m", "3个月"), "3m"),
    (("近六个月", "六个月", "6m", "6个月"), "6m"),
    (("年初至今", "ytd"), "ytd"),
    (("两年", "2y"), "2y"),
    (("三年", "3y"), "3y"),
    (("五年", "5y"), "5y"),
)

_REPORT_TYPE_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("深度", "详细", "deep"), "deep"),
    (("简评", "简报", "brief"), "brief"),
    (("研究报告", "投研报告", "研究", "report"), "standard"),
)

_ROUTE_SYMBOL_BLOCKLIST = {"K", "LINE", "CHART", "PLOT"}


def _news_topic(text: str, symbols: list[str]) -> str:
    low = text.lower()
    if "spacex" in low:
        return "SpaceX"
    if "lvmh" in low or "路易威登" in text:
        return "LVMH"
    return symbols[0] if symbols else text


def _route_symbols(text: str, *, limit: int = 6) -> list[str]:
    """Resolve ticker/company mentions for natural-language command routing."""
    seen: set[str] = set()
    out: list[str] = []
    for source in (text, text.upper()):
        for symbol in _extract_market_symbols(source, limit=limit):
            normalized = str(symbol or "").upper()
            if not normalized or normalized in _ROUTE_SYMBOL_BLOCKLIST:
                continue
            if len(normalized) == 1 and "." not in normalized:
                continue
            if normalized not in seen:
                seen.add(normalized)
                out.append(normalized)
                if len(out) >= limit:
                    return out
    single = _extract_market_symbol(text) or _extract_market_symbol(text.upper())
    normalized = str(single or "").upper()
    if normalized and normalized not in seen and normalized not in _ROUTE_SYMBOL_BLOCKLIST:
        out.append(normalized)
    return out


@dataclass(frozen=True)
class RoutedCommand:
    command: str
    args: str = ""

    @property
    def text(self) -> str:
        return f"{self.command} {self.args}".strip()


@dataclass(frozen=True)
class TechnicalArgs:
    symbol: str
    days: int = 120


def route_top_level_text(user_input: str, available_commands: set[str]) -> RoutedCommand | None:
    """Translate bare workflow text into a slash command when possible."""

    stripped = user_input.strip()
    if not stripped or stripped.startswith("/"):
        return None
    low = stripped.lower()
    for keywords, command in _VISUAL_ROUTE_PATTERNS:
        if command not in available_commands:
            continue
        if any(k in low for k in keywords):
            symbols = _route_symbols(stripped)
            symbol = symbols[0] if symbols else ""
            if command == "/dashboard":
                mode = next(
                    (
                        dashboard_mode
                        for mode_kw, dashboard_mode in _DASHBOARD_MODE_HINTS
                        if any(k in low for k in mode_kw)
                    ),
                    "brief",
                )
                return RoutedCommand(command=command, args=mode)
            if command == "/chart":
                period = next(
                    (
                        period
                        for period_kw, period in _CHART_PERIOD_HINTS
                        if any(k in low for k in period_kw)
                    ),
                    "1y",
                )
                rest = " ".join(symbols) if symbols else stripped
                return RoutedCommand(command=command, args=f"{rest} {period}".strip())
            if command == "/report":
                report_type = next(
                    (
                        report_type
                        for type_kw, report_type in _REPORT_TYPE_HINTS
                        if any(k in low for k in type_kw)
                    ),
                    "standard",
                )
                fmt = "html"
                if any(k in low for k in ("markdown", "md")):
                    fmt = "md"
                rest = symbol or stripped
                args = " ".join(part for part in [rest, f"--type {report_type}" if report_type else "", f"--format {fmt}" if fmt else ""] if part)
                return RoutedCommand(command=command, args=args)
    if "/news" in available_commands and any(k in low for k in (
        "新闻", "消息", "最新进展", "最近进展", "news", "latest", "recent",
    )):
        symbols = _route_symbols(stripped)
        return RoutedCommand(command="/news", args=_news_topic(stripped, symbols))
    parts = stripped.split(maxsplit=1)
    keyword = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    command = TOP_LEVEL_ROUTES.get(keyword)
    if not command or command not in available_commands:
        return None
    return RoutedCommand(command=command, args=rest)


def parse_symbols(args: str, fallback: list[str] | tuple[str, ...]) -> list[str]:
    symbols = [part.upper() for part in args.split() if part.strip()]
    return symbols or [str(item).upper() for item in fallback]


def parse_technical_args(args: str, *, default_symbol: str = "AAPL", default_days: int = 120) -> TechnicalArgs:
    parts = args.strip().split()
    symbol = default_symbol.upper()
    days = default_days

    if parts and not parts[0].startswith("-") and not parts[0].startswith("days="):
        symbol = parts[0].upper()
        option_parts = parts[1:]
    else:
        option_parts = parts

    skip_next = False
    for idx, part in enumerate(option_parts):
        if skip_next:
            skip_next = False
            continue
        raw = part.strip()
        value = None
        if raw.startswith("days="):
            value = raw.split("=", 1)[1]
        elif raw.startswith("--days="):
            value = raw.split("=", 1)[1]
        elif raw == "--days" and idx + 1 < len(option_parts):
            value = option_parts[idx + 1]
            skip_next = True

        if value is not None:
            try:
                parsed = int(value)
                if parsed > 0:
                    days = parsed
            except ValueError:
                pass

    return TechnicalArgs(symbol=symbol, days=days)


async def try_top_level_route(user_input: str, commands) -> bool:
    """Execute a top-level routed slash command through a SlashCommands object."""

    routed = route_top_level_text(user_input, set(commands.commands))
    if routed is None:
        return False
    await commands.execute(routed.text)
    return True
