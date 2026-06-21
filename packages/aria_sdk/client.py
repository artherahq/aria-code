"""Embeddable Aria Agent SDK client.

The SDK owns the agent-facing event stream.  Terminal UI, Rich panels, and
interactive prompts remain CLI adapters layered above this package.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import replace
from typing import AsyncGenerator

from apps.cli.deterministic import run_deterministic_chain
from apps.cli.providers.base import (
    LLMDone,
    LLMThinking,
    LLMToken,
    LLMToolCall,
)
from runtime import (
    AgentEventCancelled,
    AgentEventComplete,
    AgentEventError,
    AgentEventStatus,
    AgentEventThinking,
    AgentEventToken,
    AgentEventToolCall,
    AgentEventToolResult,
    AgentOptions,
    ToolExecutor,
    run_agent,
)

from .providers import build_llm_provider
from .types import AriaAgentOptions, AriaMessage, AriaResult


class AriaSDKClient:
    """A reusable agent client that can be embedded outside the terminal CLI."""

    def __init__(
        self,
        options: AriaAgentOptions | None = None,
        *,
        session_id: str | None = None,
        tool_executor: ToolExecutor | None = None,
        remote_runner=None,
        tool_result_formatter=None,
    ) -> None:
        self.options = options or AriaAgentOptions()
        self.session_id = session_id or uuid.uuid4().hex
        self.messages: list[dict[str, str]] = []
        self.tool_executor = tool_executor
        self.remote_runner = remote_runner
        self.tool_result_formatter = tool_result_formatter

    async def query(
        self,
        prompt: str,
        *,
        history: list | None = None,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[AriaMessage, None]:
        """Yield SDK events for one agent turn."""

        active_history = list(history) if history is not None else list(self.messages)
        user_record = {"role": "user", "content": prompt}

        yield AriaMessage(
            kind="system",
            role="system",
            content="aria_sdk.turn_started",
            data={
                "session_id": self.session_id,
                "model": self.options.model,
                "provider": self.options.provider,
                "local_mode": self.options.local_mode,
                "cwd": self.options.cwd or os.getcwd(),
                "permission_mode": self.options.permission_mode,
            },
        )
        yield AriaMessage(kind="user", role="user", content=prompt)

        if self.options.deterministic:
            deterministic = run_deterministic_chain(
                prompt,
                model_has_tools=self.options.model_has_tools,
                history=active_history,
                has_brokers=self.options.has_brokers,
                get_broker_registry=self.options.get_broker_registry,
            )
            if deterministic.get("success"):
                content = str(deterministic.get("response", ""))
                self.messages.extend([user_record, {"role": "assistant", "content": content}])
                yield AriaMessage(
                    kind="assistant",
                    role="assistant",
                    content=content,
                    data={
                        "provider": "deterministic",
                        "tools_used": list(deterministic.get("tools_used") or []),
                        "raw": deterministic,
                    },
                )
                yield AriaMessage(
                    kind="result",
                    role="assistant",
                    content=content,
                    data={
                        "success": True,
                        "provider": "deterministic",
                        "session_id": self.session_id,
                        "tools_used": list(deterministic.get("tools_used") or []),
                    },
                )
                return

        if self.tool_executor is not None:
            async for event in self._run_agent(prompt, history=active_history, cancel_event=cancel_event):
                yield event
            return

        async for event in self._run_llm(prompt, history=active_history, cancel_event=cancel_event):
            yield event

    async def _provider_result(
        self,
        prompt: str,
        history: list,
        *,
        on_token=None,
        on_thinking=None,
        on_tool_call=None,
        on_tool_result=None,
        on_status=None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        selection = build_llm_provider(self.options)
        provider = selection.provider
        messages = list(history) + [{"role": "user", "content": prompt}]
        response_parts: list[str] = []
        tool_calls: list[dict] = []
        final = LLMDone(response="", provider=selection.name, success=True)

        async for llm_event in provider.stream(
            messages,
            list(self.options.tool_schemas),
            cancel_event=cancel_event,
        ):
            if isinstance(llm_event, LLMToken):
                response_parts.append(llm_event.text)
                if on_token:
                    on_token(llm_event.text)
            elif isinstance(llm_event, LLMThinking):
                if on_thinking:
                    on_thinking(llm_event.content)
            elif isinstance(llm_event, LLMToolCall):
                call = {"tool": llm_event.tool, "params": dict(llm_event.params)}
                tool_calls.append(call)
                if on_tool_call:
                    on_tool_call(llm_event.tool, dict(llm_event.params))
            elif isinstance(llm_event, LLMDone):
                final = llm_event

        response = final.response or "".join(response_parts)
        return {
            "success": final.success,
            "response": response,
            "provider": final.provider or selection.name,
            "tool_calls_pending": tool_calls or list(final.tool_calls_pending),
            "usage": dict(final.usage),
            "cancelled": final.cancelled,
            "error": final.error,
        }

    async def _run_agent(
        self,
        prompt: str,
        *,
        history: list,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[AriaMessage, None]:
        """Run the provider through the shared runtime tool loop."""

        if self.tool_executor is None:
            return

        final_text = ""
        async for event in run_agent(
            prompt,
            history,
            provider_fn=self._provider_result,
            tool_executor=self.tool_executor,
            options=AgentOptions(
                max_rounds=max(1, int(self.options.max_turns or 1)),
                tool_schemas=list(self.options.tool_schemas),
            ),
            remote_runner=self.remote_runner,
            cancel_event=cancel_event,
            tool_result_formatter=self.tool_result_formatter,
        ):
            if isinstance(event, AgentEventToken):
                yield AriaMessage(kind="token", role="assistant", content=event.text)
            elif isinstance(event, AgentEventThinking):
                yield AriaMessage(kind="thinking", role="assistant", content=event.content)
            elif isinstance(event, AgentEventToolCall):
                yield AriaMessage(
                    kind="tool_use",
                    role="assistant",
                    content=event.tool,
                    data={"tool": event.tool, "params": dict(event.params)},
                )
            elif isinstance(event, AgentEventToolResult):
                yield AriaMessage(
                    kind="tool_result",
                    role="tool",
                    content=event.tool,
                    data={
                        "tool": event.tool,
                        "result": dict(event.result),
                        "elapsed": event.elapsed,
                    },
                )
            elif isinstance(event, AgentEventStatus):
                yield AriaMessage(
                    kind="status",
                    role="system",
                    content=event.message,
                    data={"state": event.state},
                )
            elif isinstance(event, AgentEventCancelled):
                final_text = event.partial_text
                yield AriaMessage(
                    kind="result",
                    role="assistant",
                    content=final_text,
                    data={
                        "success": True,
                        "cancelled": True,
                        "provider": "runtime",
                        "session_id": self.session_id,
                    },
                )
            elif isinstance(event, AgentEventError):
                yield AriaMessage(
                    kind="result",
                    role="assistant",
                    content="",
                    data={
                        "success": False,
                        "provider": "runtime",
                        "session_id": self.session_id,
                        "error": event.error,
                    },
                )
            elif isinstance(event, AgentEventComplete):
                final_text = event.result.final_text
                if event.result.success:
                    self.messages.extend([
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": final_text},
                    ])
                yield AriaMessage(
                    kind="assistant",
                    role="assistant",
                    content=final_text,
                    data=event.result.to_dict(),
                )
                yield AriaMessage(
                    kind="result",
                    role="assistant",
                    content=final_text,
                    data={
                        "success": event.result.success,
                        "cancelled": event.result.cancelled,
                        "provider": event.result.provider,
                        "session_id": self.session_id,
                        "tools": list(event.result.tools),
                        "error": event.result.error,
                    },
                )

    async def _run_llm(
        self,
        prompt: str,
        *,
        history: list,
        cancel_event: asyncio.Event | None = None,
    ) -> AsyncGenerator[AriaMessage, None]:
        """Fallback to the configured LLM provider and normalize its events."""

        selection = build_llm_provider(self.options)
        provider = selection.provider
        messages = list(history) + [{"role": "user", "content": prompt}]
        token_parts: list[str] = []

        try:
            async for llm_event in provider.stream(messages, [], cancel_event=cancel_event):
                if isinstance(llm_event, LLMToken):
                    token_parts.append(llm_event.text)
                    yield AriaMessage(kind="token", role="assistant", content=llm_event.text)
                elif isinstance(llm_event, LLMThinking):
                    yield AriaMessage(kind="thinking", role="assistant", content=llm_event.content)
                elif isinstance(llm_event, LLMToolCall):
                    yield AriaMessage(
                        kind="tool_use",
                        role="assistant",
                        content=llm_event.tool,
                        data={"tool": llm_event.tool, "params": dict(llm_event.params)},
                    )
                elif isinstance(llm_event, LLMDone):
                    content = llm_event.response or "".join(token_parts)
                    if llm_event.success:
                        self.messages.extend([
                            {"role": "user", "content": prompt},
                            {"role": "assistant", "content": content},
                        ])
                    yield AriaMessage(
                        kind="assistant",
                        role="assistant",
                        content=content,
                        data={
                            "provider": llm_event.provider,
                            "usage": dict(llm_event.usage),
                            "success": llm_event.success,
                            "error": llm_event.error,
                        },
                    )
                    yield AriaMessage(
                        kind="result",
                        role="assistant",
                        content=content,
                        data={
                            "success": llm_event.success,
                            "provider": llm_event.provider,
                            "session_id": self.session_id,
                            "error": llm_event.error,
                        },
                    )
        except Exception as exc:
            yield AriaMessage(
                kind="result",
                role="assistant",
                content="",
                data={
                    "success": False,
                    "provider": selection.name,
                    "session_id": self.session_id,
                    "error": str(exc),
                },
            )


async def query(
    prompt: str,
    *,
    options: AriaAgentOptions | None = None,
    **overrides,
) -> AsyncGenerator[AriaMessage, None]:
    """Convenience async generator for one-off SDK calls."""

    resolved_options = options or AriaAgentOptions()
    if overrides:
        resolved_options = replace(resolved_options, **overrides)
    client = AriaSDKClient(resolved_options)
    async for event in client.query(prompt):
        yield event


async def run(
    prompt: str,
    *,
    options: AriaAgentOptions | None = None,
    **overrides,
) -> AriaResult:
    """Collect a one-off SDK query and return the final result."""

    final: AriaMessage | None = None
    async for event in query(prompt, options=options, **overrides):
        if event.kind == "result":
            final = event
    if final is None:
        return AriaResult(success=False, error="no_result")
    return AriaResult(
        success=bool(final.data.get("success")),
        content=final.content,
        provider=str(final.data.get("provider") or ""),
        session_id=str(final.data.get("session_id") or ""),
        error=str(final.data.get("error") or ""),
        data=dict(final.data),
    )


__all__ = [
    "AriaSDKClient",
    "query",
    "run",
]
