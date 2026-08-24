"""
tests/test_logistics_and_finance_agents.py — Tests for Logistics & Corporate Finance Agents and Tools
"""

import asyncio
import json
import pytest
from agents.registry import get_registry
from agents.warehouse.logistics_cost import LogisticsCostOptimizerAgent
from agents.warehouse.fulfillment_leadtime import FulfillmentLeadTimeAgent
from agents.financial.corporate_finance import CorporateFinanceAgent
from agents.financial.cashflow_burn import CashflowBurnRateAgent
from tools.logistics_tools import tool_analyze_logistics_data
from tools.enterprise_finance_tools import tool_analyze_financial_statements


def test_enterprise_agents_registry_discovery():
    registry = get_registry()
    logistics_cost_cls = registry.get("warehouse_logistics_cost")
    fulfillment_cls = registry.get("warehouse_fulfillment_leadtime")
    corp_fin_cls = registry.get("corporate_finance")
    cashflow_burn_cls = registry.get("cashflow_burn")

    assert logistics_cost_cls is not None
    assert fulfillment_cls is not None
    assert corp_fin_cls is not None
    assert cashflow_burn_cls is not None


def test_logistics_cost_optimizer_agent():
    agent = LogisticsCostOptimizerAgent()
    sample_waybills = [
        {"waybill_no": "WB001", "carrier": "FedEx", "actual_weight_kg": 10.0, "billed_weight_kg": 10.0, "base_freight": 100.0, "fuel_surcharge": 12.0, "total_cost": 112.0, "transit_days": 2.0, "is_on_time": True},
        {"waybill_no": "WB002", "carrier": "FedEx", "actual_weight_kg": 10.0, "billed_weight_kg": 16.0, "base_freight": 100.0, "fuel_surcharge": 25.0, "total_cost": 185.0, "transit_days": 4.0, "is_on_time": False, "status": "DELAYED"},
        {"waybill_no": "WB003", "carrier": "SF Express", "actual_weight_kg": 20.0, "billed_weight_kg": 20.0, "base_freight": 140.0, "fuel_surcharge": 10.0, "total_cost": 150.0, "transit_days": 1.5, "is_on_time": True},
    ]

    async def _run():
        res = await agent.analyze("GLOBAL_SHIPPING", {"waybills": sample_waybills})
        assert res.success is True
        assert "运费总支出" in res.analysis
        assert "异常计费发现" in res.analysis
        assert res.data_used["total_waybills"] == 3
        assert len(res.data_used["billing_anomalies"]) >= 1

    asyncio.run(_run())


def test_fulfillment_leadtime_agent():
    agent = FulfillmentLeadTimeAgent()
    sample_stages = [
        {"stage_name": "Order-to-Pack", "avg_hours": 3.5, "sla_target_hours": 4.0, "compliance_rate": 95.0, "bottleneck": False},
        {"stage_name": "Dock-to-Stock", "avg_hours": 7.5, "sla_target_hours": 6.0, "compliance_rate": 80.0, "bottleneck": True, "details": "入库质检排队延误"},
    ]

    async def _run():
        res = await agent.analyze("WH_EAST_01", {"fulfillment_stages": sample_stages})
        assert res.success is True
        assert "SLA 达标率" in res.analysis
        assert "瓶颈" in res.analysis
        assert len(res.data_used["bottlenecks"]) == 1

    asyncio.run(_run())


def test_corporate_finance_agent():
    agent = CorporateFinanceAgent()
    payload = {
        "company_name": "TechGlobal Ltd",
        "income_statement": {
            "period": "2025-FY",
            "revenue": 20000000.0,
            "cost_of_goods_sold": 11000000.0,
            "operating_expenses": 4500000.0,
            "net_income": 3600000.0,
        },
        "balance_sheet": {
            "period": "2025-FY",
            "cash_and_equivalents": 6000000.0,
            "accounts_receivable": 2500000.0,
            "inventory": 1800000.0,
            "accounts_payable": 1400000.0,
            "total_assets": 16000000.0,
            "total_liabilities": 5000000.0,
            "total_equity": 11000000.0,
            "current_liabilities": 3000000.0,
            "current_assets": 10300000.0,
        },
        "cashflow": {
            "period": "2025-FY",
            "operating_cash_flow": 4200000.0,
            "capital_expenditures": 1000000.0,
        },
    }

    async def _run():
        res = await agent.analyze("TechGlobal Ltd", payload)
        assert res.success is True
        assert "杜邦 ROE" in res.analysis
        assert "营运资本周转" in res.analysis
        assert "Altman Z-Score" in res.analysis
        assert res.data_used["gross_margin_pct"] == 45.0
        assert res.data_used["dupont"]["roe"] > 0
        assert res.data_used["solvency_risk"] == "SAFE"

    asyncio.run(_run())


def test_cashflow_burn_rate_agent():
    agent = CashflowBurnRateAgent()
    payload = {
        "company_name": "ScaleUp AI",
        "cash": 4000000.0,
        "monthly_inflows": 500000.0,
        "monthly_outflows": 900000.0,
        "ar_aging": {
            "within_30_days": 800000.0,
            "days_31_60": 300000.0,
            "days_61_90": 250000.0,
            "over_90_days": 150000.0,
        },
    }

    async def _run():
        res = await agent.analyze("ScaleUp AI", payload)
        assert res.success is True
        assert "月度净消耗" in res.analysis
        assert "现金可持续跑道" in res.analysis
        assert "账龄结构" in res.analysis
        assert res.data_used["net_burn"] == 400000.0
        assert res.data_used["runway_months"] == 10.0

    asyncio.run(_run())


def test_logistics_tool_execution():
    tool_res = tool_analyze_logistics_data({})
    assert tool_res["success"] is True
    assert "data" in tool_res
    assert tool_res["data"]["total_waybills"] >= 1


def test_enterprise_finance_tool_execution():
    tool_res = tool_analyze_financial_statements({"company_name": "Enterprise Test"})
    assert tool_res["success"] is True
    assert "data" in tool_res
    assert "dupont" in tool_res["data"]
    assert "working_capital" in tool_res["data"]
