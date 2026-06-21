"""LLM provider abstraction layer.

Defines the minimal Protocol that every provider must satisfy so the
agent loop can call any backend (Ollama, AriaSSE, DeepSeek, etc.)
without importing provider-specific code.

Usage
-----
Implement the protocol on any class or pass a coroutine that matches
``stream()``'s signature as a bare ``provider_fn`` callable.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional, Protocol, runtime_checkable


# ── Event types emitted by LLMProvider.stream() ──────────────────────────────

@dataclass(frozen=True)
class LLMToken:
    """A single text token from the model."""
    text: str


@dataclass(frozen=True)
class LLMThinking:
    """One thinking/reasoning token (extended-thinking models)."""
    content: str


@dataclass(frozen=True)
class LLMToolCall:
    """Model requested a tool call."""
    tool: str
    params: dict


@dataclass(frozen=True)
class LLMToolResult:
    """Provider reported a tool execution result summary."""
    tool: str
    summary: str


@dataclass(frozen=True)
class LLMStatus:
    """Provider emitted a streaming status update."""
    state: str
    message: str


@dataclass(frozen=True)
class LLMDone:
    """Stream finished. Carries the aggregated result."""
    response: str
    tool_calls_pending: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    provider: str = "unknown"
    success: bool = True
    cancelled: bool = False
    error: str = ""


# Union type for type-checkers
LLMEvent = LLMToken | LLMThinking | LLMToolCall | LLMToolResult | LLMStatus | LLMDone


def _resolve_ollama_stream():
    """Prefer the aria_cli rebound stream_ollama when available."""
    import sys

    aria_cli = sys.modules.get("aria_cli")
    rebound = getattr(aria_cli, "stream_ollama", None) if aria_cli else None
    if callable(rebound):
        return rebound
    from apps.cli.providers.llm.ollama_stream import stream_ollama

    return stream_ollama


async def _stream_callback_provider(invoke, *, done_provider: str) -> AsyncGenerator[LLMEvent, None]:
    """Convert a callback-based provider coroutine into a real async event stream."""

    queue: asyncio.Queue[LLMEvent] = asyncio.Queue()

    def _on_token(tok: str) -> None:
        queue.put_nowait(LLMToken(text=tok))

    def _on_thinking(content: str) -> None:
        queue.put_nowait(LLMThinking(content=content))

    def _on_tool_call(tool: str, params: dict) -> None:
        queue.put_nowait(LLMToolCall(tool=tool, params=params))

    def _on_tool_result(tool: str, summary: str) -> None:
        queue.put_nowait(LLMToolResult(tool=tool, summary=summary))

    def _on_status(state: str, message: str) -> None:
        queue.put_nowait(LLMStatus(state=state, message=message))

    task = asyncio.create_task(
        invoke(_on_token, _on_thinking, _on_tool_call, _on_tool_result, _on_status)
    )
    while not task.done() or not queue.empty():
        try:
            yield await asyncio.wait_for(queue.get(), timeout=0.05)
        except asyncio.TimeoutError:
            continue

    try:
        result = await task
    except Exception as exc:
        yield LLMDone(response="", provider=done_provider, success=False, error=str(exc))
        return

    yield LLMDone(
        response=result.get("response", ""),
        tool_calls_pending=result.get("tool_calls_pending", []),
        usage=result.get("usage", {}),
        provider=result.get("provider", done_provider),
        success=result.get("success", False),
        cancelled=result.get("cancelled", False),
        error=result.get("error", ""),
    )


# ── Protocol ─────────────────────────────────────────────────────────────────

@runtime_checkable
class LLMProvider(Protocol):
    """Minimal interface every LLM backend must implement.

    ``stream()`` is an async generator that yields ``LLMEvent`` objects.
    The final event is always ``LLMDone``; callers may break early on
    ``LLMDone`` or consume the full stream.

    Parameters
    ----------
    messages:
        Full conversation history (list of {"role": …, "content": …} dicts).
    tools:
        OpenAI-format function schema list; empty list disables tool calls.
    cancel_event:
        asyncio.Event that, when set, signals the provider to stop.
    """

    async def stream(
        self,
        messages: list,
        tools: list,
        *,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[LLMEvent, None]:
        ...


# ── Thin adapters (wrap existing callables as LLMProvider) ───────────────────

class OllamaProvider:
    """Wraps ``stream_ollama`` as an ``LLMProvider``.

    Import lazily to avoid circular dependencies — ``stream_ollama`` lives in
    the same providers package and rebinds globals from aria_cli at startup.
    """

    def __init__(
        self,
        ollama_url: str,
        model: str,
        *,
        system_override: Optional[str] = None,
        show_market_prefetch_status: bool = True,
    ) -> None:
        self.ollama_url = ollama_url
        self.model = model
        self.system_override = system_override
        self.show_market_prefetch_status = show_market_prefetch_status

    async def stream(
        self,
        messages: list,
        tools: list,
        *,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[LLMEvent, None]:
        stream_ollama = _resolve_ollama_stream()

        # Extract last user message as the prompt; the rest is history
        history = [m for m in messages if not (m.get("role") == "user" and m is messages[-1])]
        prompt = messages[-1].get("content", "") if messages else ""

        async def _invoke(on_token, on_thinking, on_tool_call, on_tool_result, _on_status):
            return await stream_ollama(
                self.ollama_url,
                prompt,
                history,
                model=self.model,
                on_token=on_token,
                on_thinking=on_thinking,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                cancel_event=cancel_event,
                enable_tools=bool(tools),
                system_override=self.system_override,
                show_market_prefetch_status=self.show_market_prefetch_status,
            )

        async for event in _stream_callback_provider(_invoke, done_provider="ollama"):
            yield event


class AriaSSEProvider:
    """Wraps ``stream_chat`` (Aria cloud SSE) as an ``LLMProvider``."""

    def __init__(
        self,
        api_url: str,
        model: str,
        *,
        auth_token: Optional[str] = None,
        thinking_mode: str = "auto",
        user_context: Optional[dict] = None,
        system_override: Optional[str] = None,
        project_context: str = "",
    ) -> None:
        self.api_url = api_url
        self.model = model
        self.auth_token = auth_token
        self.thinking_mode = thinking_mode
        self.user_context = user_context or {}
        self.system_override = system_override
        self.project_context = project_context

    async def stream(
        self,
        messages: list,
        tools: list,
        *,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncGenerator[LLMEvent, None]:
        from apps.cli.providers.llm.sse_stream import stream_chat

        history = [m for m in messages if not (m.get("role") == "user" and m is messages[-1])]
        prompt = messages[-1].get("content", "") if messages else ""

        uctx = dict(self.user_context)
        if self.system_override:
            uctx["system_role_override"] = self.system_override

        async def _invoke(on_token, on_thinking, on_tool_call, on_tool_result, on_status):
            return await stream_chat(
                self.api_url,
                prompt,
                history,
                model=self.model,
                thinking_mode=self.thinking_mode,
                user_context=uctx or None,
                auth_token=self.auth_token,
                on_token=on_token,
                on_thinking=on_thinking,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                on_status=on_status,
                cancel_event=cancel_event,
                project_context=self.project_context,
            )

        async for event in _stream_callback_provider(_invoke, done_provider="aria_sse"):
            yield event
