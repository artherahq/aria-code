"""Context builders for market-analysis commands."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional


TA_SESSION_CACHE: dict[str, dict[str, Any]] = {}
TA_SESSION_CACHE_TTL = 600


def _cached_ta(symbol: str) -> dict[str, Any]:
    cached = TA_SESSION_CACHE.get(symbol)
    if cached and (time.time() - float(cached.get("ts", 0))) < TA_SESSION_CACHE_TTL:
        return {**(cached.get("data") or {}), "_cached": True}
    return {}


def _store_ta(symbol: str, data: dict[str, Any]) -> None:
    if data.get("success"):
        TA_SESSION_CACHE[symbol] = {"data": data, "ts": time.time()}


def build_analyze_prompt(symbol: str, context: str, is_cn: bool) -> str:
    """Build the LLM prompt for /analyze from a prepared market context."""

    symbol = symbol.upper()
    _no_explain = (
        "注意：如某字段数据缺失，请直接省略该项，不要写'数据缺失'或'未提供'的说明文字。"
        "对于无数据的章节整体跳过即可。\n"
        if is_cn else
        "Note: If any data field is missing, skip that item entirely. Do not write 'data unavailable' or explain why it is missing.\n"
    )
    if is_cn:
        return (
            f"{context}\n\n"
            f"请对以上 {symbol} 进行综合分析，包括：\n"
            f"1. 技术面分析（趋势判断、支撑/阻力、指标信号）\n"
            f"2. 基本面评估（估值合理性、盈利能力）\n"
            f"3. 风险提示（简要 2-3 条）\n"
            f"4. 综合建议（操作方向 + 关键价位参考）\n"
            f"\n{_no_explain}"
        )
    return (
        f"{context}\n\n"
        f"Please provide a comprehensive analysis of {symbol}, covering:\n"
        f"1. Technical analysis (trend, support/resistance, indicator signals)\n"
        f"2. Fundamentals (valuation, profitability — only if data is available)\n"
        f"3. Risk assessment (2-3 key points)\n"
        f"4. Summary outlook with key price levels\n"
        f"\n{_no_explain}"
    )


async def build_analyze_context(
    symbol: str,
    is_cn: bool,
    *,
    has_mdc: bool = False,
    get_mdc: Callable[[], Any] | None = None,
    ashare_name_lookup: Callable[[str], str | None] | None = None,
    has_brokers: bool = False,
    get_broker_registry: Callable[[], Any] | None = None,
    logger: logging.Logger | None = None,
) -> str:
    """Fetch real market data and build a structured LLM context."""

    log = logger or logging.getLogger(__name__)
    loop = asyncio.get_event_loop()
    ctx_lines: list[str] = [f"## {symbol} 市场数据" if is_cn else f"## {symbol} Market Data"]

    quote: dict[str, Any] = {}
    technical: dict[str, Any] = {}
    quality: dict[str, Any] = {}

    # ── Primary: cloud DataService bundle ────────────────────────────────────
    try:
        from packages.aria_services import data as service_data
        try:
            from datasources.router import get_router as get_data_router
            router = get_data_router()
        except Exception:
            router = None
        bundle = await loop.run_in_executor(
            None,
            lambda: service_data.DataService(router=router).bundle(
                symbol, history_days=370, technical_days=120,
            ),
        )
        quote = bundle.quote or {}
        technical = bundle.technical or {}
        quality = dict(bundle.quality or {})
        if not quality.get("providers") and getattr(bundle, "provider_chain", None):
            quality["providers"] = list(getattr(bundle, "provider_chain", []) or [])
        if not quality.get("missing_fields") and getattr(bundle, "missing_fields", None):
            quality["missing_fields"] = list(getattr(bundle, "missing_fields", []) or [])
        _store_ta(symbol, technical)
    except Exception as exc:
        log.debug("analyze data service failed for %s: %s", symbol, exc)

    # ── Fallback 1: market data client ────────────────────────────────────────
    if not quote and has_mdc and get_mdc:
        try:
            mdc = get_mdc()
            raw_q = await loop.run_in_executor(None, mdc.quote, symbol)
            if raw_q:
                quote = raw_q if isinstance(raw_q, dict) else (raw_q.to_dict() if hasattr(raw_q, "to_dict") else vars(raw_q))
            if quote.get("price"):
                raw_ta = await loop.run_in_executor(None, mdc.technical_indicators, symbol, 120)
                if isinstance(raw_ta, dict) and raw_ta.get("success"):
                    technical = raw_ta
                    _store_ta(symbol, technical)
                else:
                    technical = _cached_ta(symbol)
        except Exception:
            technical = _cached_ta(symbol)

    # ── Fallback 2: DataRouter (yfinance direct) ─────────────────────────────
    if not quote or not quote.get("price"):
        try:
            from datasources.router import DataRouter
            router_direct = DataRouter()
            raw_q = await loop.run_in_executor(None, router_direct.quote, symbol)
            if raw_q:
                quote = raw_q.to_dict() if hasattr(raw_q, "to_dict") else vars(raw_q)
        except Exception as exc:
            log.debug("DataRouter.quote fallback failed for %s: %s", symbol, exc)

    # If no technical from above, try cached
    if not technical:
        technical = _cached_ta(symbol)

    if quality:
        ctx_lines.append(f"\n### {'数据质量' if is_cn else 'Data Quality'}")
        status = quality.get("status")
        if status:
            ctx_lines.append(f"- {'状态' if is_cn else 'Status'}: {status}")
        providers = quality.get("providers") or []
        if providers:
            ctx_lines.append(f"- {'数据源' if is_cn else 'Providers'}: {', '.join(map(str, providers))}")
        missing = quality.get("missing_fields") or []
        if missing:
            ctx_lines.append(f"- {'缺失字段' if is_cn else 'Missing fields'}: {', '.join(map(str, missing))}")
        warnings = quality.get("warnings") or []
        if warnings:
            ctx_lines.append(f"- {'警告' if is_cn else 'Warnings'}: {'; '.join(map(str, warnings[:3]))}")

    # ── Price / header line ───────────────────────────────────────────────────
    price      = quote.get("price") if quote else None
    change_pct = quote.get("change_pct") if quote else None
    name       = (quote.get("name") or symbol) if quote else symbol

    if price and float(price) == 0.0:
        price = None  # treat 0 as missing

    if is_cn and (not name or name == symbol or str(name).isascii()):
        try:
            cn_name = ashare_name_lookup(symbol) if ashare_name_lookup else None
            if cn_name:
                name = cn_name
        except Exception as exc:
            log.debug("ashare name lookup failed for %s: %s", symbol, exc)

    if price:
        chg_str = f"{float(change_pct):+.2f}%" if change_pct is not None else ""
        header  = f"- 价格: {float(price):.2f}" if is_cn else f"- Price: {float(price):.2f}"
        if chg_str:
            header += f"  ({chg_str})"
        if name and name != symbol:
            header += f"  [{name}]"
        ctx_lines.append(header)
        # 52-week range
        h52 = quote.get("high_52w", 0)
        l52 = quote.get("low_52w", 0)
        if h52 and l52 and float(h52) > 0:
            ctx_lines.append(
                f"- 52周区间: {float(l52):.2f} — {float(h52):.2f}"
                if is_cn else
                f"- 52-week range: {float(l52):.2f} — {float(h52):.2f}"
            )
    else:
        ctx_lines.append(
            "- 价格: 获取失败（稍后重试或配置数据服务 key）"
            if is_cn else
            "- Price: unavailable (configure a data service key via /apikey)"
        )

    # ── Technical indicators ──────────────────────────────────────────────────
    rsi      = technical.get("rsi")
    macd_hist= technical.get("macd_hist")
    ma20     = technical.get("ma20")
    ma60     = technical.get("ma60")
    bb_upper = technical.get("bb_upper")
    bb_lower = technical.get("bb_lower")

    has_tech = any(v is not None for v in (rsi, macd_hist, ma20))
    if has_tech:
        ctx_lines.append(f"\n### {'技术指标' if is_cn else 'Technical Indicators'}")
        if rsi is not None:
            rsi_desc = ("超买" if rsi > 70 else "超卖" if rsi < 30 else "中性") if is_cn else (
                       "Overbought" if rsi > 70 else "Oversold" if rsi < 30 else "Neutral")
            ctx_lines.append(f"- RSI (14): {rsi:.1f}  [{rsi_desc}]")
        if macd_hist is not None:
            trend = ("多头 Bullish" if macd_hist > 0 else "空头 Bearish") if is_cn else (
                    "Bullish" if macd_hist > 0 else "Bearish")
            ctx_lines.append(f"- MACD 柱状图: {macd_hist:.4f}  [{trend}]" if is_cn else
                             f"- MACD histogram: {macd_hist:.4f}  [{trend}]")
        if ma20 and price:
            rel = ("上方 ↑" if float(price) > ma20 else "下方 ↓") if is_cn else (
                  "above ↑" if float(price) > ma20 else "below ↓")
            ctx_lines.append(f"- MA20: {ma20:.2f}  [价格在{rel}]" if is_cn else
                             f"- MA20: {ma20:.2f}  [Price {rel}]")
        if ma60 and price:
            rel = ("上方 ↑" if float(price) > ma60 else "下方 ↓") if is_cn else (
                  "above ↑" if float(price) > ma60 else "below ↓")
            ctx_lines.append(f"- MA60: {ma60:.2f}  [价格在{rel}]" if is_cn else
                             f"- MA60: {ma60:.2f}  [Price {rel}]")
        if bb_upper and bb_lower:
            ctx_lines.append(f"- 布林带: {bb_lower:.2f} — {bb_upper:.2f}" if is_cn else
                             f"- Bollinger Bands: {bb_lower:.2f} — {bb_upper:.2f}")
        if technical.get("_cached"):
            ctx_lines.append("  [以上技术指标来自缓存]" if is_cn else "  [Technical data from session cache]")

    # ── Support / resistance (computed from history) ──────────────────────────
    await _append_support_resistance(ctx_lines, symbol, is_cn, price, ma20, ma60, bb_upper, bb_lower, loop, log)

    # ── Fundamentals ──────────────────────────────────────────────────────────
    await _append_fundamentals(ctx_lines, symbol, is_cn, loop, log, quote)

    # ── Broker position ───────────────────────────────────────────────────────
    _append_broker_position(
        ctx_lines, symbol, is_cn,
        has_brokers=has_brokers, get_broker_registry=get_broker_registry, logger=log,
    )

    return "\n".join(ctx_lines)


async def _append_support_resistance(
    ctx_lines: list[str],
    symbol: str,
    is_cn: bool,
    price: Optional[float],
    ma20: Optional[float],
    ma60: Optional[float],
    bb_upper: Optional[float],
    bb_lower: Optional[float],
    loop,
    log: logging.Logger,
) -> None:
    """Compute support/resistance from price history and key MAs."""
    levels: dict[str, list[float]] = {"support": [], "resistance": []}

    try:
        from datasources.router import DataRouter
        hist_result = await loop.run_in_executor(None, DataRouter().history, symbol, 90)
        if hist_result and hist_result.data is not None and not hist_result.data.empty:
            df = hist_result.data
            close_col = next((c for c in df.columns if "close" in c.lower()), None)
            high_col  = next((c for c in df.columns if "high"  in c.lower()), None)
            low_col   = next((c for c in df.columns if "low"   in c.lower()), None)

            if close_col and len(df) >= 10:
                closes = df[close_col].dropna()
                highs  = df[high_col].dropna() if high_col else closes
                lows   = df[low_col].dropna() if low_col else closes

                # Rolling 10-day swing highs (local maxima)
                for i in range(5, len(highs) - 5):
                    window = highs.iloc[i-5:i+5]
                    if float(highs.iloc[i]) == float(window.max()):
                        levels["resistance"].append(float(highs.iloc[i]))
                # Rolling 10-day swing lows (local minima)
                for i in range(5, len(lows) - 5):
                    window = lows.iloc[i-5:i+5]
                    if float(lows.iloc[i]) == float(window.min()):
                        levels["support"].append(float(lows.iloc[i]))
    except Exception as exc:
        log.debug("support/resistance history failed for %s: %s", symbol, exc)

    # Add MA lines as dynamic support/resistance
    if price:
        p = float(price)
        if ma20:
            (levels["support"] if p > ma20 else levels["resistance"]).append(ma20)
        if ma60:
            (levels["support"] if p > ma60 else levels["resistance"]).append(ma60)
        # Bollinger bands
        if bb_lower:
            levels["support"].append(bb_lower)
        if bb_upper:
            levels["resistance"].append(bb_upper)

        if levels["support"] or levels["resistance"]:
            ctx_lines.append(f"\n### {'关键价位' if is_cn else 'Key Price Levels'}")

            # Pick the 3 nearest support levels below price
            sup = sorted(set(round(v, 2) for v in levels["support"] if v < p), reverse=True)[:3]
            # Pick the 3 nearest resistance levels above price
            res = sorted(set(round(v, 2) for v in levels["resistance"] if v > p))[:3]

            if sup:
                sup_str = "  /  ".join(f"{v:.2f}" for v in sup)
                ctx_lines.append(f"- 支撑位: {sup_str}" if is_cn else f"- Support: {sup_str}")
            if res:
                res_str = "  /  ".join(f"{v:.2f}" for v in res)
                ctx_lines.append(f"- 阻力位: {res_str}" if is_cn else f"- Resistance: {res_str}")


async def _append_fundamentals(
    ctx_lines: list[str],
    symbol: str,
    is_cn: bool,
    loop,
    log: logging.Logger,
    quote: dict[str, Any],
) -> None:
    fund_lines: list[str] = []

    # Try DataRouter (yfinance / alpha_vantage / edgar chain)
    try:
        from datasources.router import DataRouter
        fund = await loop.run_in_executor(None, DataRouter().fundamentals, symbol)
        if fund:
            def _row(label_cn: str, label_en: str, val: Optional[float], fmt: str = ".2f") -> None:
                if val is not None and val != 0.0:
                    formatted = f"{val:{fmt}}"
                    fund_lines.append(f"- {label_cn}: {formatted}" if is_cn else f"- {label_en}: {formatted}")

            _row("市盈率 (TTM)", "P/E ratio (TTM)",           fund.pe_ttm)
            _row("市净率",       "Price-to-Book",              fund.pb)
            _row("ROE",          "Return on Equity",           fund.roe,             ".2f")
            _row("营收增速",     "Revenue Growth (YoY)",       fund.revenue_growth,  ".2f")
            _row("净利增速",     "Earnings Growth (YoY)",      fund.net_profit_growth, ".2f")
            _row("股息率",       "Dividend Yield",             fund.dividend_yield,  ".2f")

            # Market cap: format nicely (USD or CNY)
            if fund.total_mv and fund.total_mv > 0:
                mv = fund.total_mv
                if mv >= 1e12:
                    mv_str = f"{mv/1e12:.2f}T"
                elif mv >= 1e9:
                    mv_str = f"{mv/1e9:.2f}B"
                elif mv >= 1e8:
                    mv_str = f"{mv/1e8:.2f}亿" if is_cn else f"{mv/1e9:.2f}B"
                else:
                    mv_str = f"{mv:,.0f}"
                fund_lines.append(f"- 总市值: {mv_str}" if is_cn else f"- Market Cap: {mv_str}")

            if fund.source:
                fund_lines.append(f"  [数据源: {fund.source}]" if is_cn else f"  [source: {fund.source}]")
    except Exception as exc:
        log.debug("fundamentals fetch failed for %s: %s", symbol, exc)

    # Also try quote-level PE/PB if fundamentals didn't yield anything
    if not fund_lines and quote:
        pe = quote.get("pe_ttm", 0)
        pb = quote.get("pb", 0)
        if pe and float(pe) > 0:
            fund_lines.append(f"- 市盈率 (TTM): {float(pe):.2f}" if is_cn else f"- P/E ratio (TTM): {float(pe):.2f}")
        if pb and float(pb) > 0:
            fund_lines.append(f"- 市净率: {float(pb):.2f}" if is_cn else f"- Price-to-Book: {float(pb):.2f}")

    if fund_lines:
        ctx_lines.append(f"\n### {'基本面' if is_cn else 'Fundamentals'}")
        ctx_lines.extend(fund_lines)


def _append_broker_position(
    ctx_lines: list[str],
    symbol: str,
    is_cn: bool,
    *,
    has_brokers: bool,
    get_broker_registry: Callable[[], Any] | None,
    logger: logging.Logger,
) -> None:
    if not has_brokers or not get_broker_registry:
        return
    try:
        registry = get_broker_registry()
        broker = registry.active()
        if not broker or not broker.is_connected:
            return
        positions = broker.positions()
        symbol_norm = symbol.lstrip("0").upper()
        match = None
        for position in positions:
            pos_symbol = str(position.symbol or "").lstrip("0").upper()
            if pos_symbol == symbol_norm or pos_symbol.startswith(symbol_norm) or symbol_norm.startswith(pos_symbol):
                match = position
                break
        ctx_lines.append(f"\n### {'我的持仓' if is_cn else 'Your Position'}")
        if not match:
            ctx_lines.append(f"- 当前未持有此股 [{broker.label}]" if is_cn else f"- Not currently held [{broker.label}]")
            return
        qty  = getattr(match, "quantity", None) or getattr(match, "qty", None)
        cost = getattr(match, "cost_price", None) or getattr(match, "avg_cost", None)
        pnl  = getattr(match, "pnl", None)
        pnl_pct = getattr(match, "pnl_pct", None)
        market_value = getattr(match, "market_value", None)
        ctx_lines.append(f"- 持有: 是 [{broker.label}]" if is_cn else f"- Held: Yes [{broker.label}]")
        if qty is not None:
            ctx_lines.append(f"- 持仓量: {qty:,}" if is_cn else f"- Quantity: {qty:,}")
        if cost is not None:
            ctx_lines.append(f"- 成本价: {cost:.3f}" if is_cn else f"- Avg Cost: {cost:.3f}")
        if market_value is not None:
            ctx_lines.append(f"- 市值: {market_value:,.2f}" if is_cn else f"- Market Value: {market_value:,.2f}")
        if pnl is not None and pnl_pct is not None:
            sign = "+" if pnl >= 0 else ""
            ctx_lines.append(
                f"- 浮动盈亏: {sign}{pnl:,.2f}  ({sign}{pnl_pct:.2f}%)"
                if is_cn else
                f"- Unrealized P&L: {sign}{pnl:,.2f}  ({sign}{pnl_pct:.2f}%)"
            )
    except Exception as exc:
        logger.debug("broker position lookup failed for %s: %s", symbol, exc)
