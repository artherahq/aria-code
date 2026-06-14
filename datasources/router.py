"""
datasources/router.py — 数据源路由器
=====================================
读取 ~/.aria/datasources.yaml 配置，按优先级依次尝试每个数据源，
首个成功的结果直接返回；所有失败则返回 None。

配置示例 (~/.aria/datasources.yaml):
    a_shares:
      - akshare
      - tushare
    us_stocks:
      - yfinance
      - finnhub
    crypto:
      - ccxt/binance
      - yfinance
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Type

from .base import (
    BaseDataSource, QuoteResult, HistoryResult, FundamentalsResult, _detect_market
)
from .sources import (
    AkshareSource, YFinanceSource, TushareSource,
    FREDSource, EDGARSource, AlphaVantageSource, WorldBankSource,
)

logger = logging.getLogger(__name__)

# ── 数据源目录 ────────────────────────────────────────────────────────────────
_SOURCE_REGISTRY: Dict[str, Type[BaseDataSource]] = {
    "akshare":       AkshareSource,
    "yfinance":      YFinanceSource,
    "tushare":       TushareSource,
    "fred":          FREDSource,
    "edgar":         EDGARSource,
    "alpha_vantage": AlphaVantageSource,
    "world_bank":    WorldBankSource,
}


def register_datasource(name: str, cls: Type[BaseDataSource]) -> None:
    """注册自定义数据源（供插件/用户扩展）"""
    _SOURCE_REGISTRY[name.lower()] = cls
    logger.info(f"✓ 注册自定义数据源: {name}")


# ── 默认优先级链 ──────────────────────────────────────────────────────────────
_DEFAULT_CHAINS: Dict[str, List[str]] = {
    "a_share": ["tushare", "akshare"],
    "us":      ["yfinance", "alpha_vantage", "edgar"],
    "hk":      ["yfinance"],
    "crypto":  ["yfinance"],
    "macro":   ["fred", "world_bank"],
}

# ── 配置文件路径 ──────────────────────────────────────────────────────────────
_CONFIG_PATHS = [
    Path.home() / ".aria" / "datasources.yaml",
    Path.home() / ".aria" / "datasources.json",
    Path(".aria.json"),
]


def _load_user_chains() -> Dict[str, List[str]]:
    for p in _CONFIG_PATHS:
        if not p.exists():
            continue
        try:
            if p.suffix in (".yaml", ".yml"):
                import yaml
                with open(p, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
            else:
                import json
                with open(p, encoding="utf-8") as f:
                    data = json.load(f)
            ds = data.get("datasources", data)
            if isinstance(ds, dict):
                chains = {}
                key_map = {
                    "a_shares": "a_share", "a_share": "a_share",
                    "us_stocks": "us", "us": "us",
                    "hk_stocks": "hk", "hk": "hk",
                    "crypto": "crypto",
                }
                for k, v in ds.items():
                    market = key_map.get(k.lower(), k.lower())
                    if isinstance(v, list):
                        chains[market] = v
                return chains
        except Exception as e:
            logger.debug(f"加载 datasources 配置 {p} 失败: {e}")
    return {}


class DataRouter:
    """
    统一数据路由器。线程安全（内部缓存用锁保护）。

    用法:
        router = DataRouter()
        q = router.quote("600519")
        h = router.history("AAPL", days=90)
        f = router.fundamentals("000858")
    """

    def __init__(self):
        self._user_chains = _load_user_chains()
        self._source_cache: Dict[str, BaseDataSource] = {}
        self._lock = threading.Lock()

    def _get_chain(self, market: str) -> List[str]:
        user = self._user_chains.get(market) or self._user_chains.get(
            {"a_share": "a_shares"}.get(market, market)
        )
        if user:
            return user
        return _DEFAULT_CHAINS.get(market, ["yfinance"])

    def _get_source(self, name: str) -> Optional[BaseDataSource]:
        with self._lock:
            if name not in self._source_cache:
                cls = _SOURCE_REGISTRY.get(name.lower())
                if not cls:
                    logger.debug(f"未知数据源: {name}")
                    return None
                src = cls()
                if not src.is_configured():
                    logger.debug(f"数据源 {name} 未配置（缺少 API key）")
                    return None
                self._source_cache[name] = src
            return self._source_cache[name]

    def quote(self, symbol: str) -> Optional[QuoteResult]:
        market = _detect_market(symbol)
        for src_name in self._get_chain(market):
            src = self._get_source(src_name)
            if not src or not src.supports(symbol):
                continue
            try:
                result = src.quote(symbol)
                if result:
                    logger.debug(f"quote({symbol}) ← {src_name}")
                    return result
            except Exception as e:
                logger.debug(f"[{src_name}] quote {symbol} 异常: {e}")
        logger.warning(f"所有数据源均无法获取 {symbol} 行情")
        return None

    def history(
        self, symbol: str, days: int = 90, interval: str = "1d"
    ) -> Optional[HistoryResult]:
        market = _detect_market(symbol)
        for src_name in self._get_chain(market):
            src = self._get_source(src_name)
            if not src or not src.supports(symbol):
                continue
            try:
                result = src.history(symbol, days=days, interval=interval)
                if result is not None:
                    logger.debug(f"history({symbol}) ← {src_name}")
                    return result
            except Exception as e:
                logger.debug(f"[{src_name}] history {symbol} 异常: {e}")
        return None

    def fundamentals(self, symbol: str) -> Optional[FundamentalsResult]:
        market = _detect_market(symbol)
        for src_name in self._get_chain(market):
            src = self._get_source(src_name)
            if not src or not src.supports(symbol):
                continue
            try:
                result = src.fundamentals(symbol)
                if result:
                    logger.debug(f"fundamentals({symbol}) ← {src_name}")
                    return result
            except Exception as e:
                logger.debug(f"[{src_name}] fundamentals {symbol} 异常: {e}")
        return None

    def list_sources(self) -> List[Dict[str, Any]]:
        """列出所有数据源及其状态（用于 /config 展示）"""
        result = []
        for name, cls in _SOURCE_REGISTRY.items():
            src = cls()
            result.append({
                "name":      name,
                "markets":   cls.markets,
                "needs_key": cls.requires_key,
                "configured": src.is_configured(),
            })
        return result


# ── 单例 ──────────────────────────────────────────────────────────────────────
_router: Optional[DataRouter] = None

def get_router() -> DataRouter:
    global _router
    if _router is None:
        _router = DataRouter()
    return _router
