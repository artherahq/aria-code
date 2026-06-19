"""Structured task tracking for the Aria agent loop (Claude Code TodoWrite parity).

The model calls the ``update_todos`` tool with the full current task list each
time progress changes. We keep the latest list in a module-global so the
renderer (and any UI surface) can show a live checklist of multi-step work.

Design notes
------------
* State is intentionally a process-global, mirroring how the screenshot tool
  stashes a pending image. Tool handlers only receive ``params``; they have no
  reference to the terminal, so a module global is the pragmatic channel.
* The list is replaced wholesale on every call (not merged) so the model owns
  the source of truth and we never drift out of sync with its plan.
"""
from __future__ import annotations

from typing import Any, Dict, List

_VALID_STATUS = ("pending", "in_progress", "completed")

# Latest task list the model published this turn.
_ACTIVE_TODOS: List[Dict[str, str]] = []


def get_active_todos() -> List[Dict[str, str]]:
    """Return a copy of the current task list."""
    return list(_ACTIVE_TODOS)


def clear_todos() -> None:
    """Reset the task list (call at the start of a new user turn)."""
    _ACTIVE_TODOS.clear()


def _normalize(todos: Any) -> List[Dict[str, str]]:
    """Coerce model-supplied todos into a clean list of {content, status}."""
    out: List[Dict[str, str]] = []
    if not isinstance(todos, list):
        return out
    for item in todos:
        if isinstance(item, str):
            content, status = item, "pending"
        elif isinstance(item, dict):
            content = str(
                item.get("content")
                or item.get("task")
                or item.get("title")
                or item.get("step")
                or ""
            ).strip()
            status = str(item.get("status", "pending")).strip().lower()
        else:
            continue
        if not content:
            continue
        if status not in _VALID_STATUS:
            # Accept a few common synonyms
            status = {
                "done": "completed", "complete": "completed", "finished": "completed",
                "doing": "in_progress", "active": "in_progress", "wip": "in_progress",
                "todo": "pending", "open": "pending", "not_started": "pending",
            }.get(status, "pending")
        out.append({"content": content, "status": status})
    return out


def update_todos(params: dict) -> dict:
    """Tool handler: replace the active task list with the model's latest plan."""
    todos = _normalize(params.get("todos", params.get("tasks", [])))
    if not todos:
        return {
            "success": False,
            "error": "update_todos 需要非空的 todos 数组，每项形如 "
                     "{\"content\": \"步骤描述\", \"status\": \"pending|in_progress|completed\"}",
        }

    # At most one in_progress is the convention — demote extras to pending order
    seen_in_progress = False
    for t in todos:
        if t["status"] == "in_progress":
            if seen_in_progress:
                t["status"] = "pending"
            else:
                seen_in_progress = True

    _ACTIVE_TODOS.clear()
    _ACTIVE_TODOS.extend(todos)

    _render(todos)

    done = sum(1 for t in todos if t["status"] == "completed")
    total = len(todos)
    return {
        "success": True,
        "data": {
            "total": total,
            "completed": done,
            "in_progress": sum(1 for t in todos if t["status"] == "in_progress"),
            "pending": sum(1 for t in todos if t["status"] == "pending"),
            "todos": todos,
        },
        # Compact text the model reads back so it knows the tracked state
        "summary": f"任务进度 {done}/{total} 已完成",
    }


def _render(todos: List[Dict[str, str]]) -> None:
    """Print the task list as a checklist. Uses rich when available."""
    try:
        import aria_cli as _ac
        console = getattr(_ac, "console", None)
        has_rich = getattr(_ac, "HAS_RICH", False)
    except Exception:
        console, has_rich = None, False

    _icons = {
        "completed":   ("[green]✓[/green]", "✓"),
        "in_progress": ("[yellow]▶[/yellow]", "▶"),
        "pending":     ("[dim]○[/dim]", "○"),
    }
    done = sum(1 for t in todos if t["status"] == "completed")
    total = len(todos)

    if has_rich and console is not None:
        from rich.panel import Panel
        from rich.text import Text
        body = Text()
        for i, t in enumerate(todos):
            icon_rich, _ = _icons.get(t["status"], _icons["pending"])
            style = (
                "green" if t["status"] == "completed"
                else "bold yellow" if t["status"] == "in_progress"
                else "dim"
            )
            line = Text.from_markup(f"{icon_rich} ")
            content = t["content"]
            if t["status"] == "completed":
                line.append(content, style="dim strike")
            else:
                line.append(content, style=style)
            body.append_text(line)
            if i < len(todos) - 1:
                body.append("\n")
        console.print(Panel(
            body,
            title=f"[bold]任务清单[/bold]  [dim]{done}/{total}[/dim]",
            border_style="cyan",
            padding=(0, 1),
        ))
    else:
        print(f"\n任务清单 ({done}/{total}):")
        for t in todos:
            _, icon_plain = _icons.get(t["status"], _icons["pending"])
            print(f"  {icon_plain} {t['content']}")


UPDATE_TODOS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "update_todos",
        "description": (
            "Track progress on a multi-step task as a live checklist. Call this when a task "
            "has 3+ distinct steps: first to lay out the plan (all pending), then again each "
            "time you start a step (mark it in_progress) or finish one (mark it completed). "
            "Keep exactly one step in_progress at a time. Always send the FULL list every call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "description": "The complete current task list (replaces the previous list).",
                    "items": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "Short step description"},
                            "status": {
                                "type": "string",
                                "enum": list(_VALID_STATUS),
                                "description": "pending | in_progress | completed",
                            },
                        },
                        "required": ["content", "status"],
                    },
                },
            },
            "required": ["todos"],
        },
    },
}
