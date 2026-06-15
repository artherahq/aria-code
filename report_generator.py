"""
report_generator.py — 专业研报生成引擎
=======================================
输入: symbol + TeamResult（可选）
输出: 单文件 HTML（内嵌图表 base64）→ 可直接在浏览器打印为 PDF

特性:
  · Bloomberg 风格暗色主题
  · mplfinance K线图 + 成交量（fallback: 收盘价折线）
  · 数据清洗质量报告（来自 data_cleaner.py）
  · 多 Agent 分析卡片
  · 关键财务指标表格
  · 完全离线，无外部 CDN 依赖
"""

from __future__ import annotations

import asyncio
import base64
import html
import io
import logging
import os
import math
import re as _re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from artifacts import create_artifact, write_artifact_metadata, write_artifact_raw_data

try:
    import pandas as pd
except ImportError:
    pd = None  # type: ignore

logger = logging.getLogger(__name__)


def _history_records_to_df(records: List[Dict[str, Any]]):
    import pandas as _pd
    if not records:
        return _pd.DataFrame()
    df = _pd.DataFrame(records)
    if df.empty or "date" not in df.columns:
        return _pd.DataFrame()
    df["date"] = _pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).set_index("date").sort_index()
    rename = {
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            df[col] = 0
        df[col] = _pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["Close"])
    return df[["Open", "High", "Low", "Close", "Volume"]]


def _merge_present(target: Dict[str, Any], source: Dict[str, Any], keys: List[str]) -> None:
    for key in keys:
        value = source.get(key)
        if value not in (None, "", [], {}):
            target[key] = value


def _fetch_report_data_sync(symbol: str) -> Tuple[Any, Any, Dict[str, Any]]:
    """Fetch report data with fallback providers and source diagnostics."""
    from data_cleaner import CleanResult, clean_price_series, get_clean_prices, get_fundamentals
    import pandas as _pd

    provider_chain: List[str] = []
    data_warnings: List[str] = []
    df = _pd.DataFrame()
    clean_result = CleanResult(df, quality_score=0.0)

    try:
        df, clean_result = get_clean_prices(symbol, period="1y")
        if df is not None and not df.empty:
            provider_chain.append("data_cleaner")
    except Exception as exc:
        data_warnings.append(f"data_cleaner prices: {exc}")
        df = _pd.DataFrame()
        clean_result = CleanResult(df, quality_score=0.0)

    fundamentals: Dict[str, Any]
    try:
        fundamentals = get_fundamentals(symbol)
        provider_chain.append("fundamentals")
    except Exception as exc:
        data_warnings.append(f"fundamentals: {exc}")
        fundamentals = {
            "company_name": symbol,
            "symbol": symbol,
            "currency": "CNY" if str(symbol).isdigit() and len(str(symbol)) == 6 else "USD",
        }

    try:
        from packages.aria_services.data import DataService
        bundle = DataService().bundle(symbol, history_days=370, technical_days=120)

        hist = bundle.history
        if (df is None or df.empty) and hist.get("success"):
            fallback_df = _history_records_to_df(hist.get("data") or [])
            if not fallback_df.empty:
                df = fallback_df
                clean_result = clean_price_series(df, symbol)
        elif not hist.get("success"):
            data_warnings.append(hist.get("error") or "history unavailable")

        quote = bundle.quote
        if quote.get("success"):
            _merge_present(
                fundamentals,
                quote,
                ["price", "prev_close", "open", "high", "low", "volume", "turnover",
                 "market_cap", "currency", "name"],
            )
            q_name = quote.get("name")
            cur_name = fundamentals.get("company_name", "")
            # Prefer Chinese name from market data over English fallback from yfinance.
            # Override when: name is missing, is the bare symbol, or is pure ASCII
            # (yfinance fallback) while the quote provides a localized name.
            if q_name and (
                cur_name in (None, "", symbol)
                or (cur_name.isascii() and not q_name.isascii())
            ):
                fundamentals["company_name"] = q_name
        else:
            data_warnings.append(quote.get("error") or "quote unavailable")

        fund = bundle.fundamentals
        if fund.get("success"):
            _merge_present(
                fundamentals,
                fund,
                ["sector", "industry", "market_cap", "pe_ratio", "pe_ttm",
                 "pb_ratio", "pb", "ps_ratio", "roe", "revenue", "net_income",
                 "eps", "dividend_yield", "52w_high", "52w_low", "description"],
            )

        ti = bundle.technical
        if ti.get("success"):
            _merge_present(
                fundamentals,
                ti,
                ["rsi", "macd", "signal", "ma5", "ma10", "ma20", "ma60",
                 "bb_upper", "bb_lower", "price"],
            )
        else:
            data_warnings.append(ti.get("error") or "technical indicators unavailable")

        provider_chain.extend(bundle.provider_chain)
        data_warnings.extend(bundle.warnings)
        data_warnings.extend(bundle.errors)
        if bundle.missing_fields:
            data_warnings.append("missing fields: " + ", ".join(bundle.missing_fields))
        fundamentals["data_status"] = bundle.status
        fundamentals["data_quality"] = bundle.quality
        fundamentals["data_stale"] = bool(bundle.quality.get("stale") if bundle.quality else False)
    except Exception as exc:
        data_warnings.append(f"data_service: {exc}")

    if df is not None and not df.empty:
        try:
            close_series = df["Close"].dropna()
            last_close = float(close_series.iloc[-1])
            fundamentals.setdefault("price", last_close)
            if len(close_series) >= 2:
                fundamentals.setdefault("prev_close", float(close_series.iloc[-2]))
            trailing_year = close_series.tail(252)
            fundamentals.setdefault("52w_high", float(trailing_year.max()))
            fundamentals.setdefault("52w_low", float(trailing_year.min()))
        except Exception:
            pass

    fundamentals["data_provider_chain"] = list(dict.fromkeys(str(p) for p in provider_chain if p))
    fundamentals["data_warnings"] = data_warnings[:6]
    fundamentals.setdefault("company_name", symbol)
    fundamentals.setdefault("symbol", symbol)
    return df, clean_result, fundamentals

