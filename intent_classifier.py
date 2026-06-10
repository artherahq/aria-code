"""
intent_classifier.py — Aria intent classification using Prelude model or keyword fallback.

Two-tier design:
  Tier 1 (fast, accurate): aria-prelude via Ollama — the model trained specifically
                           for intent routing (adapters: intent-route-control,
                           intent-rag_gate, intent-upgrade_gate, intent-clarify).
  Tier 2 (instant, offline): keyword regex — exact same logic as before, used when
                              Ollama / aria-prelude is not available.

Intent labels (matching CODING_SYSTEM_PROMPT routing in aria_cli.py):
  "coding"      → code generation, backtest scripts, chart scripts
  "analysis"    → stock/macro analysis, technical/fundamental research
  "realtime"    → live price / quote / market data queries (needs tool)
  "general"     → conceptual / educational finance questions (no tools needed)
  "finance"     → default finance chat with tool access

Usage::

    from intent_classifier import classify_intent, INTENT_CODING, INTENT_ANALYSIS

    intent = await classify_intent_async("写一个 AAPL 动量策略", ollama_url)
    # → "coding"

    intent = classify_intent_sync("什么是夏普比率")
    # → "general"
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from typing import Optional

# ── Intent constants ──────────────────────────────────────────────────────────
INTENT_CODING   = "coding"
INTENT_ANALYSIS = "analysis"
INTENT_REALTIME = "realtime"
INTENT_GENERAL  = "general"
INTENT_FINANCE  = "finance"   # default / catch-all

# ── Prelude model name ────────────────────────────────────────────────────────
_PRELUDE_MODEL = "aria-prelude"
_PRELUDE_TIMEOUT = 3.0   # seconds — must be fast; fallback on timeout

# ── Prelude system prompt (mirrors the adapter training format) ───────────────
_PRELUDE_SYSTEM = (
    "You are an intent classifier for a quantitative finance AI assistant.\n"
    "Classify the user message into EXACTLY ONE of these labels:\n"
    "  coding    — code generation, script writing, backtest, chart plotting\n"
    "  analysis  — stock analysis, market research, technical/fundamental analysis\n"
    "  realtime  — live price, current quote, today's market data\n"
    "  general   — conceptual/educational finance question, no live data needed\n"
    "  finance   — other finance chat, portfolio, risk, strategy discussion\n"
    "Reply with ONLY the label word, nothing else."
)


# ── Keyword fallback (tier-2, instant) ───────────────────────────────────────

_CODING_KW = (
    "write", "generate", "create", "script", "code", "plot", "backtest",
    "策略", "代码", "回测", "编写", "生成", "k线", "k-line", "kline",
    "python", "dashboard", "写一个", "生成代码", "写代码", "编写代码",
    "analyze and save", "analysis script",
)
_ANALYSIS_KW = (
    "analyze", "analysis", "分析", "研究", "评估", "研判",
    "技术面", "基本面", "走势", "趋势", "行情",
    "stock analysis", "technical analysis", "fundamental",
    "valuation", "estimate", "outlook", "投资建议", "买入", "卖出",
)

# Topics that must NOT be classified as stock technical "analysis" even if they
# contain analysis keywords.  They get routed to "finance" (general chat) instead,
# because the stock-analysis prompt requires injected market data that doesn't
# exist for real estate or pure macroeconomic questions.
_NON_STOCK_ANALYSIS_TOPICS = (
    # Real-estate
    "房价", "楼市", "房产", "房地产", "租金", "二手房", "折旧价", "商铺",
    # Pure macro — "宏观角度分析" should be finance chat, not stock template
    "宏观", "宏观经济", "宏观政策", "宏观角度",
    "货币政策", "财政政策", "gdp", "通胀", "通货膨胀", "cpi", "ppi",
    # Non-chartable commodities / currencies
    "黄金走势", "原油走势", "汇率走势", "美元指数",
)

# Pure macro/conceptual topics — no live market data needed, route to GENERAL
# (no tools invoked).  More specific than _NON_STOCK_ANALYSIS_TOPICS: real-estate
# still goes to FINANCE because the user might want live data, but "宏观角度" is
# clearly a discussion question, not a quote lookup.
_MACRO_GENERAL_TOPICS = (
    "宏观", "宏观经济", "宏观政策", "宏观角度", "宏观分析",
    "货币政策", "财政政策", "gdp", "通胀", "通货膨胀", "cpi", "ppi",
    "值得投资吗", "应该投资吗", "是否值得", "投资逻辑",
    "长期展望", "未来前景", "宏观前景",
)
_REALTIME_KW = (
    "今天", "today", "现在", "now", "current", "latest", "最新",
    "市值", "price", "股价", "quote", "行情", "涨跌", "涨幅",
    "market cap", "how much", "what is the price",
)
_GENERAL_KW = (
    "什么是", "what is", "what are", "how does", "explain", "define",
    "解释", "定义", "概念", "原理", "介绍", "怎么", "如何理解",
    "是什么", "为什么", "区别", "difference between",
    "tell me about", "describe", "how to", "举例", "example",
)
_FINANCE_CONCEPT_KW = (
    "dcf", "pe", "pb", "ps", "ev", "ebitda", "wacc", "capm",
    "beta", "alpha", "sharpe", "sortino", "var", "cvar", "drawdown",
    "black-scholes", "期权", "期货", "衍生品", "套利",
    "量化", "quant", "回测", "因子", "ic值", "ir值",
    "market cap", "市盈率", "市净率", "净利润", "估值", "valuation",
)


def classify_intent_sync(message: str) -> str:
    """
    Tier-2 keyword-based classification (synchronous, always available).
    Returns one of the INTENT_* constants.
    """
    low = message.lower().strip()

    has_coding = any(k in low for k in _CODING_KW)
    has_realtime = any(k in low for k in _REALTIME_KW)

    if has_coding:
        return INTENT_CODING
    # Live-data terms must win over broad analysis words.  "分析苹果今天的市场"
    # contains both "分析" and "今天"; routing it as generic analysis lets the
    # model answer from memory instead of using market data.
    if has_realtime:
        return INTENT_REALTIME
    # Stock/market analysis — but NOT if the topic is non-chartable (real-estate,
    # macro-economy, etc.) where the stock-analysis template produces garbage output.
    if any(k in low for k in _ANALYSIS_KW):
        if any(t in low for t in _MACRO_GENERAL_TOPICS):
            return INTENT_GENERAL   # pure macro discussion — no tools needed
        if any(t in low for t in _NON_STOCK_ANALYSIS_TOPICS):
            return INTENT_FINANCE   # real-estate etc. — finance chat, no stock template
        return INTENT_ANALYSIS
    # Finance concept terms → keep full finance context, not general
    if any(k in low for k in _FINANCE_CONCEPT_KW):
        return INTENT_FINANCE
    if any(k in low for k in _GENERAL_KW):
        return INTENT_GENERAL
    return INTENT_FINANCE


def _prelude_available(ollama_url: str) -> bool:
    """Quick sync check: is aria-prelude loaded in Ollama?"""
    try:
        with urllib.request.urlopen(
            ollama_url.rstrip("/") + "/api/tags", timeout=1
        ) as r:
            data = json.loads(r.read())
        models = [m["name"] for m in data.get("models", [])]
        return any(m.startswith(_PRELUDE_MODEL) for m in models)
    except Exception:
        return False


async def classify_intent_async(
    message: str,
    ollama_url: str = "http://localhost:11434",
    *,
    timeout: float = _PRELUDE_TIMEOUT,
) -> str:
    """
    Tier-1 classification using aria-prelude via Ollama.
    Falls back to tier-2 keyword classification on any error or timeout.
    """
    try:
        import aiohttp
    except ImportError:
        return classify_intent_sync(message)

    if not _prelude_available(ollama_url):
        return classify_intent_sync(message)

    payload = {
        "model": _PRELUDE_MODEL,
        "messages": [
            {"role": "system", "content": _PRELUDE_SYSTEM},
            {"role": "user",   "content": message},
        ],
        "stream": False,
        "options": {"num_predict": 8, "temperature": 0.0},
    }
    url = ollama_url.rstrip("/") + "/api/chat"
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                url, json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    return classify_intent_sync(message)
                data = await resp.json()
        raw = data.get("message", {}).get("content", "").strip().lower()
        # Accept only known labels
        for label in (INTENT_CODING, INTENT_ANALYSIS, INTENT_REALTIME,
                      INTENT_GENERAL, INTENT_FINANCE):
            if label in raw:
                # Post-override: even if the prelude model says "analysis" or "finance",
                # non-stock topics must not get the stock-analysis template.
                if label == INTENT_ANALYSIS:
                    low_msg = message.lower()
                    if any(t in low_msg for t in _MACRO_GENERAL_TOPICS):
                        return INTENT_GENERAL   # macro → educational, no tools
                    if any(t in low_msg for t in _NON_STOCK_ANALYSIS_TOPICS):
                        return INTENT_FINANCE   # real-estate etc. → finance chat
                if label == INTENT_FINANCE:
                    # Even a "finance" label from the model shouldn't invoke live tools
                    # for pure macro conceptual questions.
                    low_msg = message.lower()
                    if any(t in low_msg for t in _MACRO_GENERAL_TOPICS):
                        return INTENT_GENERAL
                return label
    except Exception:
        pass
    return classify_intent_sync(message)
