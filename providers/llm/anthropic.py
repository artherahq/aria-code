"""
providers/llm/anthropic.py — Anthropic Claude Provider
=======================================================
支持 claude-3-5-sonnet / claude-3-haiku / claude-3-opus 等。
支持流式 thinking（扩展思考模式）。

需要设置: ANTHROPIC_API_KEY=sk-ant-...
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncIterator, Dict, List, Optional

from .base import BaseLLMProvider, Message, ProviderConfig

logger = logging.getLogger(__name__)

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(BaseLLMProvider):

    provider_name     = "anthropic"
    supports_tools    = True
    supports_thinking = True    # claude-3-5-sonnet 支持 extended thinking
    local             = False

    DEFAULT_MODEL = "claude-3-5-haiku-latest"

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self.api_key = config.api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.model   = config.model or self.DEFAULT_MODEL

    async def is_available(self) -> bool:
        return bool(self.api_key)

    # Minimum system-prompt length (chars) to bother caching.
    # Prompts shorter than this don't benefit from prompt caching.
    _CACHE_MIN_CHARS = 1024

    def _build_cached_system(self, system_text: str) -> list:
        """
        Wrap a long system prompt in a cache_control block so Anthropic
        caches it across calls.  Returns the `system` field value (a list
        of content blocks, or a plain string for short prompts).

        Anthropic prompt caching docs:
          https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
        """
        if len(system_text) < self._CACHE_MIN_CHARS:
            return system_text  # type: ignore[return-value]
        return [
            {
                "type": "text",
                "text": system_text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    async def stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        cancel_event=None,
    ) -> AsyncIterator[Dict[str, Any]]:
        import aiohttp

        temp     = temperature if temperature is not None else self.config.temperature
        n_tokens = max_tokens  if max_tokens  is not None else self.config.max_tokens

        # Anthropic 格式：system 单独提取
        system_parts = [m.content for m in messages if m.role == "system"]
        system_text  = "\n\n".join(system_parts) if system_parts else None
        anthro_msgs  = [
            {"role": m.role, "content": m.content}
            for m in messages if m.role != "system"
        ]

        payload: Dict[str, Any] = {
            "model":      self.model,
            "max_tokens": n_tokens,
            "messages":   anthro_msgs,
            "stream":     True,
        }
        if system_text:
            # Use prompt caching for long system prompts to reduce TTFT and cost
            payload["system"] = self._build_cached_system(system_text)
        if temp > 0:
            payload["temperature"] = temp
        if tools:
            payload["tools"] = [
                {
                    "name":         t.get("name"),
                    "description":  t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]

        headers = {
            "x-api-key":           self.api_key,
            "anthropic-version":   _ANTHROPIC_VERSION,
            "anthropic-beta":      "prompt-caching-2024-07-31",
            "content-type":        "application/json",
        }

        proxy = (os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
                 or os.getenv("HTTP_PROXY")  or os.getenv("http_proxy"))
        try:
            async with aiohttp.ClientSession() as sess:
                async with sess.post(
                    _ANTHROPIC_API_URL, json=payload, headers=headers,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=self.config.timeout)
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        yield {"type": "error",
                               "message": f"Anthropic HTTP {resp.status}: {body[:300]}"}
                        return

                    _pending_tool_name = ""
                    _pending_tool_args = ""

                    async for raw in resp.content:
                        if cancel_event and cancel_event.is_set():
                            return
                        line = raw.decode("utf-8", errors="ignore").strip()
                        if not line or not line.startswith("data:"):
                            continue

                        try:
                            data = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue

                        etype = data.get("type", "")

                        # 思考 token（扩展思考模式）
                        if etype == "content_block_start":
                            cb = data.get("content_block", {})
                            if cb.get("type") == "thinking":
                                yield {"type": "thinking", "text": cb.get("thinking", "")}
                            elif cb.get("type") == "tool_use":
                                _pending_tool_name = cb.get("name", "")
                                _pending_tool_args = ""

                        elif etype == "content_block_delta":
                            delta = data.get("delta", {})
                            dt    = delta.get("type", "")
                            if dt == "text_delta":
                                yield {"type": "token", "text": delta.get("text", "")}
                            elif dt == "thinking_delta":
                                yield {"type": "thinking", "text": delta.get("thinking", "")}
                            elif dt == "input_json_delta":
                                _pending_tool_args += delta.get("partial_json", "")

                        elif etype == "content_block_stop":
                            if _pending_tool_name:
                                try:
                                    args = json.loads(_pending_tool_args)
                                except Exception:
                                    args = {"_raw": _pending_tool_args}
                                yield {"type": "tool_call",
                                       "name": _pending_tool_name,
                                       "arguments": args}
                                _pending_tool_name = ""
                                _pending_tool_args = ""

                        elif etype == "message_stop":
                            yield {"type": "done"}
                            return

        except aiohttp.ClientConnectorError as e:
            yield {"type": "error", "message": f"Anthropic 连接失败: {e}"}
        except Exception as e:
            yield {"type": "error", "message": f"Anthropic 错误: {e}"}