# ── Signal styles ─────────────────────────────────────────────────────────────
_SIGNAL_COLOR = {
    "STRONG_BUY": ("#0d3b1e", "#3fb950", "▲▲"),
    "BUY":        ("#0d3b1e", "#3fb950", "▲ "),
    "HOLD":       ("#1f2836", "#79c0ff", "─ "),
    "SELL":       ("#3d1218", "#f85149", "▼ "),
    "STRONG_SELL":("#3d1218", "#f85149", "▼▼"),
}


def _sig_style(signal: str):
    return _SIGNAL_COLOR.get((signal or "HOLD").upper(),
                              ("#1f2836", "#79c0ff", "─ "))


# ── Chart Generation ──────────────────────────────────────────────────────────

def _chart_to_b64(fig) -> str:
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return b64


def generate_price_chart(df, symbol: str, fundamentals: Dict) -> Optional[str]:
    """
    Returns base64-encoded PNG of a dark-theme candlestick chart.
    Falls back to a line chart if mplfinance is unavailable.
    """
    if df is None or df.empty:
        return None

    # Need at minimum Close column
    close_col = next((c for c in df.columns if c.lower() == "close"), None)
    if not close_col:
        return None

    # Ensure DatetimeIndex
    import pandas as _pd
    try:
        if not isinstance(df.index, _pd.DatetimeIndex):
            df.index = _pd.to_datetime(df.index)
    except Exception:
        pass

    # Use last 6 months
    df6 = df.tail(126).copy()

    # ── mplfinance path ────────────────────────────────────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import mplfinance as mpf
        import matplotlib.pyplot as plt

        mc = mpf.make_marketcolors(
            up="#3fb950", down="#f85149", edge="inherit",
            wick={"up": "#3fb950", "down": "#f85149"},
            volume={"up": "#1e4d2b", "down": "#4d1219"},
        )
        style = mpf.make_mpf_style(
            marketcolors=mc,
            facecolor="#0d1117",
            edgecolor="#21262d",
            figcolor="#0d1117",
            gridcolor="#161b22",
            gridstyle="--",
            rc={
                "axes.labelcolor":  "#8b949e",
                "axes.edgecolor":   "#21262d",
                "xtick.color":      "#8b949e",
                "ytick.color":      "#8b949e",
                "font.size":        9,
            },
        )

        has_ohlcv = all(c in df6.columns for c in ("Open", "High", "Low", "Close", "Volume"))
        plot_type = "candle" if has_ohlcv else "line"
        addplots  = []

        if "Close" in df6.columns:
            ma20 = df6["Close"].rolling(20).mean()
            ma50 = df6["Close"].rolling(50).mean()
            if not ma20.dropna().empty:
                addplots.append(mpf.make_addplot(ma20, color="#388bfd", width=1.2,
                                                  label="MA20"))
            if not ma50.dropna().empty:
                addplots.append(mpf.make_addplot(ma50, color="#8957e5", width=1.2,
                                                  label="MA50"))

        kwargs: Dict[str, Any] = dict(
            type=plot_type,
            style=style,
            figsize=(11, 5.5),
            returnfig=True,
            datetime_format="%m/%d",
            xrotation=0,
        )
        if has_ohlcv:
            kwargs["volume"] = True
            kwargs["volume_panel"] = 1
            kwargs["panel_ratios"] = (3, 1)
        if addplots:
            kwargs["addplot"] = addplots

        fig, axes = mpf.plot(df6, **kwargs)

        # Title
        price  = fundamentals.get("price")
        p_str  = f"  ${price:,.2f}" if price else ""
        axes[0].set_title(
            f"{symbol}{p_str}  ·  6-Month Price History",
            color="#c9d1d9", fontsize=11, pad=8,
        )

        return _chart_to_b64(fig)

    except ImportError:
        pass
    except Exception as e:
        logger.debug("[report] mplfinance chart: %s", e)

    # ── matplotlib fallback: line chart ────────────────────────────────────
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(11, 4), facecolor="#0d1117")
        ax.set_facecolor("#0d1117")
        close = df6["Close"] if "Close" in df6.columns else df6.iloc[:, 0]
        ax.plot(df6.index, close, color="#3fb950", linewidth=1.5)

        # MA lines
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        ax.plot(df6.index, ma20, color="#388bfd", linewidth=1.0, alpha=0.8, label="MA20")
        ax.plot(df6.index, ma50, color="#8957e5", linewidth=1.0, alpha=0.8, label="MA50")

        ax.set_title(f"{symbol}  ·  6-Month Close Price",
                     color="#c9d1d9", fontsize=11)
        ax.tick_params(colors="#8b949e")
        ax.spines[:].set_edgecolor("#21262d")
        ax.grid(color="#161b22", linestyle="--", linewidth=0.5)
        ax.legend(facecolor="#161b22", edgecolor="#21262d",
                  labelcolor="#8b949e", fontsize=9)
        fig.tight_layout()
        return _chart_to_b64(fig)

    except Exception as e:
        logger.debug("[report] matplotlib fallback: %s", e)
        return _generate_svg_line_chart(df6, symbol)


