"""
datasources/ — Aria Code 统一市场数据层
========================================
用户在 ~/.aria/datasources.yaml 配置数据源优先级，
路由器按序尝试，首个成功的结果直接返回。

支持市场:
  A股   → akshare / tushare / eastmoney
  美股   → yfinance / polygon / finnhub / alphavantage
  加密   → ccxt (binance/okx) / yfinance
  基本面 → tushare / yfinance

快速使用:
    from datasources.router import DataRouter
    router = DataRouter()
    print(router.quote("AAPL"))
    print(router.quote("000001"))   # A股
    print(router.quote("BTC/USDT")) # 加密
"""

from .router import DataRouter, get_router

__all__ = ["DataRouter", "get_router"]
