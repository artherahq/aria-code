"""
market_data_client.py — Arthera unified real-time market data client.

Design principles
─────────────────
1. Proxy-bypassed  : uses requests.Session(trust_env=False) so Chinese data
   sources work even when HTTP_PROXY / HTTPS_PROXY is set (VPN / Clash).
2. Multi-source fallback chain:
     US/Global  → yfinance (primary)  → Alpha Vantage (if key set)
     A-shares   → Eastmoney push2 API → AKShare (historical)
     Crypto     → ccxt (binance/okx)  → yfinance fallback
3. Unified output schema — every function returns a consistent dict so callers
   don't care which data source actually served the data.
4. No blocking calls inside async context — use run_in_executor where needed.

Quick usage
───────────
    from market_data_client import MarketDataClient
    mdc = MarketDataClient()
    print(mdc.quote("NVDA"))
    print(mdc.quote("000001"))          # A-share
    print(mdc.quote("BTC/USDT"))        # crypto
    print(mdc.history("AAPL", days=30))
    print(mdc.indices())                # major global indices
    print(mdc.northbound_flow())        # 北向资金
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Simple in-process cache (TTL-based) ─────────────────────────────────────

class _Cache:
    def __init__(self):
        self._store: Dict[str, tuple] = {}  # key → (value, expire_ts)
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            entry = self._store.get(key)
            if entry and time.time() < entry[1]:
                return entry[0]
            return None

    def set(self, key: str, value, ttl: int = 60):
        with self._lock:
            self._store[key] = (value, time.time() + ttl)

_cache = _Cache()


def _session() -> requests.Session:
    """Return a requests Session that bypasses system proxy."""
    s = requests.Session()
    s.trust_env = False          # ignore HTTP_PROXY / HTTPS_PROXY
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Referer": "https://finance.eastmoney.com/",
    })
    return s


# ── Symbol classification ────────────────────────────────────────────────────

def _is_ashare(symbol: str) -> bool:
    s = symbol.strip().upper()
    digits = s.lstrip("SZ").lstrip("SH")
    return (
        (s.startswith(("60","00","30","68","83","87")) and s.isdigit() and len(s) == 6)
        or (s.startswith("SH") or s.startswith("SZ"))
        or digits.isdigit() and len(digits) == 6
    )

def _is_crypto(symbol: str) -> bool:
    return "/" in symbol or symbol.upper().endswith(("USDT","BTC","ETH","BNB"))

def _normalise_ashare(symbol: str) -> str:
    s = symbol.strip().upper().lstrip("SH").lstrip("SZ")
    s = s.lstrip("0") if s.startswith(("60","00","30","68","83","87")) else s
    return s.zfill(6)

def _ashare_secid(code: str) -> str:
    """Convert 6-digit code → Eastmoney secid (1.XXXXXX or 0.XXXXXX)."""
    code = code.zfill(6)
    if code.startswith(("60", "68", "83", "87")):
        return f"1.{code}"   # 上交所
    return f"0.{code}"       # 深交所


# ═══════════════════════════════════════════════════════════════════════════
# MarketDataClient
# ═══════════════════════════════════════════════════════════════════════════

class MarketDataClient:
    """Unified market data access with proxy bypass and multi-source fallback."""

    EM_QUOTE_URL   = "https://push2.eastmoney.com/api/qt/stock/get"
    EM_ULIST_URL   = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    EM_NORTHBOUND  = "https://push2.eastmoney.com/api/qt/kamt/get"
    EM_HIST_URL    = "https://push2.eastmoney.com/api/qt/stock/kline/get"
    EM_HOT_URL     = "https://push2.eastmoney.com/api/qt/clist/get"
    EM_LIMIT_URL   = "https://push2.eastmoney.com/api/qt/clist/get"

    # Eastmoney field map for stock quote
    _EM_FIELDS = "f43,f44,f45,f46,f47,f48,f57,f58,f169,f170,f171,f116,f117,f162,f167,f168"

    def __init__(self, alpha_vantage_key: str = ""):
        self._sess  = _session()
        self._av_key = alpha_vantage_key or os.getenv("ALPHA_VANTAGE_KEY", "")

    # ── Public API ───────────────────────────────────────────────────────────

    def quote(self, symbol: str) -> Dict[str, Any]:
        """Real-time quote for US stock / A-share / crypto / index.

        Returns unified dict:
          symbol, name, price, change, change_pct, volume, market_cap,
          high, low, open, prev_close, provider, timestamp
        """
        ckey = f"quote:{symbol}"
        cached = _cache.get(ckey)
        if cached:
            return cached

        if _is_ashare(symbol):
            result = self._quote_ashare(symbol)
        elif _is_crypto(symbol):
            result = self._quote_crypto(symbol)
        else:
            result = self._quote_yfinance(symbol)

        if result.get("success"):
            _cache.set(ckey, result, ttl=30)   # 30s cache for quotes
        return result

    def history(self, symbol: str, days: int = 252,
                interval: str = "1d") -> Dict[str, Any]:
        """OHLCV history as a list of dicts (sorted ascending by date).

        Returns: {success, symbol, data: [{date,open,high,low,close,volume},...],
                  provider}
        """
        ckey = f"hist:{symbol}:{days}:{interval}"
        cached = _cache.get(ckey)
        if cached:
            return cached

        if _is_ashare(symbol):
            result = self._history_ashare(symbol, days, interval)
        elif _is_crypto(symbol):
            result = self._history_crypto(symbol, days, interval)
        else:
            result = self._history_yfinance(symbol, days, interval)

        if result.get("success"):
            _cache.set(ckey, result, ttl=300)  # 5min cache for history
        return result

    def indices(self) -> Dict[str, Any]:
        """Real-time major global and Chinese indices."""
        ckey = "indices:global"
        cached = _cache.get(ckey)
        if cached:
            return cached
        result = self._fetch_indices()
        if result.get("success"):
            _cache.set(ckey, result, ttl=60)
        return result

    def northbound_flow(self) -> Dict[str, Any]:
        """北向资金 (沪股通+深股通) net buy/sell today."""
        ckey = "northbound"
        cached = _cache.get(ckey)
        if cached:
            return cached
        result = self._fetch_northbound()
        if result.get("success"):
            _cache.set(ckey, result, ttl=120)
        return result

    def hot_stocks(self, market: str = "cn", top_n: int = 20) -> Dict[str, Any]:
        """热门/活跃股票榜单."""
        ckey = f"hot:{market}:{top_n}"
        cached = _cache.get(ckey)
        if cached:
            return cached
        if market == "cn":
            result = self._fetch_hot_ashare(top_n)
        else:
            result = self._fetch_hot_us(top_n)
        if result.get("success"):
            _cache.set(ckey, result, ttl=120)
        return result

    def multi_quote(self, symbols: List[str]) -> Dict[str, Any]:
        """Batch quotes for multiple symbols."""
        results = {}
        for sym in symbols:
            r = self.quote(sym)
            results[sym] = r
        return {"success": True, "quotes": results}

    def technical_indicators(self, symbol: str, days: int = 120) -> Dict[str, Any]:
        """Compute RSI, MACD, Bollinger Bands, MA from history data."""
        hist = self.history(symbol, days=days)
        if not hist.get("success"):
            return hist
        try:
            df = pd.DataFrame(hist["data"])
            df["close"] = pd.to_numeric(df["close"])
            df["high"]  = pd.to_numeric(df["high"])
            df["low"]   = pd.to_numeric(df["low"])
            close = df["close"]

            # RSI(14)
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            rsi   = (100 - 100 / (1 + rs)).iloc[-1]

            # MACD(12,26,9)
            ema12  = close.ewm(span=12).mean()
            ema26  = close.ewm(span=26).mean()
            macd   = ema12 - ema26
            signal = macd.ewm(span=9).mean()
            hist_m = macd - signal

            # Bollinger Bands(20)
            ma20  = close.rolling(20).mean()
            std20 = close.rolling(20).std()
            bb_upper = (ma20 + 2 * std20).iloc[-1]
            bb_lower = (ma20 - 2 * std20).iloc[-1]
            bb_mid   = ma20.iloc[-1]

            # Moving averages
            mas = {}
            for n in [5, 10, 20, 60, 120]:
                if len(close) >= n:
                    mas[f"ma{n}"] = round(float(close.rolling(n).mean().iloc[-1]), 4)

            current_price = float(close.iloc[-1])
            return {
                "success": True,
                "symbol":  symbol,
                "price":   current_price,
                "rsi":     round(float(rsi), 2) if not np.isnan(rsi) else None,
                "macd":    round(float(macd.iloc[-1]), 4),
                "macd_signal": round(float(signal.iloc[-1]), 4),
                "macd_hist":   round(float(hist_m.iloc[-1]), 4),
                "bb_upper": round(float(bb_upper), 4),
                "bb_mid":   round(float(bb_mid), 4),
                "bb_lower": round(float(bb_lower), 4),
                "bb_position": round((current_price - float(bb_lower)) /
                                     max(float(bb_upper - bb_lower), 1e-9), 4),
                **mas,
                "provider": "local_pandas",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def fundamentals(self, symbol: str) -> Dict[str, Any]:
        """US stock fundamentals via yfinance."""
        if _is_ashare(symbol):
            return self._fundamentals_ashare(symbol)
        try:
            import yfinance as yf
            t = yf.Ticker(symbol)
            info = t.info or {}
            return {
                "success":     True,
                "symbol":      symbol,
                "name":        info.get("longName",""),
                "sector":      info.get("sector",""),
                "industry":    info.get("industry",""),
                "market_cap":  info.get("marketCap"),
                "pe_ratio":    info.get("trailingPE"),
                "fwd_pe":      info.get("forwardPE"),
                "pb_ratio":    info.get("priceToBook"),
                "ps_ratio":    info.get("priceToSalesTrailing12Months"),
                "ev_ebitda":   info.get("enterpriseToEbitda"),
                "revenue":     info.get("totalRevenue"),
                "net_income":  info.get("netIncomeToCommon"),
                "eps":         info.get("trailingEps"),
                "fwd_eps":     info.get("forwardEps"),
                "dividend_yield": info.get("dividendYield"),
                "beta":        info.get("beta"),
                "52w_high":    info.get("fiftyTwoWeekHigh"),
                "52w_low":     info.get("fiftyTwoWeekLow"),
                "analyst_target": info.get("targetMeanPrice"),
                "recommendation": info.get("recommendationKey"),
                "employees":   info.get("fullTimeEmployees"),
                "description": (info.get("longBusinessSummary","")[:300]
                                if info.get("longBusinessSummary") else ""),
                "provider": "yfinance",
            }
        except Exception as e:
            return {"success": False, "error": str(e), "symbol": symbol}

    # ── US / Global (yfinance) ───────────────────────────────────────────────

    def _quote_yfinance(self, symbol: str) -> Dict[str, Any]:
        try:
            import yfinance as yf
            t    = yf.Ticker(symbol)
            fi   = t.fast_info
            info = {}
            try:
                info = t.info or {}
            except Exception:
                pass
            price = float(fi.last_price or 0)
            prev  = float(fi.previous_close or price)
            chg   = price - prev
            chg_p = chg / prev * 100 if prev else 0
            return {
                "success":    True,
                "symbol":     symbol.upper(),
                "name":       info.get("longName","") or info.get("shortName",""),
                "price":      round(price, 4),
                "change":     round(chg, 4),
                "change_pct": round(chg_p, 2),
                "volume":     int(fi.three_month_average_volume or 0),
                "market_cap": fi.market_cap,
                "high":       round(float(fi.day_high or 0), 2),
                "low":        round(float(fi.day_low  or 0), 2),
                "open":       round(float(fi.open     or 0), 4),
                "prev_close": round(prev, 4),
                "currency":   fi.currency or "USD",
                "market":     "US",
                "provider":   "yfinance",
                "timestamp":  datetime.now().isoformat(),
            }
        except Exception as e:
            return {"success": False, "error": str(e), "symbol": symbol}

    def _history_yfinance(self, symbol: str, days: int, interval: str) -> Dict[str, Any]:
        try:
            import yfinance as yf
            period_map = {1: "5d", 5: "5d", 30: "1mo", 60: "3mo",
                          90: "3mo", 120: "6mo", 180: "6mo",
                          252: "1y", 365: "1y", 730: "2y", 1260: "5y"}
            period = period_map.get(days) or f"{days}d"
            iv_map = {"1d": "1d", "1h": "1h", "15m": "15m", "5m": "5m"}
            iv = iv_map.get(interval, "1d")
            df = yf.Ticker(symbol).history(period=period, interval=iv)
            if df.empty:
                return {"success": False, "error": "No data returned", "symbol": symbol}
            records = []
            for ts, row in df.iterrows():
                records.append({
                    "date":   str(ts.date()) if hasattr(ts, "date") else str(ts)[:10],
                    "open":   round(float(row["Open"]), 4),
                    "high":   round(float(row["High"]), 4),
                    "low":    round(float(row["Low"]), 4),
                    "close":  round(float(row["Close"]), 4),
                    "volume": int(row["Volume"]),
                })
            return {"success": True, "symbol": symbol, "data": records,
                    "provider": "yfinance", "count": len(records)}
        except Exception as e:
            return {"success": False, "error": str(e), "symbol": symbol}

    # ── A-share (Eastmoney push2 API) ────────────────────────────────────────

    def _quote_ashare(self, symbol: str) -> Dict[str, Any]:
        """A股报价: yfinance (.SS/.SZ) 为主，东方财富 API 为辅."""
        code = _normalise_ashare(symbol)

        # ── 主路径: yfinance（支持代理环境）────────────────────────────────
        try:
            import yfinance as yf
            # 判断交易所: 6/688开头 → 上交所(.SS), 其余 → 深交所(.SZ)
            suffix = ".SS" if code.startswith(("6", "688", "83", "87")) else ".SZ"
            yf_sym = code + suffix
            t      = yf.Ticker(yf_sym)
            fi     = t.fast_info
            price  = float(fi.last_price or 0)
            prev   = float(fi.previous_close or price)
            chg    = price - prev
            chg_p  = chg / prev * 100 if prev else 0
            info   = {}
            try: info = t.info or {}
            except Exception: pass
            name = info.get("longName") or info.get("shortName") or code
            return {
                "success":    True,
                "symbol":     code,
                "name":       name,
                "price":      round(price, 4),
                "change":     round(chg, 4),
                "change_pct": round(chg_p, 2),
                "volume":     int(fi.three_month_average_volume or 0),
                "market_cap": fi.market_cap,
                "high":       round(float(fi.day_high or 0), 2),
                "low":        round(float(fi.day_low  or 0), 2),
                "open":       round(float(fi.open     or 0), 4),
                "prev_close": round(prev, 4),
                "currency":   "CNY",
                "market":     "CN",
                "provider":   "yfinance",
                "timestamp":  datetime.now().isoformat(),
            }
        except Exception as yf_err:
            logger.debug("yfinance A-share failed %s: %s", code, yf_err)

        # ── 备用路径: 东方财富 push2 API ─────────────────────────────────
        secid = _ashare_secid(code)
        try:
            r = self._sess.get(self.EM_QUOTE_URL, params={
                "secid":  secid,
                "fields": self._EM_FIELDS,
                "ut":     "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2, "invt": 2,
            }, timeout=8)
            d = r.json().get("data", {}) or {}
            price    = float(d.get("f43", 0))
            prev     = float(d.get("f46", price))
            chg      = float(d.get("f169", 0))
            chg_pct  = float(d.get("f170", 0))
            return {
                "success":    True,
                "symbol":     code,
                "name":       d.get("f58", code),
                "price":      price,
                "change":     chg,
                "change_pct": chg_pct,
                "volume":     int(d.get("f47", 0)),
                "turnover":   float(d.get("f48", 0)),
                "market_cap": float(d.get("f116", 0)) * 1e4,
                "high":       float(d.get("f44", 0)),
                "low":        float(d.get("f45", 0)),
                "prev_close": prev,
                "currency":   "CNY",
                "market":     "CN",
                "provider":   "eastmoney",
                "timestamp":  datetime.now().isoformat(),
            }
        except Exception as em_err:
            return {"success": False, "error": str(em_err), "symbol": symbol}

    def _history_ashare(self, symbol: str, days: int, interval: str) -> Dict[str, Any]:
        code  = _normalise_ashare(symbol)
        secid = _ashare_secid(code)
        klt_map = {"1d": 101, "1w": 102, "1mo": 103, "1h": 60, "30m": 30}
        klt = klt_map.get(interval, 101)
        end_date   = datetime.now().strftime("%Y%m%d%H%M%S")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        try:
            r = self._sess.get(self.EM_HIST_URL, params={
                "secid":   secid,
                "klt":     klt,
                "fqt":     1,       # 前复权
                "lmt":     days + 50,
                "end":     end_date,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56",
                "ut":      "bd1d9ddb04089700cf9c27f6f7426281",
            }, timeout=10)
            raw = r.json().get("data", {}) or {}
            name = raw.get("name", code)
            klines = raw.get("klines", [])
            records = []
            for k in klines:
                parts = k.split(",")
                if len(parts) >= 6:
                    records.append({
                        "date":   parts[0],
                        "open":   float(parts[1]),
                        "close":  float(parts[2]),
                        "high":   float(parts[3]),
                        "low":    float(parts[4]),
                        "volume": int(float(parts[5])),
                    })
            return {"success": True, "symbol": code, "name": name,
                    "data": records, "provider": "eastmoney", "count": len(records)}
        except Exception as e:
            return {"success": False, "error": str(e), "symbol": symbol}

    def _fundamentals_ashare(self, symbol: str) -> Dict[str, Any]:
        """A股基本面：东方财富个股资金流."""
        code = _normalise_ashare(symbol)
        # 通过 yfinance 尝试 (港股 / ADR)
        try:
            import yfinance as yf
            yf_sym = code + ".SS" if code.startswith("6") else code + ".SZ"
            return self._quote_yfinance(yf_sym)
        except Exception as e:
            return {"success": False, "error": str(e), "symbol": symbol}

    # ── Crypto (ccxt) ────────────────────────────────────────────────────────

    def _quote_crypto(self, symbol: str) -> Dict[str, Any]:
        try:
            import ccxt
            sym = symbol.upper()
            if "/" not in sym:
                sym = sym.rstrip("USDT") + "/USDT"
            ex = ccxt.binance({"enableRateLimit": True,
                               "proxies": {"http": "", "https": ""}})
            ticker = ex.fetch_ticker(sym)
            price  = float(ticker["last"] or 0)
            prev   = float(ticker.get("previousClose") or ticker.get("open") or price)
            chg    = price - prev
            chg_p  = chg / prev * 100 if prev else 0
            return {
                "success":    True,
                "symbol":     sym,
                "price":      price,
                "change":     round(chg, 6),
                "change_pct": round(chg_p, 2),
                "volume":     float(ticker.get("baseVolume", 0)),
                "high":       float(ticker.get("high", 0) or 0),
                "low":        float(ticker.get("low", 0) or 0),
                "market_cap": None,
                "currency":   "USDT",
                "market":     "CRYPTO",
                "provider":   "ccxt/binance",
                "timestamp":  datetime.now().isoformat(),
            }
        except Exception as e:
            # fallback to yfinance
            yf_sym = symbol.replace("/","").replace("USDT","-USD")
            return self._quote_yfinance(yf_sym)

    def _history_crypto(self, symbol: str, days: int, interval: str) -> Dict[str, Any]:
        try:
            import ccxt
            sym = symbol.upper()
            if "/" not in sym:
                sym = sym.rstrip("USDT") + "/USDT"
            iv_map = {"1d":"1d","1h":"1h","15m":"15m","4h":"4h"}
            tf = iv_map.get(interval, "1d")
            limit = min(days, 1000)
            ex = ccxt.binance({"enableRateLimit": True,
                               "proxies": {"http": "", "https": ""}})
            ohlcv = ex.fetch_ohlcv(sym, timeframe=tf, limit=limit)
            records = [{"date":   datetime.utcfromtimestamp(c[0]/1000).strftime("%Y-%m-%d"),
                        "open":   c[1], "high": c[2], "low": c[3],
                        "close":  c[4], "volume": c[5]}
                       for c in ohlcv]
            return {"success": True, "symbol": sym, "data": records,
                    "provider": "ccxt/binance", "count": len(records)}
        except Exception as e:
            yf_sym = symbol.replace("/","").replace("USDT","-USD")
            return self._history_yfinance(yf_sym, days, interval)

    # ── Global indices ────────────────────────────────────────────────────────

    def _fetch_indices(self) -> Dict[str, Any]:
        indices = {}
        # A股指数 (东方财富)
        cn_secids = "1.000001,0.399001,0.399006,1.000016,1.000688"
        cn_names  = {"000001":"上证指数","399001":"深证成指",
                     "399006":"创业板指","000016":"上证50","000688":"科创50"}
        try:
            r = self._sess.get(self.EM_ULIST_URL, params={
                "fltt": 2, "invt": 2,
                "fields": "f1,f2,f3,f4,f12,f14",
                "secids": cn_secids,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            }, timeout=8)
            for item in r.json().get("data",{}).get("diff",[]):
                code = item.get("f12","")
                indices[cn_names.get(code, code)] = {
                    "price":      round(float(item.get("f2",0)), 2),
                    "change_pct": round(float(item.get("f3",0)), 2),
                    "change":     round(float(item.get("f4",0)), 2),
                    "market":     "CN",
                }
        except Exception as e:
            logger.debug("CN indices error: %s", e)

        # Global indices (yfinance)
        global_map = {
            "^GSPC":  "S&P 500",
            "^IXIC":  "纳斯达克",
            "^DJI":   "道琼斯",
            "^HSI":   "恒生指数",
            "^N225":  "日经225",
            "^FTSE":  "富时100",
            "GC=F":   "黄金",
            "CL=F":   "原油WTI",
            "BTC-USD":"比特币",
        }
        try:
            import yfinance as yf
            tickers = yf.Tickers(" ".join(global_map.keys()))
            for sym, name in global_map.items():
                try:
                    fi    = tickers.tickers[sym].fast_info
                    price = float(fi.last_price or 0)
                    prev  = float(fi.previous_close or price)
                    chg_p = (price - prev) / prev * 100 if prev else 0
                    indices[name] = {
                        "price":      round(price, 2),
                        "change_pct": round(chg_p, 2),
                        "change":     round(price - prev, 4),
                        "market":     "US" if sym.startswith("^") else "COMMOD",
                    }
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Global indices yfinance error: %s", e)

        return {"success": True, "indices": indices,
                "timestamp": datetime.now().isoformat()}

    # ── 北向资金 ────────────────────────────────────────────────────────────

    def _fetch_northbound(self) -> Dict[str, Any]:
        try:
            r = self._sess.get(self.EM_NORTHBOUND, params={
                "fields1": "f1,f2,f3,f4",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "klt": 101, "lmt": 5,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            }, timeout=8)
            data = r.json().get("data", {}) or {}
            sh   = data.get("s2n", {}) or {}   # 沪股通
            sz   = data.get("s3n", {}) or {}   # 深股通
            def _val(obj, key):
                try: return float(obj.get(key, 0)) / 1e8   # 元 → 亿
                except: return 0.0
            sh_net = _val(sh, "f2")
            sz_net = _val(sz, "f2")
            total  = sh_net + sz_net
            return {
                "success":     True,
                "total_net":   round(total, 2),
                "sh_net":      round(sh_net, 2),
                "sz_net":      round(sz_net, 2),
                "unit":        "亿元",
                "direction":   "净流入" if total > 0 else "净流出",
                "provider":    "eastmoney",
                "timestamp":   datetime.now().isoformat(),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ── 热门股榜单 ────────────────────────────────────────────────────────────

    def _fetch_hot_ashare(self, top_n: int = 20) -> Dict[str, Any]:
        try:
            r = self._sess.get(self.EM_HOT_URL, params={
                "pn": 1, "pz": top_n, "po": 1, "np": 1,
                "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                "fltt": 2, "invt": 2, "fid": "f6",
                "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
                "fields": "f2,f3,f4,f5,f6,f7,f12,f14,f62",
            }, timeout=8)
            items = r.json().get("data",{}).get("diff",[]) or []
            stocks = []
            for d in items[:top_n]:
                stocks.append({
                    "code":       d.get("f12",""),
                    "name":       d.get("f14",""),
                    "price":      round(float(d.get("f2",0))/100, 2),
                    "change_pct": round(float(d.get("f3",0))/100, 2),
                    "volume":     int(d.get("f5",0)),
                    "turnover":   float(d.get("f6",0)),
                    "amplitude":  round(float(d.get("f7",0))/100, 2),
                })
            return {"success": True, "market": "CN", "stocks": stocks,
                    "count": len(stocks), "provider": "eastmoney"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _fetch_hot_us(self, top_n: int = 10) -> Dict[str, Any]:
        """US most active stocks via yfinance screener."""
        watchlist = ["NVDA","AAPL","TSLA","MSFT","AMZN","META","GOOGL","AMD","INTC","PLTR"]
        results = []
        try:
            import yfinance as yf
            for sym in watchlist[:top_n]:
                try:
                    fi = yf.Ticker(sym).fast_info
                    p  = float(fi.last_price or 0)
                    prev = float(fi.previous_close or p)
                    chg_p = (p-prev)/prev*100 if prev else 0
                    results.append({"symbol": sym, "price": round(p,2),
                                    "change_pct": round(chg_p,2)})
                except Exception:
                    pass
            return {"success": True, "market": "US", "stocks": results,
                    "count": len(results), "provider": "yfinance"}
        except Exception as e:
            return {"success": False, "error": str(e)}


# ── Module-level singleton ───────────────────────────────────────────────────

_mdc: Optional[MarketDataClient] = None

def get_mdc() -> MarketDataClient:
    global _mdc
    if _mdc is None:
        _mdc = MarketDataClient()
    return _mdc


# ── Convenience functions (module-level API) ─────────────────────────────────

def quote(symbol: str) -> Dict[str, Any]:
    return get_mdc().quote(symbol)

def history(symbol: str, days: int = 252, interval: str = "1d") -> Dict[str, Any]:
    return get_mdc().history(symbol, days=days, interval=interval)

def indices() -> Dict[str, Any]:
    return get_mdc().indices()

def northbound_flow() -> Dict[str, Any]:
    return get_mdc().northbound_flow()

def technical_indicators(symbol: str, days: int = 120) -> Dict[str, Any]:
    return get_mdc().technical_indicators(symbol, days=days)

def fundamentals(symbol: str) -> Dict[str, Any]:
    return get_mdc().fundamentals(symbol)

def hot_stocks(market: str = "cn", top_n: int = 20) -> Dict[str, Any]:
    return get_mdc().hot_stocks(market=market, top_n=top_n)


if __name__ == "__main__":
    import json, sys
    sym = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(json.dumps(quote(sym), indent=2, ensure_ascii=False, default=str))
