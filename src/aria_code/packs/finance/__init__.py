"""Finance as a domain pack — the contract's first consumer.

This does not reimplement anything.  It wraps the existing symbol resolution
and deterministic handlers so finance stops being the substrate and becomes one
pack among peers.  Behaviour is intended to be unchanged; the regression suites
in tests/test_market_history_inheritance.py and tests/test_intent_signals.py
are the safety net for that claim.

What changes is *reach*.  Previously the strategy, realty, and stock-chart
handlers ran against every message in the deterministic chain, before the model
saw it.  Behind the contract they run only when this pack has resolved a real
financial entity from the user's message, so a logistics or clinical question
never touches them.
"""

from __future__ import annotations

from typing import Sequence

from aria_code.packs.base import BaseDomainPack, EntityMatch, PackActivation

PACK_NAME = "finance"

# Tools this pack owns.  Exposed to the model only while the pack is active, so
# a code session is not offered a quote tool it can misfire (which is how a
# repository question became a MongoDB stock lookup).
FINANCE_TOOLS = (
    "get_market_data",
    "get_market_history",
    "analyze_news",
    "broker_query",
    "run_backtest",
)

# A bare uppercase run merely *looks* like a ticker — "EMS", "ESB", and "MDB"
# all appeared in an architecture diagram in the incident that motivated this
# contract.  Resolution against a real symbol table scores high; a shape-only
# guess scores below the activation threshold and therefore does not switch the
# pack on by itself.
_RESOLVED_CONFIDENCE = 0.95
_SHAPE_ONLY_CONFIDENCE = 0.3


def _resolve_symbols(message: str) -> list[tuple[str, str, int]]:
    """Return (canonical, surface, position) for symbols named in *message*."""
    try:
        from aria_code.apps.cli.market_universe import resolve_market_mentions
    except Exception:
        return []
    try:
        hits = resolve_market_mentions(message, limit=6)
    except Exception:
        return []
    out: list[tuple[str, str, int]] = []
    for position, item in hits:
        symbol = str(getattr(item, "symbol", "") or "").strip()
        if symbol:
            out.append((symbol.upper(), str(getattr(item, "name", "") or ""), position))
    return out


# Bare uppercase runs are resolved against an ALLOWLIST, not a blocklist.
# market_detect takes the opposite approach — it rejects known non-tickers like
# BUY and SELL — which is why "MongoDB" in an architecture sentence resolved to
# the ticker MDB: nothing had thought to forbid it.  An allowlist fails closed:
# an unknown uppercase word scores below the activation threshold, so the worst
# case is a missed activation the user can force with "$MDB".
_KNOWN_TICKERS = frozenset({
    # US mega/large cap
    "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "AVGO",
    "BRK.A", "BRK.B", "JPM", "V", "MA", "UNH", "XOM", "WMT", "JNJ", "PG",
    "HD", "COST", "ORCL", "CRM", "AMD", "INTC", "CSCO", "ADBE", "NFLX",
    "PEP", "KO", "MCD", "NKE", "DIS", "BA", "CAT", "GE", "GS", "MS",
    "BAC", "WFC", "C", "T", "VZ", "PFE", "MRK", "ABBV", "LLY", "TMO",
    "PLTR", "SNOW", "UBER", "ABNB", "COIN", "SHOP", "SQ", "PYPL", "SMCI",
    "MU", "QCOM", "TXN", "AMAT", "LRCX", "KLAC", "ARM", "TSM", "ASML",
    # Common ETFs
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "GLD", "SLV", "USO", "TLT",
    "ARKK", "XLF", "XLE", "XLK", "SOXL", "TQQQ", "SQQQ", "HYG", "EEM",
    # China ADRs
    "BABA", "JD", "PDD", "NIO", "LI", "XPEV", "BIDU", "TME", "BILI", "NTES",
})


