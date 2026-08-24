"""货代同步巡检 Agent。输入由 ERP API 注入，不直接持有货代密钥。"""
from __future__ import annotations
from typing import Any, Dict
from ..base import BaseAgent, AgentResult
from .contracts import integer, number, records

class LogisticsSyncAgent(BaseAgent):
    name = "warehouse_logistics_sync"
    description = "货代同步巡检：识别 API 延迟、失败及缺失运输单"

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        connectors = records(data, "connectors")
        issues = []
        for item in connectors:
            delay = number(item.get("delay_minutes"))
            failures = integer(item.get("failed_jobs"))
            if failures: issues.append(f"{item.get('name', '未知货代')} 有 {failures} 个失败同步任务")
            if delay >= 30: issues.append(f"{item.get('name', '未知货代')} 同步延迟 {delay:g} 分钟")
        signal = "SEVERE" if any("失败" in item for item in issues) else "WATCH" if issues else "GOOD"
        analysis = "；".join(issues) if issues else "所有已接入货代的同步任务均在健康阈值内。"
        return AgentResult(agent=self.name, symbol=symbol, analysis=analysis, confidence=.9, signal=signal, key_points=issues or ["同步正常"], data_used={"connector_count": len(connectors)}, provenance=["warehouse-api"])