def _generate_svg_line_chart(df, symbol: str) -> Optional[str]:
    """Dependency-free inline SVG fallback for environments without chart libs."""
    try:
        if df is None or df.empty:
            return None
        close_col = next((c for c in df.columns if c.lower() == "close"), None)
        if not close_col:
            return None
        values = [float(v) for v in df[close_col].dropna().tail(126).tolist() if math.isfinite(float(v))]
        if len(values) < 2:
            return None
        width, height, pad = 920, 320, 34
        lo, hi = min(values), max(values)
        if math.isclose(lo, hi):
            lo *= 0.99
            hi *= 1.01
        pts = []
        for i, value in enumerate(values):
            x = pad + (width - pad * 2) * i / max(len(values) - 1, 1)
            y = pad + (height - pad * 2) * (1 - (value - lo) / (hi - lo))
            pts.append(f"{x:.1f},{y:.1f}")
        return f"""
<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_e(symbol)} price chart" class="inline-chart">
  <rect x="0" y="0" width="{width}" height="{height}" rx="8" fill="#0d1117"/>
  <line x1="{pad}" y1="{height-pad}" x2="{width-pad}" y2="{height-pad}" stroke="#21262d"/>
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height-pad}" stroke="#21262d"/>
  <polyline points="{" ".join(pts)}" fill="none" stroke="#c08050" stroke-width="3"/>
  <text x="{pad}" y="{pad - 10}" fill="#8b949e" font-size="12">{_e(symbol)} Close</text>
  <text x="{width-pad-150}" y="{pad - 10}" fill="#8b949e" font-size="12">{values[-1]:,.2f}</text>
</svg>"""
    except Exception as e:
        logger.debug("[report] svg fallback: %s", e)
        return None


# ── Number Formatting ─────────────────────────────────────────────────────────

def _fmt(val, precision: int = 2, pct: bool = False,
         currency: str = "", na: str = "—") -> str:
    if val is None:
        return na
    try:
        v = float(val)
    except (TypeError, ValueError):
        return na
    if not math.isfinite(v):
        return na

    if pct:
        return f"{v * 100:.{precision}f}%"
    if currency:
        if abs(v) >= 1e12:
            return f"{currency}{v/1e12:.2f}T"
        if abs(v) >= 1e9:
            return f"{currency}{v/1e9:.2f}B"
        if abs(v) >= 1e6:
            return f"{currency}{v/1e6:.2f}M"
        return f"{currency}{v:,.{precision}f}"
    return f"{v:,.{precision}f}"


def _fmt_metric(val, precision: int = 2, pct: bool = False,
                currency: str = "", zero_is_missing: bool = False) -> str:
    try:
        v = float(val)
        if zero_is_missing and abs(v) < 1e-12:
            return "—"
    except (TypeError, ValueError):
        pass
    return _fmt(val, precision=precision, pct=pct, currency=currency)


def _color_val(val, good_positive: bool = True) -> str:
    """Return HTML color for a numeric value."""
    try:
        v = float(val)
        if v > 0:
            return "#3fb950" if good_positive else "#f85149"
        if v < 0:
            return "#f85149" if good_positive else "#3fb950"
    except (TypeError, ValueError):
        pass
    return "#8b949e"


# ── HTML Builder ──────────────────────────────────────────────────────────────

def _e(text: str) -> str:
    """HTML-escape user-facing strings."""
    return html.escape(str(text or ""))


def _md_to_html(text: str, max_chars: int = 0) -> str:
    """Convert LLM-generated markdown to safe HTML for embedding in reports.

    Order of operations: escape → fix &lt;br&gt; → tables → inline styles → newlines.
    """
    if not text:
        return ""
    if max_chars and len(text) > max_chars:
        text = text[:max_chars] + "…"

    # 1. HTML-escape all user content first (XSS prevention)
    t = html.escape(text)

    # 2. LLMs sometimes emit literal &lt;br&gt; inside markdown — convert to newline
    t = t.replace("&lt;br&gt;", "\n")

    # 3. Markdown tables → HTML tables
    # Pre-join continuation lines: table cells may contain \n (from &lt;br&gt;).
    # A non-pipe line immediately following a pipe line is part of that row's cell.
    raw_lines = t.split("\n")
    merged: List[str] = []
    for raw_line in raw_lines:
        if merged and merged[-1].lstrip().startswith("|") and raw_line and not raw_line.lstrip().startswith("|"):
            merged[-1] += "<br>" + raw_line
        else:
            merged.append(raw_line)

    out_lines: List[str] = []
    i = 0
    while i < len(merged):
        line = merged[i]
        # Detect header row: has | and next line is a separator (|---|---|)
        if (
            "|" in line
            and i + 1 < len(merged)
            and _re.match(r"^\s*\|[\s\-:|]+\|", merged[i + 1])
        ):
            header_cells = [c.strip() for c in line.strip("|").split("|")]
            th = "".join(f"<th>{c}</th>" for c in header_cells)
            i += 2  # skip header + separator
            tbody_rows = ""
            while i < len(merged) and "|" in merged[i] and merged[i].lstrip().startswith("|"):
                cells = [c.strip() for c in merged[i].strip("|").split("|")]
                tbody_rows += "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"
                i += 1
            out_lines.append(
                f'<table class="md-table">'
                f"<thead><tr>{th}</tr></thead>"
                f"<tbody>{tbody_rows}</tbody>"
                f"</table>"
            )
        else:
            out_lines.append(line)
            i += 1
    t = "\n".join(out_lines)

    # 4. Bold: **text** or __text__
    t = _re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t, flags=_re.DOTALL)
    t = _re.sub(r"__(.+?)__", r"<strong>\1</strong>", t, flags=_re.DOTALL)

    # 5. Italic: *text* (not **)
    t = _re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", t)

    # 6. Inline code
    t = _re.sub(r"`([^`]+)`", r"<code>\1</code>", t)

    # 7. H2/H3 headers (## Heading → styled paragraph)
    t = _re.sub(r"^#{1,3}\s+(.+)$", r'<strong class="md-h">\1</strong>', t, flags=_re.MULTILINE)

    # 8. Horizontal rules (---  or  ***)
    t = _re.sub(r"\n[-*]{3,}\n", '\n<hr class="md-hr">\n', t)

    # 9. Newlines → <br> (done last so table/hr markup isn't broken)
    t = t.replace("\n", "<br>\n")

    return t


