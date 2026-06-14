"""Stock chart analysis handlers extracted from aria_cli.py."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Callable


def _fmt_num(value, digits: int = 2, prefix: str = "") -> str:
    try:
        if value is None or (hasattr(value, "__class__") and str(value) == "nan"):
            return "N/A"
        return f"{prefix}{float(value):,.{digits}f}"
    except Exception:
        return "N/A"


def _fmt_int(value) -> str:
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return "N/A"


def handle_stock_chart_analysis_direct(symbol: str, period: str = "1y") -> dict:
    """直接调用图表逻辑（不经过消息解析）"""
    import html as _html
    try:
        import pandas as _pd
        import yfinance as _yf
    except Exception as exc:
        return {"success": False, "error": f"缺少依赖: {exc}"}

    # Normalize period aliases
    _PERIOD_MAP = {
        "1m": "1mo", "3m": "3mo", "6m": "6mo",
        "1y": "1y", "2y": "2y", "3y": "3y", "5y": "5y",
        "ytd": "ytd", "max": "max",
    }
    period = _PERIOD_MAP.get(period.lower(), period)

    ticker = _yf.Ticker(symbol)
    try:
        hist = ticker.history(period=period, interval="1d", auto_adjust=False)
    except Exception:
        hist = None

    if hist is None or hist.empty:
        return {"success": False, "error": f"无法获取 {symbol} 行情数据"}

    hist = hist.dropna(subset=["Close"]).copy()
    hist["MA20"] = hist["Close"].rolling(20).mean()
    hist["MA50"] = hist["Close"].rolling(50).mean()
    delta = hist["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    hist["RSI14"] = 100 - (100 / (1 + gain / loss.replace(0, _pd.NA)))
    ema12 = hist["Close"].ewm(span=12, adjust=False).mean()
    ema26 = hist["Close"].ewm(span=26, adjust=False).mean()
    hist["MACD"]        = ema12 - ema26
    hist["MACD_SIGNAL"] = hist["MACD"].ewm(span=9, adjust=False).mean()

    last       = hist.iloc[-1]
    last_close = float(last["Close"])
    info = {}
    try:
        info = ticker.get_info() or {}
    except Exception:
        pass

    name     = info.get("longName") or info.get("shortName") or symbol
    currency = info.get("currency", "USD")

    safe_sym = re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)
    from artifacts import create_artifact, write_artifact_metadata, write_artifact_raw_data
    _artifact = create_artifact("reports/stock-charts", symbol, f"{safe_sym}_chart", ".html")
    out_file = _artifact.path

    x        = [idx.strftime("%Y-%m-%d") for idx in hist.index]
    close_v  = [None if _pd.isna(v) else round(float(v), 4) for v in hist["Close"]]
    ma20_v   = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MA20"]]
    ma50_v   = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MA50"]]
    rsi_v    = [None if _pd.isna(v) else round(float(v), 1) for v in hist["RSI14"]]
    macd_v   = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MACD"]]
    macd_s_v = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MACD_SIGNAL"]]

    rsi14  = float(last["RSI14"]) if _pd.notna(last.get("RSI14")) else None
    ma20   = float(last["MA20"]) if _pd.notna(last.get("MA20")) else None
    ma50   = float(last["MA50"]) if _pd.notna(last.get("MA50")) else None
    macd_l = float(last["MACD"]) if _pd.notna(last.get("MACD")) else None
    macd_s = float(last["MACD_SIGNAL"]) if _pd.notna(last.get("MACD_SIGNAL")) else None

    trend    = ("偏多" if ma20 and ma50 and last_close > ma20 > ma50 else
                "偏空" if ma20 and ma50 and last_close < ma20 < ma50 else "震荡")
    rsi_view = ("超买" if rsi14 and rsi14 >= 70 else "超卖" if rsi14 and rsi14 <= 30 else "中性")
    momentum = "MACD偏多" if macd_l and macd_s and macd_l > macd_s else "MACD偏弱"

    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(name)} 分析图表</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body{{margin:0;font-family:-apple-system,sans-serif;background:#f7f8fa;color:#17202a}}
  main{{max-width:1100px;margin:0 auto;padding:24px}}
  h1{{margin:0 0 4px;font-size:24px}} .meta{{color:#667085;font-size:13px;margin-bottom:16px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin:12px 0}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px}}
  .lbl{{color:#667085;font-size:11px}} .val{{font-size:17px;font-weight:650;margin-top:3px}}
  .green{{color:#16a34a}} .red{{color:#dc2626}} .note{{color:#9ca3af;font-size:12px;margin-top:12px}}
</style></head>
<body><main>
<h1>{_html.escape(name)} ({_html.escape(symbol)})</h1>
<p class="meta">生成时间: {datetime.now():%Y-%m-%d %H:%M} | 数据来源: Yahoo Finance | Aria Code</p>
<div class="grid">
  <div class="card"><div class="lbl">最新收盘</div>
    <div class="val">{currency} {last_close:,.2f}</div></div>
  <div class="card"><div class="lbl">MA20</div>
    <div class="val {'green' if ma20 and last_close > ma20 else 'red'}">{f'{ma20:,.2f}' if ma20 else '—'}</div></div>
  <div class="card"><div class="lbl">MA50</div>
    <div class="val {'green' if ma50 and last_close > ma50 else 'red'}">{f'{ma50:,.2f}' if ma50 else '—'}</div></div>
  <div class="card"><div class="lbl">RSI(14)</div>
    <div class="val {'red' if rsi14 and rsi14>=70 else 'green' if rsi14 and rsi14<=30 else ''}">{f'{rsi14:.1f}' if rsi14 else '—'} {rsi_view}</div></div>
  <div class="card"><div class="lbl">趋势</div><div class="val">{trend}</div></div>
  <div class="card"><div class="lbl">动能</div><div class="val">{momentum}</div></div>
</div>
<div id="price-chart"></div>
<div id="rsi-chart" style="margin-top:8px"></div>
<div id="macd-chart" style="margin-top:8px"></div>
<p class="note">⚠️ 本图表仅供参考，不构成投资建议。</p>
</main>
<script>
const x={x};
Plotly.newPlot('price-chart',[
  {{x,y:{close_v},type:'scatter',name:'收盘价',line:{{color:'#2563eb',width:2}}}},
  {{x,y:{ma20_v}, type:'scatter',name:'MA20', line:{{color:'#f59e0b',width:1.5,dash:'dot'}}}},
  {{x,y:{ma50_v}, type:'scatter',name:'MA50', line:{{color:'#ef4444',width:1.5,dash:'dot'}}}}
],{{title:'{_html.escape(symbol)} 价格走势',height:340,plot_bgcolor:'#fff',
   paper_bgcolor:'#fff',xaxis:{{showgrid:true,gridcolor:'#f3f4f6'}},
   yaxis:{{showgrid:true,gridcolor:'#f3f4f6',title:'价格 ({currency})'}}}},
  {{responsive:true,displaylogo:false}});
Plotly.newPlot('rsi-chart',[
  {{x,y:{rsi_v},type:'scatter',name:'RSI(14)',line:{{color:'#8b5cf6',width:1.5}}}}
],{{title:'RSI(14)',height:180,plot_bgcolor:'#fff',paper_bgcolor:'#fff',
   shapes:[{{type:'line',x0:x[0],x1:x[x.length-1],y0:70,y1:70,
              line:{{color:'#dc2626',width:1,dash:'dot'}}}},
             {{type:'line',x0:x[0],x1:x[x.length-1],y0:30,y1:30,
              line:{{color:'#16a34a',width:1,dash:'dot'}}}}]}},
  {{responsive:true,displaylogo:false}});
Plotly.newPlot('macd-chart',[
  {{x,y:{macd_v},  type:'scatter',name:'MACD', line:{{color:'#2563eb',width:1.5}}}},
  {{x,y:{macd_s_v},type:'scatter',name:'Signal',line:{{color:'#f59e0b',width:1.5,dash:'dot'}}}}
],{{title:'MACD',height:180,plot_bgcolor:'#fff',paper_bgcolor:'#fff'}},
  {{responsive:true,displaylogo:false}});
</script></body></html>"""

    out_file.write_text(html_doc, encoding="utf-8")
    _raw_prices = []
    try:
        _raw_prices = hist.reset_index().tail(370).to_dict(orient="records")
    except Exception:
        _raw_prices = []
    write_artifact_metadata(_artifact, {
        "kind": "stock_chart",
        "status": "complete",
        "symbol": symbol,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "provider_chain": ["yfinance"],
            "rows": int(len(hist)),
            "missing_fields": [
                k for k, v in {
                    "ma20": ma20,
                    "ma50": ma50,
                    "rsi14": rsi14,
                    "macd": macd_l,
                    "macd_signal": macd_s,
                }.items()
                if v is None
            ],
        },
        "metrics": {
            "last_close": last_close,
            "trend": trend,
            "rsi14": rsi14,
            "momentum": momentum,
        },
    })
    write_artifact_raw_data(_artifact, {
        "symbol": symbol,
        "provider": "yfinance",
        "info": info,
        "prices": _raw_prices,
    })
    return {
        "success":    True,
        "chart_path": str(out_file),
        "response":   f"图表已生成：{out_file.name}",
        "symbol":     symbol,
        "last_close": last_close,
        "trend":      trend,
        "rsi":        rsi14,
        "momentum":   momentum,
    }


