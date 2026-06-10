"""
datasources/sources/yfinance_source.py — yfinance 美股/港股/加密 数据源
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from ..base import BaseDataSource, FundamentalsResult, HistoryResult, QuoteResult, _detect_market

logger = logging.getLogger(__name__)


class YFinanceSource(BaseDataSource):

    name         = "yfinance"
    markets      = ["us", "hk", "crypto"]
    requires_key = False

    def _to_yf_symbol(self, symbol: str) -> str:
        s = symbol.upper()
        if "/" in s:   # BTC/USDT → BTC-USD
            base, quote = s.split("/", 1)
            return f"{base}-{'USD' if 'USDT' in quote else quote}"
        return s

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        try:
            import yfinance as yf
            ticker = yf.Ticker(self._to_yf_symbol(symbol))
            info   = ticker.info or {}
            price  = (info.get("currentPrice") or info.get("regularMarketPrice")
                      or info.get("previousClose") or 0)
            prev   = info.get("previousClose", price)
            chg    = price - prev
            chg_p  = (chg / prev * 100) if prev else 0
            market = _detect_market(symbol)
            currency = "HKD" if market == "hk" else ("USD" if market in ("us","crypto") else "USD")
            return QuoteResult(
                symbol      = symbol,
                name        = info.get("shortName") or info.get("longName") or symbol,
                price       = float(price),
                change      = float(chg),
                change_pct  = float(chg_p),
                volume      = float(info.get("volume") or info.get("regularMarketVolume") or 0),
                market_cap  = float(info.get("marketCap") or 0),
                pe_ttm      = float(info.get("trailingPE") or 0),
                pb          = float(info.get("priceToBook") or 0),
                high_52w    = float(info.get("fiftyTwoWeekHigh") or 0),
                low_52w     = float(info.get("fiftyTwoWeekLow") or 0),
                currency    = currency,
                market      = market,
                source      = self.name,
            )
        except Exception as e:
            logger.debug(f"[yfinance] quote {symbol} 失败: {e}")
            return None

    def history(self, symbol: str, days: int = 90, interval: str = "1d") -> Optional[HistoryResult]:
        try:
            import yfinance as yf
            ticker  = yf.Ticker(self._to_yf_symbol(symbol))
            df      = ticker.history(period=f"{days}d", interval=interval)
            if df is None or df.empty:
                return None
            df.columns = [c.lower() for c in df.columns]
            return HistoryResult(symbol=symbol, data=df, source=self.name, interval=interval)
        except Exception as e:
            logger.debug(f"[yfinance] history {symbol} 失败: {e}")
            return None

    def fundamentals(self, symbol: str) -> Optional[FundamentalsResult]:
        try:
            import yfinance as yf
            info = yf.Ticker(self._to_yf_symbol(symbol)).info or {}
            return FundamentalsResult(
                symbol          = symbol,
                pe_ttm          = float(info.get("trailingPE") or 0),
                pb              = float(info.get("priceToBook") or 0),
                roe             = float(info.get("returnOnEquity") or 0) * 100,
                revenue_growth  = float(info.get("revenueGrowth") or 0) * 100,
                dividend_yield  = float(info.get("dividendYield") or 0) * 100,
                total_mv        = float(info.get("marketCap") or 0),
                source          = self.name,
            )
        except Exception as e:
            logger.debug(f"[yfinance] fundamentals {symbol} 失败: {e}")
            return None