def _kpi_card(label: str, value: str, sub: str = "", color: str = "#e6edf3") -> str:
    return f"""
    <div class="kpi-card">
        <div class="kpi-label">{_e(label)}</div>
        <div class="kpi-value" style="color:{color}">{_e(value)}</div>
        {f'<div class="kpi-sub">{_e(sub)}</div>' if sub else ""}
    </div>"""


def _agent_card(agent_name: str, signal: str, confidence: float,
                analysis: str, key_points: List[str]) -> str:
    bg, accent, icon = _sig_style(signal)
    kps = "".join(f'<li>{_md_to_html(kp)}</li>' for kp in (key_points or [])[:4])
    analysis_html = _md_to_html(analysis or "", max_chars=3000)
    return f"""
    <div class="agent-card" style="border-color:{accent}40;">
        <div class="agent-header">
            <span class="agent-name">{_e(agent_name.upper())}</span>
            <span class="agent-signal" style="color:{accent};background:{bg};">
                {icon} {_e(signal)}
            </span>
            <span class="agent-conf" style="color:{accent};">{confidence:.0%}</span>
        </div>
        {f'<ul class="agent-kps">{kps}</ul>' if kps else ""}
        {f'<div class="agent-analysis">{analysis_html}</div>' if analysis_html else ""}
    </div>"""


def _metrics_row(label: str, value: str, highlight: bool = False) -> str:
    style = "background:#161b22;" if highlight else ""
    return (f'<tr style="{style}">'
            f'<td class="metric-label">{_e(label)}</td>'
            f'<td class="metric-value">{_e(value)}</td>'
            f'</tr>')


