"""Market data handlers extracted from aria_cli.py.

Handles market data prefetching, snapshot rows, and full snapshot analysis.
Imports market detection helpers from apps.cli.utils.market_detect.
_HAS_MDC and _get_mdc are resolved via lazy import to avoid circular deps.
"""
from __future__ import annotations
import json
import os
from datetime import datetime
from pathlib import Path

from apps.cli.utils.market_detect import (
    _re_sym, _STOCK_PATTERN,
    _CRYPTO_WORDS, _COMPANY_TO_TICKER,
    _FINANCIAL_TERMS_BLOCKLIST,
    _extract_market_symbol, _extract_market_symbols, _extract_symbol_from_history,
    _is_realty_query, _is_market_snapshot_request,
    _format_compact_market_cap, _market_snapshot_trend,
    _has_unresolved_company_mention,
    _PRIVATE_COMPANY_PROFILES,
)

_PROVIDERS_FILE = Path.home() / ".arthera" / "providers.json"


def _detect_lang(text: str) -> str:
    """Return 'zh' for predominantly Chinese input, 'en' otherwise."""
    if not text:
        return "zh"
    zh_chars = sum(1 for c in text if '一' <= c <= '鿿')
    return "zh" if zh_chars / max(len(text), 1) > 0.15 else "en"


# ── TA session cache (populated during prefetch, read during snapshot) ────────
_TA_SESSION_CACHE: dict = {}
_TA_SESSION_CACHE_TTL = 600  # 10 minutes


def _fmt_int(value) -> str:
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return "N/A"


_DATA_KEY_MAP = {
    "finnhub":   "FINNHUB_API_KEY",
    "alphavantage": "ALPHAVANTAGE_API_KEY",
    "polygon":   "POLYGON_API_KEY",
}


def _get_provider_key(provider: str) -> str:
    """Return configured API key for a provider (env var takes priority over providers.json)."""
    env_var = _DATA_KEY_MAP.get(provider.lower(), "")
    if env_var:
        val = os.getenv(env_var, "")
        if val:
            return val
    try:
        if _PROVIDERS_FILE.exists():
            raw = json.loads(_PROVIDERS_FILE.read_text(encoding="utf-8"))
            for section in ("llm", "data"):
                entry = raw.get(section, {}).get(provider.lower(), {})
                if entry.get("api_key"):
                    return entry["api_key"]
    except Exception:
        pass
    return ""


# Lazy MDC accessor (mirrors the pattern in market_tools.py)
def _get_mdc_lazy():
    try:
        from market_data_client import get_mdc as _gm
        return _gm()
    except Exception:
        return None

def _has_mdc_lazy() -> bool:
    try:
        import market_data_client  # noqa
        return True
    except ImportError:
        return False


def _try_prefetch_market_data(message: str, history: list = None) -> str:
    """
    Pre-fetch real market data and inject it into the system prompt so local
    models always answer with real numbers instead of hallucinating.

    For technical-analysis queries (support/resistance/RSI/MACD) also fetches
    technical indicators and computes key price levels from the data.

    跟进问题支持：当前消息无标的但含市场关键词时，从会话历史继承最近标的
    （如上一轮问"寒武纪趋势"，这一轮问"现在的股票和趋势呢"）。

    Returns "" if no market query detected or fetch fails.
    """
    # Real-estate queries must not prefetch stock market data
    if _is_realty_query(message):
        return ""

    # Trigger for any market / analysis query
    _market_kw = (
        "股票","股价","价格","涨跌","市值","行情","市场","现在多少","现价","今天价格",
        "分析","走势","技术面","基本面","估值","涨跌幅",
        "支撑","阻力","支撑位","阻力位","技术指标","技术分析",
        "stock","price","quote","analyze","analysis","crypto",
        "btc","eth","比特币","以太坊","rsi","macd","bollinger",
    )
    msg_low = message.lower()
    if not any(k in msg_low for k in _market_kw):
        return ""

    # Detect if this is a technical analysis request
    _tech_kw = ("技术面","技术分析","技术指标","支撑","阻力","支撑位","阻力位",
                 "rsi","macd","bollinger","均线","走势","趋势","technical")
    _is_tech_query = any(k in msg_low for k in _tech_kw)

    symbol = None
    msg_for_lookup = message.lower()  # case-insensitive company name matching
    # 1. Known Chinese company / index name → ticker (longest match first)
    for cn, tick in sorted(_COMPANY_TO_TICKER.items(), key=lambda x: -len(x[0])):
        if cn.lower() in msg_for_lookup:
            symbol = tick
            break
    # 2. Crypto name → symbol
    if not symbol:
        for cn, tick in _CRYPTO_WORDS.items():
            if cn.lower() in msg_for_lookup:
                symbol = tick
                break
    # 3. Uppercase ticker pattern (blocklist prevents DCF/EPS/etc being matched)
    if not symbol:
        m = _re_sym.search(r'\b([A-Z]{2,5}(?:\.(?:HK|SH|SZ))?)\b', message)
        if m and m.group(1) not in _FINANCIAL_TERMS_BLOCKLIST:
            symbol = m.group(1)

    # 4. 跟进问题：从会话历史继承最近提到的标的
    if not symbol and history:
        symbol = _extract_symbol_from_history(history) or None

    if not symbol:
        return ""

    if not _has_mdc_lazy():
        return (
            f"\n## 实时行情状态\n"
            f"- 标的：{symbol}\n"
            f"- 状态：本地 market_data_client 未加载，无法获取实时行情。\n"
            f"- 输出要求：明确说明数据不可用，并建议用户执行 `/quote {symbol}`；"
            "不要输出示例价格、占位符或技术指标。\n"
        )

    try:
        mdc = _get_mdc_lazy()
        r = mdc.quote(symbol)
        if not r.get("success"):
            return (
                f"\n## 实时行情状态\n"
                f"- 标的：{symbol}\n"
                f"- 状态：当前数据服务无法获取该标的的实时行情。\n"
                f"- 可用操作：运行 `/quote {symbol}` 重试。\n"
                f"- 输出要求：不要输出示例价格、占位符、RSI、MACD 或支撑阻力位。\n"
            )
        price    = r.get("price", "N/A")
        chg      = r.get("change_pct", 0)
        name     = r.get("name", symbol)
        currency = r.get("currency", "USD")
        high     = r.get("high", "N/A")
        low      = r.get("low", "N/A")
        vol      = r.get("volume", "N/A")
        mktcap   = r.get("market_cap")
        cap_str  = ""
        if mktcap and mktcap == mktcap:  # excludes NaN
            if mktcap >= 1e12:
                cap_str = f"${mktcap/1e12:.2f}T"
            elif mktcap >= 1e9:
                cap_str = f"${mktcap/1e9:.1f}B"
        sign = "+" if chg >= 0 else ""
        provider = r.get("provider", "API")

        block = (
            f"\n## 📊 {symbol} 实时行情（来源：{provider}）\n"
            f"- **名称**：{name}\n"
            f"- **最新价**：{currency} {price}\n"
            f"- **涨跌幅**：{sign}{chg:.2f}%\n"
            f"- **今日高/低**：{high} / {low}\n"
            f"- **成交量**：{vol}\n"
            + (f"- **市值**：{cap_str}\n" if cap_str else "")
        )

        # For technical analysis queries: fetch indicators and compute support/resistance
        if _is_tech_query:
            try:
                import time as _time_ta
                _raw_ti = mdc.technical_indicators(symbol, days=120)
                if isinstance(_raw_ti, dict) and _raw_ti.get("success"):
                    _TA_SESSION_CACHE[symbol] = {"data": _raw_ti, "ts": _time_ta.time()}
                    ti = _raw_ti
                else:
                    # Fall back to session cache
                    _cached_ta = _TA_SESSION_CACHE.get(symbol)
                    ti = (_cached_ta["data"] if _cached_ta and
                          (_time_ta.time() - _cached_ta["ts"]) < _TA_SESSION_CACHE_TTL
                          else {})
                if ti.get("success"):
                    rsi   = ti.get("rsi")
                    macd  = ti.get("macd")
                    msig  = ti.get("macd_signal")
                    mhist = ti.get("macd_hist")
                    bbu   = ti.get("bb_upper")
                    bbm   = ti.get("bb_mid")
                    bbl   = ti.get("bb_lower")
                    ma20  = ti.get("ma20")
                    ma60  = ti.get("ma60")
                    ma5   = ti.get("ma5")

                    # Derive support / resistance from MAs and Bollinger Bands
                    supports    = sorted([v for v in [ma20, ma60, bbl] if v], reverse=False)
                    resistances = sorted([v for v in [bbu, bbm] if v], reverse=False)
                    if isinstance(price, (int, float)):
                        # Primary support = nearest MA below current price
                        supports    = [f"{currency} {v:.2f}" for v in supports if v < price]
                        resistances = [f"{currency} {v:.2f}" for v in resistances if v > price]
                    else:
                        supports    = [f"{currency} {v:.2f}" for v in supports]
                        resistances = [f"{currency} {v:.2f}" for v in resistances]

                    # Pre-compute signal labels so the model doesn't need to interpret
                    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                    if rsi is not None:
                        if rsi >= 70:
                            rsi_signal = f"⚠️ 超买 (RSI={rsi:.1f} ≥ 70，回调风险)"
                        elif rsi <= 30:
                            rsi_signal = f"⚠️ 超卖 (RSI={rsi:.1f} ≤ 30，反弹机会)"
                        else:
                            rsi_signal = f"中性 (RSI={rsi:.1f}，30-70区间，无超买超卖)"
                    else:
                        rsi_signal = "N/A"

                    # Show MACD histogram prominently (not the MACD line)
                    if mhist is not None:
                        macd_hist_str = f"{mhist:.4f}"
                        macd_signal = "金叉/多头" if mhist > 0 else "死叉/空头"
                        macd_label = f"MACD hist={macd_hist_str}，信号：{macd_signal}"
                    else:
                        macd_hist_str = "N/A"
                        macd_signal = "N/A"
                        macd_label = "N/A"

                    block += (
                        f"\n## 📈 技术分析数据（基于120日历史，已预计算信号）\n\n"
                        f"### 技术指标与信号\n"
                        f"| 指标 | 数值 | 信号判断 |\n"
                        f"| --- | --- | --- |\n"
                        f"| RSI(14) | {rsi_str} | {rsi_signal} |\n"
                        f"| MACD hist(12,26,9) | {macd_hist_str} | {macd_signal}（hist{'>'if mhist and mhist>0 else '<'}0） |\n"
                        + (f"| MA5 | {currency} {ma5:.2f} | 短期均线 |\n" if ma5 else "")
                        + (f"| MA20 | {currency} {ma20:.2f} | 中期支撑/压力 |\n" if ma20 else "")
                        + (f"| MA60 | {currency} {ma60:.2f} | 长期支撑/压力 |\n" if ma60 else "")
                        + (f"| BB Upper | {currency} {bbu:.2f} | 上轨阻力 |\n" if bbu else "")
                        + (f"| BB Lower | {currency} {bbl:.2f} | 下轨支撑 |\n" if bbl else "")
                        + f"\n### 关键价位（直接引用这些数字）\n"
                        + f"- **支撑位**：{', '.join(supports) if supports else '无（当前价已在主要支撑下方）'}\n"
                        + f"- **阻力位**：{', '.join(resistances) if resistances else '无（当前价已突破布林上轨）'}\n"
                        + f"\n### 技术信号汇总\n"
                        + f"- RSI：{rsi_signal}\n"
                        + f"- MACD：{macd_label}\n"
                    )
            except Exception:
                pass  # Technical fetch failure is non-fatal; basic quote still injected

        block += f"\n*⚠️ 以上均为真实市场数据。请严格基于这些数字作答，不要修改或编造任何价格/指标数值。货币单位：{currency}。*\n"
        return block

    except Exception:
        return ""