def handle_stock_chart_analysis(
    message: str,
    *,
    is_chart_request: Callable[[str], bool],
    extract_symbol: Callable[[str], str],
) -> dict:
    """Deterministic path for stock analysis + chart requests.

    This avoids weak local models writing fake scripts or leaking pseudo tool
    calls. It fetches historical data, computes common indicators, writes a
    standalone HTML chart, and returns a concise Markdown analysis.
    """
    if not is_chart_request(message):
        return {"success": False, "error": "not_stock_chart_analysis"}

    symbol = extract_symbol(message) or "AAPL"
    period = "1y"
    interval = "1d"

    try:
        import html as _html
        import pandas as _pd
        import yfinance as _yf
    except Exception as exc:
        return {
            "success": False,
            "error": f"缺少图表分析依赖：{exc}",
            "response": "当前环境缺少 `yfinance` 或 `pandas`，无法生成股票图表。",
        }

    provider = "Yahoo Finance"
    ticker = None
    try:
        ticker = _yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval, auto_adjust=False)
    except Exception as exc:
        hist = None
        yahoo_error = str(exc)
    else:
        yahoo_error = ""

    if hist is None or hist.empty:
        try:
            import requests as _requests
            period2 = int(time.time())
            period1 = period2 - 370 * 86400
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                f"?period1={period1}&period2={period2}&interval=1d"
                f"&events=history&includeAdjustedClose=true"
            )
            resp = _requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            payload = resp.json()
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if result:
                ts = result.get("timestamp") or []
                quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
                dates = _pd.to_datetime(ts, unit="s")
                hist = _pd.DataFrame({
                    "Open": quote.get("open", []),
                    "High": quote.get("high", []),
                    "Low": quote.get("low", []),
                    "Close": quote.get("close", []),
                    "Volume": quote.get("volume", []),
                }, index=dates).dropna(subset=["Close"])
                meta = result.get("meta") or {}
                if meta.get("currency"):
                    provider_currency = meta.get("currency")
                else:
                    provider_currency = None
                provider = "Yahoo Chart API"
            else:
                provider_currency = None
        except Exception as exc:
            hist = None
            chart_error = str(exc)
        else:
            chart_error = ""

    if hist is None or hist.empty:
        try:
            stooq_symbol = symbol.lower()
            if "." not in stooq_symbol:
                stooq_symbol = f"{stooq_symbol}.us"
            url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
            hist = _pd.read_csv(url)
            if hist is not None and not hist.empty:
                hist["Date"] = _pd.to_datetime(hist["Date"])
                hist = hist.set_index("Date").sort_index().tail(260)
                provider = "Stooq"
        except Exception as exc:
            return {
                "success": False,
                "error": f"获取 {symbol} 历史行情失败：Yahoo={yahoo_error or 'empty'}; YahooChart={chart_error or 'empty'}; Stooq={exc}",
                "response": f"无法获取 {symbol} 历史行情，图表未生成。请稍后重试，或检查网络/数据源访问。",
            }

    if hist is None or hist.empty or "Close" not in hist.columns:
        return {
            "success": False,
            "error": f"{symbol} 历史行情为空：Yahoo={yahoo_error or 'empty'}",
            "response": f"没有拿到 {symbol} 的可用历史行情，图表未生成。请稍后重试，或检查网络/数据源访问。",
        }

    hist = hist.dropna(subset=["Close"]).copy()
    hist["MA20"] = hist["Close"].rolling(20).mean()
    hist["MA50"] = hist["Close"].rolling(50).mean()
    hist["MA200"] = hist["Close"].rolling(200).mean()
    delta = hist["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, _pd.NA)
    hist["RSI14"] = 100 - (100 / (1 + rs))
    ema12 = hist["Close"].ewm(span=12, adjust=False).mean()
    ema26 = hist["Close"].ewm(span=26, adjust=False).mean()
    hist["MACD"] = ema12 - ema26
    hist["MACD_SIGNAL"] = hist["MACD"].ewm(span=9, adjust=False).mean()

    last = hist.iloc[-1]
    first_close = hist["Close"].iloc[0]
    last_close = float(last["Close"])
    ytd_like_return = (last_close / float(first_close) - 1) * 100 if first_close else 0
    ma20 = float(last["MA20"]) if _pd.notna(last["MA20"]) else None
    ma50 = float(last["MA50"]) if _pd.notna(last["MA50"]) else None
    ma200 = float(last["MA200"]) if _pd.notna(last["MA200"]) else None
    rsi14 = float(last["RSI14"]) if _pd.notna(last["RSI14"]) else None
    macd = float(last["MACD"]) if _pd.notna(last["MACD"]) else None
    macd_sig = float(last["MACD_SIGNAL"]) if _pd.notna(last["MACD_SIGNAL"]) else None
    high_52w = float(hist["High"].max()) if "High" in hist else float(hist["Close"].max())
    low_52w = float(hist["Low"].min()) if "Low" in hist else float(hist["Close"].min())

    info = {}
    try:
        if ticker is None:
            ticker = _yf.Ticker(symbol)
        info = ticker.get_info() or {}
    except Exception:
        info = {}
    name = info.get("longName") or info.get("shortName") or symbol
    pe = info.get("trailingPE")
    market_cap = info.get("marketCap")
    currency = info.get("currency") or locals().get("provider_currency") or "USD"

    if ma20 and ma50 and last_close > ma20 > ma50:
        trend = "偏多"
    elif ma20 and ma50 and last_close < ma20 < ma50:
        trend = "偏空"
    else:
        trend = "震荡/中性"
    momentum = "MACD偏多" if macd is not None and macd_sig is not None and macd > macd_sig else "MACD偏弱"
    rsi_view = "超买" if rsi14 is not None and rsi14 >= 70 else ("超卖" if rsi14 is not None and rsi14 <= 30 else "中性")

    safe_symbol = re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)
    from artifacts import create_artifact, write_artifact_metadata, write_artifact_raw_data
    _artifact = create_artifact("reports/stock-charts", symbol, f"{safe_symbol}_analysis_chart", ".html")
    out_file = _artifact.path

    x = [idx.strftime("%Y-%m-%d") for idx in hist.index]
    close = [None if _pd.isna(v) else round(float(v), 4) for v in hist["Close"]]
    volume = [None if _pd.isna(v) else int(float(v)) for v in hist.get("Volume", _pd.Series(index=hist.index, dtype=float))]
    ma20_arr = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MA20"]]
    ma50_arr = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MA50"]]
    rsi_arr = [None if _pd.isna(v) else round(float(v), 4) for v in hist["RSI14"]]

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html.escape(symbol)} 股票分析图表</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8fa; color: #17202a; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    .meta {{ color: #667085; margin-bottom: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin: 18px 0; }}
    .metric {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; }}
    .label {{ color: #667085; font-size: 12px; }}
    .value {{ font-size: 18px; font-weight: 650; margin-top: 4px; }}
    #chart {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px; }}
    .note {{ color: #667085; font-size: 13px; margin-top: 14px; }}
  </style>
</head>
<body>
<main>
  <h1>{_html.escape(name)} ({_html.escape(symbol)})</h1>
  <div class="meta">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 数据：{_html.escape(provider)} · 周期：{period}</div>
  <section class="grid">
    <div class="metric"><div class="label">最新收盘</div><div class="value">{currency} {_fmt_num(last_close)}</div></div>
    <div class="metric"><div class="label">近一年区间</div><div class="value">{_fmt_num(low_52w)} - {_fmt_num(high_52w)}</div></div>
    <div class="metric"><div class="label">MA20 / MA50</div><div class="value">{_fmt_num(ma20)} / {_fmt_num(ma50)}</div></div>
    <div class="metric"><div class="label">RSI14</div><div class="value">{_fmt_num(rsi14)}</div></div>
    <div class="metric"><div class="label">P/E</div><div class="value">{_fmt_num(pe)}</div></div>
    <div class="metric"><div class="label">成交量</div><div class="value">{_fmt_int(last.get("Volume"))}</div></div>
  </section>
  <div id="chart"></div>
  <p class="note">图表包含收盘价、MA20、MA50、成交量和 RSI14。该文件为本地 HTML，可直接在浏览器打开。</p>
</main>
<script>
const x = {json.dumps(x)};
const close = {json.dumps(close)};
const volume = {json.dumps(volume)};
const ma20 = {json.dumps(ma20_arr)};
const ma50 = {json.dumps(ma50_arr)};
const rsi = {json.dumps(rsi_arr)};
const data = [
  {{x, y: close, type: "scatter", mode: "lines", name: "Close", line: {{color: "#2563eb", width: 2}}, yaxis: "y"}},
  {{x, y: ma20, type: "scatter", mode: "lines", name: "MA20", line: {{color: "#f59e0b", width: 1.5}}, yaxis: "y"}},
  {{x, y: ma50, type: "scatter", mode: "lines", name: "MA50", line: {{color: "#10b981", width: 1.5}}, yaxis: "y"}},
  {{x, y: volume, type: "bar", name: "Volume", marker: {{color: "rgba(100,116,139,0.35)"}}, yaxis: "y2"}},
  {{x, y: rsi, type: "scatter", mode: "lines", name: "RSI14", line: {{color: "#dc2626", width: 1.5}}, yaxis: "y3"}}
];
const layout = {{
  height: 720,
  margin: {{l: 62, r: 30, t: 28, b: 42}},
  paper_bgcolor: "#fff",
  plot_bgcolor: "#fff",
  hovermode: "x unified",
  legend: {{orientation: "h", y: 1.04}},
  xaxis: {{domain: [0, 1], rangeslider: {{visible: false}}, gridcolor: "#eef2f7"}},
  yaxis: {{domain: [0.36, 1], title: "Price", gridcolor: "#eef2f7"}},
  yaxis2: {{domain: [0.18, 0.31], title: "Volume", gridcolor: "#eef2f7"}},
  yaxis3: {{domain: [0, 0.13], title: "RSI", range: [0, 100], gridcolor: "#eef2f7"}},
  shapes: [
    {{type: "line", xref: "paper", x0: 0, x1: 1, yref: "y3", y0: 70, y1: 70, line: {{color: "#ef4444", dash: "dot"}}}},
    {{type: "line", xref: "paper", x0: 0, x1: 1, yref: "y3", y0: 30, y1: 30, line: {{color: "#22c55e", dash: "dot"}}}}
  ]
}};
Plotly.newPlot("chart", data, layout, {{responsive: true, displaylogo: false}});
</script>
</body>
</html>
"""
    out_file.write_text(html_doc, encoding="utf-8")

    _raw_prices = []
    try:
        _raw_prices = hist.reset_index().tail(370).to_dict(orient="records")
    except Exception:
        _raw_prices = []
    write_artifact_metadata(_artifact, {
        "kind": "stock_chart_analysis",
        "status": "complete",
        "symbol": symbol,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "data": {
            "provider_chain": [provider],
            "rows": int(len(hist)),
            "missing_fields": [
                k for k, v in {
                    "ma20": ma20,
                    "ma50": ma50,
                    "ma200": ma200,
                    "rsi14": rsi14,
                    "macd": macd,
                    "macd_signal": macd_sig,
                    "pe": pe,
                    "market_cap": market_cap,
                }.items()
                if v is None
            ],
        },
        "metrics": {
            "last_close": last_close,
            "trend": trend,
            "rsi14": rsi14,
            "momentum": momentum,
            "ytd_like_return": ytd_like_return,
        },
    })
    write_artifact_raw_data(_artifact, {
        "symbol": symbol,
        "provider": provider,
        "info": info,
        "prices": _raw_prices,
    })

    market_cap_text = "—"
    if market_cap:
        market_cap_text = f"{currency} {market_cap / 1e12:.2f}T" if market_cap >= 1e12 else f"{currency} {market_cap / 1e9:.1f}B"

    response = (
        f"## {name} ({symbol}) 股票分析\n\n"
        f"已生成图表：[{out_file.name}]({out_file})\n\n"
        f"| 指标 | 数值 |\n"
        f"| --- | --- |\n"
        f"| 最新收盘 | {currency} {_fmt_num(last_close)} |\n"
        f"| 近一年涨跌幅 | {ytd_like_return:+.2f}% |\n"
        f"| 近一年高/低 | {_fmt_num(high_52w)} / {_fmt_num(low_52w)} |\n"
        f"| MA20 / MA50 / MA200 | {_fmt_num(ma20)} / {_fmt_num(ma50)} / {_fmt_num(ma200)} |\n"
        f"| RSI14 | {_fmt_num(rsi14)}（{rsi_view}） |\n"
        f"| MACD | {_fmt_num(macd)} / signal {_fmt_num(macd_sig)}（{momentum}） |\n"
        f"| P/E / 市值 | {_fmt_num(pe)} / {market_cap_text} |\n\n"
        f"**结论**：当前技术结构为 **{trend}**。"
        f"RSI 处于{rsi_view}区间，{momentum}。"
        f"若价格能稳定站上 MA20 和 MA50，短线结构会更健康；若跌破 MA50 或放量下行，需要降低仓位和预期。\n\n"
        f"**风险**：该分析基于 {provider} 历史行情和常用技术指标，不构成投资建议；财报、产品周期、利率和大盘风险都会影响股价。"
    )
    return {
        "success": True,
        "response": response,
        "provider": "deterministic",
        "tools_used": ["yfinance", "html_chart"],
        "chart_path": str(out_file),
    }
