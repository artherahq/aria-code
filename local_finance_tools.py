"""
local_finance_tools.py — Fully-offline financial tool implementations for Aria Code.

Purpose
-------
When running in local_mode (no Arthera backend), this module provides drop-in
replacements for every ARIA_TOOLS entry using open-source data libraries:

  A股 data    → akshare
  US/Global   → yfinance
  Crypto      → ccxt
  Backtesting → vectorbt (or pandas fallback)
  Technical   → pandas_ta (or ta-lib if installed)
  Risk        → scipy / numpy

Each tool follows the same contract as the remote Aria tools:
  handler(params: dict) -> dict  (always returns a dict, never raises)

Registration
------------
Call ``register_local_finance_tools(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)`` at
startup to extend the CLI's tool registry.  The function is idempotent.
"""

from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alibaba Cloud data client (optional — degrades gracefully when offline)
# ---------------------------------------------------------------------------

try:
    from aliyun_data_client import (
        AliyunDataClient,
        cloud_get_quote_sync,
        cloud_get_history_sync,
        cloud_get_factors_sync,
        cloud_get_ai_signal_sync,
    )
    _HAS_CLOUD = True
    logger.debug("Alibaba Cloud data client loaded ✓")
except ImportError:
    _HAS_CLOUD = False
    logger.debug("aliyun_data_client not found — cloud features disabled")

# ---------------------------------------------------------------------------
# Optional dependency guards
# ---------------------------------------------------------------------------

try:
    import yfinance as yf
    _HAS_YF = True
except ImportError:
    _HAS_YF = False

try:
    import akshare as ak
    _HAS_AK = True
except ImportError:
    _HAS_AK = False

try:
    import ccxt
    _HAS_CCXT = True
except ImportError:
    _HAS_CCXT = False

try:
    import pandas_ta as ta
    _HAS_TA = True
except ImportError:
    _HAS_TA = False

try:
    import vectorbt as vbt
    _HAS_VBT = True
except ImportError:
    _HAS_VBT = False

try:
    from scipy import stats as sp_stats
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _safe(fn, params, *args, **kwargs):
    """Wrap a tool handler so it never raises — returns error dict instead."""
    try:
        return fn(params, *args, **kwargs)
    except Exception as exc:
        logger.debug("Local finance tool error: %s", traceback.format_exc())
        return {"success": False, "error": str(exc), "traceback": traceback.format_exc()[-500:]}


def _parse_date(s: Optional[str], default_days_back: int = 365) -> str:
    if s:
        return s
    return (datetime.today() - timedelta(days=default_days_back)).strftime("%Y-%m-%d")


def _today() -> str:
    return datetime.today().strftime("%Y-%m-%d")


def _is_ashare(symbol: str) -> bool:
    s = symbol.strip().lower()
    return (
        s.startswith("sh") or s.startswith("sz")
        or (len(s) == 6 and s.isdigit())
        or s.endswith(".ss") or s.endswith(".sz")
    )


def _normalise_ashare(symbol: str) -> str:
    s = symbol.strip().lower().replace(".ss", "").replace(".sz", "")
    s = s.replace("sh", "").replace("sz", "")
    if len(s) == 6 and s.isdigit():
        prefix = "sh" if s.startswith("6") else "sz"
        return prefix + s
    return s


# ---------------------------------------------------------------------------
# 1. get_market_data
# ---------------------------------------------------------------------------