def _fetch_snapshot_row_for_symbol(symbol: str, mdc) -> dict:
    quote = {}
    fundamentals = {}
    technical = {}
    warnings: list[str] = []
    errors: list[str] = []
    quality: dict = {}
    stale = False
    try:
        from packages.aria_services.data import DataService
        service = DataService(market_client=mdc, router=None)
        quote_result = service.quote(symbol)
        fund_result = service.fundamentals(symbol)
        tech_result = service.technical_indicators(symbol, days=120)
        quote = quote_result.data or {}
        fundamentals = fund_result.data or {}
        technical = tech_result.data or {}
        warnings.extend(quote_result.warnings + fund_result.warnings + tech_result.warnings)
        errors.extend(quote_result.errors + fund_result.errors + tech_result.errors)
        provider_chain = list(dict.fromkeys(
            str(p) for p in (
                quote_result.provider_chain + fund_result.provider_chain + tech_result.provider_chain
            ) if p
        ))
        missing_fields = list(dict.fromkeys(
            quote_result.missing_fields + fund_result.missing_fields + tech_result.missing_fields
        ))
        stale = bool(quote_result.stale or tech_result.stale)
        quality = {
            "status": "partial" if missing_fields else "ok",
            "stale": stale,
            "providers": provider_chain,
            "missing_fields": missing_fields,
            "warnings": warnings[:5],
            "errors": errors[:5],
        }
    except Exception as exc:
        quote = {"success": False, "error": str(exc)}
        warnings.append(f"data_service: {exc}")
        provider_chain = []
        missing_fields = ["price", "market_cap", "technical"]
        quality = {
            "status": "unavailable",
            "stale": False,
            "providers": [],
            "missing_fields": missing_fields,
            "warnings": warnings[:5],
            "errors": [str(exc)],
        }

    currency = quote.get("currency") or fundamentals.get("currency") or "USD"
    market_cap = (
        quote.get("market_cap")
        or fundamentals.get("market_cap")
        or fundamentals.get("total_mv")
    )
    if not provider_chain:
        provider_chain = []
        for source in (quote, fundamentals, technical):
            chain = source.get("provider_chain")
            if isinstance(chain, list):
                provider_chain.extend(chain)
            elif source.get("provider"):
                provider_chain.append(source.get("provider"))
            elif source.get("source"):
                provider_chain.append(source.get("source"))
        provider_chain = list(dict.fromkeys(str(p) for p in provider_chain if p))
    if not missing_fields:
        missing_fields = []
    # ── yfinance fallback when price is 0 or missing ────────────────────────
    _price_val = quote.get("price")
    _price_bad = not quote.get("success") or _price_val in (None, "", 0) or float(_price_val or 0) == 0
    if _price_bad:
        try:
            import yfinance as _yf_snap
            _sym_yf = symbol.upper()
            _t = _yf_snap.Ticker(_sym_yf)
            _fi = _t.fast_info
            _yf_price = getattr(_fi, "last_price", None) or getattr(_fi, "previous_close", None)
            if _yf_price and float(_yf_price) > 0:
                _yf_prev = getattr(_fi, "previous_close", _yf_price)
                _yf_chg = (float(_yf_price) - float(_yf_prev)) / float(_yf_prev) * 100 if _yf_prev else 0
                _yf_info = {}
                try:
                    _yf_info = _t.info or {}
                except Exception:
                    pass
                quote = {
                    "success": True,
                    "symbol": symbol,
                    "name": _yf_info.get("shortName") or _yf_info.get("longName") or symbol,
                    "price": round(float(_yf_price), 2),
                    "change_pct": round(_yf_chg, 2),
                    "currency": _yf_info.get("currency") or "USD",
                    "market_cap": _yf_info.get("marketCap") or 0,
                    "provider": "yfinance",
                    "provider_chain": ["yfinance"],
                }
                if not provider_chain or all("edgar" in p.lower() for p in provider_chain):
                    provider_chain = ["yfinance"]
                _price_bad = False
        except Exception:
            pass
    _price_val = quote.get("price")
    if _price_bad or _price_val in (None, "", 0):
        missing_fields.append("price")
    if market_cap in (None, "", 0):
        market_cap = quote.get("market_cap") or market_cap
    if market_cap in (None, "", 0):
        missing_fields.append("market_cap")
    if not technical.get("success"):
        missing_fields.append("technical")

    return {
        "symbol": symbol,
        "name": quote.get("name") or fundamentals.get("name") or symbol,
        "success": bool(quote.get("success")),
        "price": quote.get("price"),
        "change_pct": quote.get("change_pct"),
        "currency": currency,
        "market_cap": market_cap,
        "high": quote.get("high"),
        "low": quote.get("low"),
        "trend": _market_snapshot_trend(
            quote.get("price"),
            quote.get("high"),
            quote.get("low"),
            quote.get("change_pct"),
        ),
        "provider_chain": provider_chain,
        "missing_fields": list(dict.fromkeys(missing_fields)),
        "error": quote.get("error") or "",
        "warnings": warnings,
        "errors": errors,
        "quality": quality,
        "stale": stale,
    }


