"""
agents/engineering/tester.py — Tester & Self-Healing Agent
=========================================================
Engineering agent responsible for running fast pre-flight compilation checks,
sandbox dry-runs, traceback diagnosis, and surgical patch self-healing.
Delivers concise, high-signal results without terminal log flooding.
"""

from __future__ import annotations

import logging
import pathlib
import sys
from typing import Any, Callable, Dict, List, Optional

from ..base import BaseAgent, AgentResult
from runtime.self_healing import SelfHealingEngine, SelfHealingResult

logger = logging.getLogger(__name__)


class TesterAgent(BaseAgent):
    __test__ = False
    name = "tester"
    description = "测试与自愈智能体 — 负责代码静态预检、沙箱试跑与自动诊断修复"

    def __init__(
        self,
        llm_provider=None,
        data_router=None,
        on_token: Optional[Callable[[str], None]] = None,
        on_thought: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, Dict], None]] = None,
        on_tool_end: Optional[Callable[[str, Any], None]] = None,
        lang: str = "zh",
        max_retries: int = 3,
    ) -> None:
        super().__init__(
            llm_provider=llm_provider,
            data_router=data_router,
            on_token=on_token,
            on_thought=on_thought,
            on_tool_start=on_tool_start,
            on_tool_end=on_tool_end,
            lang=lang,
        )
        self.engine = SelfHealingEngine(python_executable=sys.executable, max_retries=max_retries)

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        """
        Run test and self-healing on the target script.
        """
        script_path_str = data.get("script_path") or data.get("file_path") or ""
        if not script_path_str:
            # Look in generated folder
            gen_path = pathlib.Path.cwd() / "generated" / f"strategy_{symbol.replace('.', '_').replace('-', '_').lower()}.py"
            if gen_path.exists():
                script_path_str = str(gen_path)
            else:
                return AgentResult(
                    agent=self.name,
                    symbol=symbol,
                    analysis=f"❌ 未找到待测试的代码文件: {symbol}",
                    confidence=0.0,
                    signal="HOLD",
                    error="Missing script_path",
                )

        target_file = pathlib.Path(script_path_str)
        if not target_file.exists():
            return AgentResult(
                agent=self.name,
                symbol=symbol,
                analysis=f"❌ 目标文件不存在: {target_file}",
                confidence=0.0,
                signal="HOLD",
                error=f"File not found: {target_file}",
            )

        # Run Self-Healing Engine
        heal_result: SelfHealingResult = await self.engine.execute_and_heal(target_file)

        if heal_result.success:
            m = heal_result.metrics
            ann_ret = m.get("annual_return_pct", m.get("total_return_pct", 0.0))
            sharpe = m.get("sharpe_ratio", 0.0)
            max_dd = m.get("max_drawdown_pct", 0.0)
            win_rate = m.get("win_rate_pct", 0.0)
            trades = m.get("total_trades", 0)

            patch_msg = ""
            if heal_result.patches_applied:
                patch_msg = f"\n• 🛠️ 自动诊断与自愈修复: 已应用 {len(heal_result.patches_applied)} 处差量补丁 (未整文件重写)"

            summary = (
                f"### 🧪 回测执行验证成功\n\n"
                f"| 关键回测指标 | 数值 |\n"
                f"| :--- | :--- |\n"
                f"| **年化收益率 (Annual Return)** | `{ann_ret}%` |\n"
                f"| **夏普比率 (Sharpe Ratio)** | `{sharpe}` |\n"
                f"| **最大回撤 (Max Drawdown)** | `{max_dd}%` |\n"
                f"| **策略胜率 (Win Rate)** | `{win_rate}%` ({trades} 笔交易) |\n\n"
                f"**产物交付路径**:\n"
                f"- 策略脚本: `{target_file}`\n"
                + "\n".join(f"- 输出产物: `{p}`" for p in heal_result.artifact_paths if p != str(target_file))
                + patch_msg
            )

            signal = "STRONG_BUY" if sharpe > 1.5 and max_dd < 15.0 else ("BUY" if sharpe > 0.8 else "HOLD")

            return AgentResult(
                agent=self.name,
                symbol=symbol,
                analysis=summary,
                confidence=0.92,
                signal=signal,
                key_points=[
                    f"Annual Return: {ann_ret}%",
                    f"Sharpe Ratio: {sharpe}",
                    f"Max Drawdown: {max_dd}%",
                    f"Win Rate: {win_rate}%",
                ],
                data_used={
                    "metrics": m,
                    "artifact_paths": heal_result.artifact_paths,
                    "patches_applied": heal_result.patches_applied,
                },
            )
        else:
            return AgentResult(
                agent=self.name,
                symbol=symbol,
                analysis=f"❌ 测试自愈失败: {heal_result.error}",
                confidence=0.0,
                signal="HOLD",
                error=heal_result.error,
                data_used={"patches_applied": heal_result.patches_applied},
            )


class TesterSelfHealingAgent(TesterAgent):
    """Alias for TesterAgent."""
    __test__ = False
    name = "debugger"
    description = "调试与自愈智能体 — 负责捕捉 Traceback 并精准修补代码"
