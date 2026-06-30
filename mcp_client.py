"""
mcp_client.py — MCP (Model Context Protocol) client for Aria Code.

Connects to any MCP server via stdio transport and exposes its tools as
first-class LOCAL_TOOLS in the Aria Code tool loop.

Config file: ~/.arthera/mcp_servers.json
Example::

    {
      "servers": [
        {
          "name": "quant_engine",
          "command": "python3",
          "args": ["/path/to/mcp_server.py"],
          "env": {"PYTHONPATH": "/path/to/project"},
          "description": "Arthera quant engine MCP tools"
        },
        {
          "name": "filesystem",
          "command": "npx",
          "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/user/projects"],
          "description": "Filesystem access"
        }
      ]
    }

Usage::

    registry = MCPToolRegistry()
    await registry.start_all()
    registry.register_into(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)

    # Tools are now callable as regular LOCAL_TOOLS entries
    result = await registry.call_tool("quant_engine/backtest_strategy", {"symbol": "AAPL"})
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MCP_CONFIG_PATH = pathlib.Path.home() / ".arthera" / "mcp_servers.json"

# MCP JSON-RPC protocol version
MCP_PROTOCOL_VERSION = "2024-11-05"


def model_safe_tool_name(server_name: str, tool_name: str) -> str:
    """Return an OpenAI/Ollama-compatible qualified MCP function name."""
    server = re.sub(r"[^A-Za-z0-9_-]+", "_", str(server_name)).strip("_") or "server"
    tool = re.sub(r"[^A-Za-z0-9_-]+", "_", str(tool_name)).strip("_") or "tool"
    return f"mcp__{server}__{tool}"


# ---------------------------------------------------------------------------
# JSON-RPC helpers
# ---------------------------------------------------------------------------

def _make_request(method: str, params: Any = None, req_id: int = 1) -> bytes:
    msg = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode()


def _make_notification(method: str, params: Any = None) -> bytes:
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return (json.dumps(msg) + "\n").encode()


# ---------------------------------------------------------------------------
# MCPServer — one subprocess connection
# ---------------------------------------------------------------------------

class MCPServer:
    """
    Manages a single MCP server subprocess (stdio transport).

    Lifecycle: start() → list_tools() → call_tool(…) → stop()
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: List[str],
        env: Optional[Dict[str, str]] = None,
        description: str = "",
        timeout: float = 30.0,
    ):
        self.name        = name
        self.command     = command
        self.args        = args
        self.env         = env or {}
        self.description = description
        self.timeout     = timeout

        self._proc:    Optional[asyncio.subprocess.Process] = None
        self._req_id:  int  = 0
        self._pending: Dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._tools:   List[Dict[str, Any]] = []
        self._running  = False

    # ── internal ───────────────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    async def _send(self, data: bytes):
        if self._proc and self._proc.stdin:
            self._proc.stdin.write(data)
            await self._proc.stdin.drain()

    async def _reader_loop(self):
        """Background task: read lines from server stdout and dispatch to futures."""
        if not self._proc or not self._proc.stdout:
            return
        try:
            async for raw in self._proc.stdout:
                line = raw.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("[MCP:%s] Non-JSON: %s", self.name, line[:100])
                    continue

                req_id = msg.get("id")
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        if "error" in msg:
                            fut.set_exception(RuntimeError(str(msg["error"])))
                        else:
                            fut.set_result(msg.get("result"))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("[MCP:%s] Reader loop error: %s", self.name, exc)

    async def _request(self, method: str, params: Any = None) -> Any:
        rid = self._next_id()
        fut = asyncio.get_event_loop().create_future()
        self._pending[rid] = fut
        await self._send(_make_request(method, params, rid))
        try:
            return await asyncio.wait_for(fut, timeout=self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise TimeoutError(f"MCP {self.name}/{method} timed out after {self.timeout}s")

    # ── public API ─────────────────────────────────────────────────────────

    async def start(self) -> bool:
        """Start the server subprocess and perform MCP handshake."""
        if self._running:
            return True
        try:
            env = {**os.environ, **self.env}
            self._proc = await asyncio.create_subprocess_exec(
                self.command, *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            logger.warning("[MCP:%s] Command not found: %s", self.name, exc)
            return False
        except Exception as exc:
            logger.warning("[MCP:%s] Failed to start: %s", self.name, exc)
            return False

        self._reader_task = asyncio.create_task(self._reader_loop())

        # MCP initialize handshake
        try:
            await self._request("initialize", {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities":    {"tools": {}},
                "clientInfo":      {"name": "aria-code", "version": "3.0"},
            })
            await self._send(_make_notification("notifications/initialized"))
        except Exception as exc:
            logger.warning("[MCP:%s] Handshake failed: %s", self.name, exc)
            await self.stop()
            return False

        # Discover tools
        await self._refresh_tools()
        self._running = True
        logger.info("[MCP:%s] Started with %d tools", self.name, len(self._tools))
        return True

    async def _refresh_tools(self):
        try:
            result   = await self._request("tools/list")
            self._tools = result.get("tools", []) if result else []
        except Exception as exc:
            logger.debug("[MCP:%s] tools/list failed: %s", self.name, exc)
            self._tools = []

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return self._tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Call a tool on this server.  Returns {"success": bool, "result": …}."""
        if not self._running:
            return {"success": False, "error": f"MCP server {self.name!r} not running"}
        # Strip server prefix if present (e.g. "quant_engine/backtest_strategy")
        short_name = tool_name.split("/")[-1]
        try:
            result = await self._request("tools/call", {
                "name":      short_name,
                "arguments": arguments,
            })
            if result is None:
                return {"success": True, "result": None}
            # MCP returns {content: [{type, text}]} or plain value
            content = result.get("content", result)
            if isinstance(content, list):
                parts = [c.get("text", "") if isinstance(c, dict) else str(c) for c in content]
                text  = "\n".join(parts)
                try:
                    parsed = json.loads(text)
                    return {"success": True, **parsed} if isinstance(parsed, dict) else {"success": True, "result": parsed}
                except Exception:
                    return {"success": True, "result": text}
            return {"success": True, "result": content}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def stop(self):
        self._running = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await asyncio.wait_for(self._reader_task, timeout=1.0)
            except Exception:
                pass
            self._reader_task = None
        if self._proc:
            try:
                self._proc.stdin.close()
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


# ---------------------------------------------------------------------------
# MCPToolRegistry — manages multiple servers + tool registration
# ---------------------------------------------------------------------------

class MCPToolRegistry:
    """
    Loads MCP server config from ``~/.arthera/mcp_servers.json``, starts
    each server, discovers tools, and registers them in the CLI's tool loop.

    Internal naming convention: ``{server_name}/{tool_name}``.
    Model-facing schemas use ``mcp__{server_name}__{tool_name}`` because tool
    APIs reject slashes in function names.
    This avoids collisions when two servers expose a tool with the same name.
    """

    def __init__(self, config_path: pathlib.Path = MCP_CONFIG_PATH):
        self.config_path = config_path
        self._servers:   Dict[str, MCPServer] = {}
        self._tool_map:  Dict[str, Tuple[str, str]] = {}  # qualified_name → (server_name, tool_name)
        self._loaded     = False
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None

    # ── config ─────────────────────────────────────────────────────────────

    def load_config(self) -> List[Dict[str, Any]]:
        if not self.config_path.exists():
            return []
        try:
            with open(self.config_path) as f:
                data = json.load(f)
            return data.get("servers", [])
        except Exception as exc:
            logger.warning("MCP config load failed: %s", exc)
            return []

    def save_example_config(self):
        """Write an example mcp_servers.json if none exists."""
        if self.config_path.exists():
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        example = {
            "servers": [
                {
                    "name":        "quant_engine",
                    "command":     "python3",
                    "args":        ["path/to/mcp_server.py"],
                    "env":         {"PYTHONPATH": "path/to/project"},
                    "description": "Your Arthera quant engine (backtest, signals, factors)"
                }
            ]
        }
        with open(self.config_path, "w") as f:
            json.dump(example, f, indent=2, ensure_ascii=False)

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start_all(self) -> Dict[str, bool]:
        """Start all configured MCP servers.  Returns {name: started_ok}."""
        self._event_loop = asyncio.get_running_loop()
        server_configs = self.load_config()
        results = {}
        for cfg in server_configs:
            name    = cfg.get("name", "unnamed")
            command = cfg.get("command", "")
            args    = cfg.get("args", [])
            env     = cfg.get("env", {})
            desc    = cfg.get("description", "")
            if not command:
                continue
            if not cfg.get("enabled", True):
                logger.debug("MCP server %r disabled in config, skipping", name)
                continue
            # expand ${VAR} placeholders in args and env values
            def _expand(s: str) -> str:
                import re as _re
                return _re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), s)
            args = [_expand(a) for a in args]
            env  = {k: _expand(v) for k, v in env.items() if v}
            srv = MCPServer(name=name, command=command, args=args, env=env, description=desc)
            ok  = await srv.start()
            if ok:
                self._servers[name] = srv
                for tool in srv.tools:
                    qualified = f"{name}/{tool['name']}"
                    self._tool_map[qualified] = (name, tool["name"])
            results[name] = ok
        self._loaded = True
        return results

    async def stop_all(self):
        for srv in self._servers.values():
            await srv.stop()
        self._servers.clear()
        self._tool_map.clear()
        self._loaded = False
        self._event_loop = None

    # ── tool info ──────────────────────────────────────────────────────────

    def all_tools(self) -> List[Dict[str, Any]]:
        """Return all discovered tools with qualified names."""
        result = []
        for srv_name, srv in self._servers.items():
            for tool in srv.tools:
                t = dict(tool)
                t["qualified_name"] = f"{srv_name}/{tool['name']}"
                t["server"] = srv_name
                result.append(t)
        return result

    # ── call ───────────────────────────────────────────────────────────────

    async def call_tool(self, qualified_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if "/" in qualified_name:
            srv_name, tool_name = qualified_name.split("/", 1)
        else:
            # Try to find by short name
            srv_name, tool_name = self._resolve_short_name(qualified_name)

        srv = self._servers.get(srv_name)
        if srv is None:
            return {"success": False, "error": f"MCP server {srv_name!r} not found"}
        return await srv.call_tool(tool_name, arguments)

    def _resolve_short_name(self, name: str) -> Tuple[str, str]:
        for qname, (srv, tool) in self._tool_map.items():
            if tool == name:
                return srv, tool
        return "unknown", name

    # ── registration ───────────────────────────────────────────────────────

    def register_into(
        self,
        tool_registry: Dict,
        schema_registry: List,
        overwrite: bool = False,
    ) -> int:
        """
        Add all MCP tools into the CLI's LOCAL_TOOLS and LOCAL_TOOL_SCHEMAS.

        Model-facing tool names use ``mcp__server__tool`` while handlers retain
        the MCP server's internal ``server/tool`` qualified name.

        Returns number of tools registered.
        """
        import asyncio as _asyncio

        added = 0
        existing_names = set(tool_registry.keys())

        for srv_name, srv in self._servers.items():
            for tool in srv.tools:
                qname = f"{srv_name}/{tool['name']}"
                model_name = model_safe_tool_name(srv_name, tool["name"])
                if model_name in existing_names and not overwrite:
                    continue

                # ToolExecutor invokes synchronous handlers in a worker thread.
                # Route the coroutine back to the event loop that owns the MCP
                # subprocess streams; asyncio.run() in the worker creates a
                # different loop and fails with cross-loop Future errors.
                def _make_sync_handler(qn: str) -> Callable:
                    def _handler(params: dict) -> dict:
                        try:
                            current_loop = _asyncio.get_running_loop()
                        except RuntimeError:
                            current_loop = None
                        owner_loop = self._event_loop
                        if owner_loop and owner_loop.is_running():
                            if current_loop is owner_loop:
                                return {
                                    "success": False,
                                    "error": "MCP sync handler called on its owner event loop; use ToolExecutor.execute",
                                }
                            future = _asyncio.run_coroutine_threadsafe(
                                self.call_tool(qn, params), owner_loop
                            )
                            return future.result(timeout=60)
                        return _asyncio.run(self.call_tool(qn, params))
                    return _handler

                tool_registry[model_name] = (
                    _make_sync_handler(qname),
                    tool.get("description", f"MCP tool from {srv_name}"),
                )
                existing_names.add(model_name)
                added += 1

                # Build schema
                input_schema = tool.get("inputSchema") or tool.get("parameters") or {
                    "type": "object", "properties": {}, "required": []
                }
                schema_registry.append({
                    "type": "function",
                    "function": {
                        "name":        model_name,
                        "description": f"[{srv_name}] {tool.get('description', '')}",
                        "parameters":  input_schema,
                    },
                })

        return added

    # ── async call tool (for use inside async context) ─────────────────────

    async def call_tool_async(
        self,
        qualified_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        return await self.call_tool(qualified_name, arguments)

    # ── status ─────────────────────────────────────────────────────────────

    def status(self) -> List[Dict[str, Any]]:
        return [
            {
                "name":        name,
                "running":     srv._running,
                "tool_count":  len(srv.tools),
                "description": srv.description,
                "tools":       [t["name"] for t in srv.tools],
            }
            for name, srv in self._servers.items()
        ]


# ---------------------------------------------------------------------------
# Module-level singleton (lazy-initialized)
# ---------------------------------------------------------------------------

_registry: Optional[MCPToolRegistry] = None


def get_registry() -> MCPToolRegistry:
    global _registry
    if _registry is None:
        _registry = MCPToolRegistry()
    return _registry


async def init_mcp(tool_registry: Dict, schema_registry: List) -> Dict[str, bool]:
    """
    Convenience function: load config, start all servers, register tools.
    Call once at startup.

    Returns {server_name: started_ok} dict.
    """
    reg     = get_registry()
    results = await reg.start_all()
    n       = reg.register_into(tool_registry, schema_registry)
    if n > 0:
        logger.info("MCP: registered %d tools from %d servers", n, len(results))
    return results
