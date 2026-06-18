"""Agent-loop orchestration helpers for Aria Code.

This module intentionally starts with pure, easily-tested primitives. The CLI
still owns UI prompts and provider calls, while the runtime owns the mechanical
shape of tool batching and follow-up construction.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Awaitable, Callable, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple, Union

from .tool_executor import ToolExecutor


DEFAULT_SERIAL_TOOLS = {"write_file", "edit_file", "run_command"}

# Phrases the model uses to signal task completion
_DONE_PHRASES = frozenset([
    "task complete", "task is complete", "all done", "completed successfully",
    "here is the final", "here's the final", "analysis complete",
    "任务完成", "已完成", "分析完成", "操作完成", "已经完成", "以下是最终",
    "i have completed", "i've completed", "the task has been completed",
])


def detect_task_complete(response_text: str) -> bool:
    """Heuristic: did the AI signal task completion without requesting more tools?"""
    if not response_text:
        return False
    lower = response_text.lower()
    return any(phrase in lower for phrase in _DONE_PHRASES)


def split_tool_calls(
    pending: Sequence[dict],
    serial_tools: Iterable[str] = DEFAULT_SERIAL_TOOLS,
) -> Tuple[List[dict], List[dict]]:
    """Split tool calls into parallel-safe and serial batches.

    Beyond the static whitelist, detects data dependencies at runtime:
    if a later run_command references a file written/edited earlier in
    the same batch, it is moved to the serial queue so it runs after
    the write completes.
    """
    serial = set(serial_tools)
    parallel_batch: List[dict] = []
    serial_batch: List[dict] = []
    written_paths: set = set()

    for tc in pending:
        tool = tc.get("tool", "")
        params = tc.get("params", {})

        if tool in serial:
            # Track paths being written so dependents can detect the dependency
            for key in ("path", "file_path", "filename", "target"):
                p = params.get(key)
                if p:
                    written_paths.add(str(p))
            serial_batch.append(tc)
        elif tool == "run_command":
            cmd = str(params.get("command", ""))
            # If the command references a path currently being written → serial
            if written_paths and any(p in cmd for p in written_paths):
                serial_batch.append(tc)
            else:
                parallel_batch.append(tc)
        else:
            parallel_batch.append(tc)

    return parallel_batch, serial_batch


def collect_parallel_done(
    pending: Sequence[dict],
    parallel_results: Sequence[tuple],
    serial_tools: Iterable[str] = DEFAULT_SERIAL_TOOLS,
) -> Dict[int, dict]:
    """Map original pending indices to already-executed parallel results."""
    serial = set(serial_tools)
    done: Dict[int, dict] = {}
    for original_index, tool_call in enumerate(pending):
        if tool_call.get("tool") in serial:
            continue
        for result_tool_call, result in parallel_results:
            if result_tool_call is tool_call:
                done[original_index] = result
                break
    return done


RemoteToolRunner = Callable[[str, dict], Awaitable[dict]]
Hook = Callable[[str, str, dict, dict | None], None]
SummaryFormatter = Callable[[str, dict], str]


@dataclass
class AgentTurnState:
    """Mutable state accumulated across one agent response turn."""

    provider: str = "aws"
    total_response: str = ""
    tools_used: List[str] = field(default_factory=list)
    sources: List[dict] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "thinking_tokens": 0,
    })
    tool_time_total: float = 0.0

    def append_response(self, text: str | None) -> None:
        if text:
            self.total_response += text

    def apply_model_result(self, result: dict, fallback_response: str = "") -> None:
        self.append_response(result.get("response", fallback_response))
        self.tools_used.extend(result.get("tools_used", []))
        self.sources.extend(result.get("sources", []))
        self.provider = result.get("provider", self.provider)
        self.add_usage(result.get("usage", {}))

    def add_usage(self, usage: dict | None) -> None:
        if not usage:
            return
        self.usage["prompt_tokens"] += int(usage.get("prompt_tokens", 0) or 0)
        self.usage["completion_tokens"] += int(usage.get("completion_tokens", 0) or 0)
        self.usage["thinking_tokens"] += int(usage.get("thinking_tokens", 0) or 0)

    def add_tool_time(self, elapsed: float) -> None:
        self.tool_time_total += elapsed

    def reset_response(self) -> None:
        self.total_response = ""

    def final_text(self, fallback_response: str = "") -> str:
        return self.total_response or fallback_response

    def token_counts(self, *, token_count: int = 0, thinking_tokens: int = 0) -> Tuple[int, int, int, int]:
        prompt_t = self.usage.get("prompt_tokens", 0)
        completion_t = self.usage.get("completion_tokens", 0) or token_count
        think_t = self.usage.get("thinking_tokens", 0) or thinking_tokens
        return prompt_t, completion_t, think_t, prompt_t + completion_t + think_t

    def generation_time(self, elapsed: float) -> float:
        return elapsed - self.tool_time_total

    def unique_tools(self) -> List[str]:
        return list(dict.fromkeys(self.tools_used))

    def build_metadata(
        self,
        *,
        elapsed: float,
        token_count: int = 0,
        thinking_tokens: int = 0,
    ) -> "AgentTurnMetadata":
        prompt_t, completion_t, think_t, total_t = self.token_counts(
            token_count=token_count,
            thinking_tokens=thinking_tokens,
        )
        parts = [f"{elapsed:.1f}s"]
        gen_time = self.generation_time(elapsed)

        if total_t > 0:
            token_parts = []
            if prompt_t > 0:
                token_parts.append(f"in: {prompt_t:,}")
            if completion_t > 0:
                token_parts.append(f"out: {completion_t:,}")
            if think_t > 0:
                token_parts.append(f"think: {think_t:,}")
            parts.append(f"{total_t:,} tokens ({', '.join(token_parts)})")
            if completion_t > 0 and gen_time > 0.5:
                parts.append(f"{completion_t / gen_time:.0f} t/s")
        elif token_count > 0:
            parts.append(f"{token_count:,} tokens")
            if gen_time > 0.5:
                parts.append(f"{token_count / gen_time:.0f} t/s")

        if self.tool_time_total > 0:
            parts.append(f"tools: {self.tool_time_total:.1f}s")
        if self.provider != "aws":
            parts.append(self.provider)
        unique_tools = self.unique_tools()
        if unique_tools:
            parts.append(" ".join(unique_tools))

        # Turn-level cost — only for cloud providers with token data
        _is_cloud = self.provider not in ("ollama", "ollama_cache", "local", "")
        if _is_cloud and total_t > 0:
            _cost = (prompt_t * 0.14 + completion_t * 0.28 + think_t * 1.10) / 1_000_000
            if _cost >= 0.0001:
                parts.append(f"${_cost:.4f}")

        return AgentTurnMetadata(
            parts=parts,
            prompt_tokens=prompt_t,
            completion_tokens=completion_t,
            thinking_tokens=think_t,
            total_tokens=total_t,
            generation_time=gen_time,
            provider=self.provider,
            tools=unique_tools,
        )

    def build_result(
        self,
        *,
        elapsed: float,
        fallback_response: str = "",
        token_count: int = 0,
        thinking_tokens: int = 0,
        success: bool = True,
        cancelled: bool = False,
        error: str = "",
    ) -> "AgentTurnResult":
        metadata = self.build_metadata(
            elapsed=elapsed,
            token_count=token_count,
            thinking_tokens=thinking_tokens,
        )
        return AgentTurnResult(
            success=success,
            cancelled=cancelled,
            error=error,
            final_text=self.final_text(fallback_response),
            metadata=metadata,
            provider=metadata.provider,
            tools=metadata.tools,
            sources=list(self.sources),
        )

    def build_cancelled_result(
        self,
        *,
        elapsed: float,
        fallback_response: str = "",
        token_count: int = 0,
        thinking_tokens: int = 0,
    ) -> "AgentTurnResult":
        return self.build_result(
            elapsed=elapsed,
            fallback_response=fallback_response,
            token_count=token_count,
            thinking_tokens=thinking_tokens,
            success=True,
            cancelled=True,
        )

    def build_error_result(
        self,
        error: str | None,
        *,
        elapsed: float,
        fallback_response: str = "",
        token_count: int = 0,
        thinking_tokens: int = 0,
    ) -> "AgentTurnResult":
        return self.build_result(
            elapsed=elapsed,
            fallback_response=fallback_response,
            token_count=token_count,
            thinking_tokens=thinking_tokens,
            success=False,
            cancelled=False,
            error=error or "Unknown error",
        )


@dataclass(frozen=True)
class AgentTurnMetadata:
    """Display and accounting metadata for one completed agent turn."""

    parts: List[str]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    thinking_tokens: int = 0
    total_tokens: int = 0
    generation_time: float = 0.0
    provider: str = "aws"
    tools: List[str] = field(default_factory=list)

    def system_prompt_estimate(self, message: str) -> int:
        return max(0, self.prompt_tokens - len(message) // 3)


@dataclass(frozen=True)
class AgentTurnResult:
    """Structured result for a completed agent turn."""

    success: bool
    cancelled: bool
    error: str
    final_text: str
    metadata: AgentTurnMetadata
    provider: str = "aws"
    tools: List[str] = field(default_factory=list)
    sources: List[dict] = field(default_factory=list)

    @classmethod
    def cancelled_result(
        cls,
        *,
        metadata: AgentTurnMetadata | None = None,
        final_text: str = "",
    ) -> "AgentTurnResult":
        return cls(
            success=True,
            cancelled=True,
            error="",
            final_text=final_text,
            metadata=metadata or AgentTurnMetadata(parts=[]),
        )

    @classmethod
    def error_result(
        cls,
        error: str,
        *,
        metadata: AgentTurnMetadata | None = None,
        final_text: str = "",
    ) -> "AgentTurnResult":
        return cls(
            success=False,
            cancelled=False,
            error=error,
            final_text=final_text,
            metadata=metadata or AgentTurnMetadata(parts=[]),
        )

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "cancelled": self.cancelled,
            "error": self.error,
            "final_text": self.final_text,
            "provider": self.provider,
            "tools": list(self.tools),
            "sources": list(self.sources),
            "metadata": {
                "parts": list(self.metadata.parts),
                "prompt_tokens": self.metadata.prompt_tokens,
                "completion_tokens": self.metadata.completion_tokens,
                "thinking_tokens": self.metadata.thinking_tokens,
                "total_tokens": self.metadata.total_tokens,
                "generation_time": self.metadata.generation_time,
                "provider": self.metadata.provider,
                "tools": list(self.metadata.tools),
            },
        }

    def to_envelope(self) -> "AgentTurnEnvelope":
        return AgentTurnEnvelope.from_result(self)


@dataclass(frozen=True)
class AgentTurnEnvelope:
    """Stable runtime envelope for CLI/API consumers."""

    status: str
    success: bool
    cancelled: bool
    error: str
    final_text: str
    provider: str
    tools: List[str]
    summary: str
    metadata: dict

    @classmethod
    def from_result(cls, result: AgentTurnResult) -> "AgentTurnEnvelope":
        return cls(
            status="ok" if result.success else "error",
            success=result.success,
            cancelled=result.cancelled,
            error=result.error,
            final_text=result.final_text,
            provider=result.provider,
            tools=list(result.tools),
            summary=" · ".join(result.metadata.parts),
            metadata=result.to_dict()["metadata"],
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "success": self.success,
            "cancelled": self.cancelled,
            "error": self.error,
            "final_text": self.final_text,
            "provider": self.provider,
            "tools": list(self.tools),
            "summary": self.summary,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AgentErrorPresentation:
    """User-facing error presentation for model/agent failures."""

    error: str
    lines: List[str]
    level: str = "error"
    use_generic_error_prefix: bool = False

    @classmethod
    def from_error(cls, error: str | None) -> "AgentErrorPresentation":
        normalized = error or "Unknown error"
        if normalized in ("no_cloud_provider", "no_provider"):
            return cls(
                error=normalized,
                level="warning",
                lines=[
                    "没有可用的 AI 模型",
                    "  Ollama 未运行，且未配置云端 API Key。",
                    "  解决方案（任选其一）：",
                    "    • 启动 Ollama:  ollama serve",
                    "    • 配置云端 Key: /apikey set deepseek <your-key>",
                    "    • 导出环境变量: export DEEPSEEK_API_KEY=sk-...",
                ],
            )
        if normalized == "all_providers_failed":
            return cls(
                error=normalized,
                level="warning",
                lines=["所有云端 Provider 均请求失败，请检查网络或 API Key 是否有效。"],
            )
        return cls(
            error=normalized,
            level="error",
            lines=[f"Error: {normalized}"],
            use_generic_error_prefix=True,
        )


@dataclass
class ToolBatchState:
    """Mutable state for one model-requested batch of tool calls."""

    tool_results: List[dict] = field(default_factory=list)
    elapsed_total: float = 0.0
    cancelled: bool = False

    def add_result(
        self,
        tool_name: str,
        result: dict,
        formatter: SummaryFormatter,
        *,
        elapsed: float = 0.0,
    ) -> dict:
        self.elapsed_total += elapsed
        return record_tool_result(self.tool_results, tool_name, result, formatter)

    def cancel(self) -> None:
        self.cancelled = True

    def build_next_turn(self, total_response: str) -> Tuple[dict, dict, str]:
        return build_next_turn_messages(total_response, self.tool_results)


@dataclass(frozen=True)
class ToolCallTask:
    """One ordered tool call in a model-requested turn."""

    index: int
    tool_call: dict
    parallel_result: dict | None = None

    @property
    def tool_name(self) -> str:
        return self.tool_call.get("tool", "")

    @property
    def params(self) -> dict:
        return self.tool_call.get("params", {})

    @property
    def has_parallel_result(self) -> bool:
        return self.parallel_result is not None

    def progress_label(self, total: int) -> str:
        if total > 1:
            return f"  [{self.index + 1}/{total}] Running {self.tool_name}..."
        return f"  Running {self.tool_name}..."


@dataclass
class ToolTurnPlan:
    """Runtime plan for executing one pending tool-call turn."""

    pending: Sequence[dict]
    parallel_done: Dict[int, dict] = field(default_factory=dict)
    batch: ToolBatchState = field(default_factory=ToolBatchState)

    def tasks(self) -> List[ToolCallTask]:
        return [
            ToolCallTask(
                index=index,
                tool_call=tool_call,
                parallel_result=self.parallel_done.get(index),
            )
            for index, tool_call in enumerate(self.pending)
        ]


async def run_parallel_tools(
    pending: Sequence[dict],
    tool_executor: ToolExecutor,
    *,
    remote_runner: RemoteToolRunner | None = None,
    hook: Hook | None = None,
    serial_tools: Iterable[str] = DEFAULT_SERIAL_TOOLS,
) -> Dict[int, dict]:
    """Execute parallel-safe pending tools and return results by original index."""
    parallel_batch, _ = split_tool_calls(pending, serial_tools)

    async def _exec_one(tool_call: dict) -> tuple:
        tool_name = tool_call.get("tool", "")
        tool_params = tool_call.get("params", {})
        if tool_name in tool_executor.local_tools:
            result = await tool_executor.execute(tool_name, tool_params)
        elif remote_runner is not None:
            if hook is not None:
                hook("pre_tool", tool_name, tool_params, None)
            try:
                result = await remote_runner(tool_name, tool_params)
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
            if hook is not None:
                hook("post_tool", tool_name, tool_params, result)
        else:
            result = {"success": False, "error": f"Unknown tool: {tool_name}"}
        return tool_call, result

    parallel_results: List[tuple] = []
    if parallel_batch:
        gathered = await asyncio.gather(
            *[_exec_one(tool_call) for tool_call in parallel_batch],
            return_exceptions=True,
        )
        for item in gathered:
            if isinstance(item, Exception):
                parallel_results.append((None, {"success": False, "error": str(item)}))
            else:
                parallel_results.append(item)
    return collect_parallel_done(pending, parallel_results, serial_tools)


async def run_serial_tool(
    tool_name: str,
    tool_params: dict,
    tool_executor: ToolExecutor,
    *,
    remote_runner: RemoteToolRunner | None = None,
    hook: Hook | None = None,
) -> Tuple[dict, float]:
    """Execute one tool call and return (result, elapsed_seconds)."""
    started = time.time()
    if tool_name in tool_executor.local_tools:
        result = tool_executor.execute_local(tool_name, tool_params)
    elif remote_runner is not None:
        if hook is not None:
            hook("pre_tool", tool_name, tool_params, None)
        try:
            result = await remote_runner(tool_name, tool_params)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if hook is not None:
            hook("post_tool", tool_name, tool_params, result)
    else:
        result = {"success": False, "error": f"Unknown tool: {tool_name}"}
    return result, time.time() - started


def build_tool_followup(tool_results: Sequence[dict]) -> str:
    """Build a structured follow-up message from tool results.

    Each result block is labelled with its tool name and a success/error
    status so the model can clearly distinguish outcomes and respond
    appropriately to failures rather than silently ignoring them.
    """
    if not tool_results:
        return "No tool results. Continue with what you know or ask the user for clarification."

    blocks: List[str] = []
    error_tools: List[str] = []

    for item in tool_results:
        tool = item.get("tool", "unknown")
        result = item.get("result", "")
        result_str = str(result)

        is_error = (
            result_str.startswith("Error") or
            result_str.startswith("❌") or
            "error" in result_str[:80].lower() or
            "traceback" in result_str[:200].lower() or
            "exception" in result_str[:200].lower()
        )
        if is_error:
            error_tools.append(tool)
            blocks.append(f"[{tool}]: {result_str}\n\n### [{tool}] ❌ Error\n{result_str}")
        else:
            blocks.append(f"[{tool}]: {result_str}\n\n### [{tool}] ✓ Success\n{result_str}")

    followup = "## Tool Results\n\n" + "\n\n---\n\n".join(blocks)

    if error_tools:
        followup += (
            f"\n\n⚠ Tool(s) returned errors: {', '.join(error_tools)}. "
            "Read the error carefully. Options: (1) use read_file / search_code to diagnose, "
            "(2) use edit_file to fix the issue and retry run_command, "
            "(3) try a different approach. "
            "Do NOT give up silently — explain what failed and what you tried."
        )
    else:
        followup += (
            "\n\nAll tools completed successfully. "
            "If the task is now complete, provide your final response. "
            "If additional steps are needed, continue using tools.\n\n"
            "Please continue your analysis using these results."
        )

    return followup


def record_tool_result(
    tool_results: List[dict],
    tool_name: str,
    result: dict,
    formatter: SummaryFormatter,
) -> dict:
    """Append one tool result summary and return the appended record."""
    summary = formatter(tool_name, result)
    record = {"tool": tool_name, "result": summary}
    tool_results.append(record)
    return record


def build_next_turn_messages(total_response: str, tool_results: Sequence[dict]) -> Tuple[dict, dict, str]:
    """Build assistant/user messages and follow-up text for the next agent turn.

    When a screenshot tool stored an image in computer_use_tools._PENDING_VISION_IMAGE,
    the user message content becomes a multipart list so vision models can see the image.
    """
    followup = build_tool_followup(tool_results)
    assistant_message = {"role": "assistant", "content": total_response}

    # Check for a pending screenshot from computer_screenshot / browser_screenshot
    vision_b64: "str | None" = None
    try:
        from computer_use_tools import pop_pending_vision_image
        vision_b64 = pop_pending_vision_image()
    except ImportError:
        pass

    if vision_b64:
        user_content: "str | list" = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{vision_b64}"},
            },
            {"type": "text", "text": followup},
        ]
    else:
        user_content = followup

    user_message = {"role": "user", "content": user_content}
    return assistant_message, user_message, followup


# ── AgentEvent typed union ────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentEventToken:
    """A text token streamed from the model."""
    text: str


@dataclass(frozen=True)
class AgentEventThinking:
    """A thinking/reasoning token from extended-thinking models."""
    content: str


@dataclass(frozen=True)
class AgentEventToolCall:
    """Model requested a tool call (before execution)."""
    tool: str
    params: dict


@dataclass(frozen=True)
class AgentEventToolResult:
    """One tool has finished executing."""
    tool: str
    result: dict
    elapsed: float


@dataclass(frozen=True)
class AgentEventStatus:
    """Informational status change (e.g. provider fallback)."""
    state: str
    message: str


@dataclass(frozen=True)
class AgentEventComplete:
    """Agent loop finished normally. Carries the full turn result."""
    result: "AgentTurnResult"


@dataclass(frozen=True)
class AgentEventCancelled:
    """Agent loop was cancelled by the user."""
    partial_text: str


@dataclass(frozen=True)
class AgentEventError:
    """Agent loop encountered an unrecoverable error."""
    error: str


AgentEvent = Union[
    AgentEventToken,
    AgentEventThinking,
    AgentEventToolCall,
    AgentEventToolResult,
    AgentEventStatus,
    AgentEventComplete,
    AgentEventCancelled,
    AgentEventError,
]


# ── AgentOptions ──────────────────────────────────────────────────────────────

@dataclass
class AgentOptions:
    """Tunable parameters for one run_agent() invocation."""

    max_rounds: int = 30
    serial_tools: FrozenSet[str] = field(
        default_factory=lambda: frozenset(DEFAULT_SERIAL_TOOLS)
    )
    tool_schemas: List[dict] = field(default_factory=list)


# ── run_agent() ───────────────────────────────────────────────────────────────

async def run_agent(
    prompt: str,
    history: list,
    *,
    provider_fn: Callable,
    tool_executor: ToolExecutor,
    options: Optional["AgentOptions"] = None,
    remote_runner: Optional[RemoteToolRunner] = None,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    on_tool_call: Optional[Callable[[str, dict], None]] = None,
    on_tool_result: Optional[Callable[[str, dict], None]] = None,
    on_status: Optional[Callable[[str, str], None]] = None,
    hook: Optional[Hook] = None,
    cancel_event: Optional[asyncio.Event] = None,
    tool_result_formatter: Optional[SummaryFormatter] = None,
) -> AsyncGenerator["AgentEvent", None]:
    """Provider-agnostic multi-round agent loop.

    Yields ``AgentEvent`` objects so every caller (REPL, bot, API) can
    handle UI in its own way without duplicating round-management logic.

    Parameters
    ----------
    prompt:
        The user's message for this turn.
    history:
        Conversation history **before** the current prompt.
    provider_fn:
        Async callable ``(message, history, on_token, on_thinking,
        on_tool_call, cancel_event) -> dict``.  Must return the same
        result dict that ``stream_ollama`` / ``stream_chat`` return.
    tool_executor:
        Local tool registry.
    options:
        Tunable loop parameters (max_rounds, serial_tools, …).
    remote_runner:
        Optional async callable for tools not in ``tool_executor``.
    on_token / on_thinking / on_tool_call / on_tool_result / on_status:
        Pass-through callbacks forwarded to ``provider_fn`` so callers
        that already set up streaming callbacks don't need to change.
    hook:
        Pre/post-tool hook fired around each tool execution.
    cancel_event:
        asyncio.Event; when set the loop exits at the next safe point.
    tool_result_formatter:
        Formats a tool result dict into a summary string.  Defaults to
        ``str(result.get('output', result))``.
    """
    opts = options or AgentOptions()
    _formatter: SummaryFormatter = tool_result_formatter or (
        lambda _tool, res: str(res.get("output", res))
    )
    _serial = set(opts.serial_tools)

    turn_state = AgentTurnState(provider="unknown")
    start_time = time.time()
    current_message = prompt
    token_count = 0
    thinking_tokens = 0
    result: dict = {}

    for round_num in range(opts.max_rounds):
        # ── Provider call ────────────────────────────────────────────────────
        response_text = ""
        _round_tokens = 0

        def _wrap_on_token(tok: str) -> None:
            nonlocal response_text, token_count, _round_tokens
            response_text += tok
            _round_tokens += 1
            token_count += 1
            if on_token is not None:
                on_token(tok)

        def _wrap_on_thinking(content: str) -> None:
            nonlocal thinking_tokens
            thinking_tokens += 1
            if on_thinking is not None:
                on_thinking(content)

        def _wrap_on_tool_call(tool: str, params: dict) -> None:
            if on_tool_call is not None:
                on_tool_call(tool, params)

        try:
            result = await provider_fn(
                current_message,
                history if round_num == 0 else [],
                on_token=_wrap_on_token,
                on_thinking=_wrap_on_thinking,
                on_tool_call=_wrap_on_tool_call,
                on_tool_result=on_tool_result,
                on_status=on_status,
                cancel_event=cancel_event,
            )
        except Exception as exc:
            yield AgentEventError(error=str(exc))
            return

        if result.get("cancelled"):
            turn_state.append_response(response_text)
            yield AgentEventCancelled(partial_text=turn_state.total_response)
            return

        if not result.get("success"):
            yield AgentEventError(error=result.get("error", "Unknown error"))
            return

        turn_state.apply_model_result(result, response_text)

        pending = result.get("tool_calls_pending", [])
        if not pending:
            break

        # Warn caller on final round
        if round_num == opts.max_rounds - 1:
            yield AgentEventStatus(
                state="max_rounds",
                message=f"Max rounds ({opts.max_rounds}) reached",
            )
            break

        # ── Tool execution ───────────────────────────────────────────────────
        _parallel_done = await run_parallel_tools(
            pending,
            tool_executor,
            remote_runner=remote_runner,
            hook=hook,
            serial_tools=_serial,
        )
        tool_turn = ToolTurnPlan(pending=pending, parallel_done=_parallel_done)
        tool_batch = tool_turn.batch

        for task in tool_turn.tasks():
            if cancel_event and cancel_event.is_set():
                tool_batch.cancel()
                break

            tool_name = task.tool_name
            tool_params = task.params

            if task.has_parallel_result:
                tr = task.parallel_result
                tool_batch.add_result(tool_name, tr, _formatter)
                yield AgentEventToolResult(tool=tool_name, result=tr, elapsed=0.0)
                continue

            tr, tool_elapsed = await run_serial_tool(
                tool_name,
                tool_params,
                tool_executor,
                remote_runner=remote_runner,
                hook=hook,
            )
            tool_batch.add_result(tool_name, tr, _formatter, elapsed=tool_elapsed)
            yield AgentEventToolResult(tool=tool_name, result=tr, elapsed=tool_elapsed)

        turn_state.add_tool_time(tool_batch.elapsed_total)
        if tool_batch.cancelled:
            yield AgentEventCancelled(partial_text=turn_state.total_response)
            return

        assistant_msg, user_msg, followup = tool_batch.build_next_turn(
            turn_state.total_response
        )
        history = list(history) + [assistant_msg, user_msg]
        current_message = followup
        turn_state.reset_response()

    # ── Build final result ───────────────────────────────────────────────────
    elapsed = time.time() - start_time
    turn_result = turn_state.build_result(
        elapsed=elapsed,
        fallback_response=result.get("response", ""),
        token_count=token_count,
        thinking_tokens=thinking_tokens,
    )
    yield AgentEventComplete(result=turn_result)
