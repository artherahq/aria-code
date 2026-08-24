"""本地快照加载 —— 没有真实 ERP 系统时的数据入口。

WarehouseERPClient 只会走 ARIA_WAREHOUSE_ERP_URL 配的远程 HTTP(S) 接口，
在还没接真实 ERP/WMS 的情况下完全用不了。这个文件是平行的本地路径：用户
自己手上有什么数据（JSON 导出、Excel/CSV 表格），存成文件丢给
local_run.py，走的是跟远程 ERP 一模一样的 snapshot 形状——四个记录列表
(connectors / inbounds / skus / locations)，所以 agents/warehouse/ 下面
那三个 agent 完全不用关心数据到底是不是来自真实 ERP。

字段约定（跟三个 agent 的 records()/number()/integer() 读取对齐，非必填
字段缺省按 0/False 处理，不会报错，只是分析不出对应的异常）：

  connectors:  name, delay_minutes, failed_jobs
  inbounds:    id, expected_qty, received_qty, damaged_qty, overdue(bool)
  skus:        sku, available, safety_stock
  locations:   code, utilization (0~1 的小数，如 0.92 = 92%)

支持两种输入：
  1. 单个 .json 文件 —— {"warehouse_id": "...", "connectors": [...], ...}
     （跟远程 ERP 快照同一形状，也可以包一层 "data"）
  2. 一个目录 —— 放 connectors.csv / inbounds.csv / skus.csv / locations.csv
     其中任意几个文件（不需要四个都有），每个文件是该记录类型自己的表格，
     给不会写 JSON、只会用 Excel/Numbers 导出 CSV 的人用
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

_RECORD_LISTS = ("connectors", "inbounds", "skus", "locations")

_BOOL_TRUE = {"1", "true", "yes", "y", "是", "true"}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in _BOOL_TRUE


def _normalise_records(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _load_json(path: Path, warehouse_id: str) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    root = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(root, dict):
        raise ValueError(f"{path} 顶层必须是 JSON 对象（可选包一层 \"data\"）")
    snapshot: Dict[str, Any] = {"warehouse_id": str(root.get("warehouse_id") or warehouse_id or path.stem)}
    for key in _RECORD_LISTS:
        snapshot[key] = _normalise_records(root.get(key, []))
    return snapshot


def _read_csv_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = [dict(row) for row in reader]
    # overdue 列在 CSV 里天然是字符串，其余字段留给 contracts.number()/integer() 处理，
    # 只有这一个布尔字段需要在这里就转好，不然 "false" 这种字符串会被 Python 判成真值。
    for row in rows:
        if "overdue" in row:
            row["overdue"] = _coerce_bool(row["overdue"])
    return rows


def _load_csv_dir(path: Path, warehouse_id: str) -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {"warehouse_id": warehouse_id or path.name}
    found_any = False
    for key in _RECORD_LISTS:
        csv_path = path / f"{key}.csv"
        if csv_path.exists():
            snapshot[key] = _read_csv_rows(csv_path)
            found_any = True
        else:
            snapshot[key] = []
    if not found_any:
        raise ValueError(
            f"{path} 里没找到任何 connectors.csv / inbounds.csv / skus.csv / "
            f"locations.csv —— 至少要有一个"
        )
    return snapshot


def _load_single_csv(path: Path, warehouse_id: str) -> Dict[str, Any]:
    """单文件 CSV：靠一列 record_type（值须是 connectors/inbounds/skus/locations 之一）
    把每一行分发到对应的记录列表，给"就想丢一张表格"的人用。"""
    rows = _read_csv_rows(path)
    snapshot: Dict[str, Any] = {"warehouse_id": warehouse_id or path.stem, **{k: [] for k in _RECORD_LISTS}}
    unknown_types = set()
    for row in rows:
        rtype = str(row.pop("record_type", "")).strip().lower()
        if rtype in snapshot:
            snapshot[rtype].append(row)
        else:
            unknown_types.add(rtype or "(空)")
    if unknown_types:
        raise ValueError(
            f"{path} 里有行的 record_type 不认识: {sorted(unknown_types)}，"
            f"必须是 connectors/inbounds/skus/locations 之一"
        )
    return snapshot


def load_snapshot_from_path(path: str, warehouse_id: str = "") -> Dict[str, Any]:
    """加载本地快照，返回跟 WarehouseERPClient.fetch_snapshot() 同形状的 dict。"""
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"找不到数据文件/目录: {p}")

    if p.is_dir():
        return _load_csv_dir(p, warehouse_id)
    if p.suffix.lower() == ".json":
        return _load_json(p, warehouse_id)
    if p.suffix.lower() == ".csv":
        # 有 record_type 列走单文件多类型模式，否则报错提示用哪种格式
        with p.open("r", encoding="utf-8-sig") as f:
            header = f.readline()
        if "record_type" in header:
            return _load_single_csv(p, warehouse_id)
        raise ValueError(
            f"{p} 是单个 CSV 但没有 record_type 列——要么在表格里加一列 record_type"
            f"（值为 connectors/inbounds/skus/locations），要么把它放进一个目录，"
            f"文件名改成 connectors.csv / inbounds.csv / skus.csv / locations.csv"
        )
    raise ValueError(f"不支持的文件类型: {p.suffix}（支持 .json、.csv，或含多个 .csv 的目录）")