def _get_market_data(params: dict) -> dict:
    symbol   = params.get("symbol", "AAPL").upper()
    period   = params.get("period", "1y")        # yfinance period
    interval = params.get("interval", "1d")
    start    = params.get("start")
    end      = params.get("end", _today())

    if _is_ashare(symbol):
        return _get_ashare_data(symbol, start or _parse_date(None, 365), end)

    # ── Try Alibaba Cloud first (for US/global symbols) ────────────────
    if _HAS_CLOUD:
        try:
            cloud_q = cloud_get_quote_sync(symbol)
            if cloud_q and cloud_q.get("price"):
                return {
                    "success":      True,
                    "symbol":       symbol,
                    "latest_close": round(float(cloud_q.get("price", 0)), 4),
                    "change_pct":   round(float(cloud_q.get("change_percent", 0)), 3),
                    "volume":       int(cloud_q.get("volume", 0)),
                    "high":         round(float(cloud_q.get("high", 0)), 4),
                    "low":          round(float(cloud_q.get("low", 0)), 4),
                    "open":         round(float(cloud_q.get("open", 0)), 4),
                    "name":         cloud_q.get("name", symbol),
                    "market":       cloud_q.get("market", "US"),
                    "provider":     "aliyun_cloud",
                }
        except Exception as exc:
            logger.debug("Cloud quote failed for %s: %s — falling back to yfinance", symbol, exc)

    if not _HAS_YF:
        return {"success": False, "error": "yfinance not installed: pip install yfinance"}

    try:
        tkr  = yf.Ticker(symbol)
        if start:
            hist = tkr.history(start=start, end=end, interval=interval)
        else:
            hist = tkr.history(period=period, interval=interval)

        if hist.empty:
            return {"success": False, "error": f"No data for {symbol}"}

        info        = tkr.fast_info
        latest      = hist.iloc[-1]
        prev        = hist.iloc[-2] if len(hist) > 1 else latest
        chg         = (latest["Close"] - prev["Close"]) / prev["Close"] * 100

        return {
            "success":       True,
            "symbol":        symbol,
            "latest_close":  round(float(latest["Close"]), 4),
            "change_pct":    round(float(chg), 3),
            "volume":        int(latest["Volume"]),
            "high_52w":      round(float(info.year_high),  4) if hasattr(info, "year_high")  else None,
            "low_52w":       round(float(info.year_low),   4) if hasattr(info, "year_low")   else None,
            "market_cap":    getattr(info, "market_cap", None),
            "currency":      getattr(info, "currency", "USD"),
            "bars":          len(hist),
            "history_tail":  _df_tail(hist, 5),
            "provider":      "yfinance",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _get_ashare_data(symbol: str, start: str, end: str) -> dict:
    # ── Try Alibaba Cloud akshare_data_server first ────────────────────
    if _HAS_CLOUD:
        try:
            cloud_hist = cloud_get_history_sync(symbol, start=start, end=end)
            if cloud_hist and cloud_hist.get("data"):
                rows = cloud_hist["data"]
                if rows:
                    latest   = rows[-1]
                    prev     = rows[-2] if len(rows) > 1 else latest
                    cl, pc   = float(latest.get("close", 0)), float(prev.get("close", 0) or 0.0001)
                    chg      = (cl - pc) / pc * 100
                    # Build history_tail
                    tail = [
                        {k: v for k, v in r.items() if k in ("date", "open", "high", "low", "close", "volume")}
                        for r in rows[-5:]
                    ]
                    return {
                        "success":       True,
                        "symbol":        symbol,
                        "latest_close":  round(cl, 3),
                        "change_pct":    round(chg, 3),
                        "volume":        int(latest.get("volume", 0)),
                        "bars":          len(rows),
                        "history_tail":  tail,
                        "provider":      "aliyun_data",
                    }
        except Exception as exc:
            logger.debug("Cloud history failed for %s: %s — falling back to akshare", symbol, exc)

    if not _HAS_AK:
        return {"success": False, "error": "akshare not installed: pip install akshare"}

    norm = _normalise_ashare(symbol)
    code = norm[2:]  # strip sh/sz prefix
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=start.replace("-", ""),
                                 end_date=end.replace("-", ""),
                                 adjust="qfq")
        if df is None or df.empty:
            return {"success": False, "error": f"No A-share data for {symbol}"}

        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
            "成交额": "amount", "换手率": "turnover_rate",
        })
        latest = df.iloc[-1]
        prev   = df.iloc[-2] if len(df) > 1 else latest
        chg    = (latest["close"] - prev["close"]) / prev["close"] * 100

        return {
            "success":       True,
            "symbol":        symbol,
            "latest_close":  round(float(latest["close"]), 3),
            "change_pct":    round(float(chg), 3),
            "volume":        int(latest["volume"]),
            "turnover_rate": float(latest.get("turnover_rate", 0) or 0),
            "bars":          len(df),
            "history_tail":  _df_tail(df.rename(columns={"close": "Close", "volume": "Volume"}), 5),
            "provider":      "akshare",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 2. get_crypto_data
# ---------------------------------------------------------------------------

def _get_crypto_data(params: dict) -> dict:
    symbol   = params.get("symbol", "BTC/USDT").upper().replace("-", "/")
    exchange = params.get("exchange", "binance")
    timeframe = params.get("timeframe", "1d")
    limit    = int(params.get("limit", 100))

    if not _HAS_CCXT:
        # Fallback to yfinance for common crypto tickers
        if _HAS_YF:
            yf_sym = symbol.replace("/", "-") + ("" if symbol.endswith("USD") else "")
            return _get_market_data({"symbol": yf_sym, "period": "3mo"})
        return {"success": False, "error": "ccxt not installed: pip install ccxt"}

    try:
        ex_class = getattr(ccxt, exchange.lower(), None)
        if ex_class is None:
            return {"success": False, "error": f"Unknown exchange: {exchange}"}
        ex = ex_class({"enableRateLimit": True})
        ohlcv = ex.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not ohlcv:
            return {"success": False, "error": "Empty OHLCV data"}

        df = pd.DataFrame(ohlcv, columns=["ts", "open", "high", "low", "close", "volume"])
        df["date"] = pd.to_datetime(df["ts"], unit="ms")
        latest   = df.iloc[-1]
        prev     = df.iloc[-2]
        chg      = (latest["close"] - prev["close"]) / prev["close"] * 100
        vol_avg  = df["volume"].tail(20).mean()

        # Ticker for bid/ask
        try:
            ticker = ex.fetch_ticker(symbol)
        except Exception:
            ticker = {}

        return {
            "success":       True,
            "symbol":        symbol,
            "exchange":      exchange,
            "latest_close":  round(float(latest["close"]), 6),
            "change_pct_24h": round(float(chg), 3),
            "volume_24h":    float(latest["volume"]),
            "volume_avg_20d": round(float(vol_avg), 2),
            "bid":           ticker.get("bid"),
            "ask":           ticker.get("ask"),
            "bars":          len(df),
            "provider":      "ccxt",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 3. get_forex_data
# ---------------------------------------------------------------------------

def _get_forex_data(params: dict) -> dict:
    pair   = params.get("pair", "EURUSD=X")
    period = params.get("period", "3mo")
    if not _HAS_YF:
        return {"success": False, "error": "yfinance not installed"}
    # Normalise to yfinance format
    p = pair.replace("/", "").upper()
    if not p.endswith("=X"):
        p += "=X"
    return _get_market_data({"symbol": p, "period": period})


# ---------------------------------------------------------------------------
# 4. calculate_factors
# ---------------------------------------------------------------------------

def _calculate_factors(params: dict) -> dict:
    symbol = params.get("symbol", "AAPL")
    period = params.get("period", "1y")

    # ── Try Alibaba Cloud enhanced factor engine first ─────────────────
    if _HAS_CLOUD:
        try:
            cloud_factors = cloud_get_factors_sync(symbol)
            if cloud_factors and cloud_factors.get("success"):
                cloud_factors["provider"] = "aliyun_cloud"
                return cloud_factors
        except Exception as exc:
            logger.debug("Cloud factors failed for %s: %s — computing locally", symbol, exc)

    if _is_ashare(symbol) and _HAS_AK:
        data_result = _get_ashare_data(
            symbol,
            _parse_date(None, 365),
            _today(),
        )
    elif _HAS_YF:
        data_result = _get_market_data({"symbol": symbol, "period": period})
    else:
        return {"success": False, "error": "No data source available"}

    if not data_result.get("success"):
        return data_result

    # Re-fetch full history for factor computation
    if _is_ashare(symbol) and _HAS_AK:
        norm = _normalise_ashare(symbol)
        code = norm[2:]
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=_parse_date(None, 365).replace("-", ""),
                                 end_date=_today().replace("-", ""),
                                 adjust="qfq")
        df = df.rename(columns={"收盘": "Close", "成交量": "Volume",
                                 "开盘": "Open", "最高": "High", "最低": "Low"})
    else:
        tkr = yf.Ticker(symbol)
        df  = tkr.history(period=period)

    if df is None or len(df) < 20:
        return {"success": False, "error": "Insufficient history for factors"}

    close  = df["Close"].astype(float)
    volume = df["Volume"].astype(float)
    ret    = close.pct_change().dropna()

    factors: Dict[str, Any] = {"symbol": symbol}

    # ── Price momentum ─────────────────────────────────────────────────────
    for n in (5, 10, 20, 60, 120):
        if len(close) > n:
            factors[f"return_{n}d"] = round(float(close.pct_change(n).iloc[-1]), 5)

    # ── Moving averages ────────────────────────────────────────────────────
    for n in (5, 10, 20, 60, 120, 200):
        if len(close) >= n:
            ma = close.rolling(n).mean().iloc[-1]
            factors[f"ma_{n}"] = round(float(ma), 4)
            factors[f"ma_{n}_gap"] = round(float(close.iloc[-1] / ma - 1), 5)

    # ── Volatility ─────────────────────────────────────────────────────────
    for n in (10, 20, 60):
        if len(ret) >= n:
            factors[f"volatility_{n}d"] = round(float(ret.tail(n).std() * np.sqrt(252)), 5)

    # ── Volume ─────────────────────────────────────────────────────────────
    if len(volume) >= 20:
        vol_ma20 = volume.rolling(20).mean().iloc[-1]
        factors["volume_ratio_20d"] = round(float(volume.iloc[-1] / vol_ma20), 3) if vol_ma20 > 0 else None

    # ── RSI ────────────────────────────────────────────────────────────────
    if _HAS_TA and len(close) >= 14:
        rsi = ta.rsi(close, length=14)
        if rsi is not None and not rsi.empty:
            factors["rsi_14"] = round(float(rsi.iloc[-1]), 2)
    else:
        # Manual RSI
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rs    = gain / loss.replace(0, np.nan)
        rsi_v = (100 - 100 / (1 + rs)).iloc[-1]
        factors["rsi_14"] = round(float(rsi_v), 2) if not np.isnan(rsi_v) else None

    # ── MACD ───────────────────────────────────────────────────────────────
    if len(close) >= 26:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9, adjust=False).mean()
        factors["macd"]          = round(float(macd.iloc[-1]), 5)
        factors["macd_signal"]   = round(float(sig.iloc[-1]),  5)
        factors["macd_hist"]     = round(float((macd - sig).iloc[-1]), 5)

    # ── Bollinger Bands ────────────────────────────────────────────────────
    if len(close) >= 20:
        bb_ma  = close.rolling(20).mean()
        bb_std = close.rolling(20).std()
        bb_up  = bb_ma + 2 * bb_std
        bb_lo  = bb_ma - 2 * bb_std
        prc    = close.iloc[-1]
        factors["bb_position"] = round(float((prc - bb_lo.iloc[-1]) /
                                              (bb_up.iloc[-1] - bb_lo.iloc[-1] + 1e-8)), 4)
        factors["bb_upper"] = round(float(bb_up.iloc[-1]), 4)
        factors["bb_lower"] = round(float(bb_lo.iloc[-1]), 4)

    # ── Beta vs market ─────────────────────────────────────────────────────
    if _HAS_YF and not _is_ashare(symbol) and len(ret) >= 60:
        try:
            bench = yf.Ticker("SPY").history(period=period)["Close"].pct_change().dropna()
            aligned = pd.DataFrame({"asset": ret, "bench": bench}).dropna().tail(60)
            if len(aligned) >= 30:
                beta_v = float(np.cov(aligned["asset"], aligned["bench"])[0, 1] /
                                np.var(aligned["bench"]))
                factors["beta_60d"] = round(beta_v, 3)
        except Exception:
            pass

    # ── Trend score ────────────────────────────────────────────────────────
    trend = 0
    for ma_key, w in [("ma_5_gap", 0.15), ("ma_20_gap", 0.35), ("ma_60_gap", 0.50)]:
        v = factors.get(ma_key, 0.0) or 0.0
        trend += w * (1 if v > 0 else -1)
    factors["trend_score"] = round(trend, 3)

    factors["provider"] = "local"
    return {"success": True, **factors}


# ---------------------------------------------------------------------------
# 5. backtest_strategy
# ---------------------------------------------------------------------------

