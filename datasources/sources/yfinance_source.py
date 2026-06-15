"""
datasources/sources/yfinance_source.py — yfinance 美股/港股/加密 数据源
"""

from __future__ import annotations

import logging
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
            yf_sym = self._to_yf_symbol(symbol)
            ticker = yf.Ticker(yf_sym)

            price = None
            prev  = None
            info  = {}

            # Attempt 1: fast_info (lighter, often succeeds when info is rate-limited)
            try:
                fi = ticker.fast_info
                price = getattr(fi, "last_price", None) or getattr(fi, "previous_close", None)
                prev  = getattr(fi, "previous_close", None) or price
            except Exception:
                pass

            # Attempt 2: full info dict
            if not price:
                try:
                    info  = ticker.info or {}
                    price = (info.get("currentPrice") or info.get("regularMarketPrice")
                             or info.get("previousClose"))
                    prev  = info.get("previousClose") or price
                except Exception:
                    pass

            # Attempt 3: last close from recent history
            if not price:
                try:
                    hist = ticker.history(period="5d", interval="1d", auto_adjust=True)
                    if hist is not None and not hist.empty:
                        close_col = next((c for c in hist.columns if c.lower() == "close"), None)
                        if close_col:
                            price = float(hist[close_col].iloc[-1])
                            prev  = float(hist[close_col].iloc[-2]) if len(hist) > 1 else price
                except Exception:
                    pass

            if not price:
                return None

            # Fetch info lazily if not already fetched
            if not info:
                try:
                    info = ticker.info or {}
                except Exception:
                    pass

            chg   = price - prev if prev else 0.0
            chg_p = (chg / prev * 100) if prev else 0.0
            market   = _detect_market(symbol)
            currency = "HKD" if market == "hk" else "USD"

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
            ticker = yf.Ticker(self._to_yf_symbol(symbol))
            period = f"{days}d" if days <= 730 else "2y"
            df     = ticker.history(period=period, interval=interval, auto_adjust=True)
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
            if not info:
                return None

            def _f(key: str, mult: float = 1.0) -> Optional[float]:
                v = info.get(key)
                if v is None or v != v:  # None or NaN
                    return None
                fv = float(v) * mult
                return fv if fv != 0.0 else None

            result = FundamentalsResult(
                symbol            = symbol,
                pe_ttm            = _f("trailingPE"),
                pb                = _f("priceToBook"),
                roe               = _f("returnOnEquity", 100),
                revenue_growth    = _f("revenueGrowth",  100),
                net_profit_growth = _f("earningsGrowth", 100),
                # trailingAnnualDividendYield is consistently a fraction (e.g. 0.0035 = 0.35%)
                # dividendYield is unreliable (sometimes already pct, sometimes fraction)
                dividend_yield    = _f("trailingAnnualDividendYield", 100),
                total_mv          = _f("marketCap"),
                source            = self.name,
            )
            # Return None only if every field is None (data completely missing)
            has_any = any(
                getattr(result, f) is not None
                for f in ("pe_ttm", "pb", "roe", "revenue_growth", "total_mv")
            )
            return result if has_any else None
        except Exception as e:
            logger.debug(f"[yfinance] fundamentals {symbol} 失败: {e}")
            return None
