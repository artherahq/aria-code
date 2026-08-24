#!/usr/bin/env python3
"""本地跑通海外仓 ERP 分析：用户给一份本地数据文件，直接产出网页仪表盘。

不需要真实 ERP 系统（那是 workflow.py 的 run_warehouse_analysis() 走的
远程 ARIA_WAREHOUSE_ERP_URL 路径），也不需要任何第三方设计工具——数据
加载、多 agent 分析、HTML/PDF 渲染全部在本地完成。

用法::

    python3 -m agents.warehouse.local_run --data snapshot.json
    python3 -m agents.warehouse.local_run --data ./my_warehouse_csvs/ --open
    python3 -m agents.warehouse.local_run --data snapshot.json --pdf --warehouse-id WH-042

数据文件格式见 local_snapshot.py 顶部的字段约定。
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from aria_code.agents.team import AgentTeam
from aria_code.agents.warehouse import WAREHOUSE_SCHEME, WAREHOUSE_TEAM
from aria_code.agents.warehouse.dashboard import save_report
from aria_code.agents.warehouse.local_snapshot import load_snapshot_from_path


async def run(data_path: str, warehouse_id: str, *, open_browser: bool, also_pdf: bool) -> None:
    print(f"  [1/3] 加载本地数据: {data_path}")
    snapshot = load_snapshot_from_path(data_path, warehouse_id=warehouse_id)
    wid = snapshot["warehouse_id"]
    counts = {k: len(snapshot.get(k, []) or []) for k in ("connectors", "inbounds", "skus", "locations")}
    print(f"        仓库: {wid} | " + " ".join(f"{k}={v}" for k, v in counts.items()))

    print(f"  [2/3] 运行 warehouse agent team: {', '.join(WAREHOUSE_TEAM)}")
    team = AgentTeam(signal_scheme=WAREHOUSE_SCHEME, timeout_per_agent=20)
    result = await team.run(
        wid,
        agents=list(WAREHOUSE_TEAM),
        agent_data={agent_name: snapshot for agent_name in WAREHOUSE_TEAM},
    )
    for r in result.results:
        status = "✓" if r.success else f"✗ {r.error}"
        print(f"        {status} {r.agent}: {r.signal}")

    print("  [3/3] 生成本地仪表盘")
    html_path = save_report(wid, snapshot, result, open_browser=open_browser, also_pdf=also_pdf)
    print(f"\n  ✓ 报告已生成: {html_path}")
    if also_pdf:
        pdf_path = html_path.with_suffix(".pdf")
        print(f"  ✓ PDF: {pdf_path}" if pdf_path.exists() else "  ⚠ PDF 未生成（本机没有可用的渲染引擎）")


def main() -> None:
    ap = argparse.ArgumentParser(description="本地跑通海外仓 ERP agent 分析 + 生成网页仪表盘")
    ap.add_argument("--data", required=True, help="本地数据文件(.json/.csv)或目录路径")
    ap.add_argument("--warehouse-id", default="", help="仓库标识，缺省用文件名/目录名")
    ap.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    ap.add_argument("--pdf", action="store_true", help="额外导出 PDF")
    args = ap.parse_args()

    try:
        asyncio.run(run(args.data, args.warehouse_id, open_browser=args.open, also_pdf=args.pdf))
    except (FileNotFoundError, ValueError) as exc:
        print(f"  ✗ {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
