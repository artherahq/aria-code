"""
datasources/sources/akshare_source.py — Akshare A股数据源
==========================================================
免费，无需 API key，覆盖 A股实时行情、历史数据、北向资金等。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, Optional

from ..base import BaseDataSource, FundamentalsResult, HistoryResult, QuoteResult

logger = logging.getLogger(__name__)


class AkshareSource(BaseDataSource):

    name         = "akshare"
    markets      = ["a_share"]
    requires_key = False

    def _normalize_code(self, symbol: str) -> str:
        """'sh600519' / '600519' / '600519.SH' → '600519'"""
        s = symbol.upper().replace("SH", "").replace("SZ", "").replace("BJ", "")
        s = s.replace(".", "").strip()
        if s.startswith("6") or s.startswith("0") or s.startswith("3") or s.startswith("8"):
            return s[:6]
        return s

    def _yf_symbol(self, code: str) -> str:
        """600519 → 600519.SS，000858 → 000858.SZ，300xxx → 300xxx.SZ"""
        if code.startswith("6") or code.startswith("9"):
            return f"{code}.SS"
        return f"{code}.SZ"

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        """
        三级降级:
        1. akshare stock_zh_a_spot_em (全市场快照)
        2. akshare stock_individual_info_em (单股)
        3. yfinance .SS/.SZ (国际用户兜底)
        4. 最近交易日收盘价 from history
        """
        import warnings
        warnings.filterwarnings("ignore")
        code = self._normalize_code(symbol)

        # ── 方法1: 全市场快照 ────────────────────────────────────────────────
        try:
            import akshare as ak
            df = ak.stock_zh_a_spot_em()
            row = df[df["代码"] == code]
            if not row.empty:
                r = row.iloc[0]
                price = float(r.get("最新价", 0) or 0)
                if price > 0:
                    return QuoteResult(
                        symbol     = symbol,
                        name       = str(r.get("名称", "")),
                        price      = price,
                        change     = float(r.get("涨跌额",   0) or 0),
                        change_pct = float(r.get("涨跌幅",   0) or 0),
                        volume     = float(r.get("成交量",   0) or 0),
                        market_cap = float(r.get("总市值",   0) or 0),
                        pe_ttm     = float(r.get("市盈率-动态", 0) or 0),
                        pb         = float(r.get("市净率",   0) or 0),
                        high_52w   = float(r.get("52周最高", 0) or 0),
                        low_52w    = float(r.get("52周最低", 0) or 0),
                        currency   = "CNY",
                        market     = "a_share",
                        source     = self.name,
                    )
        except Exception:
            pass

        # ── 方法2: 单股信息（EastMoney 单接口） ────────────────────────────
        try:
            import akshare as ak
            df2 = ak.stock_individual_info_em(symbol=code)
            if df2 is not None and not df2.empty:
                info = dict(zip(df2["item"], df2["value"]))
                price = float(str(info.get("最新", info.get("收盘", 0))).replace(",", "") or 0)
                prev  = float(str(info.get("昨收", price)).replace(",", "") or price)
                if price > 0:
                    return QuoteResult(
                        symbol     = symbol,
                        name       = str(info.get("股票简称", "")),
                        price      = price,
                        change     = round(price - prev, 4),
                        change_pct = round((price - prev) / prev * 100, 2) if prev else 0,
                        currency   = "CNY",
                        market     = "a_share",
                        source     = self.name,
                    )
        except Exception:
            pass

        # ── 方法3: yfinance .SS/.SZ（海外访问兜底） ─────────────────────────
        try:
            import yfinance as yf
            yf_sym = self._yf_symbol(code)
            ticker = yf.Ticker(yf_sym)

            price = None
            prev  = None
            info  = {}
            try:
                fi    = ticker.fast_info
                price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                prev  = getattr(fi, "previous_close", None) or price
            except Exception:
                pass
            if not price:
                try:
                    info  = ticker.info or {}
                    price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
                    prev  = info.get("previousClose") or price
                except Exception:
                    pass
            if price and float(price) > 0:
                if not info:
                    try:
                        info = ticker.info or {}
                    except Exception:
                        pass
                chg   = float(price) - float(prev) if prev else 0
                chg_p = chg / float(prev) * 100 if prev else 0
                return QuoteResult(
                    symbol     = symbol,
                    name       = info.get("shortName") or info.get("longName") or symbol,
                    price      = float(price),
                    change     = chg,
                    change_pct = chg_p,
                    volume     = float(info.get("volume") or 0),
                    market_cap = float(info.get("marketCap") or 0),
                    pe_ttm     = float(info.get("trailingPE") or 0),
                    pb         = float(info.get("priceToBook") or 0),
                    currency   = "CNY",
                    market     = "a_share",
                    source     = f"{self.name}+yfinance",
                )
        except Exception:
            pass

        # ── 方法4: 最近历史收盘价 ────────────────────────────────────────────
        try:
            h = self.history(symbol, days=5)
            if h and h.data is not None and not h.data.empty:
                df = h.data
                # Flexible column detection (akshare returns Chinese column names)
                close_col = next(
                    (c for c in df.columns if "收盘" in str(c) or "close" in str(c).lower()),
                    df.columns[-1]
                )
                price = float(df[close_col].iloc[-1])
                prev  = float(df[close_col].iloc[-2]) if len(df) > 1 else price
                if price > 0:
                    return QuoteResult(
                        symbol     = symbol,
                        price      = price,
                        change     = round(price - prev, 4),
                        change_pct = round((price - prev) / prev * 100, 2) if prev else 0,
                        currency   = "CNY",
                        market     = "a_share",
                        source     = f"{self.name}(last_close)",
                    )
        except Exception as e:
            logger.debug(f"[akshare] quote history fallback {symbol} 失败: {e}")

        logger.debug(f"[akshare] quote {symbol}: 所有方法均失败")
        return None

    def history(self, symbol: str, days: int = 90, interval: str = "1d") -> Optional[HistoryResult]:
        # ── 方法1: akshare stock_zh_a_hist ──────────────────────────────────
        try:
            import akshare as ak
            code  = self._normalize_code(symbol)
            end   = date.today().strftime("%Y%m%d")
            start = (date.today() - timedelta(days=days + 10)).strftime("%Y%m%d")
            df    = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start, end_date=end, adjust="qfq"
            )
            if df is not None and not df.empty:
                return HistoryResult(symbol=symbol, data=df, source=self.name, interval=interval)
        except Exception as e:
            logger.debug(f"[akshare] history(akshare) {symbol} 失败: {e}")

        # ── 方法2: yfinance .SS/.SZ (海外兜底) ──────────────────────────────
        try:
            import yfinance as yf
            import pandas as pd
            code   = self._normalize_code(symbol)
            yf_sym = self._yf_symbol(code)
            ticker = yf.Ticker(yf_sym)
            period = f"{days}d" if days <= 730 else "2y"
            df     = ticker.history(period=period, auto_adjust=True)
            if df is not None and not df.empty:
                df.index = pd.to_datetime(df.index)
                return HistoryResult(symbol=symbol, data=df, source=f"{self.name}+yfinance", interval=interval)
        except Exception as e:
            logger.debug(f"[akshare] history(yfinance) {symbol} 失败: {e}")

        return None

    def fundamentals(self, symbol: str) -> Optional[FundamentalsResult]:
        """A股基本面：优先 akshare，降级 yfinance .SS/.SZ"""
        # ── 方法1: akshare 市场快照字段 ──────────────────────────────────────
        try:
            import akshare as ak
            import math
            code = self._normalize_code(symbol)
            df   = ak.stock_zh_a_spot_em()
            row  = df[df["代码"] == code]
            if not row.empty:
                r  = row.iloc[0]
                def _ak(key: str) -> Optional[float]:
                    v = r.get(key)
                    if v is None:
                        return None
                    try:
                        fv = float(v)
                        return None if (math.isnan(fv) or fv == 0) else fv
                    except (TypeError, ValueError):
                        return None
                pe  = _ak("市盈率-动态")
                pb  = _ak("市净率")
                mv  = _ak("总市值")
                if pe or pb:
                    return FundamentalsResult(
                        symbol=symbol, pe_ttm=pe, pb=pb,
                        total_mv=mv, source=self.name,
                    )
        except Exception:
            pass

        # ── 方法2: yfinance .SS/.SZ ───────────────────────────────────────────
        try:
            from .yfinance_source import YFinanceSource
            code   = self._normalize_code(symbol)
            yf_sym = self._yf_symbol(code)
            fund   = YFinanceSource().fundamentals(yf_sym)
            if fund:
                fund.symbol = symbol
                fund.source = f"{self.name}+yfinance"
                return fund
        except Exception as e:
            logger.debug(f"[akshare] fundamentals yfinance fallback {symbol} 失败: {e}")

        return None

    def northbound_flow(self) -> Optional[Dict]:
        """北向资金净流入（akshare 特有）"""
        try:
            import akshare as ak
            df = ak.stock_hsgt_north_net_flow_in_em(symbol="北向资金")
            if df is None or df.empty:
                return None
            latest = df.iloc[-1]
            return {
                "date":     str(latest.get("日期", "")),
                "net_flow": float(latest.get("当日净流入", 0) or 0),
                "source":   self.name,
            }
        except Exception as e:
            logger.debug(f"[akshare] northbound_flow 失败: {e}")
            return None
