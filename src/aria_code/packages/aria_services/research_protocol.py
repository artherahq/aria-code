"""Financial-research grounding policy shared by Aria adapters."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


_MARKET_IDENTIFIER = re.compile(
    r"\b[A-Z]{1,5}(?:\.[A-Z]{1,4})?\b|\b\d{6}(?:\.(?:SH|SZ|SS|HK))?\b"
)
_FINANCIAL_SUBJECTS = (
    "stock", "share", "equity", "market", "portfolio", "holding", "fund",
    "option", "bond", "forex", "crypto", "price", "volume", "valuation",
    "股票", "股价", "行情", "市场", "持仓", "组合", "基金", "期权", "债券",
    "外汇", "加密", "成交量", "估值", "市值", "财报", "基本面",
)
_EVIDENCE_INTENTS = (
    "analyze", "analysis", "forecast", "predict", "recommend", "buy", "sell",
    "current", "latest", "today", "trend", "risk", "target", "outlook",
    "分析", "预测", "推荐", "买入", "卖出", "最新", "当前", "今天", "走势",
    "趋势", "风险", "目标价", "前景", "成交量", "全面分析",
)
_GROUNDING_TOOL_FRAGMENTS = (
    "market", "quote", "price", "ohlcv", "fundamental", "filing", "news",
    "factor", "risk", "backtest", "portfolio", "prediction", "signal",
    "regime", "financial", "earnings", "valuation", "macro", "flow",
)


def requires_financial_evidence(query: str) -> bool:
    """Return whether a request needs current external financial evidence."""
    text = str(query or "").strip()
    if not text:
        return False
    lowered = text.lower()
    has_subject = bool(_MARKET_IDENTIFIER.search(text)) or any(
        term in lowered for term in _FINANCIAL_SUBJECTS
    )
    return has_subject and any(term in lowered for term in _EVIDENCE_INTENTS)


def grounding_tool_names(tool_schemas: Iterable[Mapping]) -> frozenset[str]:
    """Select registered tools whose outputs can ground a financial conclusion."""
    names: set[str] = set()
    for schema in tool_schemas:
        function = schema.get("function") if isinstance(schema, Mapping) else None
        name = str(
            (function or {}).get("name")
            if isinstance(function, Mapping)
            else schema.get("name", "")
        )
        canonical = name.rsplit("__", 1)[-1].lower()
        if name and any(fragment in canonical for fragment in _GROUNDING_TOOL_FRAGMENTS):
            names.add(name)
    return frozenset(names)

