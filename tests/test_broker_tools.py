"""
tests/test_broker_tools.py — Tests for Broker Portfolio Tool
"""

from tools.broker_tools import tool_get_broker_portfolio, register_broker_tools


def test_broker_portfolio_tool_ibkr():
    res = tool_get_broker_portfolio({"broker_id": "ibkr"})
    assert res["success"] is True
    assert res["account"]["currency"] == "USD"
    assert res["account"]["net_liquidation_value"] > 0
    assert len(res["positions"]) >= 2
    assert "NVDA" in [p["symbol"] for p in res["positions"]]


def test_broker_portfolio_tool_domestic_huatai():
    res = tool_get_broker_portfolio({"broker_id": "huatai"})
    assert res["success"] is True
    assert res["account"]["currency"] == "CNY"
    assert res["account"]["net_liquidation_value"] > 0
    assert len(res["positions"]) >= 2
    assert "600519" in [p["symbol"] for p in res["positions"]]


def test_register_broker_tools():
    tools = {}
    schemas = []
    n = register_broker_tools(tools, schemas)
    assert n == 1
    assert "get_broker_portfolio" in tools
    assert schemas[0]["name"] == "get_broker_portfolio"