def _build_html(
    symbol:       str,
    fundamentals: Dict,
    price_chart:  Optional[str],
    team_result,
    clean_result,
) -> str:
    fund   = fundamentals
    name   = fund.get("company_name", symbol)
    cur    = fund.get("currency", "USD")
    cur_sym = "¥" if cur in ("CNY","CNH","HKD") else "$"
    price  = fund.get("price")
    prev   = fund.get("prev_close")

    # Price change
    if price and prev and prev > 0:
        chg = price - prev
        chg_pct = chg / prev * 100
        if abs(chg) < 0.001:
            chg_str = "—"
            chg_color = "#8b949e"
        else:
            chg_str = f"{chg:+.2f} ({chg_pct:+.2f}%)"
            chg_color = "#3fb950" if chg >= 0 else "#f85149"
    else:
        chg_str = "—"
        chg_color = "#8b949e"

    # Final signal from team result
    final_signal = "HOLD"
    confidence   = 0.0
    synthesis    = ""
    agent_cards  = ""

    if team_result:
        final_signal = team_result.final_signal or "HOLD"
        confidence   = team_result.confidence or 0.0
        synthesis    = team_result.synthesis or ""
        cards = []
        for r in (team_result.results or []):
            if not r or r.agent == "debate":
                continue
            cards.append(_agent_card(
                r.agent, r.signal or "HOLD", r.confidence,
                r.analysis, r.key_points,
            ))
        agent_cards = "\n".join(cards)

    sig_bg, sig_accent, sig_icon = _sig_style(final_signal)

    # KPI cards
    mkt_cap_str = _fmt(fund.get("market_cap"), currency=cur_sym)
    pe_str      = _fmt_metric(fund.get("pe_ratio"), precision=1, zero_is_missing=True)
    pb_str      = _fmt_metric(fund.get("pb_ratio"), precision=2, zero_is_missing=True)
    beta_str    = _fmt(fund.get("beta"), precision=2)
    w52_high    = _fmt(fund.get("52w_high"), precision=2, currency=cur_sym)
    w52_low     = _fmt(fund.get("52w_low"),  precision=2, currency=cur_sym)

    kpis = "".join([
        _kpi_card("当前价格",
                  _fmt(price, precision=2, currency=cur_sym),
                  chg_str, chg_color),
        _kpi_card("市值", mkt_cap_str),
        _kpi_card("市盈率 (TTM)", pe_str),
        _kpi_card("市净率", pb_str),
        _kpi_card("Beta", beta_str),
        _kpi_card("52周区间", f"{w52_low} – {w52_high}"),
    ])

    # Metrics table — two column groups
    roe   = _fmt_metric(fund.get("roe"), precision=1, pct=True, zero_is_missing=True)
    roa   = _fmt_metric(fund.get("roa"), precision=1, pct=True, zero_is_missing=True)
    gm    = _fmt_metric(fund.get("gross_margin"), precision=1, pct=True, zero_is_missing=True)
    om    = _fmt_metric(fund.get("operating_margin"), precision=1, pct=True, zero_is_missing=True)
    nm    = _fmt_metric(fund.get("net_margin"), precision=1, pct=True, zero_is_missing=True)
    rev_g = _fmt_metric(fund.get("revenue_growth"), precision=1, pct=True, zero_is_missing=True)
    de    = _fmt_metric(fund.get("debt_equity"), precision=2, zero_is_missing=True)
    cr    = _fmt_metric(fund.get("current_ratio"), precision=2, zero_is_missing=True)
    dy    = _fmt_metric(fund.get("dividend_yield"), precision=2, pct=True, zero_is_missing=True)
    at    = _fmt(fund.get("analyst_target"), precision=2, currency=cur_sym)
    ac    = fund.get("analyst_count") or "—"
    rec   = (fund.get("recommendation") or "").upper().replace("_"," ")
    rsi   = _fmt_metric(fund.get("rsi"), precision=1)
    macd  = _fmt_metric(fund.get("macd"), precision=3)
    signal = _fmt_metric(fund.get("signal"), precision=3)
    ma20  = _fmt(fund.get("ma20"), precision=2, currency=cur_sym)
    ma60  = _fmt(fund.get("ma60"), precision=2, currency=cur_sym)
    bb_upper = _fmt(fund.get("bb_upper"), precision=2, currency=cur_sym)
    bb_lower = _fmt(fund.get("bb_lower"), precision=2, currency=cur_sym)

    metrics_rows = "".join([
        _metrics_row("收益/盈利",   "",          highlight=True),
        _metrics_row("ROE",         roe),
        _metrics_row("ROA",         roa),
        _metrics_row("毛利率",      gm),
        _metrics_row("营业利润率",  om),
        _metrics_row("净利率",      nm),
        _metrics_row("收入增速",    rev_g),
        _metrics_row("财务健康",    "",          highlight=True),
        _metrics_row("负债/权益",   de),
        _metrics_row("流动比率",    cr),
        _metrics_row("股息率",      dy),
        _metrics_row("分析师评级",  "",          highlight=True),
        _metrics_row("评级",        rec or "—"),
        _metrics_row("目标价",      at),
        _metrics_row("覆盖分析师",  str(ac)),
        _metrics_row("技术指标",    "",          highlight=True),
        _metrics_row("RSI(14)",     rsi),
        _metrics_row("MACD",        macd),
        _metrics_row("Signal",      signal),
        _metrics_row("MA20",        ma20),
        _metrics_row("MA60",        ma60),
        _metrics_row("布林上轨",    bb_upper),
        _metrics_row("布林下轨",    bb_lower),
    ])

    # Chart
    chart_section = ""
    if price_chart and price_chart.lstrip().startswith("<svg"):
        chart_section = price_chart
    elif price_chart:
        chart_section = (
            f'<img src="data:image/png;base64,{price_chart}" '
            f'style="width:100%;border-radius:6px;" alt="Price Chart"/>'
        )
    else:
        chart_section = '<p class="no-data">图表暂不可用：历史价格数据不足或本地绘图库不可用。</p>'

    # Synthesis
    synthesis_html = ""
    if synthesis:
        synthesis_html = f"""
        <section class="card">
            <div class="card-header">综合结论</div>
            <div class="card-body synthesis-body">{_md_to_html(synthesis)}</div>
            <div class="synthesis-footer">
                <span style="color:{sig_accent}">{sig_icon} {_e(final_signal)}</span>
                &nbsp;&nbsp;置信度&nbsp;{confidence:.0%}
                &nbsp;&nbsp;|&nbsp;&nbsp;耗时&nbsp;{getattr(team_result,"elapsed_sec",0):.1f}s
            </div>
        </section>"""

    # Agent grid
    agent_section = ""
    if agent_cards:
        agent_section = f"""
        <section class="card">
            <div class="card-header">多 Agent 研究团队</div>
            <div class="agent-grid">{agent_cards}</div>
        </section>"""

    # Data quality section
    quality_html = ""
    if clean_result:
        q = clean_result
        if getattr(q, "df", None) is not None and not q.df.empty:
            qc = "#3fb950" if q.quality_score >= 90 else (
                 "#d29922" if q.quality_score >= 70 else "#f85149")
            quality_html = f"""
            <section class="quality-bar">
                <span>数据质量</span>
                <span class="quality-score" style="color:{qc}">
                    {q.quality_score:.0f}/100
                </span>
                <span class="quality-detail">
                    {q.outlier_count} 异常 · {q.fill_count} 填充 ·
                    {q.real_gap_days} 缺口天
                </span>
            </section>"""
        else:
            quality_html = """
            <section class="quality-bar">
                <span>数据质量</span>
                <span class="quality-score" style="color:#d29922">数据不足</span>
                <span class="quality-detail">未取得足够历史行情，指标和图表已降级显示</span>
            </section>"""

    # Description
    desc_html = ""
    desc = (fund.get("description") or "").strip()
    if desc:
        desc_html = f"""
        <section class="card">
            <div class="card-header">公司简介</div>
            <div class="card-body" style="font-size:13px;color:#8b949e;line-height:1.7">
                {_e(desc)}
            </div>
        </section>"""

    ts     = datetime.now().strftime("%Y-%m-%d %H:%M")
    sector = fund.get("sector", "")
    exch   = fund.get("exchange", "")
    sub    = " · ".join(filter(None, [sector, exch, cur]))
    source_chain = fund.get("data_provider_chain") or []
    source_text = " → ".join(source_chain) if source_chain else "公开市场数据源"
    warnings = fund.get("data_warnings") or []
    warning_text = ""
    if warnings:
        warning_text = " · 数据降级: " + "；".join(str(w)[:120] for w in warnings[:3])

    return _HTML_TEMPLATE.replace("{{CSS}}", _CSS) \
        .replace("{{SYMBOL}}",       _e(symbol)) \
        .replace("{{COMPANY_NAME}}", _e(name)) \
        .replace("{{SUBTITLE}}",     _e(sub)) \
        .replace("{{TIMESTAMP}}",    _e(ts)) \
        .replace("{{SIGNAL}}",       _e(final_signal)) \
        .replace("{{SIGNAL_BG}}",    sig_bg) \
        .replace("{{SIGNAL_ACCENT}}", sig_accent) \
        .replace("{{SIGNAL_ICON}}",  sig_icon) \
        .replace("{{CONFIDENCE}}",   f"{confidence:.0%}") \
        .replace("{{KPI_CARDS}}",    kpis) \
        .replace("{{CHART_SECTION}}", chart_section) \
        .replace("{{METRICS_ROWS}}", metrics_rows) \
        .replace("{{AGENT_SECTION}}", agent_section) \
        .replace("{{SYNTHESIS}}",    synthesis_html) \
        .replace("{{DESC_SECTION}}", desc_html) \
        .replace("{{QUALITY_BAR}}",  quality_html) \
        .replace("{{DATA_SOURCE}}",  _e(source_text + warning_text))


