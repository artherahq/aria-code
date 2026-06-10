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

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        """
        优先用 stock_zh_a_spot_em（全市场实时快照），
        如超时/失败则降级到 stock_individual_info_em（单股信息）。
        """
        try:
            import akshare as ak, warnings
            warnings.filterwarnings("ignore")
            code = self._normalize_code(symbol)

            # 方法一：全市场快照（网络慢时可能超时）
            try:
                df = ak.stock_zh_a_spot_em()
                row = df[df["代码"] == code]
                if not row.empty:
                    r = row.iloc[0]
                    return QuoteResult(
                        symbol      = symbol,
                        name        = str(r.get("名称", "")),
                        price       = float(r.get("最新价",   0) or 0),
                        change      = float(r.get("涨跌额",   0) or 0),
                        change_pct  = float(r.get("涨跌幅",   0) or 0),
                        volume      = float(r.get("成交量",   0) or 0),
                        market_cap  = float(r.get("总市值",   0) or 0),
                        pe_ttm      = float(r.get("市盈率-动态", 0) or 0),
                        pb          = float(r.get("市净率",   0) or 0),
                        high_52w    = float(r.get("52周最高", 0) or 0),
                        low_52w     = float(r.get("52周最低", 0) or 0),
                        currency    = "CNY",
                        market      = "a_share",
                        source      = self.name,
                    )
            except Exception:
                pass

            # 方法二：单股实时行情（Eastmoney 推送）
            df2 = ak.stock_individual_info_em(symbol=code)
            if df2 is not None and not df2.empty:
                info = dict(zip(df2["item"], df2["value"]))
                price = float(str(info.get("最新", info.get("收盘", 0))).replace(",","") or 0)
                prev  = float(str(info.get("昨收", price)).replace(",","") or price)
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
            return None
        except Exception as e:
            logger.debug(f"[akshare] quote {symbol} 失败: {e}")
            return None

    def history(self, symbol: str, days: int = 90, interval: str = "1d") -> Optional[HistoryResult]:
        try:
            import akshare as ak
            code  = self._normalize_code(symbol)
            end   = date.today().strftime("%Y%m%d")
            start = (date.today() - timedelta(days=days + 10)).strftime("%Y%m%d")
            df    = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start, end_date=end, adjust="qfq"
            )
            if df is None or df.empty:
                return None
            return HistoryResult(symbol=symbol, data=df, source=self.name, interval=interval)
        except Exception as e:
            logger.debug(f"[akshare] history {symbol} 失败: {e}")
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
