from typing import Any
import json
from .base import BaseAgent
from .registry import get_registry

class OrchestratorAgent(BaseAgent):
    name = "orchestrator"
    description = "动态分析用户意图，从注册中心挑选最合适的专业智能体进行流转和编排。"

    def get_prompt(self, context: Any) -> str:
        registry = get_registry()
        agents = registry.list()
        # Remove orchestrator itself from the list to avoid recursive loop
        agents = [a for a in agents if a["name"] != "orchestrator"]
        
        agents_info = "\n".join([f"- {a['name']}: {a['description']}" for a in agents])
        
        prompt = (
            "你是一个高级架构师智能体 (Orchestrator)。\n"
            "你的任务是分析用户的请求，并从以下可用的专业智能体库中，挑选出最合适的一个或多个智能体来解决问题。\n"
            "可用智能体库:\n"
            f"{agents_info}\n\n"
            "请直接输出 JSON 格式的结果，包含两项:\n"
            "1. 'plan': 简要的一句话解释为什么选择这些智能体。\n"
            "2. 'agents': 一个字符串列表，包含需要调用的智能体名称（最多选 3 个）。\n"
            "注意: 必须且只能输出合法的 JSON，不要有任何代码块包裹或前言后语。\n"
        )
        return prompt

    def get_tools(self) -> list:
        return []

    async def execute(self, provider, model_name: str, **kwargs) -> Any:
        context = kwargs.get("context", "")
        # Because Orchestrator doesn't have its own tools, it just calls the LLM with a basic generate_text
        # We need a simple non-streaming call or accumulate the stream.
        from aria_code.aria_cli import send_message
        prompt = self.get_prompt(context)
        
        # Or we can just use the provider directly if we have a simple function.
        # But BaseAgent usually operates via `AgentTeam` which handles the loops.
        # Since Orchestrator is special, it should probably be used by the CLI directly.
        pass

