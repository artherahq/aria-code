"""
tests/test_extended_tools.py — Unit tests for Slack, Feishu, TradingView, QuickBooks, Shopify, Snowflake tools
"""

import pytest
from aria_code.tools.extended_tools import (
    tool_send_slack_notification,
    tool_push_feishu_card,
    tool_parse_tradingview_alert,
    tool_get_quickbooks_pnl,
    tool_get_shopify_store_analytics,
    tool_query_snowflake_data,
    register_extended_tools,
)


def test_tool_send_slack_notification():
    res = tool_send_slack_notification({"channel": "#trading-desk", "text": "Trade Alert: Buy AAPL"})
    assert res["success"] is True
    assert res["channel"] == "#trading-desk"
    assert "ts" in res


def test_tool_push_feishu_card():
    res = tool_push_feishu_card({"title": "量化调仓审批", "receive_id": "ou_12345"})
    assert res["success"] is True
    assert res["status"] == "PENDING"
    assert "instance_code" in res


def test_tool_parse_tradingview_alert():
    res = tool_parse_tradingview_alert({
        "ticker": "NASDAQ:NVDA",
        "action": "BUY",
        "price": 130.5,
        "strategy": "Trend Breakout V1",
    })
    assert res["success"] is True
    assert res["ticker"] == "NASDAQ:NVDA"
    assert res["action"] == "BUY"
    assert res["order_intent"]["symbol"] == "NVDA"
    assert res["order_intent"]["limit_price"] == 130.5


def test_tool_get_quickbooks_pnl():
    res = tool_get_quickbooks_pnl({"company_name": "Arthera Corp", "period": "2026-Q2"})
    assert res["success"] is True
    metrics = res["metrics"]
    assert metrics["total_revenue"] == 485000.0
    assert metrics["gross_margin_pct"] > 0
    assert metrics["runway_months"] > 0
    assert res["health_rating"] == "HEALTHY"


def test_tool_get_shopify_store_analytics():
    res = tool_get_shopify_store_analytics({"store_domain": "store.arthera.com"})
    assert res["success"] is True
    assert res["metrics"]["total_gmv"] > 0
    assert res["metrics"]["blended_roas"] > 0


def test_tool_query_snowflake_data():
    res = tool_query_snowflake_data({"sql": "SELECT * FROM analytics LIMIT 5"})
    assert res["success"] is True
    assert res["row_count"] == 5
    assert len(res["columns"]) >= 3
    assert len(res["rows"]) == 5


def test_register_extended_tools():
    dummy = {}
    register_extended_tools(dummy)
    assert "send_slack_notification" in dummy
    assert "push_feishu_card" in dummy
    assert "parse_tradingview_alert" in dummy
    assert "get_quickbooks_pnl" in dummy
    assert "get_shopify_store_analytics" in dummy
    assert "query_snowflake_data" in dummy
