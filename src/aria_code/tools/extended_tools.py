"""Enterprise connector tools — SHAPES ONLY, not implementations.

Read this before using anything here.
=====================================
None of these functions contact the service they name. Every one returns a
hardcoded payload: ``tool_send_slack_notification`` reports
``"Successfully posted to Slack channel"`` with an invented timestamp and
permalink without opening a socket, and the QuickBooks, Shopify and Snowflake
tools return fabricated figures in the same way.

They define the intended request/response shape for six integrations that have
not been built. That is a legitimate thing to have; what is not legitimate is
letting a model call one and report the fabricated result to a user as fact —
"I posted the alert to #trading-desk" when nothing was sent, or a cash-runway
number invented out of nothing.

So ``register_extended_tools`` wraps each one in a guard that refuses to run
and says why. The shapes stay here as the specification for whoever implements
them; ``ARIA_ALLOW_STUB_TOOLS=1`` returns the canned payloads for tests and
demos, and names itself in the result so the caller cannot mistake it for real.

Implementing one means replacing the body with a real client call and dropping
it from ``_STUB_TOOLS``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def tool_send_slack_notification(params: Dict[str, Any]) -> Dict[str, Any]:
    """Send an automated report or approval card to a connected Slack channel."""
    channel = params.get("channel", "#trading-desk")
    text = params.get("text", "Aria Quantitative Alert: Portfolio Rebalanced.")
    blocks = params.get("blocks", [])

    return {
        "success": True,
        "channel": channel,
        "ts": "1724502800.102900",
        "permalink": f"https://arthera.slack.com/archives/{channel.replace('#', '')}/p1724502800102900",
        "message": f"Successfully posted to Slack channel {channel}",
    }


def tool_push_feishu_card(params: Dict[str, Any]) -> Dict[str, Any]:
    """Send an interactive approval card to Feishu / Lark."""
    title = params.get("title", "Aria 投资策略审批通知")
    receive_id = params.get("receive_id", "ou_arthera_admin_001")

    return {
        "success": True,
        "instance_code": "FEISHU_APPR_8829104",
        "status": "PENDING",
        "target": receive_id,
        "title": title,
        "message": f"已将「{title}」推送到飞书工作台，等待责任人审批",
    }


def tool_parse_tradingview_alert(params: Dict[str, Any]) -> Dict[str, Any]:
    """Ingest TradingView Webhook alert and map to quantitative execution order."""
    ticker = params.get("ticker", "NASDAQ:NVDA")
    action = params.get("action", "BUY").upper()
    price = float(params.get("price", 128.5))
    timeframe = params.get("timeframe", "1h")
    strategy = params.get("strategy", "Aria Momentum Alpha V3")

    return {
        "success": True,
        "ticker": ticker,
        "action": action,
        "trigger_price": price,
        "timeframe": timeframe,
        "strategy": strategy,
        "order_intent": {
            "symbol": ticker.split(":")[-1] if ":" in ticker else ticker,
            "side": action,
            "limit_price": price,
            "quantity": 100,
        },
        "message": f"TradingView 信号已解析: {strategy} 触发 {action} {ticker} @ {price}",
    }


def tool_get_quickbooks_pnl(params: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch corporate P&L statement, burn rate, and runway from QuickBooks / Xero."""
    company = params.get("company_name", "Arthera Enterprise Corp")
    period = params.get("period", "2026-Q2")

    total_income = 485000.0
    cogs = 142000.0
    gross_profit = total_income - cogs
    operating_expenses = 195000.0
    net_operating_income = gross_profit - operating_expenses
    monthly_burn = 45000.0
    cash_balance = 1250000.0
    runway_months = round(cash_balance / monthly_burn, 1)

    return {
        "success": True,
        "company_name": company,
        "period": period,
        "currency": "USD",
        "metrics": {
            "total_revenue": total_income,
            "cogs": cogs,
            "gross_profit": gross_profit,
            "gross_margin_pct": round((gross_profit / total_income) * 100, 2),
            "operating_expenses": operating_expenses,
            "net_income": net_operating_income,
            "net_margin_pct": round((net_operating_income / total_income) * 100, 2),
            "cash_burn_rate_monthly": monthly_burn,
            "total_cash_reserve": cash_balance,
            "runway_months": runway_months,
            "overdue_ar": 18500.0,
        },
        "health_rating": "HEALTHY",
    }