def _try_handle_multi_market_snapshot(message: str, symbols: list[str]) -> dict:
    if len(symbols) < 2:
        return {"success": False, "error": "not_multi_symbol"}
    if not _has_mdc_lazy():
        return {
            "success": True,
            "response": (
                "Market Snapshot\n\n"
                "当前本地行情客户端未加载，无法获取多标的实时行情。\n\n"
                f"可运行 `/quote {' '.join(symbols)}` 重试。"
            ),
            "tools_used": ["market_snapshot"],
        }
    mdc = _get_mdc_lazy()
    rows = [_fetch_snapshot_row_for_symbol(symbol, mdc) for symbol in symbols]
    now = datetime.now().strftime("%Y-%m-%d")
    provider_chain = list(dict.fromkeys(
        provider for row in rows for provider in row.get("provider_chain", [])
    ))
    missing = sorted(set(
        f"{row['symbol']}:{field}"
        for row in rows for field in row.get("missing_fields", [])
    ))
    stale_symbols = [row["symbol"] for row in rows if row.get("stale")]
    warnings = [w for row in rows for w in (row.get("warnings") or [])]
    errors = [e for row in rows for e in (row.get("errors") or [])]
    table = [
        "Market Snapshot · " + now,
        "",
        "| Symbol | Company | Price | Change | Market Cap | Trend |",
        "|---|---|---:|---:|---:|---|",
    ]
    for row in rows:
        currency = row.get("currency") or "USD"
        if row.get("success") and row.get("price") not in (None, ""):
            try:
                price = f"{currency} {float(row['price']):,.2f}"
            except Exception:
                price = "—"
            try:
                change = float(row.get("change_pct") or 0)
                change_text = f"{change:+.2f}%"
            except Exception:
                change_text = "—"
        else:
            price = "—"
            change_text = "—"
        table.append(
            f"| {row['symbol']} | {row.get('name') or row['symbol']} | {price} | "
            f"{change_text} | {_format_compact_market_cap(row.get('market_cap'), currency)} | "
            f"{row.get('trend') or '—'} |"
        )

    table.append("")
    table.append("Data")
    table.append(f"- quote/fundamentals: {', '.join(provider_chain) if provider_chain else 'unavailable'}")
    table.append(f"- stale: {', '.join(stale_symbols) if stale_symbols else 'none'}")
    if missing:
        table.append(f"- missing: {', '.join(missing)}")
    else:
        table.append("- missing: none")
    if warnings:
        table.append(f"- warnings: {'; '.join(str(w) for w in warnings[:3])}")
    if errors:
        table.append(f"- errors: {'; '.join(str(e) for e in errors[:3])}")
    table.append("- technical: shown as trend only when indicators are unavailable")
    table.append("")
    table.append("Next")
    table.append("- `/ta " + symbols[0] + "` · `/ta " + symbols[1] + "` — full technical chart")
    table.append("- `/report " + symbols[0] + "` · `/report " + symbols[1] + "` — generate research reports")
    table.append("")
    table.append("市场快照 · 本内容不构成投资建议")
    return {"success": True, "response": "\n".join(table), "tools_used": ["market_snapshot"]}


def _render_private_company_analysis(profile_key: str, message: str) -> dict:
    """Render a structured analysis for a private company using static profile data."""
    p = _PRIVATE_COMPANY_PROFILES.get(profile_key, {})
    if not p:
        return {"success": False, "error": "no_private_profile"}

    name = p.get("name", profile_key)
    val = p.get("valuation_usd", "N/A")
    rev = p.get("rev_est", "N/A")
    growth = p.get("rev_growth", "N/A")
    comps = p.get("comparables", [])

    lines = [
        f"## {name}",
        f"> ⚠️  **私有公司 — 无公开交易数据**  所有数字均来自公开报道与融资文件，非官方财报。",
        "",
        "### 估值与规模",
        f"| 指标 | 数据 |",
        f"|------|------|",
        f"| 最新估值 | **${val}B**（{p.get('last_funding', 'N/A')}）|",
        f"| 收入估算 | ~${rev}B/年（YoY +{growth}%）|",
        f"| 员工数量 | ~{p.get('employees', 'N/A')}k |",
        f"| 创立时间 | {p.get('founded', 'N/A')} — {p.get('founder', 'N/A')} |",
        f"| 总部 | {p.get('hq', 'N/A')} |",
        f"| IPO 状态 | {p.get('ipo_status', 'N/A')} |",
        "",
    ]

    segs = p.get("segments", [])
    if segs:
        lines += ["### 业务板块", ""]
        for s in segs:
            lines.append(f"- {s}")
        lines.append("")

    highlights = p.get("highlights", [])
    if highlights:
        lines += ["### 核心亮点", ""]
        for h in highlights:
            lines.append(f"✅ {h}")
        lines.append("")

    risks = p.get("risks", [])
    if risks:
        lines += ["### 主要风险", ""]
        for r in risks:
            lines.append(f"⚠️  {r}")
        lines.append("")

    if comps:
        comp_str = " · ".join(comps)
        lines += [
            "### 可比公司（均已上市）",
            f"> 可对比分析：{comp_str}",
            f"> 例如：`分析 {comps[0]} 的财务数据` 或 `/ta {comps[0]}` 查看技术面",
            "",
        ]

    lines += [
        "---",
        "*数据来源：公开融资公告、新闻报道。私有公司无 SEC/证监会披露义务，以上估算存在较大不确定性。*",
    ]

    return {
        "success": True,
        "response": "\n".join(lines),
        "tools_used": ["private_company_profile"],
    }


