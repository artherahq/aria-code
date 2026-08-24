from typing import List, Dict, Any
import json
import logging
from agents.registry import get_registry

logger = logging.getLogger(__name__)

async def dynamic_agent_orchestration(user_query: str, provider: Any) -> Dict[str, Any]:
    """
    Dynamic routing to pick the best agents for the user's query.
    """
    registry = get_registry()
    agents = registry.list()
    
    # Filter out orchestrator or core infrastructure agents that shouldn't be dynamically routed
    agents = [a for a in agents if a["name"] not in {"orchestrator", "supervisor"}]
    
    agents_info = "\n".join([f"- {a['name']}: {a.get('description', '')}" for a in agents])
    
    prompt = (
        f"你是一个高级的智能体编排中心 (Orchestrator)。\n"
        f"用户请求:\n{user_query}\n\n"
        f"可用智能体库:\n{agents_info}\n\n"
        f"请直接输出一段合法的 JSON，选择最合适的智能体来解决用户请求(最多选3个)。\n"
        f"JSON 结构必须是:\n"
        f"{{\n"
        f"  \"plan\": \"解释为什么选择这些智能体\",\n"
        f"  \"agents\": [\"agent_name_1\", \"agent_name_2\"]\n"
        f"}}\n"
    )

    try:
        # We assume provider is an async function or object that can generate text
        # But we'll just try to use a simple completion if available
        messages = [{"role": "user", "content": prompt}]
        
        # Use provider to generate
        full_response = ""
        async for chunk in provider.stream(messages=messages, tools=[]):
            if chunk.text:
                full_response += chunk.text
        
        # Parse JSON
        start_idx = full_response.find("{")
        end_idx = full_response.rfind("}")
        if start_idx != -1 and end_idx != -1:
            json_str = full_response[start_idx:end_idx+1]
            return json.loads(json_str)
        else:
            return {"plan": "未能解析 JSON", "agents": ["technical"]}
    except Exception as e:
        logger.error(f"Orchestration failed: {e}")
        # Default fallback
        return {"plan": "降级路由", "agents": ["technical", "fundamental"]}

