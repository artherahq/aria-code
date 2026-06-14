"""
datasources/sources/alpha_vantage_source.py — Alpha Vantage 免费层数据
======================================================================
免费 API key 从 https://www.alphavantage.co/support/#api-key 申请（秒得）。
免费限制: 25 请求/天，5 请求/分钟。

功能: 美股/ETF 行情、技术指标、外汇汇率、大宗商品、基本面数据。

配置: ALPHA_VANTAGE_KEY 环境变量 或 ~/.aria/.env
"""

from __future__ import annotations

import logging
import os
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, Optional

from ..base import BaseDataSource, FundamentalsResult, HistoryResult, QuoteResult

logger = logging.getLogger(__name__)

_BASE = "https://www.alphavantage.co/query"


def _load_key() -> str:
    key = os.getenv("ALPHA_VANTAGE_KEY", "")
    if not key:
        for p in [Path.home() / ".aria" / ".env", Path.home() / ".arthera" / ".env"]:
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.startswith("ALPHA_VANTAGE_KEY="):
                        key = line.split("=", 1)[1].strip()
                        break
            if key:
                break
    return key


def _fetch(params: dict, timeout: int = 15) -> Optional[dict]:
    import json
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{_BASE}?{qs}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aria-code/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        logger.debug(f"[alpha_vantage] fetch 失败: {e}")
        return None


class AlphaVantageSource(BaseDataSource):
    """
    Alpha Vantage 数据源。
    免费 key 足够日常使用（25次/天）；付费 key 无频率限制。
    """

    name         = "alpha_vantage"
    markets      = ["us", "hk", "forex", "commodity"]
    requires_key = True

    def __init__(self, config=None):
        super().__init__(config)
        self._key = _load_key()

    def is_configured(self) -> bool:
        return bool(self._key)

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        data = _fetch({"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": self._key})
        if not data:
            return None
        q = data.get("Global Quote", {})
        if not q or "05. price" not in q:
            return None
        price  = float(q.get("05. price", 0))
        change = float(q.get("09. change", 0))
        pct    = float(q.get("10. change percent", "0%").replace("%", ""))
        vol    = float(q.get("06. volume", 0))
        return QuoteResult(
            symbol     = symbol,
            price      = price,
            change     = change,
            change_pct = pct,
            volume     = vol,
            market     = "us",
            source     = self.name,
            timestamp  = q.get("07. latest trading day", ""),
        )

    def history(
        self,
        symbol: str,
        days: int = 365,
        interval: str = "1d",
    ) -> Optional[HistoryResult]:
        try:
            import pandas as pd
            size   = "compact" if days <= 100 else "full"
            func   = "TIME_SERIES_DAILY_ADJUSTED"
            data   = _fetch({"function": func, "symbol": symbol,
                             "outputsize": size, "apikey": self._key})
            if not data:
                return None
            ts = data.get("Time Series (Daily)", {})
            if not ts:
                return None
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            rows   = []
            for d, v in ts.items():
                if d < cutoff:
                    continue
                rows.append({
                    "date":   d,
                    "open":   float(v.get("1. open", 0)),
                    "high":   float(v.get("2. high", 0)),
                    "low":    float(v.get("3. low", 0)),
                    "close":  float(v.get("5. adjusted close", v.get("4. close", 0))),
                    "volume": float(v.get("6. volume", 0)),
                })
            if not rows:
                return None
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return HistoryResult(symbol=symbol, data=df, source=self.name, interval="1d")
        except Exception as e:
            logger.debug(f"[alpha_vantage] history {symbol} 失败: {e}")
            return None

    def fundamentals(self, symbol: str) -> Optional[FundamentalsResult]:
        data = _fetch({"function": "OVERVIEW", "symbol": symbol, "apikey": self._key})
        if not data or not data.get("Symbol"):
            return None
        def _f(k):
            v = data.get(k, "None")
            try:
                return float(v) if v not in ("None", "-", "") else 0.0
            except ValueError:
                return 0.0
        return FundamentalsResult(
            symbol          = symbol,
            pe_ttm          = _f("TrailingPE"),
            pb              = _f("PriceToBookRatio"),
            roe             = _f("ReturnOnEquityTTM") * 100,
            revenue_growth  = _f("RevenueGrowthQtrlyYOY"),
            dividend_yield  = _f("DividendYield") * 100,
            source          = self.name,
        )

    def get_forex(self, from_currency: str, to_currency: str) -> Optional[Dict]:
        """实时外汇汇率。"""
        data = _fetch({"function": "CURRENCY_EXCHANGE_RATE",
                       "from_currency": from_currency,
                       "to_currency": to_currency,
                       "apikey": self._key})
        if not data:
            return None
        r = data.get("Realtime Currency Exchange Rate", {})
        return {
            "from": from_currency, "to": to_currency,
            "rate": float(r.get("5. Exchange Rate", 0)),
            "time": r.get("6. Last Refreshed", ""),
        }

    def get_commodity(self, symbol: str = "WTI") -> Optional[HistoryResult]:
        """大宗商品历史价格（WTI/BRENT/GOLD/COPPER 等）。"""
        _MAP = {"WTI": "WTI", "BRENT": "BRENT", "GOLD": "GOLD",
                "COPPER": "COPPER", "ALUMINUM": "ALUMINUM", "WHEAT": "WHEAT"}
        func = _MAP.get(symbol.upper(), symbol.upper())
        data = _fetch({"function": func, "interval": "monthly", "apikey": self._key})
        if not data:
            return None
        try:
            import pandas as pd
            rows = [{"date": r["date"], "close": float(r["value"])}
                    for r in data.get("data", []) if r.get("value") not in (".", None)]
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return HistoryResult(symbol=symbol, data=df, source=self.name, interval="1mo")
        except Exception as e:
            logger.debug(f"[alpha_vantage] commodity {symbol} 失败: {e}")
            return None
