"""
tools/enterprise_finance_tools.py — Corporate Financial Statement Analysis Tool
================================================================================
Provides CLI and agent tools to ingest and analyze company financial reports.
Now supports fetching real data from Yahoo Finance.
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

def compute_financial_metrics(income: dict, balance: dict, cashflow: dict) -> dict:
    """Perform real financial math."""
    revenue = income.get("revenue", 0) or 1
    net_income = income.get("net_income", 0)
    gross_profit = income.get("gross_profit", 0)
    total_assets = balance.get("total_assets", 0) or 1
    total_equity = balance.get("total_equity", 0) or 1
    current_assets = balance.get("current_assets", 0)
    current_liabilities = balance.get("current_liabilities", 0) or 1
    inventory = balance.get("inventory", 0)
    cogs = income.get("cost_of_goods_sold", 0) or 1
    
    # Dupont
    net_margin = net_income / revenue
    asset_turnover = revenue / total_assets
    equity_multiplier = total_assets / total_equity
    roe = net_margin * asset_turnover * equity_multiplier
    
    # Working capital
    current_ratio = current_assets / current_liabilities
    quick_ratio = (current_assets - inventory) / current_liabilities
    
    # Altman Z-Score approximation (simplified)
    working_capital = current_assets - current_liabilities
    retained_earnings = balance.get("retained_earnings", 0)
    ebit = income.get("operating_income", 0)
    
    z_score = 1.2 * (working_capital/total_assets) + 1.4 * (retained_earnings/total_assets) + 3.3 * (ebit/total_assets)
    
    return {
        "gross_margin_pct": round((gross_profit / revenue) * 100, 2),
        "net_margin_pct": round(net_margin * 100, 2),
        "dupont": {
            "roe": round(roe * 100, 2),
            "net_profit_margin": round(net_margin * 100, 2),
            "asset_turnover": round(asset_turnover, 2),
            "equity_multiplier": round(equity_multiplier, 2)
        },
        "working_capital": {
            "current_ratio": round(current_ratio, 2),
            "quick_ratio": round(quick_ratio, 2)
        },
        "altman_z_score": round(z_score, 2),
        "solvency_risk": "SAFE" if z_score > 2.99 else ("DISTRESS" if z_score < 1.8 else "GREY")
    }

def tool_analyze_financial_statements(params: Dict[str, Any]) -> Dict[str, Any]:
    company_name = params.get("company_name", "").upper()
    data = params.get("financials", {})
    
    income = data.get("income_statement", {})
    balance = data.get("balance_sheet", {})
    cashflow = data.get("cashflow", {})
    
    # Try fetching real data via yfinance if it looks like a ticker and we have no data
    if company_name and len(company_name) <= 5 and not income:
        try:
            import yfinance as yf
            ticker = yf.Ticker(company_name)
            inc_df = ticker.income_stmt
            bal_df = ticker.balance_sheet
            cf_df = ticker.cashflow
            
            if not inc_df.empty and not bal_df.empty:
                # Take most recent year
                inc_latest = inc_df.iloc[:, 0]
                bal_latest = bal_df.iloc[:, 0]
                
                income = {
                    "revenue": inc_latest.get("Total Revenue", 0),
                    "cost_of_goods_sold": inc_latest.get("Cost Of Revenue", 0),
                    "gross_profit": inc_latest.get("Gross Profit", 0),
                    "operating_income": inc_latest.get("Operating Income", 0),
                    "net_income": inc_latest.get("Net Income", 0)
                }
                balance = {
                    "total_assets": bal_latest.get("Total Assets", 0),
                    "total_equity": bal_latest.get("Stockholders Equity", 0),
                    "current_assets": bal_latest.get("Current Assets", 0),
                    "current_liabilities": bal_latest.get("Current Liabilities", 0),
                    "inventory": bal_latest.get("Inventory", 0),
                    "retained_earnings": bal_latest.get("Retained Earnings", 0)
                }
        except ImportError:
            pass
        except Exception as e:
            logger.warning(f"yfinance failed for {company_name}: {e}")
            
    # No statements, no diagnosis.
    #
    # A "sensible mock" used to fill in here — revenue 12,000,000, net income
    # 2,000,000 — and the result then carried the requested company's name.
    # Asked about a real company whose filings could not be fetched, the tool
    # returned a complete, confident diagnosis of a business that does not
    # exist, labelled with that company's ticker. There is no way for the
    # caller to tell that apart from a real one.
    if not income:
        return {
            "success": False,
            "error": (
                f"No financial statements available"
                f"{f' for {company_name}' if company_name else ''}. "
                "Pass financials={income_statement, balance_sheet, cashflow}, or "
                "give a ticker whose filings yfinance can fetch. "
                "This tool does not estimate missing figures."
            ),
        }

    metrics = compute_financial_metrics(income, balance, cashflow)
    metrics["company_name"] = company_name
    metrics["data_source"] = (
        "caller-supplied statements" if params.get("financials") else f"yfinance:{company_name}"
    )

    return {
        "success": True,
        "data": metrics,
        "summary": (
            f"{company_name} 财务诊断完成（来源：{metrics['data_source']}）: 毛利率 {metrics.get('gross_margin_pct')}%，"
            f"净利率 {metrics.get('net_margin_pct')}%，杜邦 ROE {metrics.get('dupont', {}).get('roe')}%，"
            f"偿债安全性 {metrics.get('solvency_risk')} (Altman Z: {metrics.get('altman_z_score')})。"
        ),
    }

def register_enterprise_finance_tools(tools_dict: Dict[str, Any], schemas_list: List[Dict[str, Any]]) -> int:
    tools_dict["analyze_financial_statements"] = (tool_analyze_financial_statements, "Analyze company financial statements (P&L, Balance Sheet, Cash Flow) and Dupont decomposition")
    schemas_list.append({
        "name": "analyze_financial_statements",
        "description": "Analyze corporate financial statements, working capital cycle, and solvency. If company_name is a ticker (e.g. AAPL), it fetches real Yahoo Finance data.",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Optional path to JSON financial statements"},
                "financials": {"type": "object", "description": "Optional dict containing income_statement, balance_sheet, cashflow"},
                "company_name": {"type": "string", "description": "Company name or ticker symbol (e.g. AAPL)"},
            },
        },
    })
    return 1