def _backtest_strategy(params: dict) -> dict:
    """
    Simple backtest engine.  Supports:
      strategy = "sma_cross" | "rsi_mean_revert" | "momentum" | "buy_hold"
    """
    symbol   = params.get("symbol", "AAPL")
    strategy = params.get("strategy", "sma_cross").lower().replace(" ", "_").replace("-", "_")
    start    = params.get("start", _parse_date(None, 3 * 365))
    end      = params.get("end", _today())
    fast     = int(params.get("fast_period", 20))
    slow     = int(params.get("slow_period", 60))
    rsi_lo   = float(params.get("rsi_oversold", 30))
    rsi_hi   = float(params.get("rsi_overbought", 70))
    mom_n    = int(params.get("momentum_period", 20))

    # Fetch data
    if _is_ashare(symbol) and _HAS_AK:
        norm = _normalise_ashare(symbol)
        code = norm[2:]
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=start.replace("-", ""),
                                 end_date=end.replace("-", ""),
                                 adjust="qfq")
        df = df.rename(columns={"收盘": "Close", "成交量": "Volume",
                                 "日期": "Date", "开盘": "Open",
                                 "最高": "High", "最低": "Low"})
        df["Date"] = pd.to_datetime(df["Date"])
        df = df.set_index("Date").sort_index()
    elif _HAS_YF:
        df = yf.Ticker(symbol).history(start=start, end=end)
    else:
        return {"success": False, "error": "No data source available"}

    if df is None or len(df) < max(slow, 60):
        return {"success": False, "error": f"Insufficient data: {len(df) if df is not None else 0} bars"}

    close = df["Close"].astype(float)

    # ── Signal generation ─────────────────────────────────────────────────
    if strategy in ("sma_cross", "ma_cross"):
        ma_f = close.rolling(fast).mean()
        ma_s = close.rolling(slow).mean()
        signal = (ma_f > ma_s).astype(int)

    elif strategy in ("rsi_mean_revert", "rsi"):
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
        signal = pd.Series(0, index=rsi.index)
        in_pos = False
        for i in range(len(rsi)):
            r = rsi.iloc[i]
            if np.isnan(r):
                signal.iloc[i] = 0
            elif r < rsi_lo and not in_pos:
                in_pos = True
                signal.iloc[i] = 1
            elif r > rsi_hi and in_pos:
                in_pos = False
                signal.iloc[i] = 0
            else:
                signal.iloc[i] = int(in_pos)

    elif strategy in ("momentum", "mom"):
        ret_n  = close.pct_change(mom_n)
        signal = (ret_n > 0).astype(int)

    else:  # buy_hold
        signal = pd.Series(1, index=close.index)

    # ── Vectorbt backtest (if available) ─────────────────────────────────
    if _HAS_VBT:
        try:
            pf = vbt.Portfolio.from_signals(
                close, signal.shift(1).fillna(0) == 1,
                signal.shift(1).fillna(0) == 0,
                freq="D",
            )
            stats = pf.stats()
            return {
                "success":          True,
                "symbol":           symbol,
                "strategy":         strategy,
                "start":            str(df.index[0].date()),
                "end":              str(df.index[-1].date()),
                "bars":             len(df),
                "total_return":     round(float(stats.get("Total Return [%]", 0) / 100), 4),
                "annual_return":    round(float(stats.get("Annualized Return [%]", 0) / 100), 4),
                "sharpe_ratio":     round(float(stats.get("Sharpe Ratio", 0) or 0), 3),
                "sortino_ratio":    round(float(stats.get("Sortino Ratio", 0) or 0), 3),
                "max_drawdown":     round(float(stats.get("Max Drawdown [%]", 0) / 100), 4),
                "win_rate":         round(float(stats.get("Win Rate [%]", 0) / 100), 3),
                "total_trades":     int(stats.get("Total Trades", 0) or 0),
                "provider":         "vectorbt",
            }
        except Exception as exc:
            logger.debug("vectorbt failed, falling back to manual: %s", exc)

    # ── Manual pandas backtest fallback ──────────────────────────────────
    sig      = signal.shift(1).fillna(0)
    ret      = close.pct_change().fillna(0)
    port_ret = (ret * sig).fillna(0)
    equity   = (1 + port_ret).cumprod()

    total_r   = float(equity.iloc[-1] - 1)
    n_years   = max((df.index[-1] - df.index[0]).days / 365.25, 0.01)
    annual_r  = float((1 + total_r) ** (1 / n_years) - 1)
    rf        = 0.04  # risk-free rate
    excess    = port_ret - rf / 252
    sharpe    = float(excess.mean() / port_ret.std() * np.sqrt(252)) if port_ret.std() > 0 else 0.0
    neg_ret   = port_ret[port_ret < 0]
    sortino   = float(excess.mean() / neg_ret.std() * np.sqrt(252)) if len(neg_ret) > 0 and neg_ret.std() > 0 else 0.0

    # Max drawdown
    peak    = equity.cummax()
    dd      = (equity - peak) / peak
    max_dd  = float(dd.min())

    # Trade stats
    trades     = (sig.diff() != 0) & (sig == 1)
    exits      = (sig.diff() != 0) & (sig == 0)
    n_trades   = int(trades.sum())
    trade_rets = []
    entry_date = None
    entry_price = None
    for date, row in sig.items():
        if row == 1 and entry_price is None:
            entry_price = float(close.loc[date])
            entry_date  = date
        elif row == 0 and entry_price is not None:
            trade_rets.append(float(close.loc[date]) / entry_price - 1)
            entry_price = None

    win_rate = sum(1 for r in trade_rets if r > 0) / len(trade_rets) if trade_rets else 0.0

    # Benchmark: buy-and-hold
    bh_ret  = float(close.iloc[-1] / close.iloc[0] - 1)

    return {
        "success":          True,
        "symbol":           symbol,
        "strategy":         strategy,
        "start":            str(df.index[0].date()),
        "end":              str(df.index[-1].date()),
        "bars":             len(df),
        "total_return":     round(total_r, 4),
        "annual_return":    round(annual_r, 4),
        "sharpe_ratio":     round(sharpe, 3),
        "sortino_ratio":    round(sortino, 3),
        "max_drawdown":     round(max_dd, 4),
        "win_rate":         round(win_rate, 3),
        "total_trades":     n_trades,
        "benchmark_return": round(bh_ret, 4),
        "alpha":            round(annual_r - bh_ret / n_years, 4),
        "provider":         "pandas",
    }


# ---------------------------------------------------------------------------
# 6. get_risk_metrics
# ---------------------------------------------------------------------------

def _get_risk_metrics(params: dict) -> dict:
    symbol     = params.get("symbol", "AAPL")
    period     = params.get("period", "1y")
    conf_level = float(params.get("confidence", 0.95))

    if _is_ashare(symbol) and _HAS_AK:
        data = _get_ashare_data(symbol, _parse_date(None, 365), _today())
        if not data.get("success"):
            return data
        norm = _normalise_ashare(symbol)
        code = norm[2:]
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=_parse_date(None, 365).replace("-", ""),
                                 end_date=_today().replace("-", ""),
                                 adjust="qfq")
        df = df.rename(columns={"收盘": "Close"})
        close = df["Close"].astype(float)
    elif _HAS_YF:
        close = yf.Ticker(symbol).history(period=period)["Close"].astype(float)
    else:
        return {"success": False, "error": "No data source"}

    if close is None or len(close) < 30:
        return {"success": False, "error": "Insufficient data"}

    ret  = close.pct_change().dropna()
    mu   = float(ret.mean())
    sig  = float(ret.std())

    # VaR (parametric)
    if _HAS_SCIPY:
        var_daily = float(-sp_stats.norm.ppf(1 - conf_level, mu, sig))
    else:
        var_daily = float(-np.percentile(ret, (1 - conf_level) * 100))

    var_monthly = var_daily * np.sqrt(21)

    # CVaR (Expected Shortfall)
    losses = -ret
    cvar   = float(losses[losses >= var_daily].mean())

    # Max drawdown
    equity = (1 + ret).cumprod()
    peak   = equity.cummax()
    dd     = (equity - peak) / peak
    max_dd = float(dd.min())

    # Calmar
    annual_ret = mu * 252
    calmar     = annual_ret / abs(max_dd) if max_dd != 0 else 0.0

    # Downside deviation (Sortino denominator)
    neg_ret     = ret[ret < 0]
    down_dev    = float(neg_ret.std() * np.sqrt(252)) if len(neg_ret) > 0 else 0.0

    # Skewness / kurtosis
    skew_v = float(ret.skew()) if _HAS_SCIPY else 0.0
    kurt_v = float(ret.kurtosis()) if _HAS_SCIPY else 0.0

    return {
        "success":             True,
        "symbol":              symbol,
        "confidence_level":    conf_level,
        "var_daily":           round(var_daily, 5),
        "var_monthly":         round(float(var_monthly), 5),
        "cvar_daily":          round(cvar, 5),
        "max_drawdown":        round(max_dd, 5),
        "annual_volatility":   round(float(sig * np.sqrt(252)), 5),
        "annual_return":       round(float(annual_ret), 5),
        "sharpe_ratio":        round(float((annual_ret - 0.04) / (sig * np.sqrt(252))), 3) if sig > 0 else 0.0,
        "calmar_ratio":        round(float(calmar), 3),
        "downside_deviation":  round(down_dev, 5),
        "skewness":            round(skew_v, 3),
        "kurtosis":            round(kurt_v, 3),
        "provider":            "local",
    }


# ---------------------------------------------------------------------------
# 7. optimize_positions
# ---------------------------------------------------------------------------