# ── Public API ────────────────────────────────────────────────────────────────

async def generate_report(
    symbol:      str,
    team_result  = None,
    output_dir:  Optional[Path] = None,
) -> Optional[Path]:
    """
    Main entry point.

    1. Fetch + clean price data via data_cleaner
    2. Fetch fundamentals
    3. Generate price chart
    4. Render HTML with all data + agent analysis
    5. Write to output_dir / {SYMBOL}_report_{timestamp}.html
    """
    import pandas as _pd

    ts_dt = datetime.now()
    if output_dir:
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = None
    else:
        artifact = create_artifact("reports/market", symbol, f"{symbol}_market_report", ".html", timestamp=ts_dt)
        out_dir = artifact.directory

    logger.info("[report] generating %s", symbol)

    # Fetch data (run in thread to avoid blocking the event loop)
    loop  = asyncio.get_event_loop()

    try:
        df, clean_result, fundamentals = await loop.run_in_executor(
            None, lambda: _fetch_report_data_sync(symbol)
        )
    except Exception as e:
        logger.error("[report] data fetch failed: %s", e)
        df            = _pd.DataFrame()
        clean_result  = None
        import re as _re
        _is_ashare = bool(_re.match(r"^[036]\d{5}$", symbol))
        fundamentals  = {"company_name": symbol, "symbol": symbol,
                         "currency": "CNY" if _is_ashare else "USD"}

    # Generate chart (CPU-bound, run in thread)
    price_chart = None
    if not df.empty:
        try:
            price_chart = await loop.run_in_executor(
                None, lambda: generate_price_chart(df, symbol, fundamentals)
            )
        except Exception as e:
            logger.debug("[report] chart failed: %s", e)

    # Render
    try:
        report_html = _build_html(symbol, fundamentals, price_chart,
                                   team_result, clean_result)
    except Exception as e:
        logger.error("[report] render failed: %s", e)
        return None

    ts    = ts_dt.strftime("%Y%m%d_%H%M")
    out_f = artifact.path if artifact else out_dir / f"{symbol}_report_{ts}.html"
    out_f.write_text(report_html, encoding="utf-8")

    if artifact:
        missing_fields = [
            key for key in ("price", "market_cap", "pe_ratio", "pb_ratio", "roe", "rsi", "macd")
            if fundamentals.get(key) in (None, "", 0)
        ]
        status = "complete" if not df.empty and fundamentals.get("price") else "data_unavailable"
        if status == "complete" and missing_fields:
            status = "partial"
        provider_chain = fundamentals.get("data_provider_chain") or []
        warnings = fundamentals.get("data_warnings") or []
        data_quality = fundamentals.get("data_quality") or {}
        write_artifact_metadata(artifact, {
            "kind": "market_report",
            "status": data_quality.get("status") or status,
            "symbol": symbol,
            "created_at": ts_dt.isoformat(timespec="seconds"),
            "data": {
                "provider_chain": provider_chain,
                "warnings": warnings,
                "errors": data_quality.get("errors") or [],
                "stale": bool(data_quality.get("stale", False)),
                "quality": data_quality,
                "missing_fields": missing_fields,
                "rows": int(len(df)) if df is not None else 0,
                "quality_score": getattr(clean_result, "quality_score", None),
                "chart_rendered": bool(price_chart),
            },
            "model": {
                "team_result": bool(team_result),
            },
        })
        raw_records = []
        try:
            raw_records = df.reset_index().tail(370).to_dict(orient="records") if df is not None and not df.empty else []
        except Exception:
            raw_records = []
        write_artifact_raw_data(artifact, {
            "symbol": symbol,
            "fundamentals": fundamentals,
            "prices": raw_records,
        })

    logger.info("[report] saved: %s", out_f)
    return out_f


# ── PDF Export ────────────────────────────────────────────────────────────────

def export_pdf(html_path: Path) -> Optional[Path]:
    """
    Convert an HTML report to PDF alongside the source file.
    Tries weasyprint (pure Python) first, then wkhtmltopdf binary.
    Returns the PDF path on success, None if neither tool is available.
    """
    pdf_path = html_path.with_suffix(".pdf")

    try:
        import weasyprint
        weasyprint.HTML(filename=str(html_path)).write_pdf(str(pdf_path))
        logger.info("[report] pdf via weasyprint: %s", pdf_path)
        return pdf_path
    except ImportError:
        pass
    except Exception as e:
        logger.debug("[report] weasyprint failed: %s", e)

    import shutil, subprocess as _sp
    if shutil.which("wkhtmltopdf"):
        try:
            r = _sp.run(
                ["wkhtmltopdf", "--quiet", "--print-media-type",
                 str(html_path), str(pdf_path)],
                capture_output=True, timeout=60,
            )
            if r.returncode == 0 and pdf_path.exists():
                logger.info("[report] pdf via wkhtmltopdf: %s", pdf_path)
                return pdf_path
        except Exception as e:
            logger.debug("[report] wkhtmltopdf failed: %s", e)

    return None


# ── Reports Index ─────────────────────────────────────────────────────────────