def _bare_uppercase_runs(message: str) -> list[tuple[str, str, int, float]]:
    """Uppercase runs, scored by whether they are recognised or merely shaped.

    A recognised ticker activates the pack.  An unrecognised run is still
    reported, at a confidence below the activation threshold, so the CLI can
    offer "did you mean $MDB?" without answering a code question with a stock
    quote on its own initiative.
    """
    import re

    out: list[tuple[str, str, int, float]] = []
    for match in re.finditer(r"(?<![A-Za-z0-9$])([A-Z]{1,5})(?![A-Za-z0-9])", message or ""):
        candidate = match.group(1)
        confidence = (
            _RESOLVED_CONFIDENCE if candidate in _KNOWN_TICKERS else _SHAPE_ONLY_CONFIDENCE
        )
        out.append((candidate, candidate, match.start(), confidence))
    return out


def _explicit_ticker(message: str) -> list[tuple[str, str, int]]:
    """Tickers written in a form that states the intent explicitly.

    ``$AAPL`` and a bare A-share code are unambiguous.  A bare uppercase word
    is not, and is deliberately excluded here.
    """
    import re

    out: list[tuple[str, str, int]] = []
    for match in re.finditer(r"\$([A-Za-z]{1,5})(?![A-Za-z0-9])", message or ""):
        out.append((match.group(1).upper(), match.group(0), match.start()))
    for match in re.finditer(r"(?<!\d)((?:60|68|00|30)\d{4})(?!\d)", message or ""):
        out.append((match.group(1), match.group(0), match.start()))
    return out


class FinancePack(BaseDomainPack):
    """Recognises tradable instruments and owns the market workflows."""

    name = PACK_NAME

    def resolve_entities(self, message: str) -> Sequence[EntityMatch]:
        if not (message or "").strip():
            return ()

        seen: set[str] = set()
        entities: list[EntityMatch] = []

        for value, surface, position in _explicit_ticker(message):
            if value in seen:
                continue
            seen.add(value)
            entities.append(EntityMatch(
                pack=PACK_NAME, kind="instrument", value=value,
                surface=surface, position=position,
                confidence=_RESOLVED_CONFIDENCE,
            ))

        for value, surface, position, confidence in _bare_uppercase_runs(message):
            if value in seen:
                continue
            seen.add(value)
            entities.append(EntityMatch(
                pack=PACK_NAME, kind="instrument", value=value,
                surface=surface, position=position,
                confidence=confidence,
            ))

        for value, surface, position in _resolve_symbols(message):
            if value in seen:
                continue
            seen.add(value)
            entities.append(EntityMatch(
                pack=PACK_NAME, kind="instrument", value=value,
                surface=surface or value, position=position,
                confidence=_RESOLVED_CONFIDENCE,
            ))

        return tuple(entities)

    def handlers(self) -> Sequence[object]:
        """The deterministic handlers, now reachable only when active."""
        try:
            from aria_code.apps.cli.handlers.chart_handlers import (
                handle_stock_chart_analysis,
            )
            from aria_code.apps.cli.handlers.strategy_advice import (
                handle_strategy_advice,
            )
        except Exception:
            return ()
        return (handle_strategy_advice, handle_stock_chart_analysis)

    def tool_names(self) -> Sequence[str]:
        return FINANCE_TOOLS

    def prompt_fragment(self, activation: PackActivation) -> str:
        primary = activation.primary
        if primary is None:
            return ""
        symbols = ", ".join(sorted({e.value for e in activation.entities}))
        return (
            f"金融标的已识别：{symbols}。\n"
            "使用工具取回的数据回答，不要凭记忆给出价格或指标；"
            "标注数据来源与时间戳；输出不构成投资建议。"
        )


FINANCE_PACK = FinancePack()


def register() -> FinancePack:
    """Register the finance pack.  Idempotent."""
    from aria_code.packs.registry import register_pack

    register_pack(FINANCE_PACK)
    return FINANCE_PACK


__all__ = ["FINANCE_PACK", "FINANCE_TOOLS", "PACK_NAME", "FinancePack", "register"]
