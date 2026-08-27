"""
tools/logistics_tools.py — Enterprise Logistics & Supply Chain Analysis Tool
=============================================================================
Provides CLI and agent tools to ingest and analyze logistics data:
- Ingests waybill CSV, Excel, or JSON
- Calculates freight cost breakdown, carrier performance matrix, and billing discrepancies
- Generates structured summary and exportable reports
"""

from __future__ import annotations

import csv
import json
import logging
import pathlib
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def tool_analyze_logistics_data(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze shipping waybills from a file path or raw records.
    Params:
        file_path (str, optional): Path to CSV/JSON/Excel with waybill records
        waybills (list, optional): Raw waybill records
    """
    # Where the data came from, carried through to the result. A caller
    # cannot judge an anomaly report without knowing whether it was computed
    # from the file they passed or from the local ERP snapshot.
    origin = "none"
    file_path = params.get("file_path")
    waybills = params.get("waybills", [])
    if waybills:
        origin = "caller-supplied records"

    if file_path:
        p = pathlib.Path(file_path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "error": f"File not found: {file_path}"}
        try:
            if p.suffix == ".json":
                content = json.loads(p.read_text(encoding="utf-8"))
                waybills = content if isinstance(content, list) else content.get("waybills", [])
            elif p.suffix == ".csv":
                with open(p, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    waybills = list(reader)
            origin = f"file:{p.name}"
        except Exception as exc:
            return {"success": False, "error": f"Failed to parse file {file_path}: {exc}"}


    if not waybills and not file_path:
        import os
        import sqlite3
        db_path = os.path.expanduser("~/.aria/erp_warehouse.db")
        if os.path.exists(db_path):
            try:
                origin = "local ERP snapshot (~/.aria/erp_warehouse.db)"
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                # Fetch realistic anomaly data (e.g., where billed_weight is > 1.2x actual_weight)
                cursor.execute("SELECT * FROM logistics_waybills WHERE billed_weight_kg > actual_weight_kg * 1.2 LIMIT 10")
                anomalies = [dict(row) for row in cursor.fetchall()]
                
                # Fetch carrier stats
                cursor.execute("SELECT carrier, COUNT(*) as count, AVG(transit_days) as avg_days, SUM(total_cost) as total_spend FROM logistics_waybills GROUP BY carrier")
                carrier_stats = [dict(row) for row in cursor.fetchall()]
                
                # Fetch totals
                cursor.execute("SELECT COUNT(*) as total_waybills, SUM(total_cost) as total_spend FROM logistics_waybills")
                totals = dict(cursor.fetchone())
                
                return {
                    "success": True,
                    "data": {
                        "total_waybills": totals["total_waybills"],
                        "total_freight_spend": totals["total_spend"],
                        "carrier_metrics": carrier_stats,
                        "billing_anomalies": anomalies,
                        "data_source": origin,
                        # The anomaly query is LIMIT 10, so this is a sample of
                        # the exceptions rather than all of them. Saying so is
                        # the difference between "10 anomalies exist" and "here
                        # are 10 of them".
                        "billing_anomalies_truncated": len(anomalies) >= 10,
                    },
                    "summary": (
                        f"数据来源：本地 ERP 快照 ({db_path})，共 {totals['total_waybills']} 条单据，"
                        f"总运费支出 {totals['total_spend']:,.2f} 元。"
                        f"抽样列出计费异常 {len(anomalies)} 笔"
                        f"{'（查询上限 10 笔，实际可能更多）' if len(anomalies) >= 10 else ''}。"
                    )
                }
            except Exception as e:
                logger.error(f"DB query failed: {e}")
            finally:
                if 'conn' in locals():
                    conn.close()

    # No data means no analysis.
    #
    # This used to substitute a "representative sample dataset" and analyse
    # that instead, returning a normal-looking result with no indication the
    # numbers were invented. Called with no arguments it reported a specific
    # freight spend and carrier metrics for a business it had never seen — and once the
    # tool became visible to the model, that is a figure it would relay to the
    # user as theirs. Demo data is only safe when it announces itself.
    if not waybills:
        return {
            "success": False,
            "error": (
                "No waybill data supplied. Pass file_path (a CSV/JSON of waybills) "
                "or waybills directly. This tool analyses data you provide; it does "
                "not connect to a carrier or a TMS."
            ),
        }

    try:
        from aria_code.packages.quant_engine.services.logistics_analytics_service import LogisticsAnalyticsService
        service = LogisticsAnalyticsService()
        audit = service.analyze_shipping_data(waybills)
        res = audit.to_dict()
    except Exception:
        # Fallback local calculation
        tot = sum(float(w.get("total_cost", 0.0)) for w in waybills)
        # The on-time rate used to be hardcoded to 75.0 and returned beside a
        # genuinely computed freight total, so a fabricated figure travelled
        # under the same roof as a real one. Compute it, and report it as
        # unknown when the records do not carry the field.
        timed = [w for w in waybills if w.get("is_on_time") is not None]
        on_time_rate = (
            round(100.0 * sum(1 for w in timed if w.get("is_on_time")) / len(timed), 1)
            if timed else None
        )
        res = {
            "total_waybills": len(waybills),
            "total_freight_spend": tot,
            "overall_on_time_rate": on_time_rate,
            "carrier_metrics": [],
            "billing_anomalies": [],
            "cost_saving_recommendations": [],
            "note": "简化计算：分析服务不可用，仅统计总量与准时率。",
        }

    res.setdefault("data_source", origin)
    _rate = res.get("overall_on_time_rate")
    _rate_text = f"准时交付率 {_rate}%，" if _rate is not None else "准时交付率不可得，"
    return {
        "success": True,
        "data": res,
        "summary": (
            f"已审计 {res.get('total_waybills', 0)} 单运单（来源：{origin}），"
            f"运费总计 ¥{res.get('total_freight_spend', 0):,.2f}，"
            f"{_rate_text}"
            f"发现 {len(res.get('billing_anomalies', []))} 笔计费异常。"
        ),
    }


def register_logistics_tools(tools_dict: Dict[str, Any], schemas_list: List[Dict[str, Any]]) -> int:
    """Register logistics tools into LOCAL_TOOLS."""
    tools_dict["analyze_logistics_data"] = (tool_analyze_logistics_data, "Analyze freight shipping logs, carrier performance, and billing anomalies")
    schemas_list.append({
        "name": "analyze_logistics_data",
        "description": "Analyze enterprise freight waybills, carrier cost comparison, and billing anomalies",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Optional CSV or JSON file path with waybills"},
                "waybills": {"type": "array", "description": "Optional list of waybill records"},
            },
        },
    })
    return 1
