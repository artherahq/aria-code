"""
datasources/sources/tushare_source.py — Tushare A股数据源
==========================================================
需要 TUSHARE_TOKEN 环境变量（已在 .env 中配置）。
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from ..base import BaseDataSource, FundamentalsResult, HistoryResult, QuoteResult

logger = logging.getLogger(__name__)


def _load_token() -> str:
    token = os.getenv("TUSHARE_TOKEN", "")
    if not token:
        env_paths = [
            Path.cwd() / ".env",
            Path.cwd().parent / ".env",
            Path("/Users/mac/Desktop/Arthera/.env"),
        ]
        for p in env_paths:
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    if line.startswith("TUSHARE_TOKEN="):
                        token = line.split("=", 1)[1].strip()
                        break
            if token:
                break
    return token


class TushareSource(BaseDataSource):

    name         = "tushare"
    markets      = ["a_share"]
    requires_key = True

    def __init__(self, config=None):
        super().__init__(config)
        self._token = _load_token()
        self._pro   = None

    def is_configured(self) -> bool:
        return bool(self._token)

    def _get_pro(self):
        if self._pro is None:
            import tushare as ts
            ts.set_token(self._token)
            self._pro = ts.pro_api()
        return self._pro

    def _to_ts_code(self, symbol: str) -> str:
        s = symbol.upper().replace("SH","").replace("SZ","").replace(".","").strip()
        code = s[:6]
        return f"{code}.SH" if code.startswith(("6","9")) else f"{code}.SZ"

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        """
        使用 pro.daily 获取最近交易日收盘价（不调用 daily_basic 避免限频）。
        PE/PB 留给 fundamentals() 单独获取。
        """
        try:
            import time as _t
            pro     = self._get_pro()
            ts_code = self._to_ts_code(symbol)
            end     = date.today().strftime("%Y%m%d")
            start   = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
            df      = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            _t.sleep(0.35)
            if df is None or df.empty:
                return None
            # trade_date 格式 YYYYMMDD，字符串排序等价于日期排序
            latest = df.sort_values("trade_date", ascending=False).iloc[0]
            price  = float(latest.get("close",     0))
            prev   = float(latest.get("pre_close", price))
            return QuoteResult(
                symbol     = symbol,
                price      = price,
                change     = round(price - prev, 4),
                change_pct = float(latest.get("pct_chg", 0)),
                volume     = float(latest.get("vol",    0)),
                currency   = "CNY",
                market     = "a_share",
                source     = self.name,
                timestamp  = str(latest.get("trade_date", "")),
            )
        except Exception as e:
            logger.debug(f"[tushare] quote {symbol} 失败: {e}")
            return None

    def history(self, symbol: str, days: int = 90, interval: str = "1d") -> Optional[HistoryResult]:
        try:
            import time as _t, pandas as pd
            pro     = self._get_pro()
            ts_code = self._to_ts_code(symbol)
            end     = date.today().strftime("%Y%m%d")
            start   = (date.today() - timedelta(days=days + 15)).strftime("%Y%m%d")
            df      = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
            _t.sleep(0.4)
            if df is None or df.empty:
                return None
            df["trade_date"] = pd.to_datetime(df["trade_date"])
            df = df.rename(columns={"trade_date": "date", "vol": "volume"})
            df = df.set_index("date").sort_index()
            return HistoryResult(symbol=symbol, data=df, source=self.name, interval=interval)
        except Exception as e:
            logger.debug(f"[tushare] history {symbol} 失败: {e}")
            return None

    def fundamentals(self, symbol: str) -> Optional[FundamentalsResult]:
        try:
            import time as _t
            pro     = self._get_pro()
            ts_code = self._to_ts_code(symbol)
            for period in ["20241231", "20231231"]:
                df = pro.fina_indicator(
                    ts_code=ts_code, period=period,
                    fields="ts_code,roe,netprofit_yoy,revenue_yoy"
                )
                _t.sleep(0.4)
                if df is not None and not df.empty:
                    r = df.iloc[0]
                    return FundamentalsResult(
                        symbol          = symbol,
                        roe             = float(r.get("roe", 0) or 0),
                        revenue_growth  = float(r.get("revenue_yoy", 0) or 0),
                        net_profit_growth = float(r.get("netprofit_yoy", 0) or 0),
                        source          = self.name,
                    )
        except Exception as e:
            logger.debug(f"[tushare] fundamentals {symbol} 失败: {e}")
        return None