def _optimize_positions(params: dict) -> dict:
    symbols  = params.get("symbols", ["AAPL", "MSFT", "GOOGL"])
    period   = params.get("period", "1y")
    method   = params.get("method", "max_sharpe")  # max_sharpe | min_var | equal_weight
    rf       = float(params.get("risk_free_rate", 0.04))

    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",")]

    # Fetch returns
    prices = {}
    for sym in symbols:
        if _is_ashare(sym) and _HAS_AK:
            norm = _normalise_ashare(sym)
            code = norm[2:]
            df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                     start_date=_parse_date(None, 365).replace("-", ""),
                                     end_date=_today().replace("-", ""),
                                     adjust="qfq")
            df = df.rename(columns={"收盘": "Close"})
            prices[sym] = df["Close"].astype(float).values
        elif _HAS_YF:
            prices[sym] = yf.Ticker(sym).history(period=period)["Close"].astype(float).values

    if not prices:
        return {"success": False, "error": "Could not fetch prices"}

    # Align length
    min_len = min(len(v) for v in prices.values())
    ret_mat = np.column_stack([prices[s][-min_len:] for s in symbols])
    ret_mat = np.diff(np.log(ret_mat), axis=0)

    mu_vec  = ret_mat.mean(axis=0) * 252
    cov_mat = np.cov(ret_mat.T) * 252
    n       = len(symbols)

    if method == "equal_weight":
        weights = np.ones(n) / n
    elif method == "min_var":
        # Analytical min-variance
        try:
            inv_cov = np.linalg.inv(cov_mat + 1e-8 * np.eye(n))
            ones    = np.ones(n)
            w       = inv_cov @ ones
            weights = w / w.sum()
        except np.linalg.LinAlgError:
            weights = np.ones(n) / n
    else:  # max_sharpe — gradient-free grid search
        best_sharpe = -np.inf
        weights     = np.ones(n) / n
        rng         = np.random.default_rng(42)
        for _ in range(10000):
            w_try  = rng.dirichlet(np.ones(n))
            p_ret  = float(w_try @ mu_vec)
            p_vol  = float(np.sqrt(w_try @ cov_mat @ w_try))
            sharpe = (p_ret - rf) / p_vol if p_vol > 0 else -np.inf
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                weights     = w_try

    # Portfolio metrics
    p_ret  = float(weights @ mu_vec)
    p_vol  = float(np.sqrt(weights @ cov_mat @ weights))
    sharpe = (p_ret - rf) / p_vol if p_vol > 0 else 0.0

    return {
        "success":          True,
        "method":           method,
        "symbols":          symbols,
        "weights":          {sym: round(float(w), 4) for sym, w in zip(symbols, weights)},
        "portfolio_return": round(p_ret, 4),
        "portfolio_vol":    round(p_vol, 4),
        "sharpe_ratio":     round(sharpe, 3),
        "provider":         "local",
    }


# ---------------------------------------------------------------------------
# 8. get_sector_performance (A股 + US)
# ---------------------------------------------------------------------------

