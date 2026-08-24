"""Warehouse ERP command; globals are rebound by the legacy CLI bridge."""

from __future__ import annotations

import json


import json
import asyncio
import datetime
import time
import shlex
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


class WarehouseCommandsMixin:
    """Read-only warehouse ERP analysis commands."""

    async def cmd_warehouse(self, args: str):
        """Run `/warehouse <warehouse_id> [--json]` against the configured ERP."""
        parts = args.strip().split()
        as_json = "--json" in parts
        identifiers = [part for part in parts if not part.startswith("--")]
        if len(identifiers) != 1:
            message = "用法: /warehouse <仓库编号> [--json]，例如 /warehouse WH-CN-01"
            self.context.console.print(f"[yellow]{message}[/yellow]") if self.context.has_rich else print(message)
            return

        from aria_code.agents.warehouse.workflow import run_warehouse_analysis
        from aria_code.clients.warehouse_erp_client import WarehouseERPConfigurationError, WarehouseERPRequestError

        try:
            result, snapshot = await run_warehouse_analysis(identifiers[0])
        except (WarehouseERPConfigurationError, WarehouseERPRequestError) as exc:
            message = str(exc)
            self.context.console.print(f"[red]{message}[/red]") if self.context.has_rich else print(message)
            return

        payload = {
            "warehouse_id": identifiers[0],
            "signal": result.final_signal,
            "confidence": result.confidence,
            "agents": [item.to_dict() for item in result.results],
            "snapshot_counts": {key: len(snapshot.get(key, [])) for key in ("connectors", "inbounds", "skus", "locations")},
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        lines = [
            f"仓库 {identifiers[0]} · {result.final_signal} · 置信度 {result.confidence:.0%}",
            f"货代 {payload['snapshot_counts']['connectors']} · 入库单 {payload['snapshot_counts']['inbounds']} · "
            f"SKU {payload['snapshot_counts']['skus']} · 库位 {payload['snapshot_counts']['locations']}",
        ]
        for item in result.results:
            summary = item.key_points[0] if item.key_points else item.analysis
            lines.append(f"- {item.agent}: {item.signal} — {summary}")
        output = "\n".join(lines)
        self.context.console.print(output) if self.context.has_rich else print(output)