def _try_handle_market_snapshot_analysis(message: str, history: list = None) -> dict:
    """Deterministic path for simple market analysis.

    Local small models tend to mangle injected quote fields into fragments like
    "N/A/N/A/-1.24%".  For snapshot requests, format the data directly.
    """
    if not _is_market_snapshot_request(message, history):
        return {"success": False, "error": "not_market_snapshot"}

    _symbols = _extract_market_symbols(message)
    if len(_symbols) >= 2:
        return _try_handle_multi_market_snapshot(message, _symbols)

    _msg_sym = _extract_market_symbol(message)

    # Private company: PRIVATE:Name — render static profile instead of live data
    if _msg_sym and _msg_sym.startswith("PRIVATE:"):
        return _render_private_company_analysis(_msg_sym[len("PRIVATE:"):], message)

    _hist_sym = (_extract_symbol_from_history(history) if history else "") if not _msg_sym else ""

    # Guard: if message names an unrecognised company, don't silently use history symbol
    if not _msg_sym and _has_unresolved_company_mention(message):
        return {
            "success": True,
            "response": (
                "## ❓ 无法识别的股票\n\n"
                "未能将消息中提到的公司/品牌解析为已知股票代码。\n\n"
                "请提供具体代码后重试，例如：\n"
                "- A股：`/quote 600519`（贵州茅台）\n"
                "- 港股：`/quote 0700.HK`（腾讯）\n"
                "- 美股：`/quote AAPL`\n"
                "- 欧洲：`/quote MC.PA`（LVMH/路易威登）\n\n"
                "*提示：如需全局搜索，可输入 `/ta <代码>` 获取完整技术分析。*"
            ),
            "tools_used": ["market_snapshot"],
        }

    symbol = _msg_sym or _hist_sym or "AAPL"
    def _snapshot_ashare_code(sym: str) -> str:
        s = str(sym or "").strip().upper()
        if s.endswith((".SZ", ".SS", ".SH")):
            s = s.rsplit(".", 1)[0]
        if s.startswith(("SH", "SZ")) and s[2:].isdigit() and len(s[2:]) == 6:
            s = s[2:]
        return s if s.isdigit() and len(s) == 6 else ""

    _ashare_code = _snapshot_ashare_code(symbol)
    if not _has_mdc_lazy():
        return {
            "success": True,
            "response": (
                f"## {symbol} 市场快照\n\n"
                "当前本地行情客户端未加载，无法获取实时行情。\n\n"
                f"可运行 `/quote {symbol}` 重试。"
            ),
            "tools_used": ["market_snapshot"],
        }

    import time as _time_snap

    def _clean_network_error(raw: str) -> str:
        """Convert raw exception strings to readable Chinese messages."""
        if "Connection aborted" in raw or "RemoteDisconnected" in raw:
            return "网络连接被中断（服务器关闭连接），请稍后重试"
        if "Connection refused" in raw:
            return "连接被拒绝，数据服务暂时不可用"
        if "timeout" in raw.lower() or "timed out" in raw.lower():
            return "连接超时，请稍后重试"
        if "NoneType" in raw or raw.strip() in ("None", ""):
            return "数据源未返回有效价格"
        return raw

    quote = {"success": False, "error": "未初始化"}
    _snapshot_quality = {}
    for _attempt in range(3):
        try:
            mdc = _get_mdc_lazy()
            try:
                from packages.aria_services.data import DataService as _SnapshotDataService
                _quote_result = _SnapshotDataService(market_client=mdc, router=None).quote(symbol)
                quote = _quote_result.data or {}
                _snapshot_quality = _quote_result.quality or {}
                if _quote_result.provider_chain:
                    quote.setdefault("provider_chain", _quote_result.provider_chain)
                quote.setdefault("success", bool(_quote_result.success))
            except Exception:
                quote = mdc.quote(symbol)
            if quote.get("success"):
                break
            _err_str = str(quote.get("error", ""))
            _err_lower = _err_str.lower()
            # Clean raw exception strings in-place
            if any(k in _err_str for k in ("Connection aborted", "RemoteDisconnected",
                                            "Connection refused", "timeout")):
                quote["error"] = _clean_network_error(_err_str)
            # Retry on connection errors AND rate limits
            _should_retry = (
                ("rate" in _err_lower or "429" in _err_lower or "too many" in _err_lower)
                or ("connection aborted" in _err_lower or "remotedisconnected" in _err_lower)
            )
            if _should_retry and _attempt < 2:
                _time_snap.sleep(1 + _attempt)  # 1s, 2s
                continue
            break
        except Exception as exc:
            _raw_exc = str(exc)
            _exc_lower = _raw_exc.lower()
            _clean_err = _clean_network_error(_raw_exc)
            _should_retry = (
                ("rate" in _exc_lower or "429" in _exc_lower or "too many" in _exc_lower)
                or ("connection aborted" in _exc_lower or "remotedisconnected" in _exc_lower)
            )
            if _should_retry and _attempt < 2:
                _time_snap.sleep(1 + _attempt)
                continue
            quote = {"success": False, "error": _clean_err}
            break

    # Finnhub fallback when primary data source (yfinance) failed or rate-limited
    # _get_provider_key reads both env vars AND ~/.arthera/providers.json
    # NOTE: do NOT use dir() — it returns local scope, not module globals.
    _fh_key = _get_provider_key("finnhub")
    _fh_tried = False
    if not quote.get("success") and _fh_key:
        _fh_tried = True
        try:
            import requests as _rq
            _fh_r = _rq.get(
                f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={_fh_key}",
                timeout=6
            )
            if _fh_r.status_code == 200:
                _fh = _fh_r.json()
                if _fh.get("c"):  # current price present
                    quote = {
                        "success": True, "symbol": symbol,
                        "price": round(_fh["c"], 2),
                        "change_pct": round(float(_fh.get("dp") or 0), 2),
                        "high": round(_fh.get("h", 0), 2),
                        "low":  round(_fh.get("l", 0), 2),
                        "currency": "USD", "provider": "finnhub",
                    }
        except Exception:
            pass

    # akshare fallback for A-shares when both yfinance and eastmoney fail
    _is_a_share_sym = bool(_ashare_code)
    if _is_a_share_sym and not quote.get("success"):
        try:
            import akshare as _ak
            from datetime import datetime as _dt2, timedelta as _td2
            _end_d = _dt2.now().strftime("%Y%m%d")
            _start_d = (_dt2.now() - _td2(days=7)).strftime("%Y%m%d")
            _df_q = _ak.stock_zh_a_hist(
                symbol=_ashare_code, period="daily",
                start_date=_start_d, end_date=_end_d, adjust=""
            )
            if not _df_q.empty:
                _row = _df_q.iloc[-1]
                _close = float(_row.get("收盘", 0))
                _prev = float(_df_q.iloc[-2]["收盘"]) if len(_df_q) >= 2 else _close
                _chg_p = round((_close - _prev) / _prev * 100, 2) if _prev else 0
                _name = symbol
                try:
                    _info_df = _ak.stock_individual_info_em(symbol=_ashare_code)
                    _name = str(_info_df[_info_df["item"] == "股票简称"]["value"].values[0])
                except Exception:
                    pass
                quote = {
                    "success": True, "symbol": symbol, "name": _name,
                    "price": _close, "change_pct": _chg_p,
                    "high": float(_row.get("最高", _close)),
                    "low":  float(_row.get("最低", _close)),
                    "volume": int(_row.get("成交量", 0)),
                    "currency": "CNY", "provider": "akshare",
                }
        except Exception:
            pass

    def _num(v):
        try:
            if v in (None, "", "N/A", "-", "nan"):
                return None
            return float(v)
        except Exception:
            return None

    price = _num(quote.get("price"))
    # yfinance fallback when price is 0 or None (e.g. EDGAR source returns 0.00)
    if price is None or price == 0:
        try:
            import yfinance as _yf_single
            _t_s = _yf_single.Ticker(symbol)
            _fi_s = _t_s.fast_info
            _yf_p = getattr(_fi_s, "last_price", None) or getattr(_fi_s, "previous_close", None)
            if _yf_p and float(_yf_p) > 0:
                _yf_prev_s = getattr(_fi_s, "previous_close", _yf_p)
                _yf_chg_s = (float(_yf_p) - float(_yf_prev_s)) / float(_yf_prev_s) * 100 if _yf_prev_s else 0
                _yf_info_s = {}
                try:
                    _yf_info_s = _t_s.info or {}
                except Exception:
                    pass
                price = round(float(_yf_p), 2)
                quote = {
                    "success": True, "symbol": symbol,
                    "name": _yf_info_s.get("shortName") or _yf_info_s.get("longName") or symbol,
                    "price": price,
                    "change_pct": round(_yf_chg_s, 2),
                    "currency": _yf_info_s.get("currency") or "USD",
                    "market_cap": _yf_info_s.get("marketCap") or 0,
                    "provider": "yfinance",
                }
        except Exception:
            pass
        price = _num(quote.get("price"))

    if not quote.get("success") or price is None or price == 0:
        err = quote.get("error") or "当前数据源未返回有效价格"
        if "NoneType" in str(err):
            err = "当前数据源未返回有效价格"
        is_rate_limit = "rate" in str(err).lower() or "429" in str(err) or "too many" in str(err).lower()
        if is_rate_limit:
            if _fh_tried:
                # Finnhub was tried but also failed — both sources exhausted
                _hint = "\n\n[提示] yfinance 和 Finnhub 均触发频率限制，请稍等 30 秒后重试。"
            elif _fh_key:
                # Key configured but Finnhub wasn't tried (shouldn't happen, but defensive)
                _hint = "\n\n[提示] 数据源请求频率受限，请稍等 30 秒后重试。"
            else:
                # No Finnhub key — suggest configuring one
                _hint = (
                    "\n\n[提示] 数据源请求频率受限：请稍等 30 秒后重试，"
                    "或配置 Finnhub key 使用备用数据源：`/apikey set finnhub <key>`"
                    "（注册：https://finnhub.io/register）"
                )
        else:
            _hint = ""
        return {
            "success": True,
            "response": (
                f"## {symbol} 市场快照\n\n"
                f"当前无法获取有效行情：{err}{_hint}\n\n"
                f"可运行 `/quote {symbol}` 重试；在数据恢复前不输出 RSI、MACD 或支撑/阻力位。"
            ),
            "tools_used": ["market_snapshot"],
            "rate_limited": is_rate_limit,
        }

    name = quote.get("name") or symbol
    currency = quote.get("currency") or "USD"
    chg = _num(quote.get("change_pct"))
    high = _num(quote.get("high"))
    low = _num(quote.get("low"))
    volume = quote.get("volume")
    market_cap_raw = _num(quote.get("market_cap"))
    provider = quote.get("provider") or "market_data_client"
    sign = "+" if (chg or 0) >= 0 else ""
    chg_str = f"{sign}{chg:.2f}%" if chg is not None else "—"
    range_str = f"{currency} {low:,.2f} - {currency} {high:,.2f}" if low is not None and high is not None else ""
    # Format market cap: T / B / M abbreviation
    if market_cap_raw and market_cap_raw > 0:
        if market_cap_raw >= 1e12:
            _mktcap_str = f"{currency} {market_cap_raw/1e12:.2f}T"
        elif market_cap_raw >= 1e9:
            _mktcap_str = f"{currency} {market_cap_raw/1e9:.1f}B"
        elif market_cap_raw >= 1e6:
            _mktcap_str = f"{currency} {market_cap_raw/1e6:.0f}M"
        else:
            _mktcap_str = f"{currency} {market_cap_raw:,.0f}"
    else:
        _mktcap_str = None

    # ── Technical indicators: mdc → akshare(A股) → yfinance → Finnhub ────────
    ti = {}
    try:
        ti = mdc.technical_indicators(symbol, days=120)
    except Exception:
        ti = {}

    # Akshare fallback for A-shares (6-digit code, no suffix) — more reliable than yfinance for CN
    _is_a_share = bool(_ashare_code)
    if (_is_a_share and (not ti.get("success") or ti.get("rsi") is None)):
        try:
            import akshare as _ak
            import numpy as _np_ak
            from datetime import datetime as _dt, timedelta as _td
            _ak_start = (_dt.now() - _td(days=200)).strftime("%Y%m%d")
            _ak_end   = _dt.now().strftime("%Y%m%d")
            _df_ak = _ak.stock_zh_a_hist(
                symbol=_ashare_code, period="daily",
                start_date=_ak_start, end_date=_ak_end,
                adjust="qfq",
            )
            _col_map = {
                "收盘": "Close", "成交量": "Volume",
                "close": "Close", "volume": "Volume",
            }
            _df_ak = _df_ak.rename(columns=_col_map)
            if "Close" in _df_ak.columns and len(_df_ak) >= 20:
                _c_ak = _df_ak["Close"].astype(float)
                _v_ak = _df_ak["Volume"].astype(float) if "Volume" in _df_ak.columns else None
                # RSI(14)
                _d_ak = _c_ak.diff()
                _g_ak = _d_ak.clip(lower=0).rolling(14).mean()
                _l_ak = (-_d_ak.clip(upper=0)).rolling(14).mean()
                _rs_ak = _g_ak / _l_ak.replace(0, _np_ak.nan)
                _rsi_ak = float((100 - 100 / (1 + _rs_ak)).iloc[-1])
                # MACD
                _ema12_ak = _c_ak.ewm(span=12).mean()
                _ema26_ak = _c_ak.ewm(span=26).mean()
                _macd_ak  = _ema12_ak - _ema26_ak
                _sig_ak   = _macd_ak.ewm(span=9).mean()
                _mhist_ak = float((_macd_ak - _sig_ak).iloc[-1])
                # MA / BB
                _ma20_ak  = _c_ak.rolling(20).mean()
                _std20_ak = _c_ak.rolling(20).std()
                _ma60_ak  = _c_ak.rolling(60).mean() if len(_c_ak) >= 60 else _ma20_ak
                ti = {
                    "success":   True,
                    "rsi":       round(_rsi_ak, 2) if not _np_ak.isnan(_rsi_ak) else None,
                    "macd_hist": round(_mhist_ak, 4),
                    "ma20":      round(float(_ma20_ak.iloc[-1]), 2),
                    "ma60":      round(float(_ma60_ak.iloc[-1]), 2),
                    "bb_upper":  round(float((_ma20_ak + 2*_std20_ak).iloc[-1]), 2),
                    "bb_lower":  round(float((_ma20_ak - 2*_std20_ak).iloc[-1]), 2),
                    "provider":  "akshare",
                }
                if volume is None and _v_ak is not None:
                    _rv = _v_ak.iloc[-1]
                    if not _np_ak.isnan(_rv):
                        volume = int(_rv)
        except Exception:
            pass

    # If mdc returned nothing useful (all None), try yfinance directly
    if (not ti.get("success") or ti.get("rsi") is None) and not _is_a_share:
        try:
            import yfinance as _yf
            import numpy as _np
            # A股裸6位代码需要 yfinance 后缀：6/68开头→.SS，其余→.SZ
            _yf_sym = symbol
            if symbol.isdigit() and len(symbol) == 6:
                _yf_sym = symbol + (".SS" if symbol.startswith("6") else ".SZ")
            _hist = _yf.Ticker(_yf_sym).history(period="6mo")
            if len(_hist) >= 20:
                _close = _hist["Close"]
                _vol   = _hist["Volume"]
                # RSI(14)
                _d = _close.diff()
                _g = _d.clip(lower=0).rolling(14).mean()
                _l = (-_d.clip(upper=0)).rolling(14).mean()
                _rs = _g / _l.replace(0, _np.nan)
                _rsi = float((100 - 100 / (1 + _rs)).iloc[-1])
                # MACD hist
                _ema12  = _close.ewm(span=12).mean()
                _ema26  = _close.ewm(span=26).mean()
                _macd   = _ema12 - _ema26
                _signal = _macd.ewm(span=9).mean()
                _mhist  = float((_macd - _signal).iloc[-1])
                # Bollinger Bands & MA
                _ma20  = _close.rolling(20).mean()
                _std20 = _close.rolling(20).std()
                _ma60  = _close.rolling(60).mean() if len(_close) >= 60 else _ma20
                ti = {
                    "success":   True,
                    "rsi":       round(_rsi, 2) if not _np.isnan(_rsi) else None,
                    "macd_hist": round(_mhist, 4),
                    "ma20":      round(float(_ma20.iloc[-1]), 2),
                    "ma60":      round(float(_ma60.iloc[-1]), 2),
                    "bb_upper":  round(float((_ma20 + 2 * _std20).iloc[-1]), 2),
                    "bb_lower":  round(float((_ma20 - 2 * _std20).iloc[-1]), 2),
                    "provider":  "yfinance_direct",
                }
                # Back-fill volume if missing from quote
                if volume is None or str(volume) in ("None", "N/A", ""):
                    _recent_vol = _vol.iloc[-1]
                    if not _np.isnan(_recent_vol):
                        volume = int(_recent_vol)
        except Exception:
            pass

    # Finnhub candle fallback: when price came from Finnhub but yfinance TA failed,
    # fetch 6-month daily candles from Finnhub to compute RSI/MACD/MA.
    # Only attempted for US-style symbols (no A-share 6-digit codes, no .HK).
    _is_us_sym = bool(symbol and not symbol.isdigit() and "." not in symbol)
    if (not ti.get("success") or ti.get("rsi") is None) and _fh_key and _is_us_sym:
        try:
            import requests as _rq2, time as _t2
            _to_ts = int(_t2.time())
            _from_ts = _to_ts - 180 * 86400   # ~6 months
            _cr = _rq2.get(
                f"https://finnhub.io/api/v1/stock/candle"
                f"?symbol={symbol}&resolution=D&from={_from_ts}&to={_to_ts}&token={_fh_key}",
                timeout=8,
            )
            if _cr.status_code == 200:
                _cd = _cr.json()
                if _cd.get("s") == "ok" and _cd.get("c") and len(_cd["c"]) >= 20:
                    import numpy as _np2
                    _c = _cd["c"]   # close prices list
                    _v = _cd.get("v", [])
                    import statistics as _st
                    # RSI(14) — simple loop (no pandas needed)
                    _gains, _losses = [], []
                    for i in range(1, len(_c)):
                        _delta = _c[i] - _c[i-1]
                        _gains.append(max(_delta, 0))
                        _losses.append(max(-_delta, 0))
                    _ag = sum(_gains[:14]) / 14
                    _al = sum(_losses[:14]) / 14
                    for i in range(14, len(_gains)):
                        _ag = (_ag * 13 + _gains[i]) / 14
                        _al = (_al * 13 + _losses[i]) / 14
                    _rsi_fh = (100 - 100 / (1 + _ag / _al)) if _al else 100
                    # MACD(12,26,9)
                    def _ema_list(prices, span):
                        k, result_ema = 2/(span+1), [prices[0]]
                        for p in prices[1:]: result_ema.append(p*k + result_ema[-1]*(1-k))
                        return result_ema
                    _ema12_fh = _ema_list(_c, 12)
                    _ema26_fh = _ema_list(_c, 26)
                    _macd_fh  = [a - b for a, b in zip(_ema12_fh, _ema26_fh)]
                    _sig_fh   = _ema_list(_macd_fh, 9)
                    _mhist_fh = _macd_fh[-1] - _sig_fh[-1]
                    # MA20 / MA60
                    _ma20_fh = sum(_c[-20:]) / 20
                    _ma60_fh = sum(_c[-60:]) / 60 if len(_c) >= 60 else _ma20_fh
                    # Bollinger Bands
                    _std20_fh = _st.stdev(_c[-20:])
                    ti = {
                        "success":   True,
                        "rsi":       round(_rsi_fh, 2),
                        "macd_hist": round(_mhist_fh, 4),
                        "ma20":      round(_ma20_fh, 2),
                        "ma60":      round(_ma60_fh, 2),
                        "bb_upper":  round(_ma20_fh + 2*_std20_fh, 2),
                        "bb_lower":  round(_ma20_fh - 2*_std20_fh, 2),
                        "provider":  "finnhub_candle",
                    }
                    if volume is None and _v:
                        volume = int(_v[-1]) if _v[-1] else None
        except Exception:
            pass

    # Yahoo Finance v8 direct API — different endpoint from yfinance, avoids rate-limit collision.
    # Used when yfinance (via MDC) AND Finnhub candle both fail to produce TA data.
    # Only for non-A-share symbols; A-shares use akshare which has its own path above.
    if (not ti.get("success") or ti.get("rsi") is None) and _is_us_sym:
        try:
            import json as _json_yv8, urllib.request as _urlreq_yv8
            _yv8_url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                "?interval=1d&range=6mo"
            )
            _yv8_req = _urlreq_yv8.Request(_yv8_url, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
            })
            with _urlreq_yv8.urlopen(_yv8_req, timeout=10) as _yv8_resp:
                _yv8_data = _json_yv8.loads(_yv8_resp.read())
            _yv8_result = _yv8_data["chart"]["result"][0]
            _yv8_q      = _yv8_result["indicators"]["quote"][0]
            _c_yv8      = [x for x in _yv8_q.get("close", []) if x is not None]
            _v_yv8      = [x for x in _yv8_q.get("volume", []) if x is not None]
            if len(_c_yv8) >= 26:
                _c = _c_yv8
                # RSI(14) — Wilder EMA
                _d = [_c[i] - _c[i-1] for i in range(1, len(_c))]
                _g = [max(x, 0) for x in _d];  _l = [max(-x, 0) for x in _d]
                _ag = sum(_g[:14]) / 14;        _al = sum(_l[:14]) / 14
                for i in range(14, len(_g)):
                    _ag = (_ag * 13 + _g[i]) / 14; _al = (_al * 13 + _l[i]) / 14
                _rsi_yv8 = (100 - 100 / (1 + _ag / _al)) if _al else 100.0
                # MACD(12,26,9)
                def _ema_yv8(prices, span):
                    k, r = 2 / (span + 1), [prices[0]]
                    for p in prices[1:]: r.append(p * k + r[-1] * (1 - k))
                    return r
                _ema12 = _ema_yv8(_c, 12); _ema26 = _ema_yv8(_c, 26)
                _macd  = [a - b for a, b in zip(_ema12, _ema26)]
                _sig   = _ema_yv8(_macd, 9)
                _mhist_yv8 = _macd[-1] - _sig[-1]
                # MA20 / MA60 / Bollinger
                _ma20_yv8 = sum(_c[-20:]) / 20
                _ma60_yv8 = sum(_c[-60:]) / 60 if len(_c) >= 60 else _ma20_yv8
                import statistics as _st_yv8
                _std20_yv8 = _st_yv8.stdev(_c[-20:])
                ti = {
                    "success":   True,
                    "rsi":       round(_rsi_yv8, 2),
                    "macd_hist": round(_mhist_yv8, 4),
                    "ma20":      round(_ma20_yv8, 2),
                    "ma60":      round(_ma60_yv8, 2),
                    "bb_upper":  round(_ma20_yv8 + 2 * _std20_yv8, 2),
                    "bb_lower":  round(_ma20_yv8 - 2 * _std20_yv8, 2),
                    "provider":  "yahoo_v8",
                }
                if volume is None and _v_yv8:
                    volume = int(_v_yv8[-1])
        except Exception:
            pass

    rsi = _num(ti.get("rsi"))
    mhist = _num(ti.get("macd_hist"))
    ma20 = _num(ti.get("ma20"))
    ma60 = _num(ti.get("ma60"))
    bbu = _num(ti.get("bb_upper"))
    bbl = _num(ti.get("bb_lower"))

    if rsi is None:
        rsi_view = "—"
    elif rsi >= 70:
        rsi_view = f"{rsi:.1f}，超买风险"
    elif rsi <= 30:
        rsi_view = f"{rsi:.1f}，超卖反弹可能"
    else:
        rsi_view = f"{rsi:.1f}，中性"

    if mhist is None:
        macd_view = "—"
    else:
        macd_view = f"{mhist:.4f}，{'偏多' if mhist > 0 else '偏空'}"

    supports = [v for v in (bbl, ma60, ma20) if v is not None and v < price]
    resistances = [v for v in (ma20, ma60, bbu) if v is not None and v > price]
    supports = sorted(set(round(v, 2) for v in supports), reverse=True)[:3]
    resistances = sorted(set(round(v, 2) for v in resistances))[:3]
    support_str = ", ".join(f"{currency} {v:,.2f}" for v in supports)
    resistance_str = ", ".join(f"{currency} {v:,.2f}" for v in resistances)

    # ── Signal logic ──────────────────────────────────────────────────────────
    _enough_data = (rsi is not None) or (mhist is not None)

    _SIGNAL_LABELS: dict[str, dict[str, str]] = {
        "zh": {
            "CAUTION": "超买 — 等待回调确认",
            "WATCH":   "超卖 — 关注企稳信号",
            "HOLD+":   "短线偏强，控制仓位",
            "HOLD−":   "短线偏弱，守住支撑",
            "NEUTRAL": "震荡观察，等待方向",
            "—":       "指标数据不足",
        },
        "en": {
            "CAUTION": "Overbought — wait for pullback",
            "WATCH":   "Oversold — watch for stabilization",
            "HOLD+":   "Short-term bias up, manage size",
            "HOLD−":   "Short-term bias down, hold support",
            "NEUTRAL": "Ranging — wait for direction",
            "—":       "Insufficient indicator data",
        },
    }

    if _enough_data:
        if rsi is not None and rsi >= 75:
            _sig_key = "CAUTION"
        elif rsi is not None and rsi <= 28:
            _sig_key = "WATCH"
        elif mhist is not None and mhist > 0 and (chg or 0) >= 0:
            _sig_key = "HOLD+"
        elif mhist is not None and mhist < 0 and (chg or 0) < 0:
            _sig_key = "HOLD−"
        else:
            _sig_key = "NEUTRAL"
    else:
        _sig_key = "—"

    signal = _sig_key
    signal_str = _SIGNAL_LABELS["zh"][_sig_key]  # will be overwritten after _lang is resolved below

    # ── Price-action analysis (available even without TA) ─────────────────
    _range_size = (high - low) if (high is not None and low is not None) else None
    _price_pos  = int((price - low) / _range_size * 100) if _range_size and _range_size > 0 else None
    _swing_pct  = round(_range_size / price * 100, 2) if _range_size and price else None
    _chg_abs    = abs(chg) if chg is not None else None
    _pa_lines   = []
    if _price_pos is not None:
        _pos_label = "日内高位" if _price_pos >= 70 else ("日内低位" if _price_pos <= 30 else "日内中段")
        _pa_lines.append(f"价格位置：{_pos_label}（日内第 {_price_pos} 百分位）")
    if _swing_pct:
        _pa_lines.append(f"日内振幅：{_swing_pct:.1f}%{'（波动偏大）' if _swing_pct > 3 else ''}")
    if chg is not None and _chg_abs is not None:
        if _chg_abs < 0.01:
            _pa_lines.append("今日动能：持平")
        else:
            _mo = "上涨" if chg > 0 else "下跌"
            _strength = "（大幅）" if _chg_abs > 2 else ("（温和）" if _chg_abs < 0.5 else "")
            _pa_lines.append(f"今日动能：{_mo} {_chg_abs:.2f}%{_strength}")

    # ── Build output ──────────────────────────────────────────────────────
    weekday = datetime.now().weekday()
    ti_provider = ti.get("provider", "")
    quote_chain = quote.get("provider_chain") or [provider]
    data_src = " -> ".join(str(p) for p in quote_chain if p)
    if ti_provider and ti_provider not in quote_chain:
        data_src += f" + {ti_provider}"
    _vol_str = _fmt_int(volume)
    _now_str = datetime.now().strftime("%Y-%m-%d")

    # ── Language-aware labels ─────────────────────────────────────────────
    _lang = _detect_lang(message)
    _en = _lang == "en"
    signal_str = _SIGNAL_LABELS.get(_lang, _SIGNAL_LABELS["zh"])[_sig_key]
    _L = {
        "disclaimer":   "Not investment advice" if _en else "不构成投资建议",
        "after_hours":  "After-hours" if _en else "休市/盘后",
        "market_open":  "Market open" if _en else "盘中",
        "price_hdr":    "Metric" if _en else "指标",
        "value_hdr":    "Value" if _en else "数值",
        "latest":       "Last price" if _en else "最新价",
        "day_range":    "Day range" if _en else "日内区间",
        "swing":        "Swing" if _en else "振幅",
        "mktcap":       "Mkt cap" if _en else "市值",
        "volume":       "Volume" if _en else "成交量",
        "ta_hdr":       "Indicator" if _en else "技术指标",
        "meaning_hdr":  "Meaning" if _en else "含义",
        "overbought":   "Overbought" if _en else "超买",
        "oversold":     "Oversold" if _en else "超卖",
        "neutral":      "Neutral" if _en else "中性",
        "bull_mom":     "Bullish momentum" if _en else "多头动能",
        "bear_mom":     "Bearish momentum" if _en else "空头动能",
        "above_ma20":   "Above MA20 ↑" if _en else "价格高于MA20 ↑",
        "below_ma20":   "Below MA20 ↓" if _en else "价格低于MA20 ↓",
        "above_ma60":   "Above MA60 ↑" if _en else "价格高于MA60 ↑",
        "below_ma60":   "Below MA60 ↓" if _en else "价格低于MA60 ↓",
        "support":      "Support" if _en else "支撑位",
        "resistance":   "Resistance" if _en else "阻力位",
        "signal_lbl":   "**Signal**" if _en else "**信号**",
        "pa_hdr":       "**Price action** (TA indicators unavailable)" if _en else "**价格行动分析**（仅基于价格，TA 指标暂不可用）",
        "sig_no_ta":    "TA indicators unavailable, price action above for reference" if _en else "技术指标暂缺，以上价格行动供参考",
        "ta_unavail":   (f"*TA data unavailable — retry later or run `/ta {symbol}`*") if _en
                        else f"*TA 数据暂时不可用，稍后重试或运行 `/ta {symbol}`*",
        "ta_hint_fh":   (f"*Enable full TA*: set a free Finnhub key → `/apikey set finnhub <KEY>`"
                         f"  ([finnhub.io](https://finnhub.io/register))") if _en else
                        (f"*启用完整 TA*：配置免费 Finnhub key → `/apikey set finnhub <KEY>`"
                         f"  ([注册](https://finnhub.io/register))"),
        "data_status":  "**Data status**" if _en else "**数据状态**",
        "stale_warn":   "Data may be stale, please retry later" if _en else "数据可能已过期，请稍后重试",
        "missing":      "Missing fields" if _en else "缺少字段",
        "rate_warn":    "Data source rate-limited, will auto-retry" if _en else "数据源请求频率受限，稍后自动重试",
        "timeout_warn": "Data source request timed out" if _en else "数据源请求超时",
        "nodata_warn":  "No data available for this symbol" if _en else "该标的暂无数据",
        "next_hdr":     "**Next steps**" if _en else "**下一步**",
        "team_desc":    "AI multi-factor analysis (fundamental + technical)" if _en else "AI 综合分析（基本面 + 技术面）",
        "ta_desc":      "Full technical indicator chart" if _en else "完整技术指标图表",
        "report_desc":  "Generate institutional research report" if _en else "生成机构级研究报告",
        "backtest_desc":"Backtest momentum strategy" if _en else "回测动量策略",
        "pos_high":     "Upper range" if _en else "日内高位",
        "pos_low":      "Lower range" if _en else "日内低位",
        "pos_mid":      "Mid range" if _en else "日内中段",
        "pos_pct":      "day percentile" if _en else "百分位",
        "swing_high":   "(high volatility)" if _en else "（波动偏大）",
        "flat":         "Flat" if _en else "持平",
        "rising":       "Up" if _en else "上涨",
        "falling":      "Down" if _en else "下跌",
        "strong":       " (sharp)" if _en else "（大幅）",
        "mild":         " (mild)" if _en else "（温和）",
        "day_momentum": "Momentum" if _en else "今日动能",
        "day_pos":      "Price position" if _en else "价格位置",
        "day_swing":    "Day swing" if _en else "日内振幅",
    }

    session_note = _L["after_hours"] if weekday >= 5 else _L["market_open"]

    lines = []
    # ── Header ──
    _header_name = name if name and name.upper() != symbol.upper() else ""
    if _header_name:
        lines.append(f"## {_header_name}  `{symbol}`")
    else:
        lines.append(f"## `{symbol}`")
    lines.append(f"*{data_src} · {_now_str} · {session_note} · {_L['disclaimer']}*")
    lines.append("")

    # ── Price table ──
    _chg_display = chg_str if (chg is not None and abs(chg) >= 0.005) else "—"
    lines.append(f"| {_L['price_hdr']} | {_L['value_hdr']} |")
    lines.append("|------|------|")
    lines.append(f"| {_L['latest']} | **{currency} {price:,.2f}**  `{_chg_display}` |")
    if range_str:
        swing_cell = f"  {_L['swing']} {_swing_pct:.1f}%" if _swing_pct else ""
        lines.append(f"| {_L['day_range']} | {range_str}{swing_cell} |")
    if _mktcap_str:
        lines.append(f"| {_L['mktcap']} | {_mktcap_str} |")
    if _vol_str != "N/A":
        lines.append(f"| {_L['volume']} | {_vol_str} |")

    # ── Technical table ──
    lines.append("")
    lines.append(f"| {_L['ta_hdr']} | {_L['value_hdr']} | {_L['meaning_hdr']} |")
    lines.append("|---------|------|------|")
    if rsi is not None:
        _rsi_meaning = _L["overbought"] if rsi >= 70 else (_L["oversold"] if rsi <= 30 else _L["neutral"])
        lines.append(f"| RSI(14) | {rsi_view} | {_rsi_meaning} |")
    else:
        lines.append("| RSI(14) | — | — |")
    if mhist is not None:
        _macd_meaning = _L["bull_mom"] if mhist > 0 else _L["bear_mom"]
        lines.append(f"| MACD hist | {mhist:.4f} | {_macd_meaning} |")
    else:
        lines.append("| MACD hist | — | — |")
    if ma20 is not None:
        lines.append(f"| MA20 | {currency} {ma20:,.2f} | {_L['above_ma20'] if price > ma20 else _L['below_ma20']} |")
    if ma60 is not None:
        lines.append(f"| MA60 | {currency} {ma60:,.2f} | {_L['above_ma60'] if price > ma60 else _L['below_ma60']} |")
    if support_str:
        lines.append(f"| {_L['support']} | {support_str} | |")
    if resistance_str:
        lines.append(f"| {_L['resistance']} | {resistance_str} | |")

    # ── Price-action lines (rebuild with language-aware labels) ──
    _pa_lines_l10n = []
    if _price_pos is not None:
        _pos_label = _L["pos_high"] if _price_pos >= 70 else (_L["pos_low"] if _price_pos <= 30 else _L["pos_mid"])
        _pa_lines_l10n.append(f"{_L['day_pos']}：{_pos_label}（{_en and 'day' or '日内第'} {_price_pos} {_L['pos_pct']}）")
    if _swing_pct:
        _swing_note = _L["swing_high"] if _swing_pct > 3 else ""
        _pa_lines_l10n.append(f"{_L['day_swing']}：{_swing_pct:.1f}%{_swing_note}")
    if chg is not None and _chg_abs is not None:
        if _chg_abs < 0.01:
            _pa_lines_l10n.append(f"{_L['day_momentum']}：{_L['flat']}")
        else:
            _mo = _L["rising"] if chg > 0 else _L["falling"]
            _strength = _L["strong"] if _chg_abs > 2 else (_L["mild"] if _chg_abs < 0.5 else "")
            _pa_lines_l10n.append(f"{_L['day_momentum']}：{_mo} {_chg_abs:.2f}%{_strength}")

    # ── Signal ──
    lines.append("")
    if _enough_data:
        lines.append(f"{_L['signal_lbl']}：`{signal}` — {signal_str}")
    else:
        lines.append(_L["pa_hdr"])
        for _pal in _pa_lines_l10n:
            lines.append(f"- {_pal}")
        lines.append("")
        lines.append(f"{_L['signal_lbl']}：`{signal}` — {_L['sig_no_ta']}")

    # ── Config hint (show only when TA missing) ──
    if not _enough_data:
        lines.append("")
        if _is_a_share:
            _ak_hint = (f"> **Full TA data**: akshare should be available — retry or run `/ta {symbol}`"
                        if _en else
                        f"> **完整 TA 数据**：akshare 应已可用，若持续失败请重试或运行 `/ta {symbol}`")
            lines.append(_ak_hint)
        elif not _fh_key:
            lines.append(_L["ta_hint_fh"])
        else:
            lines.append(_L["ta_unavail"])

    # ── Data quality — only show when actionable ──
    _quality_missing = _snapshot_quality.get("missing_fields") or []
    _quality_warnings = _snapshot_quality.get("warnings") or []
    _quality_errors = _snapshot_quality.get("errors") or []
    _quality_status = _snapshot_quality.get("status", "")
    _show_quality = _quality_status in ("unavailable", "partial", "stale") or bool(_quality_missing)
    if _snapshot_quality and _show_quality:
        lines.append("")
        lines.append(_L["data_status"])
        if _snapshot_quality.get("stale"):
            lines.append(f"- {_L['stale_warn']}")
        if _quality_missing:
            _missing_map = {"price": "price" if _en else "价格",
                            "volume": "volume" if _en else "成交量",
                            "change": "change %" if _en else "涨跌幅"}
            # Suppress "price" from missing list when we actually have a price to display —
            # it means the primary source (yfinance) failed but a fallback (Finnhub) succeeded.
            _filtered_missing = [
                f for f in _quality_missing
                if not (f == "price" and price is not None and price > 0)
            ]
            _missing_labels = [_missing_map.get(f, f) for f in _filtered_missing]
            if _missing_labels:
                lines.append(f"- {_L['missing']}: {', '.join(_missing_labels)}")
        _user_warnings = []
        for w in (_quality_warnings + _quality_errors)[:2]:
            _w = str(w)
            if "rate" in _w.lower() or "429" in _w.lower() or "too many" in _w.lower():
                _user_warnings.append(_L["rate_warn"])
            elif "timeout" in _w.lower():
                _user_warnings.append(_L["timeout_warn"])
            elif "not found" in _w.lower() or "no data" in _w.lower():
                _user_warnings.append(_L["nodata_warn"])
        for _uw in dict.fromkeys(_user_warnings):
            lines.append(f"- {_uw}")

    # ── Next actions ──
    lines.append("")
    lines.append(_L["next_hdr"])
    lines.append(f"- `/team {symbol}` — {_L['team_desc']}")
    lines.append(f"- `/ta {symbol}` — {_L['ta_desc']}")
    if _is_a_share:
        lines.append(f"- `/report {symbol}` — {_L['report_desc']}")
    else:
        lines.append(f"- `/backtest momentum {symbol} --period 1y` — {_L['backtest_desc']}")

    return {"success": True, "response": "\n".join(lines), "tools_used": ["market_snapshot"]}

