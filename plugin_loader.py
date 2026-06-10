"""
plugin_loader.py — Auto-discovery of custom tool plugins for Aria Code.

Scans the current directory and its parents for ``aria_tools.py`` files and
loads any tools they export.  This lets project-specific tools appear
automatically in the model's tool loop without modifying aria_cli.py.

Plugin contract (aria_tools.py)::

    # Minimal example
    def get_my_tools():
        return [
            {
                "name":        "my_custom_tool",
                "description": "Does something useful for this project",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input": {"type": "string", "description": "Input data"},
                    },
                    "required": ["input"],
                },
                "handler": lambda params: {"result": params["input"].upper()},
            }
        ]

    # Extended example with finance tools
    import akshare as ak

    def get_my_tools():
        def fetch_my_positions(params):
            # read from broker API / local file
            return {"positions": [...]}

        return [
            {
                "name":        "get_my_positions",
                "description": "Return current portfolio positions from local CSV",
                "parameters":  {"type": "object", "properties": {}, "required": []},
                "handler":     fetch_my_positions,
            },
        ]

Discovery order
---------------
1. ``./aria_tools.py``  (current working directory)
2. ``../aria_tools.py`` (one level up)
3. …up to $HOME

The first file found is used.  Set ``ARIA_TOOLS_PATH`` env var to override.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import pathlib
import sys
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Env override
ARIA_TOOLS_ENV = "ARIA_TOOLS_PATH"
PLUGIN_FILENAME = "aria_tools.py"


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def find_plugin_file(start_dir: Optional[str] = None) -> Optional[pathlib.Path]:
    """
    Walk upward from *start_dir* looking for aria_tools.py.
    Returns the first match, or None.
    """
    # Env var override takes priority
    env_path = os.getenv(ARIA_TOOLS_ENV)
    if env_path:
        p = pathlib.Path(env_path).expanduser().resolve()
        if p.exists():
            return p

    home    = pathlib.Path.home()
    current = pathlib.Path(start_dir or os.getcwd()).resolve()

    while True:
        candidate = current / PLUGIN_FILENAME
        if candidate.exists() and candidate.is_file():
            return candidate
        if current == home or current.parent == current:
            break
        current = current.parent
    return None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_plugin(plugin_path: pathlib.Path) -> List[Dict[str, Any]]:
    """
    Import *plugin_path* as a module and call its ``get_my_tools()`` function.

    Returns list of tool dicts with keys:
        name, description, parameters (JSON Schema), handler (callable)
    """
    try:
        spec   = importlib.util.spec_from_file_location("aria_tools_plugin", plugin_path)
        module = importlib.util.module_from_spec(spec)
        # Add plugin directory to sys.path so it can import local modules
        plugin_dir = str(plugin_path.parent)
        if plugin_dir not in sys.path:
            sys.path.insert(0, plugin_dir)
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning("Plugin load error (%s): %s", plugin_path, exc)
        logger.debug(traceback.format_exc())
        return []

    # Try standard function name first, then fallback names
    for fn_name in ("get_my_tools", "get_tools", "register_tools", "tools"):
        fn = getattr(module, fn_name, None)
        if callable(fn):
            try:
                tools = fn()
                if isinstance(tools, list):
                    logger.info("Plugin %s: loaded %d tools via %s()", plugin_path.name, len(tools), fn_name)
                    return _validate_tools(tools, plugin_path)
            except Exception as exc:
                logger.warning("Plugin %s: %s() raised: %s", plugin_path.name, fn_name, exc)
                continue
        # Also support module-level TOOLS list
        if fn_name == "tools" and isinstance(fn, list):
            return _validate_tools(fn, plugin_path)

    logger.warning("Plugin %s: no get_my_tools() / get_tools() function found", plugin_path.name)
    return []


def _validate_tools(raw: List[Any], plugin_path: pathlib.Path) -> List[Dict[str, Any]]:
    """Validate and normalise plugin tool definitions."""
    valid = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name    = item.get("name", "").strip()
        desc    = item.get("description", "")
        handler = item.get("handler") or item.get("fn") or item.get("function")
        params  = item.get("parameters") or item.get("params") or {
            "type": "object", "properties": {}, "required": []
        }
        if not name:
            logger.debug("Plugin tool missing 'name', skipping")
            continue
        if not callable(handler):
            logger.debug("Plugin tool %r has no callable handler, skipping", name)
            continue
        valid.append({
            "name":        name,
            "description": desc,
            "parameters":  params,
            "handler":     handler,
            "source":      str(plugin_path),
        })
    return valid


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_plugin_tools(
    tool_registry: Dict,
    schema_registry: List,
    start_dir: Optional[str] = None,
    overwrite: bool = False,
) -> Tuple[int, Optional[pathlib.Path]]:
    """
    Auto-discover and register plugin tools.

    Returns (count_registered, plugin_path_or_None).
    """
    plugin_path = find_plugin_file(start_dir)
    if plugin_path is None:
        return 0, None

    tools = load_plugin(plugin_path)
    if not tools:
        return 0, plugin_path

    added = 0
    existing_names = set(tool_registry.keys())
    existing_schema_names = {
        s.get("function", {}).get("name") for s in schema_registry
    }

    for tool in tools:
        name    = tool["name"]
        handler = tool["handler"]
        desc    = tool["description"]
        params  = tool["parameters"]

        if name in existing_names and not overwrite:
            logger.debug("Plugin tool %r skipped (already registered)", name)
            continue

        # Wrap handler with error guard
        def _safe_handler(p: dict, h: Callable = handler) -> dict:
            try:
                result = h(p)
                if not isinstance(result, dict):
                    result = {"result": result}
                return result
            except Exception as exc:
                return {"success": False, "error": str(exc), "source": "plugin"}

        tool_registry[name] = (_safe_handler, desc)
        added += 1

        if name not in existing_schema_names:
            schema_registry.append({
                "type": "function",
                "function": {
                    "name":        name,
                    "description": desc,
                    "parameters":  params,
                },
            })

    if added:
        logger.info("Plugin %s: registered %d tools", plugin_path.name, added)

    return added, plugin_path


# ---------------------------------------------------------------------------
# Hot-reload support
# ---------------------------------------------------------------------------

class PluginWatcher:
    """
    Watch the plugin file for changes and reload tools automatically.
    Uses simple mtime polling (no watchdog dependency needed).
    """

    def __init__(
        self,
        tool_registry: Dict,
        schema_registry: List,
        start_dir: Optional[str] = None,
        poll_interval: float = 3.0,
    ):
        self._tool_registry   = tool_registry
        self._schema_registry = schema_registry
        self._start_dir       = start_dir
        self._poll_interval   = poll_interval
        self._plugin_path:    Optional[pathlib.Path] = None
        self._last_mtime:     float = 0.0
        self._task:           Optional[Any] = None  # asyncio.Task
        self._plugin_tool_names: List[str] = []

    async def start(self):
        """Start the background polling task."""
        import asyncio
        # Initial load
        n, path = register_plugin_tools(
            self._tool_registry, self._schema_registry, self._start_dir
        )
        if path:
            self._plugin_path = path
            self._last_mtime  = path.stat().st_mtime
            self._plugin_tool_names = [
                t["name"] for t in load_plugin(path)
            ]
            if n:
                logger.info("PluginWatcher: initial load %d tools from %s", n, path.name)

        self._task = asyncio.create_task(self._watch_loop())

    async def _watch_loop(self):
        import asyncio
        while True:
            await asyncio.sleep(self._poll_interval)
            if self._plugin_path is None:
                # Try to find newly created plugin
                found = find_plugin_file(self._start_dir)
                if found:
                    self._plugin_path = found
                    self._last_mtime  = 0.0
            if self._plugin_path and self._plugin_path.exists():
                mtime = self._plugin_path.stat().st_mtime
                if mtime != self._last_mtime:
                    self._last_mtime = mtime
                    await self._reload()

    async def _reload(self):
        if not self._plugin_path:
            return
        # Remove previously registered plugin tools
        for name in self._plugin_tool_names:
            self._tool_registry.pop(name, None)
        # Remove from schema registry
        self._schema_registry[:] = [
            s for s in self._schema_registry
            if s.get("function", {}).get("name") not in self._plugin_tool_names
        ]
        # Re-register
        n, _ = register_plugin_tools(
            self._tool_registry, self._schema_registry,
            str(self._plugin_path.parent), overwrite=True
        )
        self._plugin_tool_names = [
            t["name"] for t in load_plugin(self._plugin_path)
        ]
        logger.info("PluginWatcher: hot-reloaded %d tools from %s", n, self._plugin_path.name)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                import asyncio
                await asyncio.wait_for(self._task, timeout=1.0)
            except Exception:
                pass
            self._task = None
