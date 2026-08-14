"""
macro_tools.py — 宏观经济数据层
=================================
来源：
  - FRED (美联储 St. Louis) — GDP, CPI, 联邦基金利率, 失业率, M2
  - AKShare              — 中国 CPI/PPI/PMI/GDP/LPR/社融
  - yfinance             — 全球央行政策利率（备用）

全部函数返回 {"success": bool, "data": [...], ...} 格式，
与 local_finance_tools 保持一致。

依赖安装（可选）：
    pip install requests           # FRED REST API
    pip install akshare            # 中国数据
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Optional imports ──────────────────────────────────────────────────────────

try:
    import requests as _req
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    import akshare as ak
    _HAS_AK = True
except ImportError:
    _HAS_AK = False

try:
    import pandas as pd
    _HAS_PD = True
except ImportError:
    _HAS_PD = False

# ── FRED API helper ──────────────────────────────────────────────────────────

FRED_BASE = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_API_BASE = "https://api.stlouisfed.org/fred"

def _get_fred_key() -> str:
    """Read FRED API key from env or ~/.arthera/providers.json."""
    key = os.getenv("FRED_API_KEY", "")
    if not key:
        try:
            import json
            p = Path.home() / ".arthera" / "providers.json"
            if p.exists():
                d = json.loads(p.read_text())
                key = d.get("fred", {}).get("api_key", "") or d.get("fred_api_key", "")
        except Exception:
            pass
    return key


def _fred_series(series_id: str, limit: int = 24, units: str = "") -> List[Dict]:
    """Fetch a FRED data series. Returns list of {date, value} dicts.

    ``units`` maps to FRED's units transformation, e.g. "pc1" = percent change
    from a year ago (used for YoY rates like CPI inflation, so we don't show
    the raw index level mislabelled as a percentage).
    """
    if not _HAS_REQUESTS:
        return []
    key = _get_fred_key()
    try:
        params: Dict[str, Any] = {
            "series_id": series_id,
            "limit": limit,
            "sort_order": "desc",
            "file_type": "json",
        }
        if units:
            params["units"] = units
        if key:
            params["api_key"] = key
            url = f"{FRED_API_BASE}/series/observations"
            r = _req.get(url, params=params, timeout=8)
            r.raise_for_status()
            obs = r.json().get("observations", [])
            return [
                {"date": o["date"], "value": float(o["value"]) if o["value"] != "." else None}
                for o in reversed(obs)
            ]
        else:
            # Public CSV endpoint (no key needed, slower). fredgraph supports
            # transformation codes via the `transformation` query param.
            obs_url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
            if units:
                obs_url += f"&transformation={units}"
            r = _req.get(obs_url, timeout=10)
            r.raise_for_status()
            lines = r.text.strip().split("\n")[1:]  # skip header
            result = []
            for line in lines[-limit:]:
                parts = line.split(",")
                if len(parts) == 2:
                    try:
                        result.append({"date": parts[0], "value": float(parts[1])})
                    except ValueError:
                        pass
            return result
    except Exception as e:
        logger.debug("FRED fetch %s failed: %s", series_id, e)
        return []


# ── US Macro ──────────────────────────────────────────────────────────────────

_FRED_SERIES_MAP = {
    "gdp":          ("GDP",     "GDP 实际值 (十亿美元, 季度)", "B"),
    "gdp_growth":   ("A191RL1Q225SBEA", "GDP 同比增速 (%)", "%"),
    "cpi":          ("CPIAUCSL", "CPI 城市消费者价格指数", "idx"),
    "cpi_yoy":      ("CPIAUCSL", "CPI 同比 (%)", "%", "pc1"),       # pc1 = YoY %
    "core_cpi_yoy": ("CPILFESL", "核心 CPI 同比 (%)", "%", "pc1"),
    "core_cpi":     ("CPILFESL", "核心 CPI (剔除食品能源)", "idx"),
    "pce":          ("PCEPI",    "PCE 通胀指数", "idx"),
    "fed_rate":     ("FEDFUNDS", "联邦基金利率 (%)", "%"),
    "unemployment": ("UNRATE",   "美国失业率 (%)", "%"),
    "m2":           ("M2SL",     "M2 货币供给 (十亿美元)", "B"),
    "10y_yield":    ("DGS10",    "10年期国债收益率 (%)", "%"),
    "2y_yield":     ("DGS2",     "2年期国债收益率 (%)", "%"),
    "vix":          ("VIXCLS",   "VIX 恐慌指数", ""),
    "retail_sales": ("RSAFS",    "零售销售额 (百万美元, 季调)", "M"),
    "industrial":   ("INDPRO",   "工业产出指数", "idx"),
    "housing":      ("HOUST",    "新屋开工 (千套, 季调年化)", "K"),
    "ppi":          ("PPIACO",   "PPI 生产者价格指数", "idx"),
}


def get_us_macro(indicator: str = "all", periods: int = 12) -> dict:
    """
    获取美国宏观经济数据。

    indicator: "gdp" | "cpi" | "fed_rate" | "unemployment" | "m2" |
               "10y_yield" | "retail_sales" | "all"
    periods: 返回最近 N 期数据
    """
    if indicator == "all":
        keys = ["gdp_growth", "cpi_yoy", "fed_rate", "unemployment", "10y_yield",
                "2y_yield", "m2", "ppi"]
    else:
        keys = [indicator] if indicator in _FRED_SERIES_MAP else []
        if not keys:
            return {"success": False, "error": f"未知指标: {indicator}。可选: {', '.join(_FRED_SERIES_MAP)}"}

    results = {}
    for key in keys:
        _spec = _FRED_SERIES_MAP[key]
        series_id, label, unit = _spec[0], _spec[1], _spec[2]
        _units = _spec[3] if len(_spec) > 3 else ""
        # pc1 (YoY) needs ~13 months of monthly data to compute the first point
        _lim = max(periods, 14) if _units == "pc1" else periods
        data = _fred_series(series_id, limit=_lim, units=_units)
        if data:
            latest = data[-1]
            prev   = data[-2] if len(data) >= 2 else None
            change = None
            if latest["value"] is not None and prev and prev["value"] is not None:
                change = round(latest["value"] - prev["value"], 3)
            results[key] = {
                "label":  label,
                "unit":   unit,
                "latest": latest,
                "prev":   prev,
                "change": change,
                "series": data[-6:],  # last 6 periods for sparkline
            }

    if not results:
        return {"success": False, "error": "FRED 数据获取失败（无 API Key 时使用公共 CSV 端点，速度较慢）"}

    # Yield curve shape
    if "10y_yield" in results and "2y_yield" in results:
        v10 = results["10y_yield"]["latest"]["value"] or 0
        v2  = results["2y_yield"]["latest"]["value"] or 0
        spread = round(v10 - v2, 3)
        results["_yield_curve"] = {
            "spread_10y_2y": spread,
            "shape": "正常" if spread > 0.2 else "倒挂" if spread < 0 else "平坦",
        }

    return {"success": True, "country": "US", "indicator": indicator,
            "data": results, "provider": "FRED"}


# ── China Macro ───────────────────────────────────────────────────────────────

def get_cn_macro(indicator: str = "all") -> dict:
    """
    获取中国宏观经济数据 (via akshare)。

    indicator: "cpi" | "ppi" | "pmi" | "gdp" | "lpr" | "m2" | "all"
    """
    if not _HAS_AK:
        return {"success": False, "error": "akshare 未安装，请运行: pip install akshare"}

    results = {}

    def _fetch(fn, key, label):
        try:
            df = fn()
            if df is None or (hasattr(df, "empty") and df.empty):
                return
            # Keep last 12 rows
            df = df.tail(12)
            records = df.to_dict("records") if _HAS_PD else []
            latest = records[-1] if records else {}
            results[key] = {"label": label, "latest": latest, "series": records}
        except Exception as e:
            logger.debug("akshare %s failed: %s", key, e)

    if indicator in ("cpi", "all"):
        _fetch(ak.macro_china_cpi_yearly, "cpi", "中国 CPI 同比 (%)")

    if indicator in ("ppi", "all"):
        _fetch(ak.macro_china_ppi_yearly, "ppi", "中国 PPI 同比 (%)")

    if indicator in ("pmi", "all"):
        _fetch(ak.macro_china_pmi_yearly, "pmi_manufacturing",
               "制造业 PMI")
        try:
            df_s = ak.macro_china_non_man_pmi()
            if df_s is not None and not df_s.empty:
                records_s = df_s.tail(12).to_dict("records")
                results["pmi_service"] = {
                    "label": "非制造业 PMI",
                    "latest": records_s[-1] if records_s else {},
                    "series": records_s,
                }
        except Exception:
            pass

    if indicator in ("gdp", "all"):
        _fetch(ak.macro_china_gdp_yearly, "gdp", "中国 GDP 同比增速 (%)")

    if indicator in ("lpr", "all"):
        try:
            df_lpr = ak.macro_china_lpr()
            if df_lpr is not None and not df_lpr.empty:
                records_l = df_lpr.tail(6).to_dict("records")
                results["lpr"] = {"label": "LPR 利率", "series": records_l,
                                  "latest": records_l[-1] if records_l else {}}
        except Exception as e:
            logger.debug("LPR fetch failed: %s", e)

    if indicator in ("m2", "all"):
        _fetch(ak.macro_china_money_supply, "m2", "中国 M2 同比增速 (%)")

    if not results:
        return {"success": False, "error": "未能获取任何中国宏观数据"}

    return {"success": True, "country": "CN", "indicator": indicator,
            "data": results, "provider": "akshare"}


# ── Economic Calendar ─────────────────────────────────────────────────────────

def get_economic_calendar(days_ahead: int = 7) -> dict:
    """
    获取未来 N 天的重大经济事件日历。
    数据来源：投资道/东方财富 (akshare)，备用: Finnhub
    """
    results = []

    # Try akshare economic calendar (东方财富)
    if _HAS_AK:
        try:
            df = ak.news_economic_baidu(date=datetime.now().strftime("%Y%m%d"))
            if df is not None and not df.empty:
                cols = [c for c in ["time","event","actual","forecast","previous","importance"]
                        if c in df.columns]
                results = df[cols].head(30).to_dict("records") if cols else []
        except Exception as e:
            logger.debug("akshare calendar failed: %s", e)

    # Fallback: Finnhub economic calendar
    if not results and _HAS_REQUESTS:
        try:
            key = _get_finnhub_key()
            if key:
                from_dt = datetime.now().strftime("%Y-%m-%d")
                to_dt   = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
                r = _req.get(
                    "https://finnhub.io/api/v1/calendar/economic",
                    params={"from": from_dt, "to": to_dt, "token": key},
                    timeout=8,
                )
                r.raise_for_status()
                results = r.json().get("economicCalendar", [])
        except Exception as e:
            logger.debug("Finnhub calendar failed: %s", e)

    if not results:
        # Static upcoming FOMC/PBOC dates as fallback
        results = [
            {"event": "FOMC Meeting", "importance": "HIGH",
             "note": "具体日期请查阅 federalreserve.gov/monetarypolicy"},
            {"event": "PBOC LPR Announcement", "importance": "HIGH",
             "note": "每月 20 日前后公布"},
            {"event": "US CPI Release", "importance": "HIGH",
             "note": "每月第二周（周二或周三）"},
            {"event": "China PMI Release", "importance": "MEDIUM",
             "note": "每月最后一天（官方）/ 次月第一工作日（财新）"},
        ]

    return {"success": True, "events": results, "days_ahead": days_ahead,
            "provider": "akshare/finnhub"}


def _get_finnhub_key() -> str:
    """Read Finnhub key from env or providers.json."""
    k = os.getenv("FINNHUB_API_KEY", "")
    if not k:
        try:
            import json
            p = Path.home() / ".arthera" / "providers.json"
            if p.exists():
                d = json.loads(p.read_text())
                k = d.get("finnhub", {}).get("api_key", "") or d.get("finnhub_api_key", "")
        except Exception:
            pass
    return k


# ── Central Bank Policy Rates ─────────────────────────────────────────────────

def get_central_bank_rates() -> dict:
    """主要央行政策利率快照。"""
    CB_TICKERS = {
        "美联储 (Fed Funds)": "FEDFUNDS",
        "欧央行 (ECB Refi)":  "ECBDFR",
        "英央行 (BoE Rate)":  "BOERUKM",
        "日央行 (BoJ Rate)":  "IRSTCI01JPM156N",
    }
    rates = {}
    for name, series in CB_TICKERS.items():
        data = _fred_series(series, limit=3)
        if data:
            latest = data[-1]
            rates[name] = latest["value"]

    # PBOC LPR via akshare
    if _HAS_AK:
        try:
            df = ak.macro_china_lpr()
            if df is not None and not df.empty:
                row = df.iloc[-1]
                rates["中国人民银行 LPR 1Y"] = float(row.get("1年期贷款市场报价利率", row.iloc[1]))
                rates["中国人民银行 LPR 5Y"] = float(row.get("5年期贷款市场报价利率", row.iloc[2]))
        except Exception:
            pass

    if not rates:
        return {"success": False, "error": "无法获取央行利率数据"}

    return {"success": True, "rates": rates, "provider": "FRED+akshare"}