def _get_sector_performance(params: dict) -> dict:
    market = params.get("market", "cn").lower()  # cn | us

    if market == "cn" and _HAS_AK:
        try:
            df = ak.stock_board_industry_name_em()
            if df is None or df.empty:
                raise ValueError("empty sector data")
            df = df.rename(columns={
                "板块名称": "sector", "最新价": "price",
                "涨跌幅": "change_pct", "成交额": "amount",
                "上涨家数": "rising", "下跌家数": "falling",
            })
            top    = df.nlargest(5,  "change_pct")[["sector", "change_pct"]].to_dict("records")
            bottom = df.nsmallest(5, "change_pct")[["sector", "change_pct"]].to_dict("records")
            return {
                "success":   True,
                "market":    "cn",
                "date":      _today(),
                "top_sectors":    top,
                "bottom_sectors": bottom,
                "total_sectors":  len(df),
                "provider":  "akshare",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    elif _HAS_YF:
        sector_etfs = {
            "Technology":    "XLK",  "Healthcare":    "XLV",
            "Financials":    "XLF",  "Consumer Disc": "XLY",
            "Industrials":   "XLI",  "Energy":        "XLE",
            "Utilities":     "XLU",  "Real Estate":   "XLRE",
            "Materials":     "XLB",  "Comm Services": "XLC",
            "Consumer Staples": "XLP",
        }
        perf = []
        for name, etf in sector_etfs.items():
            try:
                h = yf.Ticker(etf).history(period="5d")
                if len(h) >= 2:
                    chg = (h["Close"].iloc[-1] - h["Close"].iloc[-2]) / h["Close"].iloc[-2] * 100
                    perf.append({"sector": name, "etf": etf, "change_pct": round(float(chg), 2)})
            except Exception:
                pass
        perf.sort(key=lambda x: x["change_pct"], reverse=True)
        return {
            "success":       True,
            "market":        "us",
            "date":          _today(),
            "sectors":       perf,
            "top_sectors":   perf[:3],
            "bottom_sectors": perf[-3:],
            "provider":      "yfinance",
        }

    return {"success": False, "error": "akshare / yfinance not available"}


# ---------------------------------------------------------------------------
# 9. A股 northbound fund flow (北向资金)
# ---------------------------------------------------------------------------

def _get_northbound_flow(params: dict) -> dict:
    days = int(params.get("days", 10))
    if not _HAS_AK:
        return {"success": False, "error": "akshare not installed"}
    try:
        df = ak.stock_hsgt_north_net_flow_in_em(symbol="沪深港通")
        df = df.tail(days)
        latest_flow = float(df.iloc[-1].get("当日净买额", df.iloc[-1].iloc[1]))
        total_flow  = float(df.iloc[:, 1].sum())
        return {
            "success":      True,
            "latest_net_buy_yi": round(latest_flow / 1e8, 2),
            "total_net_buy_yi":  round(total_flow / 1e8, 2),
            "days":         days,
            "trend":        "inflow" if total_flow > 0 else "outflow",
            "provider":     "akshare",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 10. screen_ashare — 选股筛选器
# ---------------------------------------------------------------------------

def _screen_ashare(params: dict) -> dict:
    """Screen A-share stocks by fundamental & technical criteria."""
    min_roe         = float(params.get("min_roe", 10))
    max_pe          = float(params.get("max_pe", 50))
    min_revenue_gr  = float(params.get("min_revenue_growth", 10))
    min_market_cap  = float(params.get("min_market_cap_yi", 0))    # 亿元
    limit           = int(params.get("limit", 20))

    if not _HAS_AK:
        return {"success": False, "error": "akshare not installed"}

    try:
        # A股实时行情
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"success": False, "error": "No spot data"}

        df = df.rename(columns={
            "代码": "code", "名称": "name", "最新价": "price",
            "涨跌幅": "change_pct", "总市值": "market_cap",
            "市盈率-动态": "pe_dynamic", "市净率": "pb",
            "成交量": "volume", "换手率": "turnover_rate",
        })

        # Basic filters
        df = df[~df["name"].str.contains("ST|退", na=False)]
        df = df[df["price"].notna() & (df["price"] > 0)]

        if "pe_dynamic" in df.columns:
            df = df[df["pe_dynamic"].between(0.1, max_pe, inclusive="both")]

        if "market_cap" in df.columns and min_market_cap > 0:
            df = df[df["market_cap"] >= min_market_cap * 1e8]

        # Score on momentum
        if "change_pct" in df.columns:
            df["score"] = df["change_pct"].fillna(0)
            df = df.nlargest(limit, "score")

        cols = ["code", "name", "price", "change_pct", "pe_dynamic", "pb",
                "market_cap", "turnover_rate"]
        cols = [c for c in cols if c in df.columns]
        result_df = df[cols].head(limit)

        # Format market_cap to 亿元
        if "market_cap" in result_df.columns:
            result_df = result_df.copy()
            result_df["market_cap_yi"] = (result_df["market_cap"] / 1e8).round(1)

        stocks = result_df.to_dict("records")
        return {
            "success":      True,
            "count":        len(stocks),
            "stocks":       stocks,
            "criteria":     params,
            "provider":     "akshare",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 11. get_limit_up_pool — 涨停板池
# ---------------------------------------------------------------------------

def _get_limit_up_pool(params: dict) -> dict:
    date = params.get("date", _today())
    if not _HAS_AK:
        return {"success": False, "error": "akshare not installed"}
    try:
        date_str = date.replace("-", "")
        df = ak.stock_zt_pool_em(date=date_str)
        if df is None or df.empty:
            return {"success": True, "count": 0, "stocks": [], "date": date}

        df = df.rename(columns={
            "代码": "code", "名称": "name",
            "涨停统计": "limit_streak", "连续涨停": "consecutive",
            "首次封板时间": "first_lock_time", "涨停类型": "limit_type",
        })
        cols = [c for c in ["code", "name", "limit_streak", "consecutive",
                             "first_lock_time", "limit_type"] if c in df.columns]
        return {
            "success":  True,
            "date":     date,
            "count":    len(df),
            "stocks":   df[cols].head(50).to_dict("records"),
            "provider": "akshare",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 12. get_market_indices
# ---------------------------------------------------------------------------

def _get_market_indices(params: dict) -> dict:
    indices = {
        # US
        "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "Dow Jones": "^DJI",
        "VIX": "^VIX", "Russell 2000": "^RUT",
        # CN
        "上证综指": "000001.SS", "深证成指": "399001.SZ", "创业板": "399006.SZ",
        # Global
        "Nikkei 225": "^N225", "FTSE 100": "^FTSE", "DAX": "^GDAXI",
        "Hang Seng": "^HSI",
        # Commodities
        "Gold": "GC=F", "Crude Oil": "CL=F", "Bitcoin": "BTC-USD",
    }

    if not _HAS_YF:
        if _HAS_AK:
            # A股 indices only
            try:
                df = ak.stock_zh_index_spot_em()
                return {"success": True, "indices": df.head(10).to_dict("records"), "provider": "akshare"}
            except Exception as exc:
                return {"success": False, "error": str(exc)}
        return {"success": False, "error": "yfinance not installed"}

    results = []
    for name, ticker in indices.items():
        try:
            h = yf.Ticker(ticker).history(period="2d")
            if len(h) >= 1:
                latest = float(h["Close"].iloc[-1])
                chg    = (float(h["Close"].iloc[-1]) - float(h["Close"].iloc[0])) / float(h["Close"].iloc[0]) * 100 if len(h) >= 2 else 0.0
                results.append({"name": name, "ticker": ticker,
                                 "price": round(latest, 2), "change_pct": round(chg, 2)})
        except Exception:
            pass

    return {"success": True, "indices": results, "date": _today(), "provider": "yfinance"}


# ---------------------------------------------------------------------------
# 13. analyze_news (local sentiment via keyword scoring)
# ---------------------------------------------------------------------------

def _get_data_key(service: str) -> str:
    """Read a data service API key from env var or ~/.arthera/providers.json."""
    _DATA_ENV = {
        "finnhub":      "FINNHUB_API_KEY",
        "newsapi":      "NEWS_API_KEY",
        "brave":        "BRAVE_SEARCH_API_KEY",
        "alphavantage": "ALPHA_VANTAGE_API_KEY",
        "coingecko":    "COINGECKO_API_KEY",
        "twelvedata":   "TWELVEDATA_API_KEY",
    }
    env_var = _DATA_ENV.get(service, "")
    if env_var:
        val = os.getenv(env_var, "")
        if val:
            return val
    # Fall back to providers.json
    try:
        import pathlib as _pl, json as _json
        pf = _pl.Path.home() / ".arthera" / "providers.json"
        if pf.exists():
            raw = _json.loads(pf.read_text(encoding="utf-8"))
            entry = raw.get("data", {}).get(service, {})
            if entry.get("api_key"):
                return entry["api_key"]
    except Exception:
        pass
    return ""


def _fetch_news_finnhub(symbol: str, limit: int) -> list:
    """Fetch stock news from Finnhub API."""
    key = _get_data_key("finnhub")
    if not key:
        return []
    try:
        import requests as _req, datetime as _dt
        end_dt   = _dt.date.today().isoformat()
        start_dt = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
        url = (f"https://finnhub.io/api/v1/company-news"
               f"?symbol={symbol}&from={start_dt}&to={end_dt}&token={key}")
        resp = _req.get(url, timeout=8)
        if resp.status_code == 200:
            items = resp.json()[:limit]
            return [{"title": a.get("headline", ""), "source": a.get("source", ""),
                     "time": str(a.get("datetime", "")), "url": a.get("url", "")}
                    for a in items]
    except Exception:
        pass
    return []


def _fetch_news_newsapi(query: str, limit: int) -> list:
    """Fetch news from NewsAPI.org."""
    key = _get_data_key("newsapi")
    if not key:
        return []
    try:
        import requests as _req
        url = (f"https://newsapi.org/v2/everything"
               f"?q={query}&language=en&sortBy=publishedAt&pageSize={limit}&apiKey={key}")
        resp = _req.get(url, timeout=8)
        if resp.status_code == 200:
            data = resp.json()
            return [{"title": a.get("title", ""), "source": a.get("source", {}).get("name", ""),
                     "time": a.get("publishedAt", "")[:10], "url": a.get("url", "")}
                    for a in data.get("articles", [])[:limit]]
    except Exception:
        pass
    return []


def _analyze_news(params: dict) -> dict:
    symbol  = params.get("symbol", "")
    query   = params.get("query", params.get("topic", symbol))
    topic   = query or symbol
    limit   = int(params.get("limit", 5))

    # ── 1. A股 news via akshare (no key needed) ───────────────────────────────
    if _HAS_AK and topic and _is_ashare(topic):
        try:
            norm = _normalise_ashare(topic) if _is_ashare(topic) else topic
            code = norm[2:] if len(norm) > 2 else norm
            df   = ak.stock_news_em(symbol=code)
            if df is not None and not df.empty:
                df = df.head(limit)
                news_list = []
                for _, row in df.iterrows():
                    title = str(row.get("新闻标题", row.get("title", "")))
                    score = _score_sentiment(title)
                    news_list.append({
                        "title":     title,
                        "time":      str(row.get("发布时间", "")),
                        "sentiment": "positive" if score > 0 else ("negative" if score < 0 else "neutral"),
                        "score":     score,
                    })
                avg_score = sum(n["score"] for n in news_list) / len(news_list) if news_list else 0
                return {
                    "success": True, "symbol": topic, "news": news_list,
                    "overall_sentiment": "positive" if avg_score > 0.1 else ("negative" if avg_score < -0.1 else "neutral"),
                    "avg_score": round(avg_score, 3), "provider": "akshare",
                }
        except Exception as exc:
            pass  # fall through to other providers

    # ── 2. Finnhub (if key available) ────────────────────────────────────────
    if symbol and not _is_ashare(symbol):
        articles = _fetch_news_finnhub(symbol.upper(), limit)
        if articles:
            news_list = []
            for a in articles:
                score = _score_sentiment(a["title"])
                news_list.append({**a, "sentiment": "positive" if score > 0 else ("negative" if score < 0 else "neutral"), "score": score})
            avg_score = sum(n["score"] for n in news_list) / len(news_list) if news_list else 0
            return {
                "success": True, "symbol": symbol, "news": news_list,
                "overall_sentiment": "positive" if avg_score > 0.1 else ("negative" if avg_score < -0.1 else "neutral"),
                "avg_score": round(avg_score, 3), "provider": "finnhub",
            }

    # ── 3. NewsAPI (if key available) ─────────────────────────────────────────
    search_query = topic or symbol or "market"
    articles = _fetch_news_newsapi(search_query, limit)
    if articles:
        news_list = []
        for a in articles:
            score = _score_sentiment(a["title"])
            news_list.append({**a, "sentiment": "positive" if score > 0 else ("negative" if score < 0 else "neutral"), "score": score})
        avg_score = sum(n["score"] for n in news_list) / len(news_list) if news_list else 0
        return {
            "success": True, "symbol": topic, "news": news_list,
            "overall_sentiment": "positive" if avg_score > 0.1 else ("negative" if avg_score < -0.1 else "neutral"),
            "avg_score": round(avg_score, 3), "provider": "newsapi",
        }

    # ── 4. No data available ──────────────────────────────────────────────────
    tip = "配置数据服务 key: /apikey set finnhub <key> 或 /apikey set newsapi <key>"
    return {"success": False, "error": tip}


def _score_sentiment(text: str) -> float:
    """Keyword-based sentiment scorer (0.0 = neutral, +1 = very positive, -1 = very negative)."""
    pos = ["上涨", "涨停", "突破", "创新高", "利好", "增长", "盈利", "bull", "beat", "growth", "record", "profit", "rally", "buy", "upgrade"]
    neg = ["下跌", "跌停", "亏损", "利空", "减少", "违规", "被罚", "风险", "bear", "miss", "loss", "decline", "sell", "downgrade", "fraud"]
    t   = text.lower()
    score = sum(1 for w in pos if w in t) - sum(1 for w in neg if w in t)
    return float(max(-1, min(1, score * 0.25)))


# ---------------------------------------------------------------------------
# 14. get_commodities_data — gold, silver, oil, gas, copper, wheat, etc.
# ---------------------------------------------------------------------------

# Common commodity keywords → yfinance futures tickers
_COMMODITY_MAP: Dict[str, str] = {
    # Precious metals
    "gold":       "GC=F",   "silver":     "SI=F",   "platinum":   "PL=F",   "palladium":  "PA=F",
    # Energy
    "oil":        "CL=F",   "crude":      "CL=F",   "crude oil":  "CL=F",
    "brent":      "BZ=F",   "natural gas": "NG=F",  "natgas":     "NG=F",   "gas":        "NG=F",
    "gasoline":   "RB=F",   "heating oil": "HO=F",
    # Base metals
    "copper":     "HG=F",   "aluminum":   "ALI=F",  "nickel":     "NI=F",   "zinc":       "ZNC=F",
    # Agricultural
    "wheat":      "ZW=F",   "corn":       "ZC=F",   "soybean":    "ZS=F",   "soybeans":   "ZS=F",
    "coffee":     "KC=F",   "cocoa":      "CC=F",   "sugar":      "SB=F",   "cotton":     "CT=F",
    "rice":       "ZR=F",   "oats":       "ZO=F",
    # Livestock
    "cattle":     "LE=F",   "hogs":       "HE=F",
    # Softs / other
    "lumber":     "LBS=F",  "rubber":     "rubber", "iron ore":   "TIO=F",
    # China-traded (A股 futures) — map to closest international proxy
    "螺纹钢":     "HG=F",   "铁矿石":     "TIO=F",  "铜":         "HG=F",
    "黄金":       "GC=F",   "白银":       "SI=F",   "原油":       "CL=F",   "天然气":     "NG=F",
}


def _get_commodities_data(params: dict) -> dict:
    """Commodity spot/futures price lookup via yfinance."""
    commodity = str(params.get("commodity", "gold")).strip().lower()

    if not _HAS_YF:
        return {"success": False, "error": "yfinance not installed: pip install yfinance"}

    # Resolve ticker
    ticker = _COMMODITY_MAP.get(commodity)
    if ticker is None:
        # Try direct uppercase (user may have passed ticker like GC=F)
        ticker = commodity.upper()

    try:
        tkr  = yf.Ticker(ticker)
        hist = tkr.history(period="5d")
        if hist.empty:
            # Fallback: try without =F suffix
            alt = commodity.upper().replace("=F", "") + "=F"
            hist = yf.Ticker(alt).history(period="5d")
            if hist.empty:
                return {
                    "success": False,
                    "error":   f"No data for commodity '{commodity}' (ticker={ticker}). "
                               "Try using the yfinance ticker directly, e.g. GC=F for gold.",
                }
            ticker = alt

        latest   = float(hist["Close"].iloc[-1])
        prev     = float(hist["Close"].iloc[-2]) if len(hist) > 1 else latest
        chg      = (latest - prev) / prev * 100 if prev else 0.0
        high_5d  = float(hist["High"].max())
        low_5d   = float(hist["Low"].min())
        vol_5d   = float(hist["Volume"].mean())

        # Longer term context
        hist_1y  = tkr.history(period="1y")
        high_52w = float(hist_1y["High"].max())  if not hist_1y.empty else None
        low_52w  = float(hist_1y["Low"].min())   if not hist_1y.empty else None
        ret_1y   = float((hist_1y["Close"].iloc[-1] / hist_1y["Close"].iloc[0] - 1)) \
                   if len(hist_1y) > 1 else None

        info      = tkr.fast_info
        currency  = getattr(info, "currency", "USD")

        return {
            "success":       True,
            "commodity":     commodity,
            "ticker":        ticker,
            "latest_price":  round(latest, 4),
            "change_pct":    round(chg, 3),
            "currency":      currency,
            "high_5d":       round(high_5d, 4),
            "low_5d":        round(low_5d, 4),
            "volume_5d_avg": int(vol_5d),
            "high_52w":      round(high_52w, 4) if high_52w else None,
            "low_52w":       round(low_52w, 4) if low_52w else None,
            "return_1y":     round(ret_1y, 4) if ret_1y is not None else None,
            "pct_from_52w_high": round((latest / high_52w - 1), 4) if high_52w else None,
            "provider":      "yfinance",
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# 15. get_futures_data — generic futures via yfinance
# ---------------------------------------------------------------------------

def _get_futures_data(params: dict) -> dict:
    """Futures contract data (equity index futures, VIX, etc.)"""
    contract = str(params.get("contract", params.get("symbol", "ES=F"))).strip().upper()

    # Common index futures shortcuts
    _FUTURES_MAP = {
        "SP500": "ES=F", "SPX": "ES=F", "S&P": "ES=F",
        "NQ": "NQ=F", "NASDAQ": "NQ=F",
        "DOW": "YM=F", "DJIA": "YM=F",
        "RUSSELL": "RTY=F", "RUT": "RTY=F",
        "VIX": "^VIX",
        "NIKKEI": "NK=F", "DAX": "FDAX=F",
        "HSI": "HSI=F",
    }
    ticker = _FUTURES_MAP.get(contract, contract)
    if not ticker.endswith("=F") and not ticker.startswith("^"):
        ticker = ticker + "=F"

    return _get_market_data({"symbol": ticker, "period": "5d"})


# ---------------------------------------------------------------------------
# 15b. get_bonds_data (US Treasury yields via yfinance)
# ---------------------------------------------------------------------------

def _get_bonds_data(params: dict) -> dict:
    if not _HAS_YF:
        return {"success": False, "error": "yfinance not installed"}
    tickers = {
        "2Y": "^IRX", "5Y": "^FVX", "10Y": "^TNX", "30Y": "^TYX",
    }
    results = {}
    for tenor, sym in tickers.items():
        try:
            h = yf.Ticker(sym).history(period="5d")
            if not h.empty:
                results[tenor] = round(float(h["Close"].iloc[-1]), 3)
        except Exception:
            pass
    if not results:
        return {"success": False, "error": "Could not fetch yield data"}
    # Yield curve shape
    if "2Y" in results and "10Y" in results:
        results["10Y_2Y_spread"] = round(results["10Y"] - results["2Y"], 3)
        results["curve_shape"]   = "normal" if results["10Y_2Y_spread"] > 0 else "inverted"
    return {"success": True, "yields": results, "provider": "yfinance"}


# ---------------------------------------------------------------------------
# 16. get_ai_signal — Alibaba Cloud DeepSeek-powered signal
# ---------------------------------------------------------------------------

def _get_ai_signal(params: dict) -> dict:
    """
    AI trading signal from the Alibaba Cloud quant backend.
    Returns: action (BUY/SELL/HOLD), confidence, reasoning, stop_loss, take_profit.
    Falls back to calculate_factors when cloud is unavailable.
    """
    symbol = params.get("symbol", "600519")
    market = params.get("market", "CN" if _is_ashare(symbol) else "US")

    if _HAS_CLOUD:
        try:
            result = cloud_get_ai_signal_sync(symbol, market=market)
            if result and result.get("action"):
                return {"success": True, **result, "provider": "aliyun_cloud"}
        except Exception as exc:
            logger.debug("Cloud AI signal failed: %s", exc)

    # Local fallback: derive a simple signal from factors
    factors = _calculate_factors({"symbol": symbol})
    if not factors.get("success"):
        return factors

    rsi     = factors.get("rsi_14", 50)
    macd_h  = factors.get("macd_hist", 0) or 0
    trend   = factors.get("trend_score", 0) or 0
    vol_r   = factors.get("volume_ratio_20d", 1.0) or 1.0

    score = 0
    if rsi is not None:
        score += (0.4 if rsi < 40 else -0.4 if rsi > 70 else 0)
    score += (0.3 if macd_h > 0 else -0.3 if macd_h < 0 else 0)
    score += trend * 0.3

    action     = "BUY" if score > 0.3 else "SELL" if score < -0.3 else "HOLD"
    confidence = min(abs(score), 1.0)

    return {
        "success":    True,
        "symbol":     symbol,
        "action":     action,
        "confidence": round(confidence, 3),
        "reasoning":  f"RSI={rsi}, MACD_hist={macd_h:.5f}, trend={trend:.3f}, vol_ratio={vol_r:.2f}",
        "stop_loss":  None,
        "take_profit": None,
        "provider":   "local_fallback",
    }


# ---------------------------------------------------------------------------
# 17. get_market_insights — AI narrative analysis
# ---------------------------------------------------------------------------

def _get_market_insights(params: dict) -> dict:
    """
    Narrative market insights from Alibaba Cloud AI service.
    symbols: list of stock codes or comma-separated string.
    """
    raw = params.get("symbols", params.get("symbol", "sh600519"))
    if isinstance(raw, str):
        symbols = [s.strip() for s in raw.replace(",", " ").split() if s.strip()]
    else:
        symbols = list(raw)
    market = params.get("market", "CN")

    if _HAS_CLOUD:
        try:
            from aliyun_data_client import run_async
            result = run_async(AliyunDataClient.get().get_market_insights(symbols, market=market))
            if result:
                return {"success": True, **result, "provider": "aliyun_cloud"}
        except Exception as exc:
            logger.debug("Cloud market insights failed: %s", exc)

    # Local fallback: multi-stock factor summary
    summaries = []
    for sym in symbols[:5]:
        f = _calculate_factors({"symbol": sym})
        if f.get("success"):
            summaries.append({
                "symbol":      sym,
                "rsi_14":      f.get("rsi_14"),
                "trend_score": f.get("trend_score"),
                "macd_hist":   f.get("macd_hist"),
                "vol_ratio":   f.get("volume_ratio_20d"),
            })
    if not summaries:
        return {"success": False, "error": "Could not compute local factors"}

    return {
        "success":   True,
        "symbols":   symbols,
        "summaries": summaries,
        "note":      "Cloud AI not available — showing local factor summary",
        "provider":  "local_fallback",
    }


# ---------------------------------------------------------------------------
# 18. get_predictions — ML model predictions from cloud
# ---------------------------------------------------------------------------

def _get_predictions(params: dict) -> dict:
    """
    ML-powered stock return predictions from Alibaba Cloud QuantEngine.
    Falls back to momentum signal locally.
    """
    raw = params.get("symbols", params.get("symbol", "sh600519"))
    if isinstance(raw, str):
        symbols = [s.strip() for s in raw.replace(",", " ").split() if s.strip()]
    else:
        symbols = list(raw)
    days   = int(params.get("prediction_days", 5))
    market = params.get("market", "CN")

    if _HAS_CLOUD:
        try:
            from aliyun_data_client import run_async
            result = run_async(AliyunDataClient.get().get_predictions(symbols, prediction_days=days, market=market))
            if result and result.get("predictions"):
                return {"success": True, **result, "provider": "aliyun_cloud"}
        except Exception as exc:
            logger.debug("Cloud predictions failed: %s", exc)

    # Local fallback: simple momentum prediction
    preds = []
    for sym in symbols[:5]:
        f = _calculate_factors({"symbol": sym})
        if f.get("success"):
            r5  = f.get("return_5d", 0) or 0
            r20 = f.get("return_20d", 0) or 0
            predicted_return = round((r5 * 0.4 + r20 * 0.6) * (days / 20), 4)
            preds.append({
                "symbol":           sym,
                "predicted_return": predicted_return,
                "confidence":       0.5,
                "method":           "momentum",
            })

    if not preds:
        return {"success": False, "error": "No data for predictions"}

    return {
        "success":         True,
        "predictions":     preds,
        "prediction_days": days,
        "provider":        "local_fallback",
    }


# ---------------------------------------------------------------------------
# 19. cloud_backtest — advanced ML-powered backtest via Alibaba Cloud
# ---------------------------------------------------------------------------

def _cloud_backtest(params: dict) -> dict:
    """
    Full ML-powered backtest via Alibaba Cloud QuantEngine.
    Falls back to local pandas backtest when cloud is unavailable.
    """
    raw = params.get("symbols", params.get("symbol", "sh600519"))
    if isinstance(raw, str):
        symbols = [s.strip() for s in raw.replace(",", " ").split() if s.strip()]
    else:
        symbols = list(raw)

    strategy_cfg = params.get("strategy_config", {
        "model_type":              params.get("model_type", "lightgbm"),
        "backtest_period_months":  params.get("months", 12),
        "rebalance_freq":          params.get("rebalance_freq", "weekly"),
        "top_k":                   params.get("top_k", 3),
        "use_enhanced_factors":    True,
        "use_dynamic_position":    True,
    })
    start  = params.get("start", "")
    end    = params.get("end", "")
    market = params.get("market", "CN")

    if _HAS_CLOUD:
        try:
            from aliyun_data_client import run_async
            result = run_async(
                AliyunDataClient.get().run_backtest(
                    symbols, strategy_cfg,
                    start_date=start, end_date=end, market=market,
                )
            )
            if result and result.get("status") in ("completed", "running"):
                out = {"success": True, "provider": "aliyun_cloud"}
                r = result.get("result") or {}
                perf = r.get("performance") or {}
                out.update({
                    "backtest_id":     result.get("backtest_id"),
                    "status":          result.get("status"),
                    "total_return":    perf.get("total_return"),
                    "annual_return":   perf.get("annualized_return"),
                    "sharpe_ratio":    perf.get("sharpe_ratio"),
                    "max_drawdown":    perf.get("max_drawdown"),
                    "win_rate":        perf.get("win_rate"),
                    "total_trades":    perf.get("total_trades"),
                    "equity_curve":    r.get("equity_curve"),
                })
                return out
        except Exception as exc:
            logger.debug("Cloud backtest failed: %s", exc)

    # Local fallback: run simple pandas backtest for first symbol
    return _backtest_strategy({
        "symbol":   symbols[0] if symbols else "sh600519",
        "strategy": params.get("strategy", "sma_cross"),
        "start":    start or _parse_date(None, 730),
        "end":      end   or _today(),
    })


# ---------------------------------------------------------------------------
# Helper: format dataframe tail for display
# ---------------------------------------------------------------------------

def _df_tail(df: pd.DataFrame, n: int = 5) -> List[Dict]:
    cols = [c for c in ["date", "Close", "Open", "High", "Low", "Volume"]
            if c in df.columns]
    sub  = df[cols].tail(n)
    records = []
    for _, row in sub.iterrows():
        rec = {}
        for c in cols:
            v = row[c]
            try:
                if isinstance(v, (float, np.floating)):
                    rec[c] = round(float(v), 4)
                elif hasattr(v, "item"):
                    rec[c] = v.item()
                else:
                    rec[c] = str(v)
            except Exception:
                rec[c] = str(v)
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

# (handler, description)
LOCAL_FINANCE_TOOL_REGISTRY: Dict[str, Tuple] = {
    # ── Market data (cloud → local fallback) ─────────────────────────────
    "get_market_data":        (_get_market_data,        "Stock/ETF quotes and OHLCV history (A股/US/global, cloud-backed)"),
    "get_crypto_data":        (_get_crypto_data,        "Cryptocurrency OHLCV and ticker data via ccxt/yfinance"),
    "get_forex_data":         (_get_forex_data,         "Foreign exchange rates (yfinance)"),
    "get_commodities_data":   (_get_commodities_data,   "Commodity futures prices: gold, oil, copper, wheat, etc. (yfinance)"),
    "get_futures_data":       (_get_futures_data,       "Equity index futures: S&P, NASDAQ, VIX, Nikkei, etc. (yfinance)"),
    # ── Factor & signal (cloud → local fallback) ─────────────────────────
    "calculate_factors":      (_calculate_factors,      "Technical factors: RSI, MACD, MA gaps, volatility, momentum, trend score (cloud-enhanced)"),
    "get_ai_signal":          (_get_ai_signal,          "AI trading signal: BUY/SELL/HOLD with confidence and reasoning (Alibaba Cloud DeepSeek)"),
    "get_market_insights":    (_get_market_insights,    "AI narrative market insights for a basket of symbols (Alibaba Cloud)"),
    "get_predictions":        (_get_predictions,        "ML-powered 5/10-day return predictions (Alibaba Cloud LightGBM/XGBoost)"),
    # ── Backtest (cloud ML → local pandas fallback) ───────────────────────
    "backtest_strategy":      (_backtest_strategy,      "Run a local pandas backtest: sma_cross | rsi_mean_revert | momentum | buy_hold"),
    "cloud_backtest":         (_cloud_backtest,         "Full ML-powered backtest via Alibaba Cloud QuantEngine (rebalance freq, dynamic position)"),
    # ── Risk & portfolio ──────────────────────────────────────────────────
    "get_risk_metrics":       (_get_risk_metrics,       "VaR, CVaR, max drawdown, Sharpe, Calmar, skew, kurtosis"),
    "optimize_positions":     (_optimize_positions,     "Portfolio optimisation: max_sharpe | min_var | equal_weight"),
    # ── A股 data services ─────────────────────────────────────────────────
    "get_sector_performance": (_get_sector_performance, "Sector performance ranking (A股 industry / US SPDR ETFs)"),
    "get_northbound_flow":    (_get_northbound_flow,    "北向资金 (沪深港通) net buy flow via akshare"),
    "screen_ashare":          (_screen_ashare,          "A股选股筛选: PE, ROE, market cap, momentum"),
    "get_limit_up_pool":      (_get_limit_up_pool,      "A股涨停板池 (today's limit-up stocks via akshare)"),
    "get_market_indices":     (_get_market_indices,     "Global market indices: US, CN, EU, crypto, commodities"),
    # ── News & macro ──────────────────────────────────────────────────────
    "analyze_news":           (_analyze_news,           "News sentiment analysis (A股 via akshare)"),
    "get_bonds_data":         (_get_bonds_data,         "US Treasury yield curve (yfinance)"),
}

# OpenAI/Ollama tool schemas for local finance tools
LOCAL_FINANCE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_market_data",
            "description": "Get stock/ETF quotes and OHLCV history. Supports US stocks (AAPL), A-share (600519 or sh600519), ETFs, indices.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol":   {"type": "string", "description": "Ticker symbol, e.g. AAPL, sh600519, BTC-USD"},
                    "period":   {"type": "string", "description": "yfinance period: 1d 5d 1mo 3mo 6mo 1y 2y 5y ytd max"},
                    "interval": {"type": "string", "description": "Bar interval: 1m 5m 15m 30m 1h 1d 1wk 1mo"},
                    "start":    {"type": "string", "description": "Start date YYYY-MM-DD (overrides period)"},
                    "end":      {"type": "string", "description": "End date YYYY-MM-DD"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_crypto_data",
            "description": "Cryptocurrency OHLCV data. Uses ccxt if available, else yfinance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol":    {"type": "string", "description": "Pair, e.g. BTC/USDT or ETH/USDT"},
                    "exchange":  {"type": "string", "description": "Exchange name (binance, okx, bybit, coinbase)"},
                    "timeframe": {"type": "string", "description": "Candle timeframe: 1m 5m 1h 4h 1d"},
                    "limit":     {"type": "integer", "description": "Number of bars"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate_factors",
            "description": "Compute technical and quantitative factors: RSI, MACD, Bollinger, MA gaps, volatility, trend score, beta.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Ticker symbol"},
                    "period": {"type": "string", "description": "Lookback period (yfinance format)"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "backtest_strategy",
            "description": "Run a strategy backtest. Returns Sharpe, max drawdown, win rate, trade count, alpha vs benchmark.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol":          {"type": "string"},
                    "strategy":        {"type": "string", "description": "sma_cross | rsi_mean_revert | momentum | buy_hold"},
                    "start":           {"type": "string", "description": "Start date YYYY-MM-DD"},
                    "end":             {"type": "string", "description": "End date YYYY-MM-DD"},
                    "fast_period":     {"type": "integer", "description": "Fast MA period (default 20)"},
                    "slow_period":     {"type": "integer", "description": "Slow MA period (default 60)"},
                    "rsi_oversold":    {"type": "number",  "description": "RSI oversold threshold (default 30)"},
                    "rsi_overbought":  {"type": "number",  "description": "RSI overbought threshold (default 70)"},
                    "momentum_period": {"type": "integer", "description": "Momentum lookback bars (default 20)"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_risk_metrics",
            "description": "Portfolio / stock risk metrics: VaR, CVaR, drawdown, Sharpe, Sortino, Calmar, skewness.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol":     {"type": "string"},
                    "period":     {"type": "string", "description": "Data lookback (default 1y)"},
                    "confidence": {"type": "number",  "description": "VaR confidence level (default 0.95)"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "optimize_positions",
            "description": "Portfolio weight optimisation using Markowitz / max-Sharpe / min-variance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols":          {"type": "array",  "items": {"type": "string"}, "description": "List of ticker symbols"},
                    "method":           {"type": "string", "description": "max_sharpe | min_var | equal_weight"},
                    "period":           {"type": "string", "description": "History period (default 1y)"},
                    "risk_free_rate":   {"type": "number", "description": "Annual risk-free rate (default 0.04)"},
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sector_performance",
            "description": "Sector performance ranking. market='cn' uses akshare industry data; market='us' uses SPDR ETFs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "market": {"type": "string", "description": "cn | us (default cn)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_northbound_flow",
            "description": "北向资金 (Shanghai-HK / Shenzhen-HK Connect) net buy amount in 亿元 via akshare.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "Number of trading days to show (default 10)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screen_ashare",
            "description": "A股选股筛选器. Filters by PE, market cap, ST/退市 exclusion, momentum ranking.",
            "parameters": {
                "type": "object",
                "properties": {
                    "max_pe":              {"type": "number",  "description": "Max dynamic PE ratio (default 50)"},
                    "min_market_cap_yi":   {"type": "number",  "description": "Min market cap in 亿元 (default 0)"},
                    "limit":               {"type": "integer", "description": "Max stocks to return (default 20)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_limit_up_pool",
            "description": "Today's A股 limit-up stock pool (涨停板) via akshare. Includes consecutive limit-up count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date YYYY-MM-DD (default today)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_indices",
            "description": "Major global market indices: S&P 500, NASDAQ, 上证综指, Nikkei, Gold, BTC, etc.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_news",
            "description": "Fetch and sentiment-score recent news for a stock or topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock code or company name"},
                    "limit":  {"type": "integer", "description": "Number of articles (default 5)"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bonds_data",
            "description": "US Treasury yield curve (2Y, 5Y, 10Y, 30Y) and 10Y-2Y spread.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_commodities_data",
            "description": (
                "Commodity spot/futures price, 52-week range and 1-year return. "
                "Supports: gold, silver, oil, crude, brent, natgas, copper, wheat, corn, soybean, coffee, "
                "cotton, cattle, lumber, 黄金, 原油, 铜, etc. "
                "Also accepts yfinance tickers directly (e.g. GC=F, CL=F, NG=F)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "commodity": {
                        "type": "string",
                        "description": "Commodity name (gold, oil, copper…) or yfinance ticker (GC=F)"
                    },
                },
                "required": ["commodity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_futures_data",
            "description": "Equity index and other futures: S&P (ES=F), NASDAQ (NQ=F), Nikkei, VIX, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract": {
                        "type": "string",
                        "description": "Futures contract name or ticker: SP500, NQ, DOW, VIX, ES=F, NQ=F…"
                    },
                },
                "required": ["contract"],
            },
        },
    },
    # ── New cloud-backed tools ────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "get_ai_signal",
            "description": (
                "AI-powered trading signal (BUY/SELL/HOLD) with confidence, reasoning, stop-loss and "
                "take-profit levels. Uses Alibaba Cloud DeepSeek + QuantEngine. "
                "Falls back to local factor-based signal when cloud unavailable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string", "description": "Stock code, e.g. sh600519, AAPL"},
                    "market": {"type": "string", "description": "CN | US (auto-detected from symbol)"},
                },
                "required": ["symbol"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_insights",
            "description": (
                "AI narrative market insights for a basket of stocks. Returns sentiment, key risks, "
                "opportunities and macro context. Powered by Alibaba Cloud AI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of stock codes, e.g. ['sh600519', 'sz000858']"
                    },
                    "market": {"type": "string", "description": "CN | US (default CN)"},
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_predictions",
            "description": (
                "ML model return predictions for a list of stocks. "
                "Uses Alibaba Cloud LightGBM/XGBoost ensemble. "
                "Returns predicted_return and confidence for each symbol."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of stock codes"
                    },
                    "prediction_days": {"type": "integer", "description": "Forecast horizon in days (default 5)"},
                    "market": {"type": "string", "description": "CN | US (default CN)"},
                },
                "required": ["symbols"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cloud_backtest",
            "description": (
                "Full ML-powered backtest on Alibaba Cloud QuantEngine. "
                "Supports rebalance_freq (daily/weekly/monthly), dynamic position sizing, "
                "model_type (lightgbm/xgboost/ensemble). Falls back to local pandas backtest."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbols": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of stock codes for backtest universe"
                    },
                    "model_type":      {"type": "string",  "description": "lightgbm | xgboost | ensemble (default lightgbm)"},
                    "months":          {"type": "integer", "description": "Backtest period in months (default 12)"},
                    "rebalance_freq":  {"type": "string",  "description": "daily | weekly | monthly (default weekly)"},
                    "top_k":           {"type": "integer", "description": "Top-K stocks to hold per period (default 3)"},
                    "start":           {"type": "string",  "description": "Start date YYYY-MM-DD"},
                    "end":             {"type": "string",  "description": "End date YYYY-MM-DD"},
                    "market":          {"type": "string",  "description": "CN | US (default CN)"},
                },
                "required": [],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Registration helper
# ---------------------------------------------------------------------------

def register_local_finance_tools(
    tool_registry: Dict,
    schema_registry: List,
) -> int:
    """
    Add local finance tools to the CLI's LOCAL_TOOLS and LOCAL_TOOL_SCHEMAS.

    Only registers tools whose names are NOT already present (never overwrites
    existing tools, so remote Aria tools take precedence when backend is up).

    Returns number of tools newly registered.
    """
    added = 0
    for name, (handler, description) in LOCAL_FINANCE_TOOL_REGISTRY.items():
        if name not in tool_registry:
            tool_registry[name] = (
                lambda p, h=handler: _safe(h, p),
                description,
            )
            added += 1

    existing_schema_names = {
        s.get("function", {}).get("name") for s in schema_registry
    }
    for schema in LOCAL_FINANCE_TOOL_SCHEMAS:
        sname = schema.get("function", {}).get("name", "")
        if sname and sname not in existing_schema_names:
            schema_registry.append(schema)

    return added

    return added
