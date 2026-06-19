# TradingView Integration Plan

Aria should treat TradingView as a visualization and alert surface, not as the
source of truth for market data. The internal data stack remains responsible for
symbol resolution, provider routing, quality checks, backtests, reports, and
local artifact generation.

## Roles

| Layer | Responsibility |
|---|---|
| Intent router | Decide whether the user wants quote, TA, chart, dashboard, backtest, report, alert, or strategy code. |
| Symbol resolver | Normalize Chinese names, tickers, indices, crypto, FX, futures, A-shares, and HK stocks into canonical symbols. |
| Data router | Fetch real data through MarketDataClient and provider fallbacks; attach provider chain and quality status. |
| Chart renderer | Produce local HTML/PNG artifacts from verified OHLCV and indicators. |
| TradingView bridge | Optionally open/embed TradingView charts or receive webhook alerts, but never overwrite verified internal numbers. |

## Recommended Integration Modes

1. `Open chart in TradingView`
   - Trigger: user asks to "用 TradingView 打开", "TradingView 图", or needs manual chart inspection.
   - Output: a URL such as `https://www.tradingview.com/chart/?symbol=NASDAQ%3AQQQ`.
   - Internal data still appears in Aria output with provider attribution.

2. `TradingView-style local chart`
   - Trigger: user asks for a chart, K-line, dashboard, or HTML/PNG output.
   - Implementation: keep using local Plotly/HTML output by default; optionally add a Lightweight Charts renderer later.
   - Benefit: works offline after generation and uses the same verified OHLCV as the analysis.

3. `TradingView alert webhook`
   - Trigger: TradingView sends an alert to Aria daemon.
   - Flow: webhook payload -> symbol resolver -> data router -> risk/TA workflow -> notification/report/backtest action.
   - Security: require `TRADINGVIEW_WEBHOOK_SECRET`; reject unsigned payloads.

4. `Pine strategy companion`
   - Trigger: user asks for TradingView strategy code.
   - Output: save Pine Script to the user artifact directory and, when useful, generate a matching local Python backtest.
   - Constraint: clearly separate Pine signal logic from Aria's real-data backtest results.

## Do Not Do

- Do not use TradingView widget prices as authoritative analysis data.
- Do not mix unverified chart UI values into reports.
- Do not route every market request to TradingView; only chart/manual-inspection or alert workflows should use it.
- Do not expose local paths, secrets, or provider keys in TradingView webhook responses.

## Implementation Backlog

| Priority | Item |
|---|---|
| P0 | Keep quote/TA/report paths on verified MarketDataClient data. |
| P0 | Add canonical symbol to TradingView exchange mapping: `QQQ -> NASDAQ:QQQ`, `^IXIC -> NASDAQ:IXIC`, `0700.HK -> HKEX:700`, `600519 -> SSE:600519`, `300750 -> SZSE:300750`, `BTC-USD -> BINANCE:BTCUSDT`, `GC=F -> COMEX:GC1!`. |
| P1 | Add `/tv SYMBOL` command that prints or opens TradingView URL without replacing Aria's data output. |
| P1 | Add webhook endpoint in `aria_daemon.py` with secret validation and structured payload logs. |
| P2 | Add optional Lightweight Charts HTML renderer for local TradingView-like UX. |
| P2 | Add Pine Script export workflow for user-requested strategies. |

