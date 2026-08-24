"""Tool execution layer for Aria Code runtimes."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from .events import RuntimeTrace, ToolCallRecord

ToolHandler = Callable[[dict], dict]
RemoteExecutor = Callable[[str, dict], Awaitable[dict]]
Hook = Callable[[str, str, dict, Optional[dict]], None]
ExecutionContextProvider = Callable[[], Mapping[str, Any]]


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
        execution_context: ExecutionContextProvider | None = None,
    ) -> None:
        self.local_tools = local_tools
        self.remote_executor = remote_executor
        self.hook = hook
        self.trace = trace or RuntimeTrace()
        self.config = config or {}
        self.execution_context = execution_context

    def execute_local(self, tool_name: str, params: dict) -> dict:
        """Execute a local tool synchronously."""
        if tool_name not in self.local_tools:
            return {"success": False, "error": f"Unknown local tool: {tool_name}"}
        handler = self.local_tools[tool_name][0]
        params = self._prepare_params(
            tool_name,
            params,
            include_execution_context=True,
            bind_research_context=True,
        )
        context_error = params.pop("_execution_context_error", None)
        if context_error:
            result = {"success": False, "error": context_error}
            self.trace.emit("tool_denied", {"tool": tool_name, "reason": context_error})
            return result
        return self._call_with_trace(tool_name, params, lambda: handler(params))

    async def execute(self, tool_name: str, params: dict) -> dict:
        """Execute a tool asynchronously, using remote executor when needed."""
        if tool_name in self.local_tools:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self.execute_local, tool_name, params)
        if self.remote_executor is None:
            return {"success": False, "error": f"Unknown tool: {tool_name}"}
        params = self._prepare_params(tool_name, params, bind_research_context=True)
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

    def _prepare_params(
        self,
        tool_name: str,
        params: dict,
        *,
        include_execution_context: bool = False,
        bind_research_context: bool = False,
    ) -> dict:
        prepared = dict(params or {})
        if self.execution_context is not None and (
            include_execution_context or bind_research_context
        ):
            try:
                context = dict(self.execution_context() or {})
                if include_execution_context:
                    for key, value in context.items():
                        if str(key).startswith("_") and value is not None:
                            prepared.setdefault(str(key), value)
                    self._bind_workspace(tool_name, prepared, context)
                if bind_research_context:
                    self._bind_research_run(tool_name, prepared, context)
            except Exception:
                pass
        if tool_name == "run_command":
            prepared.setdefault("policy", self.config.get("command_policy", "safe"))
            prepared.setdefault("permission_mode", self.config.get("permission_mode", "workspace-write"))
            prepared.setdefault("network_enabled", bool(self.config.get("network_enabled", True)))
        return prepared

    @staticmethod
    def _bind_research_run(
        tool_name: str,
        prepared: dict,
        context: Mapping[str, Any],
    ) -> None:
        """Correlate an institutional research run with its upstream Aria run."""
        canonical_name = str(tool_name).rsplit("__", 1)[-1]
        if canonical_name != "research_run_create":
            return
        control_run_id = context.get("_run_id")
        trace_id = context.get("_trace_id") or control_run_id
        if control_run_id:
            prepared.setdefault("control_run_id", str(control_run_id))
        if trace_id:
            prepared.setdefault("trace_id", str(trace_id))

    @staticmethod
    def _bind_workspace(tool_name: str, prepared: dict, context: Mapping[str, Any]) -> None:
        workspace_value = context.get("_workspace")
        if not workspace_value:
            return
        workspace = Path(str(workspace_value)).expanduser().resolve()
        restricted = bool(context.get("_workspace_restricted", False))
        path_tools = {
            "read_file",
            "write_file",
            "edit_file",
            "multi_edit",
            "list_files",
            "search_code",
            "analyze_file",
            "notebook_read",
            "notebook_edit",
        }
        if tool_name in path_tools:
            raw_path = str(prepared.get("path") or ".")
            target = Path(raw_path).expanduser()
            if not target.is_absolute():
                target = workspace / target
            target = target.resolve()
            if restricted and not target.is_relative_to(workspace):
                prepared["_execution_context_error"] = (
                    f"Tool path is outside the isolated workspace: {target}"
                )
                return
            prepared["path"] = str(target)
        if tool_name in {"run_command", "github"}:
            raw_cwd = str(prepared.get("cwd") or workspace)
            cwd = Path(raw_cwd).expanduser()
            if not cwd.is_absolute():
                cwd = workspace / cwd
            cwd = cwd.resolve()
            if restricted and not cwd.is_relative_to(workspace):
                prepared["_execution_context_error"] = (
                    f"Command cwd is outside the isolated workspace: {cwd}"
                )
                return
            prepared["cwd"] = str(cwd)

    def _run_hook(self, hook_type: str, tool_name: str, params: dict, result: dict | None = None) -> None:
        if self.hook is None:
            return
        try:
            self.hook(hook_type, tool_name, params, result)
        except Exception:
            pass
