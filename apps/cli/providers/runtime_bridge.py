"""Phase-3 bridge: run one chat turn through the shared ``runtime.run_agent``.

The documented runtime next step is to route the CLI tool loop through
``run_agent`` and keep aria_cli as orchestration glue. This module is that engine
— the two adapters run_agent needs plus a thin driver:

  • build_tool_executor() — wraps the CLI's ``LOCAL_TOOLS`` ({name: (handler, schema)})
  • make_provider_fn()     — selects the provider (chat_routing) + streams it
  • run_chat_via_runtime() — drives run_agent, renders via callbacks, returns text

It is opt-in: ``send_message`` only uses it when ``config['use_runtime_loop']`` is
on, and falls back to the proven inline loop on any error. That keeps the live
path untouched until the runtime path is verified in a real REPL.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from .chat_routing import first_round_route


def build_tool_executor(local_tools, config: Optional[dict] = None):
    """Wrap the CLI's LOCAL_TOOLS registry for run_agent."""
    from runtime.tool_executor import ToolExecutor
    return ToolExecutor(local_tools, config=config or {})


def make_provider_fn(
    *,
    model: str,
    config: dict,
    api_url: Optional[str],
    ollama_url: str,
    tool_schemas: List[dict],
    thinking_mode: str = "auto",
    user_context: Optional[dict] = None,
    auth_token: Optional[str] = None,
    project_context: Any = None,
) -> Callable:
    """Build an async ``provider_fn`` for run_agent.

    Selects the provider per chat_routing (cloud → AriaSSE backend; ollama/skip →
    local Ollama) and streams it through the shared ``stream_provider_result``.
    """
    from apps.cli.providers.base import AriaSSEProvider, OllamaProvider
    from packages.aria_sdk.streaming import stream_provider_result

    async def _provider_fn(prompt, history, *, on_token=None, on_thinking=None,
                           on_tool_call=None, on_tool_result=None, on_status=None,
                           cancel_event=None):
        if first_round_route(model, config, api_url) == "cloud":
            provider = AriaSSEProvider(
                api_url, model, thinking_mode=thinking_mode,
                user_context=user_context, auth_token=auth_token,
                project_context=project_context,
            )
        else:  # 'ollama' or 'skip' → local Ollama
            provider = OllamaProvider(ollama_url, model)
        return await stream_provider_result(
            provider, prompt, history, tools=tool_schemas,
            cancel_event=cancel_event, on_token=on_token, on_thinking=on_thinking,
            on_tool_call=on_tool_call, on_tool_result=on_tool_result, on_status=on_status,
        )

    return _provider_fn


async def run_chat_via_runtime(
    *,
    prompt: str,
    history: list,
    local_tools,
    tool_schemas: List[dict],
    model: str,
    config: dict,
    api_url: Optional[str],
    ollama_url: str,
    cancel_event=None,
    on_token: Optional[Callable[[str], None]] = None,
    on_tool_call: Optional[Callable[[str, dict], None]] = None,
    on_tool_result: Optional[Callable[[str, dict], None]] = None,
    on_status: Optional[Callable[[str, str], None]] = None,
    thinking_mode: str = "auto",
    user_context: Optional[dict] = None,
    auth_token: Optional[str] = None,
    project_context: Any = None,
    max_rounds: int = 30,
) -> str:
    """Run one turn through run_agent; render via the callbacks; return the text.

    Tokens are accumulated from the event stream (no dependence on the result
    object's field names). Returns the assistant response text ("" if none).
    """
    from runtime import AgentOptions, run_agent
    from runtime.agent_loop import (
        AgentEventError, AgentEventStatus, AgentEventToken,
        AgentEventToolCall, AgentEventToolResult,
    )

    provider_fn = make_provider_fn(
        model=model, config=config, api_url=api_url, ollama_url=ollama_url,
        tool_schemas=tool_schemas, thinking_mode=thinking_mode,
        user_context=user_context, auth_token=auth_token, project_context=project_context,
    )
    executor = build_tool_executor(local_tools, config)

    text = ""
    async for ev in run_agent(
        prompt, history, provider_fn=provider_fn, tool_executor=executor,
        options=AgentOptions(max_rounds=max_rounds, tool_schemas=list(tool_schemas)),
        cancel_event=cancel_event,
    ):
        if isinstance(ev, AgentEventToken):
            text += ev.text
            if on_token:
                on_token(ev.text)
        elif isinstance(ev, AgentEventToolCall):
            if on_tool_call:
                on_tool_call(ev.tool, dict(ev.params))
        elif isinstance(ev, AgentEventToolResult):
            if on_tool_result:
                on_tool_result(ev.tool, dict(ev.result))
        elif isinstance(ev, AgentEventStatus):
            if on_status:
                on_status(getattr(ev, "phase", "") or "", ev.message)
        elif isinstance(ev, AgentEventError):
            break
    return text
