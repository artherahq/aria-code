# Auto-extracted from aria_cli.py (SlashCommands._SCAFFOLD_TEMPLATES)

SCAFFOLD_TEMPLATES = {
    "quant": {
        "desc": "量化策略项目（数据层 / 信号层 / 回测引擎 / 报告）",
        "dirs": ["data/raw", "data/processed", "strategy", "backtest", "report", "tests"],
        "files": {
            "requirements.txt": "akshare\nyfinance\npandas\nnumpy\nmatplotlib\nscipy\n",
            "data/fetcher.py": '''\
"""数据获取层：akshare A股 / yfinance 美股，带本地 CSV 缓存。"""
import os, pathlib
import akshare as ak
import yfinance as yf
import pandas as pd

CACHE = pathlib.Path(__file__).parent / "processed"
CACHE.mkdir(exist_ok=True)

def fetch_ashare(symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
cache_f = CACHE / f"{symbol}_{adjust}.csv"
if cache_f.exists():
    df = pd.read_csv(cache_f, index_col=0, parse_dates=True)
    if str(df.index[-1].date()) >= end:
        return df
df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                         start_date=start.replace("-",""),
                         end_date=end.replace("-",""), adjust=adjust)
df = df.rename(columns={"日期":"Date","开盘":"Open","最高":"High",
                         "最低":"Low","收盘":"Close","成交量":"Volume"})
df["Date"] = pd.to_datetime(df["Date"])
df = df.set_index("Date").sort_index()
df.to_csv(cache_f)
return df

def fetch_us(symbol: str, period: str = "2y") -> pd.DataFrame:
return yf.Ticker(symbol).history(period=period, auto_adjust=True)[
    ["Open","High","Low","Close","Volume"]]
''',
            "strategy/base.py": '''\
"""抽象策略基类：子类只需实现 generate_signals()。"""
from abc import ABC, abstractmethod
import pandas as pd

class Strategy(ABC):
name: str = "base"

@abstractmethod
def generate_signals(self, df: pd.DataFrame) -> pd.Series:
    """返回 +1（做多）/ -1（做空）/ 0（空仓）的 Series，与 df.index 对齐。"""
    ...
''',
            "strategy/dual_ma.py": '''\
"""双均线策略：短期 MA 上穿长期 MA 买入，下穿卖出。"""
import pandas as pd
from .base import Strategy

class DualMA(Strategy):
name = "dual_ma"

def __init__(self, fast: int = 5, slow: int = 20):
    self.fast = fast
    self.slow = slow

def generate_signals(self, df: pd.DataFrame) -> pd.Series:
    close = df["Close"]
    fast_ma = close.rolling(self.fast).mean()
    slow_ma = close.rolling(self.slow).mean()
    sig = pd.Series(0, index=df.index)
    sig[fast_ma > slow_ma] = 1
    sig[fast_ma < slow_ma] = -1
    # 只在交叉时换仓（减少换手）
    return sig.diff().fillna(0).clip(-1, 1).cumsum().clip(-1, 1)
''',
            "strategy/__init__.py": "from .dual_ma import DualMA\n",
            "backtest/engine.py": '''\
"""向量化回测引擎，含 A股交易成本。"""
import pandas as pd
import numpy as np

# A股交易成本：双边手续费 0.025%×2 + 印花税 0.05%（卖出）+ 滑点 0.1%
A_SHARE_COST = 0.025/100 * 2 + 0.05/100 + 0.1/100

def backtest(df: pd.DataFrame, signals: pd.Series,
         cost_rate: float = A_SHARE_COST,
         initial_capital: float = 1_000_000) -> pd.DataFrame:
"""
df       : OHLCV DataFrame（index=DatetimeIndex）
signals  : +1 做多 / -1 做空 / 0 空仓
返回含 equity_curve 和各日 pnl 的 DataFrame
"""
ret = df["Close"].pct_change()
pos = signals.shift(1).fillna(0)          # 信号次日执行
turnover = pos.diff().abs().fillna(0)
strat_ret = pos * ret - turnover * cost_rate
bnh_ret = ret.copy()

cum_strat = (1 + strat_ret).cumprod() * initial_capital
cum_bnh   = (1 + bnh_ret).cumprod() * initial_capital

result = pd.DataFrame({
    "strategy": cum_strat, "buy_hold": cum_bnh,
    "daily_ret": strat_ret, "position": pos,
})

# 统计指标
ann = 252
sharpe = strat_ret.mean() / strat_ret.std() * np.sqrt(ann) if strat_ret.std() else 0
max_dd  = (cum_strat / cum_strat.cummax() - 1).min()
total_r = cum_strat.iloc[-1] / initial_capital - 1
ann_r   = (1 + total_r) ** (ann / len(result)) - 1
wins    = (strat_ret[pos.shift(-1) != pos] > 0).mean()

result.attrs = dict(sharpe=round(sharpe,2), max_drawdown=round(max_dd,4),
                    total_return=round(total_r,4), annual_return=round(ann_r,4),
                    win_rate=round(wins,4))
return result
''',
            "backtest/__init__.py": "from .engine import backtest\n",
            "report/plot.py": '''\
"""生成净值曲线 + 回撤图，保存为 PNG。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import pandas as pd
import pathlib

OUT = pathlib.Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)

def plot_result(result: pd.DataFrame, title: str = "Backtest") -> pathlib.Path:
fig = plt.figure(figsize=(12, 7), facecolor="#0d1117")
gs  = gridspec.GridSpec(2, 1, height_ratios=[3,1], hspace=0.08)

ax1 = fig.add_subplot(gs[0])
ax1.plot(result.index, result["strategy"],  color="#3fb950", lw=1.5, label="Strategy")
ax1.plot(result.index, result["buy_hold"],  color="#58a6ff", lw=1.0, label="Buy & Hold", alpha=0.7)
ax1.set_facecolor("#161b22"); ax1.tick_params(colors="#8b949e"); ax1.legend(facecolor="#21262d", edgecolor="#30363d", labelcolor="#e6edf3")
ax1.set_title(title, color="#e6edf3", fontsize=13)
for spine in ax1.spines.values(): spine.set_color("#30363d")

dd = result["strategy"] / result["strategy"].cummax() - 1
ax2 = fig.add_subplot(gs[1], sharex=ax1)
ax2.fill_between(result.index, dd, 0, color="#f85149", alpha=0.6)
ax2.set_facecolor("#161b22"); ax2.tick_params(colors="#8b949e")
ax2.set_ylabel("Drawdown", color="#8b949e", fontsize=9)
for spine in ax2.spines.values(): spine.set_color("#30363d")

a = result.attrs
fig.text(0.12, 0.02, f"Annual {a.get('annual_return',0):.1%}  Sharpe {a.get('sharpe',0):.2f}  MaxDD {a.get('max_drawdown',0):.1%}  Win {a.get('win_rate',0):.1%}", color="#8b949e", fontsize=9)

out_f = OUT / f"{title.replace(' ','_')}.png"
plt.savefig(out_f, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
plt.close(fig)
print(f"图表已保存: {out_f}")
return out_f
''',
            "report/__init__.py": "from .plot import plot_result\n",
            "main.py": '''\
"""运行量化策略回测的主入口。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from data.fetcher import fetch_ashare
from strategy import DualMA
from backtest import backtest
from report import plot_result
from datetime import datetime, timedelta

SYMBOL = "600519"   # 贵州茅台（示例）
END    = datetime.now().strftime("%Y-%m-%d")
START  = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")

print(f"抓取 {SYMBOL} 数据 {START} → {END}...")
df = fetch_ashare(SYMBOL, START, END)
print(f"共 {len(df)} 条 K线")

strategy = DualMA(fast=5, slow=20)
signals  = strategy.generate_signals(df)
result   = backtest(df, signals)

a = result.attrs
print(f"\n===== 回测结果 ({SYMBOL} · 双均线5/20) =====")
print(f"总收益:   {a['total_return']:.2%}")
print(f"年化收益: {a['annual_return']:.2%}")
print(f"夏普比率: {a['sharpe']:.2f}")
print(f"最大回撤: {a['max_drawdown']:.2%}")
print(f"胜率:     {a['win_rate']:.2%}")

plot_result(result, f"{SYMBOL}_DualMA")
''',
            "tests/test_strategy.py": '''\
"""基础单元测试：策略信号生成 + 回测引擎。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pandas as pd
import numpy as np
from strategy import DualMA
from backtest import backtest

def _make_df(n=100):
dates = pd.date_range("2024-01-01", periods=n)
close = pd.Series(100 + np.cumsum(np.random.randn(n)), index=dates)
return pd.DataFrame({"Open":close,"High":close*1.01,"Low":close*0.99,"Close":close,"Volume":1e6})

def test_signal_shape():
df  = _make_df()
sig = DualMA(fast=5,slow=20).generate_signals(df)
assert len(sig) == len(df)
assert set(sig.unique()).issubset({-1,0,1})

def test_backtest_runs():
df  = _make_df()
sig = DualMA().generate_signals(df)
res = backtest(df, sig)
assert "strategy" in res.columns
assert res.attrs.get("sharpe") is not None

if __name__ == "__main__":
test_signal_shape(); test_backtest_runs(); print("All tests passed.")
''',
        },
    },
    "analysis": {
        "desc": "数据分析项目（数据加载 / 清洗 / 可视化 / 报告）",
        "dirs": ["data/raw", "data/processed", "src", "output", "notebooks"],
        "files": {
            "requirements.txt": "pandas\nnumpy\nmatplotlib\nseaborn\nopenpyxl\nyfinance\n",
            "src/loader.py": '''\
"""数据加载工具：支持 CSV / Excel / yfinance。"""
import pandas as pd, pathlib, yfinance as yf

DATA = pathlib.Path(__file__).parent.parent / "data"

def load_csv(filename: str, **kw) -> pd.DataFrame:
return pd.read_csv(DATA / "raw" / filename, **kw)

def load_excel(filename: str, sheet=0, **kw) -> pd.DataFrame:
return pd.read_excel(DATA / "raw" / filename, sheet_name=sheet, **kw)

def load_stock(symbol: str, period: str = "1y") -> pd.DataFrame:
df = yf.Ticker(symbol).history(period=period, auto_adjust=True, progress=False)
return df[["Open","High","Low","Close","Volume"]]

def save_processed(df: pd.DataFrame, name: str) -> pathlib.Path:
out = DATA / "processed" / name
df.to_csv(out); return out
''',
            "src/analyzer.py": '''\
"""常用分析函数：描述统计 / 相关性 / 滚动指标。"""
import pandas as pd, numpy as np

def describe_df(df: pd.DataFrame) -> pd.DataFrame:
return df.describe().round(4)

def correlation_matrix(df: pd.DataFrame) -> pd.DataFrame:
return df.corr().round(4)

def rolling_stats(series: pd.Series, window: int = 20) -> pd.DataFrame:
return pd.DataFrame({
    "mean":   series.rolling(window).mean(),
    "std":    series.rolling(window).std(),
    "zscore": (series - series.rolling(window).mean()) / series.rolling(window).std(),
})

def annualized_return(series: pd.Series) -> float:
ret = series.pct_change().dropna()
return float((1 + ret.mean()) ** 252 - 1)

def max_drawdown(series: pd.Series) -> float:
return float((series / series.cummax() - 1).min())
''',
            "src/visualizer.py": '''\
"""可视化工具：折线图 / 直方图 / 热力图。"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import pandas as pd, pathlib

OUT = pathlib.Path(__file__).parent.parent / "output"
OUT.mkdir(exist_ok=True)
plt.style.use("dark_background")

def plot_series(series: pd.Series, title: str = "", filename: str = "plot.png") -> pathlib.Path:
fig, ax = plt.subplots(figsize=(11,4), facecolor="#0d1117")
ax.plot(series.index, series.values, color="#58a6ff", lw=1.2)
ax.set_title(title, color="#e6edf3"); ax.set_facecolor("#161b22")
for sp in ax.spines.values(): sp.set_color("#30363d")
ax.tick_params(colors="#8b949e")
out = OUT / filename
plt.savefig(out, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
plt.close(); return out

def plot_heatmap(df: pd.DataFrame, title: str = "Correlation", filename: str = "heatmap.png") -> pathlib.Path:
fig, ax = plt.subplots(figsize=(8,6), facecolor="#0d1117")
sns.heatmap(df, annot=True, fmt=".2f", cmap="RdYlGn", ax=ax,
            linewidths=0.3, linecolor="#30363d", cbar_kws={"shrink":0.7})
ax.set_title(title, color="#e6edf3"); ax.tick_params(colors="#8b949e")
out = OUT / filename
plt.savefig(out, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
plt.close(); return out
''',
            "main.py": '''\
"""数据分析项目入口示例。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from src.loader import load_stock
from src.analyzer import describe_df, correlation_matrix, annualized_return, max_drawdown
from src.visualizer import plot_series

# 示例：分析多支股票
SYMBOLS = ["AAPL","MSFT","NVDA"]
closes = {}
for sym in SYMBOLS:
df = load_stock(sym, period="1y")
closes[sym] = df["Close"]
print(f"{sym}: 年化收益 {annualized_return(df['Close']):.2%}  最大回撤 {max_drawdown(df['Close']):.2%}")

import pandas as pd
closes_df = pd.DataFrame(closes)
print("\n相关性矩阵:")
print(correlation_matrix(closes_df))

plot_series(closes_df["AAPL"], "AAPL Close Price", "aapl_close.png")
print("图表已保存到 output/")
''',
        },
    },
    "fastapi": {
        "desc": "FastAPI 金融数据 API 服务（行情 / 技术指标 / 基本面）",
        "dirs": ["app/routers", "app/schemas", "app/services", "tests"],
        "files": {
            "requirements.txt": "fastapi\nuvicorn[standard]\nyfinance\nrequests\npandas\nnumpy\n",
            "app/__init__.py": "",
            "app/main.py": '''\
"""FastAPI 主应用入口。"""
from fastapi import FastAPI
from app.routers import market, health

app = FastAPI(title="Aria Finance API", version="1.0.0",
          description="金融行情与分析 REST API")

app.include_router(health.router)
app.include_router(market.router, prefix="/market", tags=["market"])

if __name__ == "__main__":
import uvicorn
uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
''',
            "app/routers/__init__.py": "",
            "app/routers/health.py": '''\
from fastapi import APIRouter
from datetime import datetime

router = APIRouter()

@router.get("/health")
def health_check():
return {"status": "ok", "timestamp": datetime.now().isoformat()}
''',
            "app/routers/market.py": '''\
"""行情路由：报价 / 历史 K 线 / 技术指标。"""
from fastapi import APIRouter, HTTPException, Query
from app.services.market_service import get_quote, get_history, get_technicals

router = APIRouter()

@router.get("/quote/{symbol}")
def quote(symbol: str):
data = get_quote(symbol.upper())
if not data:
    raise HTTPException(status_code=404, detail=f"No data for {symbol}")
return data

@router.get("/history/{symbol}")
def history(symbol: str, period: str = Query("3mo", description="1mo/3mo/6mo/1y/2y")):
records = get_history(symbol.upper(), period)
return {"symbol": symbol.upper(), "period": period, "data": records}

@router.get("/technicals/{symbol}")
def technicals(symbol: str):
return get_technicals(symbol.upper())
''',
            "app/services/__init__.py": "",
            "app/services/market_service.py": '''\
"""市场数据服务层（基于 yfinance）。"""
import yfinance as yf
import pandas as pd, numpy as np
from typing import Optional

def get_quote(symbol: str) -> Optional[dict]:
try:
    t = yf.Ticker(symbol)
    info = t.info or {}
    price = info.get("currentPrice") or info.get("regularMarketPrice")
    if not price:
        hist = t.history(period="1d", auto_adjust=True)
        price = float(hist["Close"].iloc[-1]) if not hist.empty else None
    return {
        "symbol": symbol,
        "price": price,
        "prev_close": info.get("previousClose"),
        "open": info.get("open"),
        "day_high": info.get("dayHigh"),
        "day_low": info.get("dayLow"),
        "volume": info.get("volume"),
        "market_cap": info.get("marketCap"),
        "pe_ratio": info.get("trailingPE"),
        "name": info.get("longName", symbol),
        "currency": info.get("currency", "USD"),
    }
except Exception:
    return None

def get_history(symbol: str, period: str = "3mo") -> list:
try:
    df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
    df = df.reset_index()
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    return df[["Date","Open","High","Low","Close","Volume"]].round(4).to_dict(orient="records")
except Exception:
    return []

def get_technicals(symbol: str) -> dict:
try:
    df = yf.Ticker(symbol).history(period="6mo", auto_adjust=True)
    close = df["Close"]
    rsi_period = 14
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(rsi_period).mean()
    loss  = (-delta.clip(upper=0)).rolling(rsi_period).mean()
    rs    = gain / loss
    rsi   = float((100 - 100 / (1 + rs)).iloc[-1])
    ema12 = close.ewm(span=12).mean()
    ema26 = close.ewm(span=26).mean()
    macd  = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    return {
        "symbol": symbol,
        "rsi": round(rsi, 2),
        "macd": round(float(macd.iloc[-1]), 4),
        "macd_signal": round(float(signal.iloc[-1]), 4),
        "ma20": round(float(close.rolling(20).mean().iloc[-1]), 2),
        "ma60": round(float(close.rolling(60).mean().iloc[-1]), 2),
        "bb_upper": round(float(close.rolling(20).mean().iloc[-1] + 2*close.rolling(20).std().iloc[-1]), 2),
        "bb_lower": round(float(close.rolling(20).mean().iloc[-1] - 2*close.rolling(20).std().iloc[-1]), 2),
    }
except Exception as e:
    return {"error": str(e)}
''',
            "tests/test_api.py": '''\
"""API 单元测试（不启动服务器）。"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_health():
r = client.get("/health")
assert r.status_code == 200
assert r.json()["status"] == "ok"

def test_quote_valid():
r = client.get("/market/quote/AAPL")
assert r.status_code == 200
data = r.json()
assert "price" in data
assert data["symbol"] == "AAPL"

def test_history():
r = client.get("/market/history/AAPL?period=1mo")
assert r.status_code == 200
assert len(r.json()["data"]) > 0

if __name__ == "__main__":
test_health(); test_quote_valid(); test_history(); print("All tests passed.")
''',
            "run.py": '''\
"""启动 FastAPI 开发服务器。"""
import uvicorn
uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
''',
        },
    },
    "dashboard": {
        "desc": "Plotly Dash 交互式金融看板",
        "dirs": ["assets", "components", "data"],
        "files": {
            "requirements.txt": "dash\nplotly\nyfinance\npandas\nnumpy\n",
            "components/__init__.py": "",
            "components/chart.py": '''\
"""K线图 + 均线组件。"""
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd, numpy as np

def build_candlestick(symbol: str, period: str = "6mo") -> go.Figure:
df = yf.Ticker(symbol).history(period=period, auto_adjust=True)
df["MA20"] = df["Close"].rolling(20).mean()
df["MA60"] = df["Close"].rolling(60).mean()

fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                     row_heights=[0.75,0.25], vertical_spacing=0.03)
fig.add_trace(go.Candlestick(
    x=df.index, open=df["Open"], high=df["High"],
    low=df["Low"], close=df["Close"], name="K线",
    increasing_line_color="#3fb950", decreasing_line_color="#f85149"
), row=1, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df["MA20"], name="MA20",
                          line=dict(color="#58a6ff", width=1)), row=1, col=1)
fig.add_trace(go.Scatter(x=df.index, y=df["MA60"], name="MA60",
                          line=dict(color="#e3b341", width=1)), row=1, col=1)
fig.add_trace(go.Bar(x=df.index, y=df["Volume"], name="成交量",
                      marker_color="#30363d"), row=2, col=1)
fig.update_layout(
    template="plotly_dark", plot_bgcolor="#0d1117", paper_bgcolor="#010409",
    title=f"{symbol}  |  {period}", height=600,
    xaxis_rangeslider_visible=False,
    legend=dict(bgcolor="#161b22", bordercolor="#30363d"),
)
return fig
''',
            "app.py": '''\
"""Dash 交互式金融看板主应用。"""
import dash
from dash import dcc, html, Input, Output, State
from components.chart import build_candlestick
import yfinance as yf

app = dash.Dash(__name__, title="Aria Dashboard")

PERIODS = ["1mo","3mo","6mo","1y","2y","5y"]

app.layout = html.Div([
html.Div([
    html.H1("Aria 金融看板", style={"color":"#e6edf3","fontSize":"22px","fontWeight":"700","margin":"0"}),
    html.Div([
        dcc.Input(id="symbol-input", value="AAPL", type="text", debounce=True,
                  placeholder="输入股票代码",
                  style={"background":"#161b22","color":"#e6edf3","border":"1px solid #30363d",
                         "borderRadius":"6px","padding":"8px 12px","width":"160px","marginRight":"8px"}),
        dcc.Dropdown(id="period-dd", options=[{"label":p,"value":p} for p in PERIODS],
                      value="6mo", clearable=False,
                      style={"width":"100px","background":"#161b22","color":"#0d1117"}),
    ], style={"display":"flex","alignItems":"center","gap":"8px"}),
], style={"display":"flex","justifyContent":"space-between","alignItems":"center",
          "padding":"16px 24px","borderBottom":"1px solid #21262d","background":"#010409"}),

dcc.Loading(dcc.Graph(id="main-chart", style={"height":"600px"}),
            color="#58a6ff"),

html.Div(id="stats-row", style={"display":"flex","gap":"10px",
         "padding":"12px 24px","background":"#010409"}),
], style={"background":"#010409","minHeight":"100vh","fontFamily":"-apple-system,sans-serif"})

@app.callback(
[Output("main-chart","figure"), Output("stats-row","children")],
[Input("symbol-input","value"), Input("period-dd","value")],
prevent_initial_call=False,
)
def update_chart(symbol, period):
if not symbol:
    return dash.no_update, dash.no_update
symbol = symbol.strip().upper()
fig = build_candlestick(symbol, period)
try:
    info = yf.Ticker(symbol).info or {}
    price   = info.get("currentPrice") or info.get("regularMarketPrice","—")
    mktcap  = info.get("marketCap")
    pe      = info.get("trailingPE","—")
    name    = info.get("longName", symbol)
    mktcap_s = f"${mktcap/1e9:.1f}B" if mktcap else "—"
except Exception:
    name,price,mktcap_s,pe = symbol,"—","—","—"

def kpi(label, val):
    return html.Div([
        html.Div(label, style={"fontSize":"11px","color":"#8b949e","marginBottom":"4px"}),
        html.Div(str(val), style={"fontSize":"18px","fontWeight":"700","color":"#e6edf3"}),
    ], style={"background":"#161b22","border":"1px solid #30363d","borderRadius":"8px",
              "padding":"12px 16px","minWidth":"120px"})

stats = [kpi("公司", name[:20]), kpi("现价", price),
         kpi("市值", mktcap_s), kpi("市盈率", pe)]
return fig, stats

if __name__ == "__main__":
app.run(debug=True, host="0.0.0.0", port=8050)
''',
            "README.md": '''\
# Aria Dashboard

交互式金融看板（Plotly Dash）。

## 启动

```bash
pip install -r requirements.txt
python app.py
```

浏览器访问 http://localhost:8050

## 功能

- K线图 + 成交量（MA20/MA60）
- 多周期切换（1mo 到 5y）
- 基本面 KPI（市值、市盈率）
''',
        },
    },
}

