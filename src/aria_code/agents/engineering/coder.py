"""
agents/engineering/coder.py — Coder Agent
=========================================
Engineering agent responsible for translating Quant Strategist Rule Trees
into self-contained, robust, executable Python / TypeScript backtest scripts.
Never dumps raw unexecuted code into user dialogue; saves directly to disk.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
from typing import Any, Callable, Dict, List, Optional

from ..base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class CoderAgent(BaseAgent):
    name = "coder"
    description = "精准编码智能体 — 将量化规则翻译为自包含脚本并静默落盘"

    def __init__(
        self,
        output_dir: Optional[pathlib.Path] = None,
        llm_provider=None,
        data_router=None,
        on_token: Optional[Callable[[str], None]] = None,
        on_thought: Optional[Callable[[str], None]] = None,
        on_tool_start: Optional[Callable[[str, Dict], None]] = None,
        on_tool_end: Optional[Callable[[str, Any], None]] = None,
        lang: str = "zh",
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
        self.output_dir = output_dir or pathlib.Path.cwd() / "generated"

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        """
        Use the LLM to dynamically generate Python code (e.g. backtests or scripts).
        """
        rule_tree = data.get("strategy_rules") or data.get("rule_tree")
        request_text = data.get("request", "Write a Python script.")
        
        system_prompt = (
            "You are an expert Python Quantitative Developer and Software Engineer.\\n"
            "Your task is to write high-quality, self-contained Python code based on the user's request.\\n"
            "If the user asks for a trading strategy, write a pandas/numpy vector backtest script.\\n"
            "Output ONLY valid Python code inside a markdown block (```python ... ```).\\n"
            "Do not include unnecessary explanations outside the code block."
        )
        
        user_prompt = f"Target Symbol: {symbol}\\nConstraints/Rules: {rule_tree}\\nUser Request: {request_text}"
        
        analysis = await self._call_llm(system_prompt, user_prompt, max_tokens=1500)
        
        # Extract python code
        import re
        match = re.search(r"```python(.*?)```", analysis, re.DOTALL)
        if match:
            python_code = match.group(1).strip()
        else:
            python_code = analysis
            
        # Write to file
        self.output_dir.mkdir(parents=True, exist_ok=True)
        safe_sym = symbol.replace(".", "_").replace("-", "_").lower() or "generic"
        script_path = self.output_dir / f"generated_code_{safe_sym}.py"
        script_path.write_text(python_code, encoding="utf-8")
        
        report = (
            f"### 💻 代码生成完毕\\n\\n"
            f"已成功为您编写 Python 脚本并保存至本地工作区：\\n"
            f"`{script_path.absolute()}`\\n\\n"
            f"**主要实现逻辑**：\\n"
            f"根据您的需求，已将核心规则转化为代码。您可以直接使用 `python {script_path.name}` 运行此文件。\\n"
        )
        
        return AgentResult(
            agent=self.name,
            symbol=symbol,
            analysis=report,
            confidence=0.95,
            signal="GOOD",
            key_points=[
                f"Generated self-contained Python script at {script_path.name}",
                "LLM dynamically wrote code based on user request",
            ],
            data_used={
                "script_path": str(script_path),
            },
        )
