"""
providers/llm/base.py — LLM Provider 统一抽象基类
==================================================
所有 provider（本地/云端）实现同一接口，上层代码无需关心具体后端。

事件类型 (stream yields):
    {"type": "token",     "text": "..."}          # 文本增量
    {"type": "thinking",  "text": "..."}          # 思考过程 (Claude/DeepSeek-R1)
    {"type": "tool_call", "name": "...", "arguments": {...}}  # 工具调用
    {"type": "done",      "text": "完整响应"}      # 流结束
    {"type": "error",     "message": "..."}       # 错误
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional


@dataclass
class Message:
    role: str       # "system" | "user" | "assistant" | "tool"
    content: str
    name: Optional[str] = None        # tool name (for role=tool)
    tool_call_id: Optional[str] = None


@dataclass
class ProviderConfig:
    """可由 providers.yaml / 环境变量 / CLI 参数覆盖"""
    name:        str
    api_key:     Optional[str]   = None
    base_url:    Optional[str]   = None
    model:       Optional[str]   = None
    temperature: float           = 0.3
    max_tokens:  int             = 4096
    timeout:     int             = 120
    # 扩展字段（各 provider 可自定义）
    extra:       Dict[str, Any]  = field(default_factory=dict)

    @classmethod
    def from_env(cls, name: str, **defaults) -> "ProviderConfig":
        """从环境变量自动读取 API Key（DEEPSEEK_API_KEY / OPENAI_API_KEY 等）"""
        key_map = {
            "deepseek":    "DEEPSEEK_API_KEY",
            "openai":      "OPENAI_API_KEY",
            "anthropic":   "ANTHROPIC_API_KEY",
            "groq":        "GROQ_API_KEY",
            "together":    "TOGETHER_API_KEY",
            "dashscope":   "DASHSCOPE_API_KEY",
            "siliconflow": "SILICONFLOW_API_KEY",
            "moonshot":    "MOONSHOT_API_KEY",
            "zhipu":       "ZHIPUAI_API_KEY",
        }
        env_var = key_map.get(name.lower())
        api_key = os.getenv(env_var, "") if env_var else ""
        return cls(name=name, api_key=api_key or None, **defaults)

    def is_configured(self) -> bool:
        """判断 provider 是否可用（本地 provider 无需 api_key）"""
        _local = {"ollama", "lmstudio", "vllm", "llamacpp", "jan"}
        if self.name.lower() in _local:
            return True
        return bool(self.api_key)


class BaseLLMProvider(ABC):
    """
    所有 LLM provider 的统一基类。

    子类只需实现 `stream()` 方法；`complete()` 会自动聚合流结果。
    """

    # 子类声明这些属性
    provider_name: str = "base"
    supports_tools: bool = False
    supports_thinking: bool = False
    local: bool = False          # True = 本地运行，不需要 api_key

    def __init__(self, config: ProviderConfig):
        self.config = config

    # ── 必须实现 ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        cancel_event=None,
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        流式生成 token。

        每次 yield 一个事件 dict（见模块文档中的事件类型）。
        """
        ...
        yield {}  # 让 Python 识别为 async generator

    # ── 默认实现（子类可覆盖）────────────────────────────────────────────────

    async def complete(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """聚合 stream() 事件，返回完整响应 dict。"""
        full_text = ""
        tool_calls = []
        async for event in self.stream(
            messages, tools=tools,
            temperature=temperature, max_tokens=max_tokens,
        ):
            t = event.get("type")
            if t == "token":
                full_text += event.get("text", "")
            elif t == "tool_call":
                tool_calls.append({
                    "name": event.get("name"),
                    "arguments": event.get("arguments", {}),
                })
            elif t == "error":
                return {"success": False, "error": event.get("message"),
                        "response": full_text, "tool_calls": tool_calls}
        return {"success": True, "response": full_text, "tool_calls": tool_calls}

    async def is_available(self) -> bool:
        """检查 provider 是否在线/可用（子类覆盖以做实际探测）"""
        return self.config.is_configured()

    def __repr__(self) -> str:
        model = self.config.model or "default"
        return f"{self.__class__.__name__}(model={model})"
