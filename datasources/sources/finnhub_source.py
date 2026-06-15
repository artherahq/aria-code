"""
datasources/sources/finnhub_source.py — Finnhub 美股/港股数据源
================================================================
使用 ~/.arthera/providers.json 中配置的 Finnhub API key，
提供实时行情、历史 K 线、基本面数据。
免费套餐：每分钟 60 次请求，支持美股 / ETF / 指数 / 外汇 / 加密。
"""

from __future__ import annotations

import json
import logging
import math
import time
import urllib.request
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import pandas as pd

from ..base import BaseDataSource, FundamentalsResult, HistoryResult, QuoteResult

logger = logging.getLogger(__name__)

_PROVIDERS_FILE = Path.home() / ".arthera" / "providers.json"
_BASE = "https://finnhub.io/api/v1"


def _read_finnhub_key() -> str:
    try:
        import os
        env = os.getenv("FINNHUB_API_KEY", "") or os.getenv("FINNHUB_KEY", "")
        if env:
            return env
        if _PROVIDERS_FILE.exists():
            raw = json.loads(_PROVIDERS_FILE.read_text(encoding="utf-8"))
            key = raw.get("data", {}).get("finnhub", {}).get("api_key", "")
            if key:
                return key
    except Exception:
        pass
    return ""


def _fh_get(path: str, key: str, params: dict = None, timeout: int = 8) -> dict:
    qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
    url = f"{_BASE}{path}?token={key}" + (f"&{qs}" if qs else "")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


class FinnhubSource(BaseDataSource):

    name         = "finnhub"
    markets      = ["us", "hk", "crypto"]
    requires_key = True

    def __init__(self, config=None):
        super().__init__(config)
        self._key = _read_finnhub_key()

    def is_configured(self) -> bool:
        return bool(self._key)

    # ── Quote ─────────────────────────────────────────────────────────────────

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        if not self._key:
            return None
        sym = symbol.upper().replace(".HK", "")
        try:
            data = _fh_get("/quote", self._key, {"symbol": sym})
            price = float(data.get("c") or 0)
            if price <= 0:
                return None
            prev = float(data.get("pc") or price)
            chg_pct = round((price - prev) / prev * 100, 2) if prev else 0

            # Extra: company profile for name / market cap
            name = sym
            mkt_cap = 0.0
            currency = "USD"
            try:
                prof = _fh_get("/stock/profile2", self._key, {"symbol": sym})
                name = prof.get("name") or sym
                mkt_cap = float(prof.get("marketCapitalization") or 0) * 1e6
                currency = prof.get("currency") or "USD"
            except Exception:
                pass

            return QuoteResult(
                symbol     = symbol,
                name       = name,
                price      = price,
                change     = round(price - prev, 4),
                change_pct = chg_pct,
                volume     = float(data.get("v") or 0),
                high_52w   = float(data.get("h") or 0),
                low_52w    = float(data.get("l") or 0),
                market_cap = mkt_cap,
                currency   = currency,
                market     = "us",
                source     = self.name,
            )
        except Exception as e:
            logger.debug(f"[finnhub] quote({symbol}) 失败: {e}")
            return None

    # ── History ───────────────────────────────────────────────────────────────

    def history(self, symbol: str, days: int = 90, interval: str = "1d") -> Optional[HistoryResult]:
        if not self._key:
            return None
        sym = symbol.upper()
        resolution = "D" if interval in ("1d", "day", "daily") else "60"
        _end   = int(time.time())
        _start = int((datetime.now() - timedelta(days=days + 5)).timestamp())
        try:
            data = _fh_get("/stock/candle", self._key, {
                "symbol": sym, "resolution": resolution,
                "from": _start, "to": _end,
            })
            if data.get("s") != "ok":
                return None
            t = data.get("t", [])
            o = data.get("o", [])
            h = data.get("h", [])
            l = data.get("l", [])
            c = data.get("c", [])
            v = data.get("v", [])
            if not c:
                return None
            df = pd.DataFrame({
                "日期":  [datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") for ts in t],
                "开盘":  o, "最高": h, "最低": l, "收盘": c, "成交量": v,
            })
            df.index = pd.to_datetime(df["日期"])
            # Also add English column aliases for DataService compatibility
            df["Open"]   = df["开盘"]
            df["High"]   = df["最高"]
            df["Low"]    = df["最低"]
            df["Close"]  = df["收盘"]
            df["Volume"] = df["成交量"]
            return HistoryResult(symbol=symbol, data=df, source=self.name, interval=interval)
        except Exception as e:
            logger.debug(f"[finnhub] history({symbol}) 失败: {e}")
            return None

    # ── Fundamentals ──────────────────────────────────────────────────────────

    def fundamentals(self, symbol: str) -> Optional[FundamentalsResult]:
        if not self._key:
            return None
        sym = symbol.upper()
        try:
            # Basic financials (PE, market cap, etc.)
            metrics_data = _fh_get("/stock/metric", self._key, {"symbol": sym, "metric": "all"})
            m = metrics_data.get("metric") or {}

            def _mf(key: str) -> Optional[float]:
                v = m.get(key)
                if v is None:
                    return None
                try:
                    fv = float(v)
                    return None if (math.isnan(fv) or fv == 0) else fv
                except (TypeError, ValueError):
                    return None

            pe   = _mf("peTTM") or _mf("peExclExtraItemsTTM")
            pb   = _mf("pbAnnual") or _mf("pbQuarterly")
            roe  = _mf("roeTTM") or _mf("roeAnnual")
            div_yield = _mf("dividendYieldIndicatedAnnual")
            rev_growth = _mf("revenueGrowthTTMYoy")
            eps_growth = _mf("epsGrowthTTMYoy")
            mktcap_raw = _mf("marketCapitalization")
            total_mv = (mktcap_raw * 1e6) if mktcap_raw else None

            if pe is None and pb is None and roe is None:
                return None

            return FundamentalsResult(
                symbol         = symbol,
                pe_ttm         = pe,
                pb             = pb,
                roe            = roe,
                revenue_growth = rev_growth,
                net_profit_growth = eps_growth,
                dividend_yield = div_yield,
                total_mv       = total_mv,
                source         = self.name,
            )
        except Exception as e:
            logger.debug(f"[finnhub] fundamentals({symbol}) 失败: {e}")
            return None