def update_reports_index(reports_root: Path) -> Optional[Path]:
    """
    Scan reports_root recursively for *_report_*.html files and write index.html.
    Returns the index path on success.
    """
    import re as _ri

    index_path = reports_root / "index.html"
    entries: List[Dict] = []

    for f in sorted(reports_root.rglob("*_report_*.html"), reverse=True):
        if f.name == "index.html":
            continue
        m = _ri.match(r"^(.+?)_report_(\d{8})_(\d{4})\.html$", f.name)
        if not m:
            continue
        sym, ds, ts_ = m.group(1), m.group(2), m.group(3)
        dt_str = f"{ds[:4]}-{ds[4:6]}-{ds[6:8]} {ts_[:2]}:{ts_[2:]}"
        size_kb = max(1, f.stat().st_size // 1024)

        signal = "HOLD"
        try:
            snip = f.read_text(encoding="utf-8", errors="ignore")[500:3000]
            sm = _ri.search(r'\b(STRONG_BUY|STRONG_SELL|BUY|SELL|HOLD)\b', snip)
            if sm:
                signal = sm.group(1)
        except Exception:
            pass

        try:
            rel = f.relative_to(reports_root)
        except ValueError:
            rel = f.name
        entries.append({"symbol": sym.upper(), "datetime": dt_str,
                        "signal": signal, "size_kb": size_kb,
                        "href": str(rel).replace("\\", "/")})

    _SIG_COLOR = {"STRONG_BUY": "#3fb950", "BUY": "#3fb950",
                  "HOLD": "#79c0ff", "SELL": "#f85149", "STRONG_SELL": "#f85149"}

    rows_html = ""
    for e in entries:
        sc = _SIG_COLOR.get(e["signal"], "#8b949e")
        rows_html += (
            f'<tr>'
            f'<td><a href="{html.escape(e["href"])}" class="sym">'
            f'{html.escape(e["symbol"])}</a></td>'
            f'<td style="color:{sc};font-weight:700">{e["signal"]}</td>'
            f'<td class="dim">{html.escape(e["datetime"])}</td>'
            f'<td class="dim">{e["size_kb"]}KB</td>'
            f'</tr>\n'
        )

    idx_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Aria Code — 研报索引</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#010409;color:#e6edf3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      font-size:14px;padding:28px 24px;max-width:900px;margin:0 auto}}
h1{{font-size:22px;font-weight:700;margin-bottom:4px}}
.sub{{color:#8b949e;font-size:12px;margin-bottom:24px}}
table{{width:100%;border-collapse:collapse}}
th{{text-align:left;padding:8px 12px;border-bottom:2px solid #30363d;
    color:#8b949e;font-size:11px;letter-spacing:.6px;text-transform:uppercase}}
td{{padding:9px 12px;border-bottom:1px solid #21262d}}
tr:hover td{{background:#161b22}}
a.sym{{color:#58a6ff;font-weight:700;text-decoration:none;font-size:15px}}
a.sym:hover{{text-decoration:underline}}
.dim{{color:#8b949e;font-size:12px}}
</style>
</head>
<body>
<h1>Aria Code 研报索引</h1>
<p class="sub">{len(entries)} 份报告 · 更新于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
<table>
<thead><tr><th>标的</th><th>信号</th><th>生成时间</th><th>大小</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
</body>
</html>"""

    index_path.write_text(idx_html, encoding="utf-8")
    logger.info("[report] index updated: %s (%d reports)", index_path, len(entries))
    return index_path


# ── HTML Template & CSS ───────────────────────────────────────────────────────

_CSS = """
:root {
    --bg0:     #010409;
    --bg1:     #0d1117;
    --bg2:     #161b22;
    --bg3:     #21262d;
    --text1:   #e6edf3;
    --text2:   #8b949e;
    --green:   #3fb950;
    --red:     #f85149;
    --blue:    #388bfd;
    --purple:  #8957e5;
    --orange:  #f0883e;
    --border:  #21262d;
    --radius:  8px;
}
* { box-sizing:border-box; margin:0; padding:0; }
body {
    background: var(--bg0);
    color: var(--text1);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Helvetica Neue", sans-serif;
    font-size: 14px;
    line-height: 1.6;
    padding: 28px 24px;
    max-width: 1180px;
    margin: 0 auto;
}
/* ── Header ── */
.report-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    border-bottom: 1px solid var(--border);
    padding-bottom: 18px;
    margin-bottom: 20px;
    flex-wrap: wrap;
    gap: 12px;
}
.header-left h1 {
    font-size: 26px;
    font-weight: 700;
    letter-spacing: -0.5px;
    color: var(--text1);
}
.header-left h1 .ticker {
    color: var(--green);
    margin-right: 10px;
    font-size: 28px;
}
.header-left .subtitle {
    color: var(--text2);
    font-size: 12px;
    margin-top: 4px;
}
.signal-badge {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 10px 20px;
    border-radius: var(--radius);
    font-size: 18px;
    font-weight: 700;
    letter-spacing: 1px;
    border: 1px solid;
}
.report-meta {
    color: var(--text2);
    font-size: 12px;
    text-align: right;
}
/* ── KPI strip ── */
.kpi-strip {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 20px;
}
.kpi-card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 12px 16px;
    flex: 1;
    min-width: 130px;
}
.kpi-label  { font-size:11px; color:var(--text2); margin-bottom:4px; }
.kpi-value  { font-size:18px; font-weight:700; color:var(--text1); }
.kpi-sub    { font-size:11px; color:var(--text2); margin-top:3px; }
/* ── Cards ── */
.card {
    background: var(--bg1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    margin-bottom: 16px;
    overflow: hidden;
}
.card-header {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 10px 16px;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.8px;
    text-transform: uppercase;
    color: var(--text2);
}
.card-body {
    padding: 16px;
}
/* ── Chart ── */
.chart-card {
    background: var(--bg1);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 4px;
    margin-bottom: 16px;
}
.no-data {
    color: var(--text2);
    font-style: italic;
    padding: 20px;
    text-align: center;
}
/* ── Two-column layout ── */
.two-col {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    margin-bottom: 16px;
}
@media (max-width: 700px) { .two-col { grid-template-columns: 1fr; } }
/* ── Metrics table ── */
.metrics-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
}
.metrics-table td {
    padding: 6px 12px;
    border-bottom: 1px solid var(--bg3);
}
.metric-label { color: var(--text2); }
.metric-value { color: var(--text1); text-align: right; font-weight: 500; }
/* ── Agent cards ── */
.agent-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
    padding: 14px;
}
.agent-card {
    background: var(--bg2);
    border: 1px solid;
    border-radius: var(--radius);
    padding: 12px;
}
.agent-header {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 8px;
}
.agent-name   { font-weight:700; font-size:12px; color:var(--text2);
                letter-spacing:0.8px; text-transform:uppercase; }
.agent-signal { font-size:12px; font-weight:700; padding:2px 8px;
                border-radius:4px; }
.agent-conf   { font-size:12px; font-weight:600; margin-left:auto; }
.agent-kps    { padding-left:16px; margin-bottom:8px; }
.agent-kps li { font-size:12px; color:var(--text2); margin-bottom:3px; }
.agent-analysis { font-size:12px; color:var(--text2);
                  border-top:1px solid var(--bg3); padding-top:8px;
                  margin-top:4px; line-height:1.65; }
.inline-chart { width:100%; height:auto; border-radius:6px; display:block; }
/* ── Synthesis ── */
.synthesis-body {
    font-size: 14px;
    line-height: 1.75;
    color: var(--text1);
}
/* ── Markdown elements ── */
.md-table { width:100%; border-collapse:collapse; font-size:12px;
            margin:8px 0; border:1px solid var(--bg3); }
.md-table th { background:var(--bg3); color:var(--text2); padding:5px 8px;
               text-align:left; font-weight:600; border:1px solid var(--border); }
.md-table td { padding:4px 8px; border:1px solid var(--bg3); color:var(--text2); }
.md-table tr:nth-child(even) { background:var(--bg2); }
.md-h { display:block; color:var(--text1); margin:8px 0 4px; font-size:13px; }
.md-hr { border:none; border-top:1px solid var(--border); margin:8px 0; }
code { background:var(--bg3); color:var(--orange); padding:1px 4px;
       border-radius:3px; font-size:11px; font-family:monospace; }
.synthesis-footer {
    border-top: 1px solid var(--border);
    padding: 10px 16px;
    font-size: 12px;
    color: var(--text2);
    display: flex;
    gap: 16px;
    align-items: center;
}
/* ── Quality bar ── */
.quality-bar {
    display: flex;
    align-items: center;
    gap: 14px;
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    padding: 10px 16px;
    font-size: 12px;
    color: var(--text2);
    margin-bottom: 16px;
}
.quality-score { font-size: 18px; font-weight: 700; }
.quality-detail { color: var(--text2); font-size: 11px; }
/* ── Footer ── */
.report-footer {
    margin-top: 28px;
    padding-top: 14px;
    border-top: 1px solid var(--border);
    font-size: 11px;
    color: var(--text2);
    line-height: 1.8;
}
@media print {
    body { background:#fff; color:#000; padding:16px; }
    .signal-badge, .agent-card { border-color:#ccc !important; }
    .card, .kpi-card { background:#f8f8f8 !important; border-color:#ddd; }
}
"""

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{SYMBOL}} — Aria Research Report</title>
<style>{{CSS}}</style>
</head>
<body>

<!-- Header -->
<div class="report-header">
  <div class="header-left">
    <h1><span class="ticker">{{SYMBOL}}</span>{{COMPANY_NAME}}</h1>
    <div class="subtitle">{{SUBTITLE}}</div>
  </div>
  <div>
    <div class="signal-badge"
         style="background:{{SIGNAL_BG}};color:{{SIGNAL_ACCENT}};border-color:{{SIGNAL_ACCENT}}40;">
      {{SIGNAL_ICON}} {{SIGNAL}} &nbsp; {{CONFIDENCE}}
    </div>
    <div class="report-meta" style="margin-top:8px;">
      Aria Code Research<br>{{TIMESTAMP}}
    </div>
  </div>
</div>

<!-- KPI Strip -->
<div class="kpi-strip">
{{KPI_CARDS}}
</div>

<!-- Quality Bar -->
{{QUALITY_BAR}}

<!-- Price Chart -->
<div class="chart-card">
{{CHART_SECTION}}
</div>

<!-- Two-column: Agent analysis + Metrics table -->
<div class="two-col">
  <div>
    {{AGENT_SECTION}}
  </div>
  <div>
    <section class="card">
      <div class="card-header">关键财务指标</div>
      <table class="metrics-table">
        <tbody>
{{METRICS_ROWS}}
        </tbody>
      </table>
    </section>
    {{DESC_SECTION}}
  </div>
</div>

<!-- Synthesis -->
{{SYNTHESIS}}

<!-- Footer -->
<div class="report-footer">
  <strong>免责声明</strong>：本报告由 Aria Code AI 系统自动生成，仅供参考，不构成任何投资建议或买卖推荐。
  数据源：{{DATA_SOURCE}}。存在延迟，请以交易所官方数据为准。
  投资有风险，入市需谨慎。&nbsp;·&nbsp; Aria Code &nbsp;·&nbsp; {{TIMESTAMP}}
</div>

</body>
</html>"""
