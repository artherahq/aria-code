"""User-facing service usage catalog for Aria Code."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceUsageSpec:
    name: str
    purpose: str
    cli_entrypoints: tuple[str, ...]
    package_sources: tuple[str, ...]
    mcp_tools: tuple[str, ...]
    next_step: str


def list_service_usage_specs() -> tuple[ServiceUsageSpec, ...]:
    """Return how each project service is meant to be used from Aria."""

    return (
        ServiceUsageSpec(
            name="market_data",
            purpose="行情、历史K线、技术指标、数据质量标记",
            cli_entrypoints=("/quote", "/market", "/ta", "/chart"),
            package_sources=("packages/data", "packages/quant_engine"),
            mcp_tools=("calculate_factors", "northbound_flow"),
            next_step="/quote AAPL 或 /chart MC.PA 1y",
        ),
        ServiceUsageSpec(
            name="signals_prediction",
            purpose="量化信号、预测收益、市场状态和仓位建议",
            cli_entrypoints=("/signal", "/predict", "/team"),
            package_sources=("packages/quant_engine", "packages/ml"),
            mcp_tools=("generate_signal", "get_ai_signal", "get_predictions", "kelly_position"),
            next_step="/signal NVDA 或 /packages tools arthera",
        ),
        ServiceUsageSpec(
            name="backtest",
            purpose="策略回测、交易成本、胜率、夏普和最大回撤",
            cli_entrypoints=("/backtest", "/strategy"),
            package_sources=("packages/quant_engine/backtest",),
            mcp_tools=("run_backtest",),
            next_step="/backtest momentum NVDA --period 1y",
        ),
        ServiceUsageSpec(
            name="risk_portfolio",
            purpose="持仓风险、组合风险、压力测试和资金管理",
            cli_entrypoints=("/risk", "/positions", "/account"),
            package_sources=("brokers", "packages/quant_engine/risk", "packages/contracts"),
            mcp_tools=("detect_regime", "kelly_position"),
            next_step="/broker guide 然后 /positions",
        ),
        ServiceUsageSpec(
            name="reports_artifacts",
            purpose="研报、图表、看板、Pine 文件和本地 artifact 管理",
            cli_entrypoints=("/report", "/dashboard", "/chart", "/tv", "/artifacts"),
            package_sources=("packages/reporting", "artifacts"),
            mcp_tools=("aria.report.generate", "aria.artifacts.list"),
            next_step="/report MSFT --format html",
        ),
        ServiceUsageSpec(
            name="broker_execution",
            purpose="券商连接、账户只读查询、仿盘账户、订单预览、确认后执行和审计",
            cli_entrypoints=("/broker", "/paper", "/trade", "/account", "/positions", "/orders"),
            package_sources=("brokers", "brokers/trading.py", "brokers/paper_broker.py"),
            mcp_tools=("broker_query", "broker_order"),
            next_step="/paper start 100000 USD 或 /broker doctor",
        ),
        ServiceUsageSpec(
            name="tradingview_webhook",
            purpose="TradingView 图表打开、Pine 策略导出、Alert Webhook 接入",
            cli_entrypoints=("/tv", "aria_daemon.py"),
            package_sources=("apps/cli/tradingview_bridge.py", "aria_daemon.py"),
            mcp_tools=(),
            next_step="/tv NVDA --pine --txt",
        ),
        ServiceUsageSpec(
            name="mcp_bridge",
            purpose="把 Arthera QuantEngine 作为 MCP 工具暴露给 Aria/Claude/Cursor",
            cli_entrypoints=("/packages connect arthera", "/mcp reload", "/packages tools arthera"),
            package_sources=("packages/quant_engine/mcp_server.py",),
            mcp_tools=("calculate_factors", "run_backtest", "price_option", "execution_schedule"),
            next_step="/packages connect arthera --reload",
        ),
    )


def service_usage_map() -> dict[str, ServiceUsageSpec]:
    return {spec.name: spec for spec in list_service_usage_specs()}
