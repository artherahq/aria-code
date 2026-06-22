"""Market command parsing and top-level routing helpers.

Keep this module independent from the terminal UI and market data providers so
CLI, Feishu, and future gateway adapters can share the same command semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from apps.cli.utils.market_detect import (
    _extract_market_symbol,
    _extract_market_symbols,
    _is_blocked_market_symbol_candidate,
)


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

_TRADINGVIEW_HINTS = ("tradingview", "trading view", "pine")
_TRADINGVIEW_ZH_HINTS = ("用tradingview", "打开tradingview", "tradingview打开", "用 tv", "tv打开", "pine脚本")
_TRADINGVIEW_BULLISH_HINTS = ("看涨", "偏多", "多头", "上涨", "bullish", "bull", "upside")
_TRADINGVIEW_BEARISH_HINTS = ("看跌", "偏空", "空头", "下跌", "bearish", "bear", "downside")
_TRADINGVIEW_ANALYSIS_HINTS = (
    "分析", "怎么看", "怎么判断", "哪些数据", "根据其的数据", "根据数据",
    "指标", "信号", "analyze", "analysis", "data", "indicator", "signal",
)

_ROUTE_SYMBOL_BLOCKLIST = {"K", "LINE", "CHART", "PLOT"}
_CHART_CONTEXT_TOKEN_BLOCKLIST = {
    "ABOVE", "BELOW", "INC", "TTM", "RATIO", "SIGNAL", "SUPPORT", "RESIST",
    "RESISTANCE", "LEVEL", "LEVELS", "HIGH", "LOW", "OPEN", "CLOSE", "AVG",
    "AVERAGE", "RETURN", "RETURNS", "TREND", "MOMENTUM",
}


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
            if (
                not normalized
                or normalized in _ROUTE_SYMBOL_BLOCKLIST
                or _is_blocked_market_symbol_candidate(normalized)
            ):
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
    if (
        normalized
        and normalized not in seen
        and normalized not in _ROUTE_SYMBOL_BLOCKLIST
        and not _is_blocked_market_symbol_candidate(normalized)
    ):
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


@dataclass(frozen=True)
class AnalysisArgs:
    symbol: str
    focus: str = ""
    lang: str = ""


_ANALYSIS_FOCUS_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("成交量", "交易量", "量价", "放量", "缩量", "volume", "volumes"), "volume"),
    (("市值", "market cap", "marketcap", "capitalization"), "market_cap"),
    (("基本面", "fundamental", "fundamentals", "valuation", "估值"), "fundamentals"),
    (("技术面", "technical", "rsi", "macd", "支撑", "阻力"), "technical"),
)


def _analysis_focus_from_text(text: str) -> str:
    low = text.lower()
    for keywords, focus in _ANALYSIS_FOCUS_HINTS:
        if any(k in low for k in keywords):
            return focus
    return ""


def parse_analysis_args(args: str, *, default_symbol: str = "AAPL") -> AnalysisArgs:
    """Resolve natural-language /analyze args to one clean symbol plus focus."""
    raw = (args or "").strip()
    focus = ""
    lang = ""
    parts = raw.split()
    cleaned_parts: list[str] = []
    skip_next = False
    for idx, part in enumerate(parts):
        if skip_next:
            skip_next = False
            continue
        token = part.strip()
        low = token.lower()
        if low.startswith("--focus="):
            focus = low.split("=", 1)[1].strip()
            continue
        if low == "--focus" and idx + 1 < len(parts):
            focus = parts[idx + 1].strip().lower()
            skip_next = True
            continue
        if low.startswith("--lang="):
            lang = low.split("=", 1)[1].strip().lower()
            continue
        if low == "--lang" and idx + 1 < len(parts):
            lang = parts[idx + 1].strip().lower()
            skip_next = True
            continue
        cleaned_parts.append(token)

    cleaned = " ".join(cleaned_parts).strip()
    if not focus:
        focus = _analysis_focus_from_text(raw)

    symbols = _route_symbols(cleaned or raw, limit=1)
    symbol = symbols[0] if symbols else ""
    if not symbol:
        symbol = _extract_market_symbol(cleaned) or _extract_market_symbol((cleaned or raw).upper())
    if not symbol:
        for token in cleaned_parts:
            candidate = token.strip(",，.。:：;；()（）[]【】").upper()
            if candidate and not candidate.startswith("-") and not _is_blocked_market_symbol_candidate(candidate):
                symbol = candidate
                break
    if lang not in ("zh", "en"):
        zh_chars = sum(1 for c in raw if "\u4e00" <= c <= "\u9fff")
        lang = "zh" if zh_chars else ""
    return AnalysisArgs(symbol=(symbol or default_symbol).upper(), focus=focus, lang=lang)


def route_top_level_text(user_input: str, available_commands: set[str]) -> RoutedCommand | None:
    """Translate bare workflow text into a slash command when possible."""

    stripped = user_input.strip()
    if not stripped or stripped.startswith("/"):
        return None
    low = stripped.lower()
    compact_low = low.replace(" ", "")
    low_words = {part.strip(".,，。:：;；") for part in low.split()}
    if "/tv" in available_commands and (
        any(k in low for k in _TRADINGVIEW_HINTS)
        or "tv" in low_words
        or any(k in compact_low for k in _TRADINGVIEW_ZH_HINTS)
        or ("tradingview" in compact_low)
    ):
        symbols = _route_symbols(stripped)
        symbol = symbols[0] if symbols else ""
        if symbol:
            opts: list[str] = []
            if any(k in low for k in ("pine", "strategy", "策略")):
                opts.append("--pine")
            if any(k in low for k in ("copy", "clipboard", "复制", "剪贴板")):
                opts.append("--copy")
            if any(k in low for k in ("reveal", "finder", "所在目录", "访达", "目录")):
                opts.append("--reveal")
            if any(k in low for k in ("txt", "text file", "文本副本", "文本")):
                opts.append("--txt")
            if any(k in low for k in ("打开", "open")) and "--pine" not in opts:
                opts.append("--open")
            if "--pine" not in opts:
                if any(k in low for k in _TRADINGVIEW_BULLISH_HINTS):
                    opts.append("--bullish")
                elif any(k in low for k in _TRADINGVIEW_BEARISH_HINTS):
                    opts.append("--bearish")
                elif any(k in low for k in _TRADINGVIEW_ANALYSIS_HINTS):
                    opts.append("--analyze")
            return RoutedCommand(command="/tv", args=" ".join([symbol, *opts]).strip())
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
    if command == "/analyze":
        parsed = parse_analysis_args(rest)
        focus_arg = f" --focus {parsed.focus}" if parsed.focus else ""
        zh_chars = sum(1 for c in stripped if "\u4e00" <= c <= "\u9fff")
        lang_arg = f" --lang {'zh' if zh_chars else 'en'}"
        return RoutedCommand(command=command, args=f"{parsed.symbol}{focus_arg}{lang_arg}".strip())
    return RoutedCommand(command=command, args=rest)


def parse_symbols(args: str, fallback: list[str] | tuple[str, ...]) -> list[str]:
    symbols = [part.upper() for part in args.split() if part.strip()]
    return symbols or [str(item).upper() for item in fallback]


def sanitize_chart_symbol_args(raw_symbols: list[str] | tuple[str, ...]) -> list[str]:
    """Drop analysis words that sometimes leak into chart symbol arguments."""
    cleaned = [str(item or "").strip().strip(",，") for item in raw_symbols]
    cleaned = [item for item in cleaned if item]
    if not cleaned:
        return []
    if len(cleaned) == 1:
        upper = cleaned[0].upper()
        return [] if _is_blocked_market_symbol_candidate(upper) else cleaned

    upper_tokens = [item.upper() for item in cleaned]
    has_context_noise = any(
        token in _CHART_CONTEXT_TOKEN_BLOCKLIST or _is_blocked_market_symbol_candidate(token)
        for token in upper_tokens
    )

    out: list[str] = []
    for raw, upper in zip(cleaned, upper_tokens):
        if upper in _CHART_CONTEXT_TOKEN_BLOCKLIST:
            continue
        if _is_blocked_market_symbol_candidate(upper):
            continue
        # MA is a valid ticker (Mastercard), but in noisy generated chart args it
        # usually comes from moving-average text next to TTM/above/below terms.
        if upper == "MA" and has_context_noise and len(cleaned) > 2:
            continue
        out.append(raw)
    return out


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
