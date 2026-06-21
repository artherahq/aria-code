"""Provider streaming helpers shared by SDK and CLI adapters."""

from __future__ import annotations

import asyncio
from typing import Callable

from apps.cli.providers.base import (
    LLMDone,
    LLMProvider,
    LLMStatus,
    LLMThinking,
    LLMToken,
    LLMToolCall,
    LLMToolResult,
)


async def stream_provider_result(
    provider: LLMProvider,
    prompt: str,
    history: list,
    *,
    tools: list | tuple | None = None,
    cancel_event: asyncio.Event | None = None,
    on_token: Callable[[str], None] | None = None,
    on_thinking: Callable[[str], None] | None = None,
    on_tool_call: Callable[[str, dict], None] | None = None,
    on_tool_result: Callable[[str, str], None] | None = None,
    on_status: Callable[[str, str], None] | None = None,
) -> dict:
    """Stream one provider turn and return the standard Aria result dict."""

    messages = list(history or []) + [{"role": "user", "content": prompt}]
    response_parts: list[str] = []
    tool_calls: list[dict] = []
    final = LLMDone(response="", provider="unknown", success=True)

    async for event in provider.stream(messages, list(tools or []), cancel_event=cancel_event):
        if isinstance(event, LLMToken):
            response_parts.append(event.text)
            if on_token:
                on_token(event.text)
        elif isinstance(event, LLMThinking):
            if on_thinking:
                on_thinking(event.content)
        elif isinstance(event, LLMToolCall):
            call = {"tool": event.tool, "params": dict(event.params)}
            tool_calls.append(call)
            if on_tool_call:
                on_tool_call(event.tool, dict(event.params))
        elif isinstance(event, LLMToolResult):
            if on_tool_result:
                on_tool_result(event.tool, event.summary)
        elif isinstance(event, LLMStatus):
            if on_status:
                on_status(event.state, event.message)
        elif isinstance(event, LLMDone):
            final = event

    response = final.response or "".join(response_parts)
    return {
        "success": final.success,
        "response": response,
        "provider": final.provider,
        "tool_calls_pending": tool_calls or list(final.tool_calls_pending),
        "usage": dict(final.usage),
        "cancelled": final.cancelled,
        "error": final.error,
    }


__all__ = ["stream_provider_result"]
