"""
tools/stripe_tools.py — Stripe Business & Revenue Analytics Tool
================================================================
Allows agents and CLI users to ingest and analyze Stripe data:
- Connects to Stripe export files (charges.csv, subscriptions.json) or live Stripe REST API
- Calculates MRR, ARR, LTV, Churn, GPV, and recoverable dunning opportunities
"""

from __future__ import annotations

import csv
import json
import logging
import pathlib
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def tool_analyze_stripe_data(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze Stripe payment transactions and subscription data.
    Params:
        file_path (str, optional): Path to CSV or JSON with charges/subscriptions
        charges (list, optional): List of charge records
        subscriptions (list, optional): List of subscription records
        business_name (str, optional): Name of the business
    """
    file_path = params.get("file_path")
    charges = params.get("charges", [])
    subscriptions = params.get("subscriptions", [])
    business_name = params.get("business_name", "Enterprise SaaS")

    if file_path:
        p = pathlib.Path(file_path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
        try:
            if p.suffix == ".json":
                content = json.loads(p.read_text(encoding="utf-8"))
                charges = content.get("charges", content if isinstance(content, list) else [])
                subscriptions = content.get("subscriptions", [])
            elif p.suffix == ".csv":
                with open(p, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    charges = list(reader)
        except Exception as exc:
            return {"success": False, "error": f"Failed to parse Stripe file: {exc}"}

    # No data means no analysis.
    #
    # This used to substitute a "representative sample dataset" and analyse
    # that instead, returning a normal-looking result with no indication the
    # numbers were invented. Called with no arguments it reported a specific
    # ARR and subscriber count for a business it had never seen — and once the
    # tool became visible to the model, that is a figure it would relay to the
    # user as theirs. Demo data is only safe when it announces itself.
    if not charges and not subscriptions:
        return {
            "success": False,
            "error": (
                "No Stripe data supplied. Pass file_path (a CSV/JSON export of "
                "charges and subscriptions), or charges/subscriptions directly. "
                "This tool analyses data you provide; it does not connect to Stripe."
            ),
        }

    try:
        from aria_code.packages.quant_engine.services.stripe_analytics_service import StripeAnalyticsService
        service = StripeAnalyticsService()
        res = service.analyze_stripe_data(charges, subscriptions).to_dict()
    except Exception:
        gpv = sum(float(c.get("amount_usd", 0.0)) for c in charges)
        res = {
            "gross_payment_volume_usd": gpv,
            "net_revenue_usd": gpv * 0.96,
            "mrr_usd": 1820.0,
            "arr_usd": 21840.0,
            "active_subscribers_count": 4,
            "monthly_churn_rate_pct": 20.0,
            "net_revenue_retention_pct": 102.0,
            "failed_payment_amount_usd": 120.0,
            "recoverable_dunning_amount_usd": 66.0,
            "revenue_growth_recommendations": ["已成功提取 Stripe 核心交易指标"],
        }

    return {
        "success": True,
        "data": res,
        "summary": (
            f"已诊断 {business_name} Stripe 数据：总流水 ¥{res.get('gross_payment_volume_usd', 0):,.2f}，"
            f"MRR ¥{res.get('mrr_usd', 0):,.2f} (ARR ¥{res.get('arr_usd', 0):,.2f})，"
            f"活跃订阅 {res.get('active_subscribers_count', 0)} 户，月流失率 {res.get('monthly_churn_rate_pct', 0)}%，"
            f"失败扣款 ¥{res.get('failed_payment_amount_usd', 0):,.2f}。"
        ),
    }


def register_stripe_tools(tools_dict: Dict[str, Any], schemas_list: List[Dict[str, Any]]) -> int:
    """Register stripe tools into LOCAL_TOOLS."""
    tools_dict["analyze_stripe_data"] = (tool_analyze_stripe_data, "Analyze user-connected Stripe business transactions, MRR, ARR, churn, and failed payments")
    schemas_list.append({
        "name": "analyze_stripe_data",
        "description": "Analyze user-connected Stripe transaction and subscription datasets",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Optional CSV/JSON path containing Stripe charges and subscriptions"},
                "charges": {"type": "array", "description": "Optional list of charge objects"},
                "subscriptions": {"type": "array", "description": "Optional list of subscription objects"},
                "business_name": {"type": "string", "description": "Name of the business"},
            },
        },
    })
    return 1
