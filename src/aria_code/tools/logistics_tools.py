"""Auditable freight analysis from supplied waybills or an existing local database."""

from __future__ import annotations

import csv
import json
import math
import pathlib
import sqlite3
from collections import defaultdict
from contextlib import closing
from typing import Any, Dict, List


def _number(record: dict[str, Any], field: str, row: int, *, required: bool = False) -> float | None:
    value = record.get(field)
    if value is None or value == "":
        if required:
            raise ValueError(f"Row {row}: {field} is required")
        return None
    if isinstance(value, bool):
        raise ValueError(f"Row {row}: {field} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Row {row}: {field} must be a number") from exc
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"Row {row}: {field} must be finite and non-negative")
    return number


def _on_time(value: Any, row: int) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    raise ValueError(f"Row {row}: is_on_time must be a boolean")


def tool_analyze_logistics_data(params: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze actual records. Empty input never produces representative data."""
    try:
        file_path = params.get("file_path")
        records = params.get("waybills")
        if file_path and records is not None:
            raise ValueError("Provide either file_path or waybills, not both")
        if file_path:
            path = pathlib.Path(file_path).expanduser().resolve()
            if path.suffix.lower() == ".json":
                content = json.loads(path.read_text(encoding="utf-8"))
                records = content if isinstance(content, list) else content.get("waybills") if isinstance(content, dict) else None
            elif path.suffix.lower() == ".csv":
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    records = list(csv.DictReader(handle))
            else:
                raise ValueError("Only CSV and JSON waybill files are supported")
            source = str(path)
        elif records is None:
            path = pathlib.Path.home() / ".aria" / "erp_warehouse.db"
            if not path.is_file():
                raise ValueError("No waybills supplied and no local logistics database found")
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as connection:
                connection.row_factory = sqlite3.Row
                records = [dict(row) for row in connection.execute("SELECT * FROM logistics_waybills")]
            source = str(path)
        else:
            source = "provided_waybills"

        if not isinstance(records, list) or not records:
            raise ValueError("No waybill records available for analysis")

        spend = 0.0
        known_delivery = 0
        on_time = 0
        carriers: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "total_spend": 0.0, "known_delivery_count": 0, "on_time_count": 0})
        anomalies: list[dict[str, Any]] = []
        for row, record in enumerate(records, 1):
            if not isinstance(record, dict):
                raise ValueError(f"Row {row}: waybill must be an object")
            cost = _number(record, "total_cost", row, required=True)
            assert cost is not None
            actual = _number(record, "actual_weight_kg", row)
            billed = _number(record, "billed_weight_kg", row)
            timely = _on_time(record.get("is_on_time"), row)
            carrier = str(record.get("carrier") or "Unknown")
            spend += cost
            carriers[carrier]["count"] += 1
            carriers[carrier]["total_spend"] += cost
            if timely is not None:
                known_delivery += 1
                on_time += int(timely)
                carriers[carrier]["known_delivery_count"] += 1
                carriers[carrier]["on_time_count"] += int(timely)
            if actual is not None and billed is not None and actual > 0 and billed > actual * 1.2:
                anomalies.append({"waybill_no": record.get("waybill_no"), "carrier": carrier, "actual_weight_kg": actual, "billed_weight_kg": billed, "reason": "Billed weight exceeds actual weight by more than 20%; verify dimensional weight before disputing"})

        metrics = []
        for carrier, item in sorted(carriers.items()):
            item["total_spend"] = round(item["total_spend"], 2)
            item["on_time_rate"] = round(item["on_time_count"] / item["known_delivery_count"] * 100, 2) if item["known_delivery_count"] else None
            metrics.append({"carrier": carrier, **item})
        rate = round(on_time / known_delivery * 100, 2) if known_delivery else None
        result = {"total_waybills": len(records), "total_freight_spend": round(spend, 2), "overall_on_time_rate": rate, "known_delivery_count": known_delivery, "carrier_metrics": metrics, "billing_anomalies": anomalies}
        rate_label = f"{rate}%" if rate is not None else "未知（无准时状态记录）"
        return {"success": True, "data": result, "source": source, "summary": f"已审计 {len(records)} 单运单，运费总计 ¥{spend:,.2f}，已知准时交付率 {rate_label}，发现 {len(anomalies)} 笔需核实的计费重量异常。"}
    except (OSError, sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"success": False, "error": str(exc)}


def register_logistics_tools(tools_dict: Dict[str, Any], schemas_list: List[Dict[str, Any]]) -> int:
    """Register the waybill analyzer as a local tool."""
    tools_dict["analyze_logistics_data"] = (tool_analyze_logistics_data, "Analyze supplied freight waybills and billing anomalies")
    schemas_list.append({
        "name": "analyze_logistics_data",
        "description": "Analyze actual waybills from supplied records, a CSV/JSON file, or an existing local database",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "CSV or JSON waybill file path"},
                "waybills": {"type": "array", "description": "Waybill records with total_cost"},
            },
        },
    })
    return 1
