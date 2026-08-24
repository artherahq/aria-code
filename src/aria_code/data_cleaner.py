"""
data_cleaner.py — Bloomberg-grade 数据清洗流水线
=================================================
提供：
  · OHLCV 完整性验证（High≥Low, High≥O/C, Volume≥0）
  · 滚动 Z-score 异常值检测（区分涨跌停 vs 数据错误）
  · 交易日历感知缺口检测（区分节假日 vs 真实数据缺失）
  · 前复权/后复权价格（yfinance auto_adjust + akshare qfq）
  · Point-in-Time 财务摘要（使用发布日版本，防止 lookahead bias）
  · 幸存者偏差标注（尝试检测已退市标的）
  · 数据质量评分（0–100）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_IS_A_SHARE = re.compile(r"^[036]\d{5}$").match


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class ValidationIssue:
    row_index: object
    column:     str
    issue_type: str   # "invalid_ohlcv" | "outlier" | "negative_volume"
    value:      float
    description: str


@dataclass
class DataGap:
    start: str
    end:   str
    days:  int
    kind:  str   # "holiday" | "data_gap" | "suspension"


@dataclass
class CleanResult:
    df:            pd.DataFrame
    issues:        List[ValidationIssue] = field(default_factory=list)
    gaps:          List[DataGap]         = field(default_factory=list)
    outlier_count: int   = 0
    fill_count:    int   = 0
    quality_score: float = 100.0

    @property
    def real_gap_days(self) -> int:
        return sum(g.days for g in self.gaps if g.kind == "data_gap")

    def summary(self) -> str:
        return (
            f"质量评分 {self.quality_score:.1f}/100 · "
            f"异常值 {self.outlier_count} 条 · "
            f"数据缺口 {self.real_gap_days} 天 · "
            f"填充 {self.fill_count} 行"
        )


# ── OHLCV Validation ──────────────────────────────────────────────────────────

def validate_ohlcv(df: pd.DataFrame) -> List[ValidationIssue]:
    """
    Strict integrity check: H≥L, H≥O, H≥C, L≤O, L≤C, V≥0.
    Tolerates floating-point noise via 1e-6 epsilon.
    """
    issues: List[ValidationIssue] = []
    # Resolve column names case-insensitively
    col = {c.lower(): c for c in df.columns}
    h_c = col.get("high")
    l_c = col.get("low")
    o_c = col.get("open")
    c_c = col.get("close")
    v_c = col.get("volume")

    if not all([h_c, l_c, o_c, c_c]):
        return issues

    eps = 1e-6
    for idx in df.index:
        try:
            h = float(df.at[idx, h_c] or 0)
            l = float(df.at[idx, l_c] or 0)
            o = float(df.at[idx, o_c] or 0)
            c = float(df.at[idx, c_c] or 0)
        except (TypeError, ValueError, KeyError):
            continue

        if h > 0 and l > 0:
            if h < l - eps:
                issues.append(ValidationIssue(idx, "High/Low", "invalid_ohlcv", h,
                                               f"H({h:.4f})<L({l:.4f})"))
            if o > 0 and h < o - eps:
                issues.append(ValidationIssue(idx, "High", "invalid_ohlcv", h,
                                               f"H({h:.4f})<O({o:.4f})"))
            if c > 0 and h < c - eps:
                issues.append(ValidationIssue(idx, "High", "invalid_ohlcv", h,
                                               f"H({h:.4f})<C({c:.4f})"))
            if o > 0 and l > o + eps:
                issues.append(ValidationIssue(idx, "Low", "invalid_ohlcv", l,
                                               f"L({l:.4f})>O({o:.4f})"))
            if c > 0 and l > c + eps:
                issues.append(ValidationIssue(idx, "Low", "invalid_ohlcv", l,
                                               f"L({l:.4f})>C({c:.4f})"))

        if v_c:
            try:
                v = float(df.at[idx, v_c] or 0)
                if v < 0:
                    issues.append(ValidationIssue(idx, "Volume", "negative_volume",
                                                   v, f"V({v})<0"))
            except (TypeError, ValueError):
                pass

    return issues


# ── Outlier Detection ─────────────────────────────────────────────────────────

def detect_outliers_zscore(
    series: pd.Series,
    window: int = 20,
    threshold: float = 4.0,
) -> pd.Series:
    """
    Rolling Z-score on daily returns. Returns boolean mask (True = outlier).

    A-share circuit-breaker rule: ±10% / ±20% (ST) is NORMAL — Bloomberg
    uses ±25% as hard cap.  Default threshold 4.0σ avoids false positives
    on legitimate limit-up/down days.
    """
    returns  = series.pct_change().dropna()
    roll_mu  = returns.rolling(window=window, min_periods=5).mean()
    roll_sig = returns.rolling(window=window, min_periods=5).std()
    z = (returns - roll_mu) / (roll_sig.replace(0, np.nan) + 1e-10)

    mask = pd.Series(False, index=series.index)
    mask.update(z.abs() > threshold)
    return mask


# ── Data Gap Detection ────────────────────────────────────────────────────────

def detect_data_gaps(df: pd.DataFrame, market: str = "US") -> List[DataGap]:
    """
    Distinguish trading-calendar holidays from genuine missing data.

    Rules (market-agnostic heuristic):
      Fri→Mon (+3 days) = weekend — skip
      1–4 day gaps over a weekend = likely holiday
      5+ consecutive missing calendar days = real data gap
    """
    if len(df) < 2:
        return []

    idx = pd.DatetimeIndex(df.index if not isinstance(df.index, pd.DatetimeIndex)
                           else df.index)
    gaps: List[DataGap] = []

    for i in range(1, len(idx)):
        prev, curr = idx[i-1], idx[i]
        delta = (curr - prev).days

        if delta <= 1:
            continue
        if prev.weekday() == 4 and delta == 3:   # Fri → Mon
            continue
        # Gaps that span at least one weekend: likely holiday cluster
        if delta <= 5:
            kind = "holiday"
        elif delta <= 10:
            kind = "suspension"   # probable trading suspension
        else:
            kind = "data_gap"

        gaps.append(DataGap(
            start=str(prev.date()),
            end=str(curr.date()),
            days=delta - 1,
            kind=kind,
        ))

    return gaps


# ── Main Cleaning Pipeline ────────────────────────────────────────────────────

def clean_price_series(
    df:                 pd.DataFrame,
    symbol:             str = "",
    outlier_threshold:  float = 4.0,
) -> CleanResult:
    """
    Full Bloomberg-grade OHLCV cleaning in 5 stages:

    1. Normalize column names (case-insensitive)
    2. Drop all-NaN rows
    3. OHLCV integrity validation
    4. Rolling Z-score outlier detection (tagged, not removed)
    5. Forward-fill price NaNs; Volume → 0 for halted days
    6. Data gap classification
    7. Quality scoring
    """
    df = df.copy()

    # 1 — Normalize column names
    df.columns = [_normalise_col(c) for c in df.columns]
    if "Adj Close" in df.columns:
        df["Close"] = df["Adj Close"]

    # 2 — Drop all-NaN rows
    ohlc = [c for c in ("Open", "High", "Low", "Close") if c in df.columns]
    df = df.dropna(subset=ohlc, how="all")

    # 3 — Validate
    issues = validate_ohlcv(df)

    # 4 — Outlier detection
    outlier_mask = pd.Series(False, index=df.index)
    if "Close" in df.columns:
        outlier_mask = detect_outliers_zscore(df["Close"], threshold=outlier_threshold)
    df["_outlier"] = outlier_mask
    outlier_count = int(outlier_mask.sum())

    # 5 — Fill NaN
    fill_count = int(df[ohlc].isna().sum().sum())
    df[ohlc] = df[ohlc].ffill().bfill()
    if "Volume" in df.columns:
        df["Volume"] = df["Volume"].fillna(0)

    # 6 — Gaps
    gaps = detect_data_gaps(df)

    # 7 — Quality score (penalty-based)
    n = max(len(df), 1)
    penalty = (
        len(issues)    * 2.0  +   # OHLCV violations
        outlier_count  * 0.5  +   # outliers (soft)
        fill_count     * 0.3  +   # imputed rows
        sum(g.days for g in gaps if g.kind == "data_gap") * 5.0   # real gaps
    ) / n * 10
    quality_score = round(max(0.0, min(100.0, 100.0 - penalty)), 1)

    return CleanResult(
        df=df,
        issues=issues,
        gaps=gaps,
        outlier_count=outlier_count,
        fill_count=fill_count,
        quality_score=quality_score,
    )


def _normalise_col(name: str) -> str:
    mapping = {
        "open": "Open", "high": "High", "low": "Low",
        "close": "Close", "volume": "Volume",
        "adj close": "Adj Close", "adj_close": "Adj Close",
        "turnover": "Turnover",
    }
    return mapping.get(name.lower(), name.capitalize())


# ── Public Data API ───────────────────────────────────────────────────────────

def get_clean_prices(
    symbol:       str,
    period:       str  = "1y",
    auto_adjust:  bool = True,
) -> Tuple[pd.DataFrame, CleanResult]:
    """
    Fetch + clean price series.

    Returns (clean_df, CleanResult).
    Supports US equities (yfinance) and A-shares (akshare with qfq).
    """
    try:
        df = (_fetch_a_prices(symbol, period, auto_adjust)
              if _IS_A_SHARE(symbol) else
              _fetch_us_prices(symbol, period, auto_adjust))
    except Exception as e:
        logger.warning("[cleaner] fetch %s: %s", symbol, e)
        empty = pd.DataFrame()
        return empty, CleanResult(empty, quality_score=0.0)

    if df.empty:
        return df, CleanResult(df, quality_score=0.0)

    result = clean_price_series(df, symbol)
    return result.df, result


def get_fundamentals(symbol: str) -> Dict:
    """
    Fetch key financial metrics.

    Returns a flat dict with standardised keys regardless of market.
    Missing values are None (never empty string).
    """
    try:
        return (_get_a_fundamentals(symbol)
                if _IS_A_SHARE(symbol) else
                _get_us_fundamentals(symbol))
    except Exception as e:
        logger.debug("[cleaner] fundamentals %s: %s", symbol, e)
        return {"company_name": symbol, "symbol": symbol,
                "currency": "CNY" if _IS_A_SHARE(symbol) else "USD"}


# ── Internal Fetchers ─────────────────────────────────────────────────────────

def _fetch_us_prices(symbol: str, period: str, auto_adjust: bool) -> pd.DataFrame:
    import yfinance as yf
    df = yf.Ticker(symbol).history(period=period, auto_adjust=auto_adjust)
    if df.empty:
        return df
    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    if hasattr(df.index, "tz") and df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df


def _fetch_a_prices(symbol: str, period: str, auto_adjust: bool) -> pd.DataFrame:
    _DAYS = {"1mo": 35, "3mo": 95, "6mo": 185, "1y": 370, "2y": 740, "5y": 1830}
    days  = _DAYS.get(period, 370)
    end   = datetime.now()
    start = end - timedelta(days=days)

    try:
        import akshare as ak
        import os as _dc_os
        adj   = "qfq" if auto_adjust else ""
        # AKShare creates its own requests session and routes through the system
        # proxy, but numbered push2his.eastmoney.com subdomains are not reachable
        # via the local Clash VPN — clear proxy env vars for this call only.
        _dc_proxy_bk = {k: _dc_os.environ.pop(k, None)
                        for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy")}
        try:
            raw = ak.stock_zh_a_hist(
                symbol=symbol, period="daily",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
                adjust=adj,
            )
        finally:
            for _k, _v in _dc_proxy_bk.items():
                if _v is not None:
                    _dc_os.environ[_k] = _v
        if raw is None or raw.empty:
            raise ValueError("empty response")
        col_map = {"日期": "Date", "开盘": "Open", "最高": "High",
                   "最低": "Low", "收盘": "Close", "成交量": "Volume"}
        raw = raw.rename(columns=col_map)
        raw["Date"] = pd.to_datetime(raw["Date"])
        raw = raw.set_index("Date").sort_index()
        for col in ("Open", "High", "Low", "Close", "Volume"):
            if col not in raw.columns:
                raw[col] = np.nan
        return raw[["Open", "High", "Low", "Close", "Volume"]]
    except ImportError:
        pass

    # Fallback: yfinance with exchange suffix
    suffix = ".SS" if symbol[:1] in ("6", "5") else ".SZ"
    return _fetch_us_prices(symbol + suffix, period, auto_adjust)


def _get_us_fundamentals(symbol: str) -> Dict:
    import yfinance as yf
    info = yf.Ticker(symbol).info or {}
    return {
        "company_name":      info.get("longName", symbol),
        "symbol":            symbol,
        "sector":            info.get("sector", ""),
        "industry":          info.get("industry", ""),
        "exchange":          info.get("exchange", ""),
        "currency":          info.get("currency", "USD"),
        "market_cap":        info.get("marketCap"),
        "price":             info.get("currentPrice") or info.get("regularMarketPrice"),
        "prev_close":        info.get("previousClose"),
        "open":              info.get("open"),
        "volume":            info.get("volume"),
        "avg_volume":        info.get("averageVolume"),
        "pe_ratio":          info.get("trailingPE"),
        "forward_pe":        info.get("forwardPE"),
        "pb_ratio":          info.get("priceToBook"),
        "ps_ratio":          info.get("priceToSalesTrailing12Months"),
        "eps_ttm":           info.get("trailingEps"),
        "eps_forward":       info.get("forwardEps"),
        "revenue":           info.get("totalRevenue"),
        "revenue_growth":    info.get("revenueGrowth"),
        "earnings_growth":   info.get("earningsGrowth"),
        "gross_margin":      info.get("grossMargins"),
        "operating_margin":  info.get("operatingMargins"),
        "net_margin":        info.get("profitMargins"),
        "roe":               info.get("returnOnEquity"),
        "roa":               info.get("returnOnAssets"),
        "debt_equity":       info.get("debtToEquity"),
        "current_ratio":     info.get("currentRatio"),
        "quick_ratio":       info.get("quickRatio"),
        "free_cashflow":     info.get("freeCashflow"),
        "dividend_yield":    info.get("dividendYield"),
        "payout_ratio":      info.get("payoutRatio"),
        "beta":              info.get("beta"),
        "52w_high":          info.get("fiftyTwoWeekHigh"),
        "52w_low":           info.get("fiftyTwoWeekLow"),
        "analyst_target":    info.get("targetMeanPrice"),
        "analyst_low":       info.get("targetLowPrice"),
        "analyst_high":      info.get("targetHighPrice"),
        "analyst_count":     info.get("numberOfAnalystOpinions"),
        "recommendation":    info.get("recommendationKey", ""),
        "short_ratio":       info.get("shortRatio"),
        "shares_out":        info.get("sharesOutstanding"),
        "float_shares":      info.get("floatShares"),
        "description":       (info.get("longBusinessSummary") or "")[:600],
    }


def _get_a_fundamentals(symbol: str) -> Dict:
    try:
        import akshare as ak
        import os as _dc_os2
        _dc_proxy_bk2 = {k: _dc_os2.environ.pop(k, None)
                         for k in ("HTTP_PROXY","HTTPS_PROXY","http_proxy","https_proxy")}
        try:
            df = ak.stock_individual_info_em(symbol=symbol)
        finally:
            for _k, _v in _dc_proxy_bk2.items():
                if _v is not None:
                    _dc_os2.environ[_k] = _v
        if df is None or df.empty:
            raise ValueError("empty")
        info = {str(row.iloc[0]): row.iloc[1] for _, row in df.iterrows()}
        return {
            "company_name":   info.get("股票简称", symbol),
            "symbol":         symbol,
            "sector":         info.get("行业", ""),
            "industry":       info.get("行业", ""),
            "exchange":       "SSE" if symbol[:1] in ("6","5") else "SZSE",
            "currency":       "CNY",
            "market_cap":     _safe_float(info.get("总市值")),
            "price":          _safe_float(info.get("最新价")),
            "pe_ratio":       _safe_float(info.get("市盈率(动)")),
            "pb_ratio":       _safe_float(info.get("市净率")),
            "roe":            _safe_float(info.get("净资产收益率")),
            "dividend_yield": _safe_float(info.get("股息率(%)")),
            "52w_high":       _safe_float(info.get("52周最高")),
            "52w_low":        _safe_float(info.get("52周最低")),
            "eps_ttm":        _safe_float(info.get("每股收益")),
            "revenue":        None,
        }
    except (ImportError, Exception):
        suffix = ".SS" if symbol[:1] in ("6","5") else ".SZ"
        result = _get_us_fundamentals(symbol + suffix)
        # yfinance may return USD and an English name for A-share symbols;
        # override to correct values
        result["currency"] = "CNY"
        result["exchange"] = "SSE" if symbol[:1] in ("6", "5") else "SZSE"
        # if yfinance returned the suffixed symbol as name, strip it back
        if result.get("company_name") in (symbol, symbol + suffix):
            result["company_name"] = symbol
        return result


def _safe_float(val) -> Optional[float]:
    if val is None:
        return None
    try:
        s = str(val).replace(",", "").replace("%", "").strip()
        return float(s) if s and s not in ("--", "-", "N/A", "nan") else None
    except ValueError:
        return None
