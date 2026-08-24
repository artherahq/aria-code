"""Deterministic strategy-advice responses.

This keeps advisory questions from being treated as code-generation tasks.
When the user asks "how should I write a strategy" or "from which angles",
the CLI should answer with a framework and explicit next commands, not create
files or run backtests without a generation/execution intent.
"""

from __future__ import annotations


_STRATEGY_TERMS = (
    "量化策略", "交易策略", "美股策略", "回测策略", "策略框架",
    "quant strategy", "trading strategy", "backtest strategy",
)

_ADVICE_TERMS = (
    "几个角度", "哪些角度", "从几个方面", "怎么写", "如何写",
    "思路", "框架", "结构", "要考虑", "你觉得", "建议",
    "how", "what should", "framework", "approach", "angles",
)

_EXECUTION_TERMS = (
    "开始写", "帮我写", "生成", "实现", "创建", "保存", "运行",
    "回测一下", "直接做", "落地", "完善代码",
    "write", "generate", "implement", "create", "save", "run",
)


def is_strategy_advice_request(message: str) -> bool:
    text = (message or "").strip()
    if not text or text.startswith("/"):
        return False
    low = text.lower()
    has_strategy = any(term in text or term in low for term in _STRATEGY_TERMS)
    has_advice = any(term in text or term in low for term in _ADVICE_TERMS)
    has_execution = any(term in text or term in low for term in _EXECUTION_TERMS)
    return bool(has_strategy and has_advice and not has_execution)


def handle_strategy_advice(message: str) -> dict:
    if not is_strategy_advice_request(message):
        return {"success": False, "error": "not_strategy_advice"}

    response = """## 美股量化策略写作框架

建议从 8 个层级写，先定义假设，再验证和风控，最后才进入交易执行。
当前是咨询型问题，不需要先写文件，也不会自动运行回测。

| 层级 | 要解决的问题 | 产物 |
|---|---|---|
| 1. 市场假设 | 为什么这个信号可能赚钱 | 策略假设、适用市场、失效条件 |
| 2. 标的池 | 交易哪些股票/ETF，如何剔除不可交易标的 | universe 规则、流动性/价格过滤 |
| 3. 数据层 | OHLCV、财报、因子、宏观、新闻是否可得且可信 | 数据源、清洗、复权、缺失处理 |
| 4. 信号层 | 入场/出场依据是什么 | 因子、阈值、信号延迟、防未来函数 |
| 5. 组合层 | 单票仓位、行业暴露、相关性怎么控制 | 权重、再平衡、最大持仓、现金规则 |
| 6. 交易成本 | 手续费、滑点、冲击成本是否吞噬收益 | 成本模型、成交假设 |
| 7. 回测评估 | 策略是否稳健，而不是只在样本内好看 | CAGR、Sharpe、最大回撤、胜率、换手、分阶段表现 |
| 8. 风控部署 | 实盘如何限制损失并监控失效 | 止损、熔断、告警、paper/live 分层 |

**推荐落地顺序**

1. 先写一页策略说明：假设、标的池、调仓频率、风险边界。
2. 再写最小回测脚本：只包含数据、信号、仓位、绩效。
3. 加 walk-forward、不同市场阶段、参数敏感性测试。
4. 通过后接 paper trading，不直接上实盘。
5. 实盘前加入订单预览、资金上限、熔断、审计日志。

**下一步命令**

- `/scaffold my_us_strategy --template strategy`：生成策略项目骨架。
- `/backtest momentum SPY --period 1y`：先跑一个内置动量回测。
- `/auto-strategy momentum SPY --target sharpe=1.2`：进入自动优化流程。

*这只是策略设计框架，不构成投资建议。需要我生成代码时，请明确说“生成/实现/写文件”。*"""

    return {
        "success": True,
        "response": response,
        "tools_used": ["strategy_advice"],
        "analysis_complete": True,
    }
