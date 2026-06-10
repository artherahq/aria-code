"""
datasources/base.py — 数据源统一接口
=====================================
所有数据源实现 BaseDataSource，输出统一 schema，
上层代码不关心具体是 akshare / yfinance / tushare。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── 统一输出 schema ────────────────────────────────────────────────────────────

@dataclass
class QuoteResult:
    symbol:         str
    name:           str           = ""
    price:          float         = 0.0
    change:         float         = 0.0
    change_pct:     float         = 0.0
    volume:         float         = 0.0
    market_cap:     float         = 0.0
    pe_ttm:         float         = 0.0
    pb:             float         = 0.0
    high_52w:       float         = 0.0
    low_52w:        float         = 0.0
    currency:       str           = "CNY"
    market:         str           = ""     # "a_share" | "us" | "hk" | "crypto"
    source:         str           = ""     # 实际使用的数据源名称
    timestamp:      str           = ""
    extra:          Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol, "name": self.name,
            "price": self.price, "change": self.change,
            "change_pct": self.change_pct, "volume": self.volume,
            "market_cap": self.market_cap, "pe_ttm": self.pe_ttm,
            "pb": self.pb, "high_52w": self.high_52w, "low_52w": self.low_52w,
            "currency": self.currency, "market": self.market,
            "source": self.source, "timestamp": self.timestamp,
            **self.extra,
        }


@dataclass
class HistoryResult:
    symbol:   str
    data:     Any          = None   # pandas DataFrame
    source:   str          = ""
    interval: str          = "1d"


@dataclass
class FundamentalsResult:
    symbol:         str
    pe_ttm:         float = 0.0
    pb:             float = 0.0
    roe:            float = 0.0
    revenue_growth: float = 0.0
    net_profit_growth: float = 0.0
    dividend_yield: float = 0.0
    total_mv:       float = 0.0
    source:         str   = ""


# ── 基类 ──────────────────────────────────────────────────────────────────────

class BaseDataSource(ABC):
    """
    所有数据源的抽象基类。

    子类实现 `supports()` 判断是否支持该 symbol，
    再实现具体的 `quote()` / `history()` / `fundamentals()`。
    """

    name: str = "base"             # 数据源唯一标识
    markets: List[str] = []        # 支持的市场: "a_share", "us", "hk", "crypto"
    requires_key: bool = False     # 是否需要 API key

    def __init__(self, config: Dict[str, Any] = None):
        self.config = config or {}
        self._available: Optional[bool] = None

    def supports(self, symbol: str) -> bool:
        """判断该数据源是否能处理这个 symbol（子类可覆盖）"""
        market = _detect_market(symbol)
        return market in self.markets

    def is_configured(self) -> bool:
        """数据源是否已配置好（有 key 等）"""
        return True

    @abstractmethod
    def quote(self, symbol: str) -> Optional[QuoteResult]:
        """获取实时行情（同步）"""
        ...

    def history(
        self,
        symbol: str,
        days: int = 90,
        interval: str = "1d",
    ) -> Optional[HistoryResult]:
        """获取历史 OHLCV（同步，子类按需实现）"""
        return None

    def fundamentals(self, symbol: str) -> Optional[FundamentalsResult]:
        """获取基本面数据（子类按需实现）"""
        return None


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _detect_market(symbol: str) -> str:
    """简单推断 symbol 所属市场"""
    s = symbol.upper().replace(" ", "")
    # 加密货币
    if "/" in s or s in ("BTC", "ETH", "SOL", "DOGE", "BNB"):
        return "crypto"
    # A股：数字代码 or 带前缀
    if s.startswith(("SH", "SZ", "BJ")):
        return "a_share"
    try:
        n = int(s[:6])
        if len(s) == 6 or (len(s) > 6 and not s[6:].isalpha()):
            return "a_share"
    except ValueError:
        pass
    # 港股
    if s.endswith(".HK") or (s.isdigit() and len(s) == 5):
        return "hk"
    # 默认美股
    return "us"
