"""
datasources/sources/world_bank_source.py — 世界银行开放数据
============================================================
完全免费，无需 API key。数据来源：https://api.worldbank.org/v2/
覆盖：GDP/人均GDP/通胀/贸易/外债/人口/能源等 16,000+ 指标，200+ 国家。

常用指标:
  NY.GDP.MKTP.CD  — GDP（当前美元）
  NY.GDP.PCAP.CD  — 人均GDP
  FP.CPI.TOTL.ZG  — 通货膨胀率（CPI 年增长率）
  NE.TRD.GNFS.ZS  — 贸易占GDP比重
  SL.UEM.TOTL.ZS  — 失业率
  SP.POP.TOTL     — 总人口
  BX.KLT.DINV.CD.WD — 外商直接投资（净流入）
"""

from __future__ import annotations

import logging
import urllib.request
from typing import Any, Dict, List, Optional

from ..base import BaseDataSource, HistoryResult, QuoteResult

logger = logging.getLogger(__name__)

_BASE = "https://api.worldbank.org/v2"

# 常用指标别名
INDICATOR_ALIASES: Dict[str, str] = {
    "GDP":       "NY.GDP.MKTP.CD",
    "GDPPC":     "NY.GDP.PCAP.CD",
    "GDPGROWTH": "NY.GDP.MKTP.KD.ZG",
    "CPI":       "FP.CPI.TOTL.ZG",
    "INFLATION": "FP.CPI.TOTL.ZG",
    "UNRATE":    "SL.UEM.TOTL.ZS",
    "POPULATION":"SP.POP.TOTL",
    "TRADE":     "NE.TRD.GNFS.ZS",
    "FDI":       "BX.KLT.DINV.CD.WD",
    "DEBT":      "GC.DOD.TOTL.GD.ZS",
    "EXPORTS":   "NE.EXP.GNFS.ZS",
    "IMPORTS":   "NE.IMP.GNFS.ZS",
}

# 国家代码别名
COUNTRY_ALIASES: Dict[str, str] = {
    "CHINA": "CN", "CN": "CN", "CHN": "CN",
    "US": "US", "USA": "US", "AMERICA": "US",
    "JAPAN": "JP", "JP": "JP",
    "GERMANY": "DE", "DE": "DE",
    "UK": "GB", "GB": "GB",
    "INDIA": "IN", "IN": "IN",
    "BRAZIL": "BR",
    "WORLD": "WLD", "GLOBAL": "WLD",
    "G7": "G7", "G20": "G20",
}


def _fetch(url: str, timeout: int = 15) -> Optional[List]:
    import json
    try:
        req = urllib.request.Request(
            url + "&format=json&per_page=100",
            headers={"User-Agent": "aria-code/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        if isinstance(data, list) and len(data) >= 2:
            return data[1]
        return None
    except Exception as e:
        logger.debug(f"[world_bank] fetch 失败: {e}")
        return None


class WorldBankSource(BaseDataSource):
    """世界银行开放数据 — 宏观经济与发展指标。"""

    name         = "world_bank"
    markets      = ["macro", "us", "cn"]
    requires_key = False

    def is_configured(self) -> bool:
        return True

    def supports(self, symbol: str) -> bool:
        s = symbol.upper()
        return (
            "." in s and len(s) > 5 or  # looks like WB indicator (NY.GDP.*)
            s in INDICATOR_ALIASES
        )

    def _resolve_indicator(self, indicator: str) -> str:
        return INDICATOR_ALIASES.get(indicator.upper(), indicator)

    def _resolve_country(self, country: str) -> str:
        return COUNTRY_ALIASES.get(country.upper(), country.upper())

    def get_indicator(
        self,
        indicator: str,
        country: str = "WLD",
        start_year: int = 2000,
        end_year: int = 2024,
    ) -> Optional[HistoryResult]:
        """
        获取指定国家/地区的经济指标历史序列。

        indicator: 世界银行指标代码或别名（GDP/CPI/UNRATE 等）
        country:   ISO 2位代码或别名（CN/US/JP/WORLD 等）
        """
        try:
            import pandas as pd
            ind = self._resolve_indicator(indicator)
            cty = self._resolve_country(country)
            url = (f"{_BASE}/country/{cty}/indicator/{ind}"
                   f"?date={start_year}:{end_year}")
            rows_raw = _fetch(url)
            if not rows_raw:
                return None
            rows = []
            for r in rows_raw:
                if r.get("value") is not None:
                    rows.append({
                        "date": f"{r['date']}-12-31",
                        "close": float(r["value"]),
                        "country": r.get("country", {}).get("value", cty),
                    })
            if not rows:
                return None
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            return HistoryResult(symbol=f"{country}:{indicator}", data=df,
                                 source=self.name, interval="1y")
        except Exception as e:
            logger.debug(f"[world_bank] get_indicator 失败: {e}")
            return None

    def compare_countries(
        self,
        indicator: str,
        countries: List[str],
        start_year: int = 2010,
    ) -> Optional[Dict[str, Any]]:
        """
        多国横向对比同一指标。
        返回: {country: [(year, value), ...], ...}
        """
        ind = self._resolve_indicator(indicator)
        cty_codes = ";".join(self._resolve_country(c) for c in countries)
        url = (f"{_BASE}/country/{cty_codes}/indicator/{ind}"
               f"?date={start_year}:2024")
        rows_raw = _fetch(url)
        if not rows_raw:
            return None

        result: Dict[str, list] = {}
        for r in rows_raw:
            if r.get("value") is None:
                continue
            cty_name = r.get("country", {}).get("value", "?")
            yr = r.get("date", "")
            val = float(r["value"])
            result.setdefault(cty_name, []).append((yr, val))

        for k in result:
            result[k].sort(key=lambda x: x[0])
        return result

    def search_indicators(self, query: str, limit: int = 10) -> List[Dict]:
        """关键词搜索世界银行指标。"""
        import json, urllib.parse
        q = urllib.parse.quote(query)
        url = f"{_BASE}/indicator?format=json&per_page={limit}&mrv=1"
        try:
            req = urllib.request.Request(
                f"{_BASE}/indicator?format=json&per_page={limit}&source=2",
                headers={"User-Agent": "aria-code/1.0"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            if isinstance(data, list) and len(data) >= 2:
                return [
                    {"id": i["id"], "name": i["name"],
                     "source": i.get("source", {}).get("value", "")}
                    for i in data[1] if query.lower() in i.get("name", "").lower()
                ][:limit]
        except Exception as e:
            logger.debug(f"[world_bank] search 失败: {e}")
        return []

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        # World Bank indicators aren't real-time quotes
        return None

    def history(self, symbol: str, days: int = 365 * 5, interval: str = "1y") -> Optional[HistoryResult]:
        # Parse "CN:GDP" or just "GDP" (default to WLD)
        if ":" in symbol:
            country, indicator = symbol.split(":", 1)
        else:
            country, indicator = "WLD", symbol
        years = max(1, days // 365)
        start = 2024 - years
        return self.get_indicator(indicator, country, start_year=start)
