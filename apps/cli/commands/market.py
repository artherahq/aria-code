"""Market command parsing and top-level routing helpers.

Keep this module independent from the terminal UI and market data providers so
CLI, Feishu, and future gateway adapters can share the same command semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


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

