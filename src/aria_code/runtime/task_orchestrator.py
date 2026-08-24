"""Deterministic task graphs for visible, verifiable Agent execution.

This is deliberately a planner rather than another LLM.  The primary model
still reasons about the work, while the runtime supplies a stable contract:
what can run in parallel, what must be verified, and when a result is too weak
to be presented as complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from aria_code.apps.cli.finance_service_catalog import classify_finance_market
from aria_code.apps.cli.intent_router import build_intent_route


@dataclass(frozen=True)
class TaskStage:
    key: str
    title: str
    objective: str
    mode: str = "read-only"
    depends_on: tuple[str, ...] = ()
    verification: str = ""


@dataclass(frozen=True)
class TaskGraph:
    request: str
    kind: str
    stages: tuple[TaskStage, ...]

    def ready(self, completed: Iterable[str]) -> tuple[TaskStage, ...]:
        done = set(completed)
        return tuple(stage for stage in self.stages if stage.key not in done and set(stage.depends_on) <= done)


def build_task_graph(request: str) -> TaskGraph:
    """Build an auditable graph from the user's request without network I/O."""
    text = str(request or "").strip()
    route = build_intent_route(text)
    market = classify_finance_market(text)
    financial_intents = {
        "market_snapshot", "market_analysis", "market_research", "report", "chart",
        "backtest", "strategy", "ashare", "hk_market", "us_market", "crypto",
        "forex", "commodity",
    }

    if route.allows_code_autorun or route.primary == "code":
        return TaskGraph(text, "engineering", (
            TaskStage("inspect", "范围与现状", "读取相关文件、约束和现有测试，确认最小改动范围。", verification="列出将修改的文件和不触碰的用户改动。"),
            TaskStage("implement", "实现", "在隔离工作区完成最小可验证实现。", mode="workspace-write", depends_on=("inspect",)),
            TaskStage("verify", "验证", "运行相关测试、静态检查和必要的手动冒烟检查。", depends_on=("implement",), verification="失败必须保留错误和未验证项。"),
            TaskStage("review", "审查与交付", "审查 diff、风险和验证证据，再总结交付结果。", depends_on=("verify",), verification="未经验证的改动不得标记为完成。"),
        ))

    if market is not None or any(intent in financial_intents for intent in route.intents):
        market_label = market.label if market else "目标市场"
        stages = [
            TaskStage(
                "evidence", "数据与事实", f"取得 {market_label} 的行情、时间戳、来源和可用覆盖范围。",
                verification="缺失、过期或来源失败必须显式标记，不能补造数值。",
            ),
            TaskStage(
                "research", "新闻与基本面", "核验与任务相关的公告、财报、新闻或宏观事件；区分事实和推断。",
                depends_on=("evidence",), verification="每个关键结论需要来源、日期和影响路径。",
            ),
            TaskStage(
                "analysis", "分析与情景", "基于证据输出趋势、风险、正反情景和失效条件。",
                depends_on=("evidence", "research"), verification="不得把技术指标、新闻情绪或预测概率表述为确定事实。",
            ),
        ]
        if market and market.key == "CN" and any(word in text.lower() for word in ("预测", "明天", "明日", "次日", "涨", "跌")):
            stages.append(TaskStage(
                "prediction", "量化预测校验", "调用 A 股次交易日引擎，并记录标的覆盖率与样本外评估口径。",
                depends_on=("evidence",), verification="必须展示数据日期、覆盖率、回测/评估质量和非覆盖标的。",
            ))
        stages.append(TaskStage(
            "quality_gate", "交付前验证", "检查事实、数据日期、矛盾结论和风险披露。",
            depends_on=tuple(stage.key for stage in stages if stage.key != "quality_gate"),
            verification="任何关键数据不合格时降级为资料不足，不输出投资指令。",
        ))
        return TaskGraph(text, "financial_research", tuple(stages))

    return TaskGraph(text, "general", (
        TaskStage("clarify", "目标与约束", "提取目标、已知信息、边界和缺失前提。"),
        TaskStage("execute", "执行", "按目标完成研究、分析或操作。", depends_on=("clarify",)),
        TaskStage("verify", "结果核验", "核对结果是否直接回答目标，并披露不确定性。", depends_on=("execute",)),
    ))
