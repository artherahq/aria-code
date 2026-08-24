"""
agents/supervisor.py — 动态规划与路由 Agent
===========================================
负责根据用户意图和资产类型，动态挑选最合适的 Sub-agents 执行分析。
"""

import json
import logging
from typing import Any, Dict

from .base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

class SupervisorAgent(BaseAgent):
    name: str = "supervisor"
    description: str = "动态规划与路由 Agent，决定应唤醒哪些子 Agent"

    @staticmethod
    def _fallback_selection(available: list[str]) -> list[str]:
        """Choose a deterministic, registry-safe fallback team.

        The Supervisor is optional infrastructure: losing its LLM response must
        never cause ``AgentTeam`` to receive names that are absent from the
        registry. Prefer the familiar financial specialists, then fill from
        any remaining available read-only agents.
        """
        preferred = ["macro", "fundamental", "technical", "risk"]
        chosen = [name for name in preferred if name in available]
        for name in available:
            if name not in chosen:
                chosen.append(name)
            if len(chosen) == 5:
                break
        return chosen[:5]

    @classmethod
    def _normalize_selection(cls, selected: Any, available: list[str]) -> list[str]:
        """Validate model output and complete it with safe deterministic choices."""
        if not isinstance(selected, list):
            selected = []

        allowed = set(available)
        normalized: list[str] = []
        for value in selected:
            name = str(value or "").strip().lower()
            if name in allowed and name not in normalized:
                normalized.append(name)
            if len(normalized) == 5:
                break

        # The prompt asks for 2–5 agents.  On a partial installation, use all
        # available specialists rather than inventing an unavailable name.
        minimum = min(2, len(available))
        if len(normalized) < minimum:
            for name in cls._fallback_selection(available):
                if name not in normalized:
                    normalized.append(name)
                if len(normalized) >= minimum:
                    break

        return normalized[:5]

    async def analyze(self, symbol: str, data: Dict[str, Any]) -> AgentResult:
        from .registry import get_registry
        registry = get_registry()
        agents_info = [
            info for info in registry.list()
            if info["name"] not in ("supervisor", "synthesis", "debate", "base")
        ]
        available = [str(info["name"]).lower() for info in agents_info]

        system_prompt = (
            "You are the Supervisor Agent of a financial analysis system.\n"
            "Your task is to select the most appropriate specialist agents to analyze the given asset.\n"
            "Available agents:\n"
        )
        for info in agents_info:
            system_prompt += f"- {info['name']}: {info['description']}\n"

        system_prompt += (
            "\nSelect between 2 to 5 agents that are best suited for this asset.\n"
            "For example, do not select crypto agents for standard stocks, and do not select realty agents for equities.\n"
            "Output a strict JSON object with a single key 'selected_agents' containing a list of strings (the names of the agents to run).\n"
            "Do not include markdown blocks or any other text.\n"
            "Example: {\"selected_agents\": [\"macro\", \"technical\"]}"
        )

        user_prompt = f"Analyze asset: {symbol}\n"
        if data and "market_data_block" in data:
            user_prompt += f"Context:\n{data['market_data_block']}\n"

        response_text = await self._call_llm(system_prompt, user_prompt, max_tokens=200, quote=data.get("quote"))

        selected: Any = []
        try:
            start = response_text.find('{')
            end = response_text.rfind('}') + 1
            if start != -1 and end != 0:
                parsed = json.loads(response_text[start:end])
                selected = parsed.get("selected_agents", [])
        except Exception as e:
            logger.warning(f"Supervisor json parse failed: {e}")

        selected = self._normalize_selection(selected, available)

        return AgentResult(
            agent=self.name,
            symbol=symbol,
            analysis=json.dumps(selected),
            confidence=1.0,
            signal="HOLD",
        )
