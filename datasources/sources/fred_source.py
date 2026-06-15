"""
datasources/sources/fred_source.py — FRED (Federal Reserve Economic Data)
=========================================================================
完全免费，无需 API key（可选 key 提升频率限制）。
覆盖：宏观经济指标、利率、CPI、GDP、就业数据等 800,000+ 系列。

配置（可选）: FRED_API_KEY 环境变量 或 ~/.aria/.env

FRED series 示例：
  DGS10      — 10年美国国债收益率
  FEDFUNDS   — 联邦基金利率
  CPIAUCSL   — 消费者价格指数
  GDP        — 美国 GDP
  UNRATE     — 失业率
  SP500      — S&P 500 指数
  DEXCNUS    — 人民币/美元汇率
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import BaseDataSource, HistoryResult, QuoteResult

logger = logging.getLogger(__name__)

_FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv?id="
_FRED_API  = "https://api.stlouisfed.org/fred"

# 常用宏观指标 symbol → FRED series id 映射
MACRO_ALIASES: Dict[str, str] = {
    "US10Y":    "DGS10",
    "US2Y":     "DGS2",
    "US3M":     "DTB3",
    "FEDFUNDS": "FEDFUNDS",
    "CPI":      "CPIAUCSL",
    "CPIYOY":   "CPIAUCSL",
    "PCE":      "PCEPI",
    "GDP":      "GDP",
    "UNRATE":   "UNRATE",
    "NFP":      "PAYEMS",
    "SP500":    "SP500",
    "NASDAQ":   "NASDAQCOM",
    "WILSHIRE": "WILL5000PR",
    "USDINR":   "DEXINUS",
    "USDCNY":   "DEXCHUS",
    "USDEUR":   "DEXUSEU",
    "VIX":      "VIXCLS",
    "M2":       "M2SL",
    "MORTGAGE": "MORTGAGE30US",
    "HOUSING":  "HOUST",
}


def _load_api_key() -> str:
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        for p in [Path.home() / ".aria" / ".env", Path.home() / ".arthera" / ".env"]:
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.startswith("FRED_API_KEY="):
                        key = line.split("=", 1)[1].strip()
                        break
            if key:
                break
    if not key:
        try:
            import json as _json
            _p = Path.home() / ".arthera" / "providers.json"
            if _p.exists():
                _raw = _json.loads(_p.read_text(encoding="utf-8"))
                key = _raw.get("data", {}).get("fred", {}).get("api_key", "")
        except Exception:
            pass
    return key


class FREDSource(BaseDataSource):
    """
    Federal Reserve Economic Data — 宏观经济数据源。
    无需 key 即可使用 CSV 下载接口；有 key 可使用 JSON API 获得更多元数据。
    """

    name         = "fred"
    markets      = ["us", "macro"]
    requires_key = False

    def __init__(self, config=None):
        super().__init__(config)
        self._api_key = _load_api_key()

    def is_configured(self) -> bool:
        return True  # 无需 key，免费开放

    def supports(self, symbol: str) -> bool:
        s = symbol.upper()
        return s in MACRO_ALIASES or s in MACRO_ALIASES.values()

    def _resolve_series(self, symbol: str) -> str:
        s = symbol.upper()
        return MACRO_ALIASES.get(s, s)

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        try:
            h = self.history(symbol, days=30)
            if h is None or h.data is None or h.data.empty:
                return None
            last = h.data.iloc[-1]
            val  = float(last.get("close", last.iloc[0]))
            series_id = self._resolve_series(symbol)
            return QuoteResult(
                symbol    = symbol,
                name      = f"FRED:{series_id}",
                price     = val,
                currency  = "USD",
                market    = "macro",
                source    = self.name,
                timestamp = str(h.data.index[-1].date()),
            )
        except Exception as e:
            logger.debug(f"[fred] quote {symbol} 失败: {e}")
            return None

    def history(
        self,
        symbol: str,
        days: int = 365,
        interval: str = "1d",
        _timeout: int = 12,
    ) -> Optional[HistoryResult]:
        try:
            import pandas as pd
            import urllib.request, json

            series_id = self._resolve_series(symbol)
            start = (date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
            end   = date.today().strftime("%Y-%m-%d")

            def _parse_api(url: str) -> Optional[pd.DataFrame]:
                req = urllib.request.Request(url, headers={"User-Agent": "aria-code/1.0"})
                with urllib.request.urlopen(req, timeout=_timeout) as resp:
                    data = json.loads(resp.read())
                obs = data.get("observations", [])
                rows = []
                for o in obs:
                    try:
                        rows.append({"date": o["date"], "close": float(o["value"])})
                    except (ValueError, KeyError):
                        pass
                if not rows:
                    return None
                _df = pd.DataFrame(rows)
                _df["date"] = pd.to_datetime(_df["date"])
                return _df.set_index("date").sort_index()

            def _parse_csv(url: str) -> Optional[pd.DataFrame]:
                req = urllib.request.Request(url, headers={"User-Agent": "aria-code/1.0"})
                with urllib.request.urlopen(req, timeout=_timeout) as resp:
                    _df = pd.read_csv(resp, parse_dates=["DATE"], index_col="DATE")
                _df.columns = ["close"]
                _df = _df.replace(".", float("nan")).dropna()
                _df["close"] = _df["close"].astype(float)
                return _df if not _df.empty else None

            df = None

            # 优先使用 JSON API（有 key 速率更高）
            if self._api_key:
                api_url = (
                    f"{_FRED_API}/series/observations?series_id={series_id}"
                    f"&observation_start={start}&observation_end={end}"
                    f"&api_key={self._api_key}&file_type=json"
                )
                try:
                    df = _parse_api(api_url)
                except Exception as _e1:
                    logger.debug(f"[fred] JSON API 失败，尝试 CSV: {_e1}")

            # 回落：免费 CSV 下载接口（无需 key，但国内可能超时）
            if df is None:
                csv_url = (
                    f"{_FRED_BASE}{series_id}"
                    f"&cosd={start}&coed={end}"
                )
                try:
                    df = _parse_csv(csv_url)
                except Exception as _e2:
                    logger.debug(f"[fred] CSV 也失败: {_e2}")

            if df is None:
                return None
            return HistoryResult(symbol=symbol, data=df, source=self.name, interval="1d")
        except Exception as e:
            logger.debug(f"[fred] history {symbol} 失败: {e}")
            return None

    def search_series(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """用关键词搜索 FRED 系列（需要 API key）。"""
        if not self._api_key:
            return [{"error": "需要 FRED_API_KEY 才能搜索系列"}]
        try:
            import urllib.request, json, urllib.parse
            q   = urllib.parse.quote(query)
            url = (f"{_FRED_API}/series/search?search_text={q}"
                   f"&limit={limit}&api_key={self._api_key}&file_type=json")
            req = urllib.request.Request(url, headers={"User-Agent": "aria-code/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return [
                {"id": s["id"], "title": s["title"], "frequency": s.get("frequency_short"),
                 "units": s.get("units_short"), "last_updated": s.get("last_updated")}
                for s in data.get("seriess", [])
            ]
        except Exception as e:
            return [{"error": str(e)}]
