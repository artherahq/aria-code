"""One explicit capability contract for every supported financial market.

This avoids asking an LLM to infer whether an A-share prediction engine, a
crypto funding-rate feed, or a foreign-exchange quote is applicable.  Routing
is deterministic and descriptions only advertise data the CLI can actually
request through its existing tools.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from apps.cli.market_universe import resolve_market_mentions


@dataclass(frozen=True)
class FinanceMarketService:
    key: str
    label: str
    aliases: tuple[str, ...]
    services: tuple[str, ...]
    analysis: tuple[str, ...]
    prediction: str = ""


MARKET_SERVICES: tuple[FinanceMarketService, ...] = (
    FinanceMarketService(
        "CN", "A 股", ("a股", "沪深", "上证", "深证", "创业板", "科创板"),
        ("实时行情", "历史K线", "技术指标", "基本面", "公告/新闻", "回测", "组合风控"),
        ("行情与成交量", "基本面与监管公告", "板块与资金流", "T+1/涨跌停约束回测"),
        "次交易日预测（覆盖率、数据日期、回测质量必须同时披露）",
    ),
    FinanceMarketService(
        "HK", "港股", ("港股", "恒生", "h股"),
        ("实时行情", "历史K线", "技术指标", "基本面", "新闻", "组合风控"),
        ("港币计价与交易时段", "中概/内地政策传导", "公司基本面与行业比较"),
    ),
    FinanceMarketService(
        "US", "美股", ("美股", "纳斯达克", "纽交所", "标普", "道指"),
        ("实时行情", "历史K线", "技术指标", "财报", "新闻", "回测", "组合风控"),
        ("财报和指引", "宏观利率与行业估值", "盘前/盘后与公司事件"),
    ),
    FinanceMarketService(
        "GLOBAL", "全球股票与指数", ("欧股", "日股", "全球市场", "指数"),
        ("实时行情", "历史K线", "技术指标", "新闻", "组合风控"),
        ("本币与汇率", "本地交易时段", "指数/行业相对表现"),
    ),
    FinanceMarketService(
        "CRYPTO", "加密资产", ("加密", "比特币", "以太坊", "btc", "eth", "币安", "okx"),
        ("现货行情", "历史K线", "技术指标", "交易所账户只读查询", "资金费率/衍生品（数据源可用时）"),
        ("24/7市场与流动性", "交易所及对手方风险", "杠杆/资金费率与波动"),
    ),
    FinanceMarketService(
        "FX", "外汇", ("外汇", "汇率", "美元兑", "eur/", "usd/", "jpy"),
        ("实时汇率", "历史K线", "技术指标", "宏观/新闻"),
        ("利差与央行政策", "汇率方向和报价惯例", "跨币种风险"),
    ),
    FinanceMarketService(
        "FUTURES", "商品与期货", ("期货", "商品", "黄金", "白银", "原油", "铜", "天然气"),
        ("连续合约行情", "历史K线", "技术指标", "宏观/新闻"),
        ("合约月份与展期", "库存/供需/地缘事件", "保证金和杠杆风险"),
    ),
)

_BY_KEY = {service.key: service for service in MARKET_SERVICES}


def classify_finance_market(text: str) -> FinanceMarketService | None:
    """Classify a request without network calls; named symbols win over words."""
    query = str(text or "")
    mentions = resolve_market_mentions(query, limit=1, load_universe=lambda: [])
    if mentions:
        key = mentions[0][1].market
        if key == "INDEX":
            symbol = mentions[0][1].symbol.upper()
            if symbol.endswith((".SS", ".SZ")):
                key = "CN"
            elif symbol in {"^HSI", "^HSCE"}:
                key = "HK"
            else:
                key = "GLOBAL"
        return _BY_KEY.get(key)
    low = query.lower()
    for service in MARKET_SERVICES:
        if any(alias in low for alias in service.aliases):
            return service
    if re.search(r"\b\d{6}(?:\.(?:sh|sz|ss))?\b", low):
        return _BY_KEY["CN"]
    if re.search(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,4})?\b", query):
        return _BY_KEY["US"]
    return None


def market_service_summary(text: str = "") -> dict[str, object]:
    """Return a display-ready and machine-readable market service contract."""
    selected = classify_finance_market(text)
    services = (selected,) if selected else MARKET_SERVICES
    return {
        "selected": selected.key if selected else None,
        "markets": [
            {
                "key": item.key,
                "label": item.label,
                "services": item.services,
                "analysis": item.analysis,
                "prediction": item.prediction or None,
            }
            for item in services
        ],
    }