def tool_get_shopify_store_analytics(params: Dict[str, Any]) -> Dict[str, Any]:
    """Fetch Shopify / Amazon Seller GMV, order volume, and ROAS."""
    store = params.get("store_domain", "shop.arthera.com")

    total_gmv = 248600.0
    total_orders = 3120
    aov = round(total_gmv / total_orders, 2)
    ad_spend = 48000.0
    roas = round(total_gmv / ad_spend, 2)

    return {
        "success": True,
        "store_domain": store,
        "period_days": 30,
        "currency": "USD",
        "metrics": {
            "total_gmv": total_gmv,
            "total_orders": total_orders,
            "average_order_value": aov,
            "refund_rate_pct": 2.45,
            "ad_spend": ad_spend,
            "blended_roas": roas,
            "out_of_stock_skus": 3,
        },
    }


def tool_query_snowflake_data(params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute read-only SQL query against Snowflake / BigQuery Lakehouse."""
    sql = params.get("sql", "SELECT date, ticker, factor_score FROM analytics.equity_factors LIMIT 5")

    return {
        "success": True,
        "query_id": "01b23456-0001-abcd-0001-1234567890ab",
        "execution_time_ms": 142,
        "row_count": 5,
        "columns": ["date", "ticker", "factor_score", "sector", "z_score"],
        "rows": [
            ["2026-08-20", "NVDA", 2.84, "Semiconductors", 2.15],
            ["2026-08-20", "AAPL", 1.95, "Consumer Tech", 1.42],
            ["2026-08-20", "MSFT", 2.10, "Software Cloud", 1.68],
            ["2026-08-20", "AMZN", 1.82, "E-Commerce", 1.30],
            ["2026-08-20", "GOOGL", 1.65, "Internet Media", 1.15],
        ],
    }


# name → the service it would need, for the message the guard returns.
_STUB_TOOLS = {
    "send_slack_notification": "Slack (bot token + channel)",
    "push_feishu_card": "Feishu/Lark (app credentials + webhook)",
    "parse_tradingview_alert": "TradingView webhook receiver",
    "get_quickbooks_pnl": "QuickBooks Online API (OAuth)",
    "get_shopify_store_analytics": "Shopify Admin API (store + token)",
    "query_snowflake_data": "Snowflake (account, warehouse, credentials)",
}


def stub_guard(name: str, service: str, handler):
    """Wrap a shape-only tool so it reports its status instead of inventing one.

    Returning fabricated success is the one behaviour that must not survive:
    the model relays it as fact, and the user is told a message was sent or a
    figure was read when neither happened.
    """
    import os

    def _guarded(params: Dict[str, Any]) -> Dict[str, Any]:
        if os.getenv("ARIA_ALLOW_STUB_TOOLS", "").strip() in ("1", "true", "yes"):
            result = dict(handler(params) or {})
            result["stub"] = True
            result["warning"] = f"{name} returned canned data; {service} is not connected."
            return result
        return {
            "success": False,
            "error": (
                f"{name} is not implemented — it is a shape-only stub. "
                f"Connecting it requires {service}. "
                f"Do not report its result as if the action happened."
            ),
            "stub": True,
        }

    _guarded.__name__ = getattr(handler, "__name__", name)
    _guarded.__doc__ = getattr(handler, "__doc__", "")
    return _guarded


def register_extended_tools(registry_or_dict: Any) -> None:
    """Register the extended connector stubs, guarded.

    Registered as ``(handler, description)`` pairs: bare functions were being
    stored here, and ToolExecutor indexes ``local_tools[name][0]``, so calling
    any of these raised ``TypeError: 'function' object is not subscriptable``.
    They were unreachable in practice only because none of them had a schema.
    """
    handlers = {
        "send_slack_notification": tool_send_slack_notification,
        "push_feishu_card": tool_push_feishu_card,
        "parse_tradingview_alert": tool_parse_tradingview_alert,
        "get_quickbooks_pnl": tool_get_quickbooks_pnl,
        "get_shopify_store_analytics": tool_get_shopify_store_analytics,
        "query_snowflake_data": tool_query_snowflake_data,
    }
    tools = {
        name: (
            stub_guard(name, _STUB_TOOLS[name], handler),
            f"[not implemented] {name} — needs {_STUB_TOOLS[name]}",
        )
        for name, handler in handlers.items()
    }
    if hasattr(registry_or_dict, "register"):
        for name, entry in tools.items():
            registry_or_dict.register(name, entry[0])
    elif isinstance(registry_or_dict, dict):
        registry_or_dict.update(tools)
