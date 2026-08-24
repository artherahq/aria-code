"""
tests/test_stripe_agent.py — Tests for StripeRevenueAgent and tool_analyze_stripe_data
"""

import asyncio
import pytest
from aria_code.agents.registry import get_registry
from aria_code.agents.financial.stripe_revenue import StripeRevenueAgent
from aria_code.tools.stripe_tools import tool_analyze_stripe_data


def test_stripe_agent_registry_discovery():
    registry = get_registry()
    agent_cls = registry.get("stripe_revenue")
    assert agent_cls is not None


def test_stripe_revenue_agent_analysis():
    agent = StripeRevenueAgent()
    payload = {
        "business_name": "CloudSaaS Inc",
        "charges": [
            {"amount_usd": 150.0, "status": "succeeded", "fee_usd": 4.65, "refunded_amount_usd": 0.0},
            {"amount_usd": 600.0, "status": "succeeded", "fee_usd": 17.70, "refunded_amount_usd": 0.0},
            {"amount_usd": 150.0, "status": "failed", "fee_usd": 0.0, "refunded_amount_usd": 0.0},
        ],
        "subscriptions": [
            {"subscription_id": "sub_1", "customer_id": "cus_1", "plan_name": "Pro", "mrr_usd": 150.0, "status": "active"},
            {"subscription_id": "sub_2", "customer_id": "cus_2", "plan_name": "Enterprise", "mrr_usd": 600.0, "status": "active"},
        ],
    }

    async def _run():
        res = await agent.analyze("CloudSaaS Inc", payload)
        assert res.success is True
        assert "Stripe 商业营收" in res.analysis
        assert "总交易流水" in res.analysis
        assert "MRR" in res.analysis
        assert res.data_used["gross_payment_volume_usd"] == 750.0
        assert res.data_used["mrr_usd"] == 750.0
        assert res.data_used["arr_usd"] == 9000.0

    asyncio.run(_run())


def test_stripe_tool_execution():
    tool_res = tool_analyze_stripe_data({"business_name": "Test Co"})
    assert tool_res["success"] is True
    assert "data" in tool_res
    assert tool_res["data"]["gross_payment_volume_usd"] > 0
    assert tool_res["data"]["mrr_usd"] > 0
