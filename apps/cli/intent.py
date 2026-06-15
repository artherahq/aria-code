"""Intent classification helpers extracted from aria_cli.py.

All functions are pure — no I/O, no globals, fully unit-testable.
"""
from __future__ import annotations

# ── Keyword lists ─────────────────────────────────────────────────────────────

CODING_KEYWORDS = (
    "write", "generate", "create", "script", "code", "plot", "backtest",
    "策略", "代码", "回测", "编写", "生成", "k线", "k-line", "kline",
    "analyze and save", "analysis script", "python", "dashboard",
    "写一个", "生成代码", "写代码", "编写代码",
)

ANALYSIS_KEYWORDS = (
    "analyze", "analysis", "分析", "研究", "评估", "研判",
    "技术面", "基本面", "走势", "趋势", "行情",
    "技术分析", "技术指标", "支撑", "阻力", "支撑位", "阻力位",
    "rsi", "macd", "bollinger", "布林", "均线", "kdj", "kdj指标",
    "stock analysis", "technical analysis", "fundamental",
    "valuation", "estimate", "outlook", "投资建议", "买入", "卖出",
    "看多", "看空", "多头", "空头", "金叉", "死叉",
)

ANALYSIS_NON_STOCK_TOPICS = (
    "房价", "楼市", "房产", "房地产", "租金", "二手房", "新房", "商铺", "折旧",
    "宏观", "宏观经济", "宏观政策", "宏观角度", "经济政策", "货币政策",
    "财政政策", "gdp", "通胀", "通货膨胀", "cpi", "ppi", "利率政策",
    "黄金", "原油", "大宗商品", "汇率", "外汇", "美元指数",
)

GENERAL_KNOWLEDGE_KEYWORDS = (
    "什么是", "what is", "what are", "how does", "explain", "define",
    "解释", "定义", "概念", "原理", "介绍", "步骤", "流程", "怎么",
    "如何理解", "是什么", "为什么", "区别", "difference between",
    "tell me about", "describe", "how to", "注册", "成立", "公司",
    "基本概念", "简介", "举例", "example", "例子",
    "足球", "篮球", "网球", "棒球", "橄榄球", "排球", "乒乓球", "羽毛球",
    "世界杯", "欧冠", "英超", "德甲", "西甲", "意甲", "法甲", "bundesliga",
    "比赛", "赛事", "比分", "进球", "射门", "门将", "球队", "球员", "教练",
    "联赛", "积分榜", "赛程", "晋级", "淘汰赛", "决赛", "半决赛",
    "football", "soccer", "match", "goal", "league", "champion",
    "nba", "nfl", "mlb", "f1", "赛车", "奥运", "olympic",
)

FINANCE_CONCEPT_TERMS = (
    "dcf", "pe", "pb", "ps", "ev", "ebitda", "ebit", "wacc", "capm",
    "beta", "alpha", "sharpe", "sortino", "var", "cvar", "drawdown",
    "black-scholes", "bs模型", "期权", "期货", "衍生品", "套利",
    "量化", "quant", "回测", "factor", "因子", "ic值", "ir值",
    "市盈率", "市净率", "净利润", "营业收入", "自由现金流", "贴现",
    "折现", "估值", "valuation", "ipo", "etf", "reits", "债券",
    "利率", "收益率", "久期", "凸性", "信用利差", "风险溢价",
    "动量", "均值回归", "布林带", "macd", "rsi", "kdj", "技术指标",
    "北向资金", "融资融券", "股指期货", "沪深300", "中证500",
)

SPORTS_KEYWORDS = (
    "足球", "世界杯", "欧冠", "英超", "德甲", "西甲", "意甲", "法甲",
    "篮球", "nba", "网球", "f1", "赛车", "奥运", "olympic",
    "比赛", "赛事", "比分", "进球", "球队", "球员", "联赛",
    "football", "soccer", "world cup", "champions league",
    "match", "score", "league", "premier league", "bundesliga",
)


# ── Classifier functions ──────────────────────────────────────────────────────

def is_coding_request(message: str) -> bool:
    """Return True if message looks like a coding/file-generation task."""
    low = message.lower()
    if any(k in low for k in CODING_KEYWORDS):
        return True
    if low.startswith("/code") or low.startswith("/gen-"):
        return True
    return False


def is_sports_query(message: str) -> bool:
    """Return True if the message is about sports/football."""
    low = message.lower()
    return any(k in low for k in SPORTS_KEYWORDS)


def is_analysis_request(message: str) -> bool:
    """Return True if message is a stock/crypto technical analysis request (not coding).

    Excludes real-estate, pure macro, and sports questions: those match keywords
    like '分析'/'走势' but should NOT use the stock technical-analysis template
    (which requires injected market data to be useful).
    """
    if is_coding_request(message):
        return False
    low = message.lower()
    if any(k in low for k in ANALYSIS_NON_STOCK_TOPICS):
        return False
    if is_sports_query(message):
        return False
    return any(k in low for k in ANALYSIS_KEYWORDS)


def is_general_knowledge(message: str) -> bool:
    """Return True for pure knowledge/explanation questions that don't need tools.

    Finance/quant terms are excluded so they keep the full FINANCE_CHAT_PROMPT
    even when phrased as "X是什么" explanatory questions.

    Pure macro/conceptual analysis questions are treated as general knowledge:
    routing them to the finance prompt with tools causes the model to fetch live
    prices and output the stock-analysis template instead of thoughtful commentary.
    """
    if is_coding_request(message) or is_analysis_request(message):
        return False
    low = message.lower().strip()
    if any(term in low for term in FINANCE_CONCEPT_TERMS):
        return False
    _macro_conceptual = (
        "宏观", "宏观经济", "宏观政策", "宏观角度", "宏观分析",
        "货币政策", "财政政策", "值得投资吗", "应该投资吗", "是否值得",
        "投资逻辑", "长期展望", "未来前景",
    )
    if (any(k in low for k in _macro_conceptual)
            and not any(c.isdigit() for c in low)):
        return True
    if len(low) < 30 and not any(c.isdigit() for c in low):
        return True
    return any(k in low for k in GENERAL_KNOWLEDGE_KEYWORDS)
