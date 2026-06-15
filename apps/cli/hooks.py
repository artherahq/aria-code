"""Aria Code lifecycle hooks — JSON-configurable, shell-executable.

Config file: ~/.arthera/hooks.json
Project-local override: .aria/hooks.json  (takes precedence)

Schema
------
{
  "PreToolUse":   [{"tool": "run_command", "command": "echo $ARIA_TOOL"}],
  "PostToolUse":  [{"command": "notify-send 'done'"}],
  "ResponseDone": [{"command": "afplay /System/Sounds/Glass.aiff"}],
  "SessionStart": [{"command": "echo starting $ARIA_SESSION"}],
  "SessionEnd":   [{"command": "echo ended"}]
}

Each hook entry:
  command  — shell command to execute (required)
  tool     — if set, only fires when tool name matches (PreToolUse / PostToolUse)
  timeout  — seconds before the hook is killed (default 10)
  blocking — if true AND exit code != 0, execution is blocked (PreToolUse only)

Env vars injected into every hook:
  ARIA_EVENT        — hook event name
  ARIA_TOOL         — tool name (Pre/PostToolUse only)
  ARIA_TOOL_PARAMS  — JSON-encoded params (Pre/PostToolUse only)
  ARIA_RESULT       — JSON-encoded result (PostToolUse only)
  ARIA_RESPONSE     — first 500 chars of response (ResponseDone)
  ARIA_SESSION      — session ID
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path.home() / ".arthera"
_GLOBAL_HOOKS_FILE = _CONFIG_DIR / "hooks.json"
_LOCAL_HOOKS_FILE = Path.cwd() / ".aria" / "hooks.json"

_VALID_EVENTS = frozenset([
    "PreToolUse", "PostToolUse",
    "ResponseDone",
    "SessionStart", "SessionEnd",
])


def load_hooks() -> dict:
    """Load and merge global + project-local hooks config."""
    merged: dict[str, list] = {e: [] for e in _VALID_EVENTS}

    for path in (_GLOBAL_HOOKS_FILE, _LOCAL_HOOKS_FILE):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for event, entries in data.items():
                    if event in _VALID_EVENTS and isinstance(entries, list):
                        merged[event].extend(entries)
            except Exception as exc:
                logger.debug("hooks.json parse error (%s): %s", path, exc)

    return merged


def fire(
    event: str,
    *,
    tool: Optional[str] = None,
    params: Optional[dict] = None,
    result: Optional[dict] = None,
    response: str = "",
    session_id: str = "",
    hooks: Optional[dict] = None,
) -> bool:
    """Fire all matching hooks for an event.

    Returns False if any *blocking* PreToolUse hook exits non-zero
    (meaning the tool call should be suppressed). Returns True otherwise.
    """
    if hooks is None:
        hooks = load_hooks()

    entries = hooks.get(event, [])
    if not entries:
        return True

    base_env = dict(os.environ)
    base_env["ARIA_EVENT"] = event
    base_env["ARIA_SESSION"] = session_id or ""
    if tool:
        base_env["ARIA_TOOL"] = tool
    if params is not None:
        base_env["ARIA_TOOL_PARAMS"] = json.dumps(params, ensure_ascii=False)[:2000]
    if result is not None:
        base_env["ARIA_RESULT"] = json.dumps(result, ensure_ascii=False)[:2000]
    if response:
        base_env["ARIA_RESPONSE"] = response[:500]

    blocked = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        cmd = entry.get("command", "").strip()
        if not cmd:
            continue

        # Tool filter: skip if hook is scoped to a different tool
        if entry.get("tool") and tool and entry["tool"] != tool:
            continue

        timeout = int(entry.get("timeout", 10))
        is_blocking = bool(entry.get("blocking", False))

        try:
            proc = subprocess.run(
                cmd,
                shell=True,
                env=base_env,
                timeout=timeout,
                capture_output=True,
                text=True,
            )
            if is_blocking and proc.returncode != 0 and event == "PreToolUse":
                logger.info(
                    "Blocking hook vetoed tool '%s': exit %d — %s",
                    tool, proc.returncode, proc.stderr[:120],
                )
                blocked = True
        except subprocess.TimeoutExpired:
            logger.debug("Hook timed out after %ds: %s", timeout, cmd[:60])
        except Exception as exc:
            logger.debug("Hook error: %s", exc)

    return not blocked


def list_hooks() -> list[dict]:
    """Return a flat list of configured hooks for display."""
    hooks = load_hooks()
    rows = []
    for event, entries in hooks.items():
        for entry in entries:
            if isinstance(entry, dict) and entry.get("command"):
                rows.append({
                    "event": event,
                    "tool": entry.get("tool", "*"),
                    "command": entry["command"][:80],
                    "blocking": entry.get("blocking", False),
                    "timeout": entry.get("timeout", 10),
                })
    return rows


def hooks_file_path(scope: str = "global") -> Path:
    """Return the hooks.json path for a given scope."""
    if scope == "local":
        return Path.cwd() / ".aria" / "hooks.json"
    return _GLOBAL_HOOKS_FILE


def create_example_hooks(path: Path) -> None:
    """Write an annotated example hooks.json if it doesn't exist."""
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    example = {
        "_comment": "Aria Code hooks — https://aria.code/docs/hooks",
        "PreToolUse": [],
        "PostToolUse": [],
        "ResponseDone": [],
        "SessionStart": [],
        "SessionEnd": [],
    }
    path.write_text(json.dumps(example, indent=2, ensure_ascii=False), encoding="utf-8")
