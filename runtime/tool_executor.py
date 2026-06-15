"""Tool execution layer for Aria Code runtimes."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from .events import RuntimeTrace, ToolCallRecord

ToolHandler = Callable[[dict], dict]
RemoteExecutor = Callable[[str, dict], Awaitable[dict]]
Hook = Callable[[str, str, dict, Optional[dict]], None]


class ToolExecutor:
    """Execute local/remote tools with hooks, policy injection, and trace records."""

    def __init__(
        self,
        local_tools: Mapping[str, tuple],
        *,
        remote_executor: RemoteExecutor | None = None,
        hook: Hook | None = None,
        trace: RuntimeTrace | None = None,
        config: Dict[str, Any] | None = None,
    ) -> None:
        self.local_tools = local_tools
        self.remote_executor = remote_executor
        self.hook = hook
        self.trace = trace or RuntimeTrace()
        self.config = config or {}

    def execute_local(self, tool_name: str, params: dict) -> dict:
        """Execute a local tool synchronously."""
        if tool_name not in self.local_tools:
            return {"success": False, "error": f"Unknown local tool: {tool_name}"}
        handler = self.local_tools[tool_name][0]
        params = self._prepare_params(tool_name, params)
        return self._call_with_trace(tool_name, params, lambda: handler(params))

    async def execute(self, tool_name: str, params: dict) -> dict:
        """Execute a tool asynchronously, using remote executor when needed."""
        if tool_name in self.local_tools:
            params = self._prepare_params(tool_name, params)
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.execute_local, tool_name, params)
        if self.remote_executor is None:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}
        params = self._prepare_params(tool_name, params)
        start = time.time()
        self._run_hook("pre_tool", tool_name, params)
        self.trace.emit("tool_call", {"tool": tool_name, "params": params})
        try:
            result = await self.remote_executor(tool_name, params)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        self._run_hook("post_tool", tool_name, params, result)
        end = time.time()
        self.trace.add_tool_call(ToolCallRecord(
            tool=tool_name,
            params=params,
            result=result,
            elapsed_ms=(end - start) * 1000,
            started_at=start,
            ended_at=end,
        ))
        return result

    def _call_with_trace(self, tool_name: str, params: dict, fn: Callable[[], dict]) -> dict:
        start = time.time()
        self._run_hook("pre_tool", tool_name, params)
        self.trace.emit("tool_call", {"tool": tool_name, "params": params})
        try:
            result = fn()
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        self._run_hook("post_tool", tool_name, params, result)
        end = time.time()
        self.trace.add_tool_call(ToolCallRecord(
            tool=tool_name,
            params=params,
            result=result,
            elapsed_ms=(end - start) * 1000,
            started_at=start,
            ended_at=end,
        ))
        return result

    def _prepare_params(self, tool_name: str, params: dict) -> dict:
        prepared = dict(params or {})
        if tool_name == "run_command":
            prepared.setdefault("policy", self.config.get("command_policy", "safe"))
            prepared.setdefault("permission_mode", self.config.get("permission_mode", "workspace-write"))
            prepared.setdefault("network_enabled", bool(self.config.get("network_enabled", True)))
        return prepared

    def _run_hook(self, hook_type: str, tool_name: str, params: dict, result: dict | None = None) -> None:
        if self.hook is None:
            return
        try:
            self.hook(hook_type, tool_name, params, result)
        except Exception:
            pass
