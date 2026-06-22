"""Background subagent task system.

Allows the main agent to spawn independent sub-tasks that run concurrently.
Tasks are tracked in memory and optionally persisted to ~/.arthera/tasks/.

Tool functions exposed to the LLM:
    spawn_task(prompt, context?)  → {"task_id": "abc123", "status": "pending"}
    task_status(task_id)          → {"status": "running|done|failed", ...}
    task_result(task_id)          → {"result": "...", "success": bool}
    task_list()                   → [{"task_id": ..., "status": ...}, ...]
    task_cancel(task_id)          → {"cancelled": bool}

Background execution is wired up in aria_cli.py via _subagent_runner().
If no runner is registered, spawn_task stores the task for manual execution.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

_TASKS: Dict[str, "SubagentTask"] = {}
_RUNNER: Optional[Callable] = None  # set by aria_cli.py


@dataclass
class SubagentTask:
    task_id: str
    prompt: str
    context: str = ""
    status: str = "pending"   # pending | running | done | failed | cancelled
    result: str = ""
    error: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0

    def age_str(self) -> str:
        elapsed = time.time() - self.created_at
        if elapsed < 60:
            return f"{int(elapsed)}s"
        if elapsed < 3600:
            return f"{elapsed/60:.1f}m"
        return f"{elapsed/3600:.1f}h"

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "prompt": self.prompt[:200] + ("…" if len(self.prompt) > 200 else ""),
            "age": self.age_str(),
        }


def register_runner(runner: Callable) -> None:
    """Register the async runner function from aria_cli.py."""
    global _RUNNER
    _RUNNER = runner


# ── Tool functions ─────────────────────────────────────────────────────────────

def tool_spawn_task(params: dict) -> dict:
    """Spawn an independent background agent task."""
    prompt = params.get("prompt", "")
    context = params.get("context", "")
    if not prompt:
        return {"success": False, "error": "Missing 'prompt'"}

    task_id = uuid.uuid4().hex[:8]
    task = SubagentTask(task_id=task_id, prompt=prompt, context=context)
    _TASKS[task_id] = task

    if _RUNNER is not None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(_run_background(task))
            else:
                loop.run_until_complete(_run_background(task))
        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
    else:
        # No runner — task stays in "pending" for manual execution
        task.status = "pending"

    return {
        "success": True,
        "task_id": task_id,
        "status": task.status,
        "message": (
            f"Task {task_id} spawned. Use task_status('{task_id}') to check progress."
            if task.status == "running" else
            f"Task {task_id} queued (no runner registered). Check /tasks."
        ),
    }


async def _run_background(task: SubagentTask) -> None:
    task.status = "running"
    try:
        full_prompt = task.prompt
        if task.context:
            full_prompt = f"{task.context}\n\n{task.prompt}"
        result_text = await _RUNNER(full_prompt)
        task.result = result_text or ""
        task.status = "done"
    except asyncio.CancelledError:
        task.status = "cancelled"
    except Exception as exc:
        task.error = str(exc)
        task.status = "failed"
    finally:
        task.completed_at = time.time()


def tool_task_status(params: dict) -> dict:
    """Check the status of a background task."""
    task_id = params.get("task_id", "")
    if not task_id:
        return {"success": False, "error": "Missing 'task_id'"}
    task = _TASKS.get(task_id)
    if not task:
        return {"success": False, "error": f"Task '{task_id}' not found"}
    return {
        "success": True,
        "task_id": task_id,
        "status": task.status,
        "age": task.age_str(),
        "prompt_preview": task.prompt[:100],
        "error": task.error or None,
    }


def tool_task_result(params: dict) -> dict:
    """Retrieve the result of a completed background task."""
    task_id = params.get("task_id", "")
    if not task_id:
        return {"success": False, "error": "Missing 'task_id'"}
    task = _TASKS.get(task_id)
    if not task:
        return {"success": False, "error": f"Task '{task_id}' not found"}
    if task.status == "running":
        return {"success": False, "error": "Task is still running", "status": "running"}
    if task.status == "failed":
        return {"success": False, "error": task.error, "status": "failed"}
    if task.status in ("pending", "cancelled"):
        return {"success": False, "error": f"Task status is '{task.status}'", "status": task.status}
    return {
        "success": True,
        "task_id": task_id,
        "status": task.status,
        "result": task.result,
        "age": task.age_str(),
    }


def tool_task_list(params: dict) -> dict:
    """List all tracked background tasks."""
    tasks = [t.to_dict() for t in _TASKS.values()]
    if not tasks:
        return {"success": True, "tasks": [], "message": "No active tasks."}
    by_status: dict = {}
    for t in tasks:
        s = t["status"]
        by_status.setdefault(s, []).append(t)
    return {
        "success": True,
        "total": len(tasks),
        "tasks": tasks,
        "summary": {s: len(v) for s, v in by_status.items()},
    }


def tool_task_cancel(params: dict) -> dict:
    """Cancel a pending or running background task."""
    task_id = params.get("task_id", "")
    if not task_id:
        return {"success": False, "error": "Missing 'task_id'"}
    task = _TASKS.get(task_id)
    if not task:
        return {"success": False, "error": f"Task '{task_id}' not found"}
    if task.status in ("done", "failed", "cancelled"):
        return {"success": False, "error": f"Task already in terminal state: {task.status}"}
    task.status = "cancelled"
    task.completed_at = time.time()
    return {"success": True, "task_id": task_id, "cancelled": True}


# ── Tool registry (added to LOCAL_TOOLS in aria_cli.py) ───────────────────────

SUBAGENT_TOOLS = {
    "spawn_task":    (tool_spawn_task,   "Spawn a background sub-task; returns task_id"),
    "task_status":   (tool_task_status,  "Check status of a background task by task_id"),
    "task_result":   (tool_task_result,  "Retrieve result of a completed background task"),
    "task_list":     (tool_task_list,    "List all active background tasks"),
    "task_cancel":   (tool_task_cancel,  "Cancel a pending or running background task"),
}

SUBAGENT_SCHEMAS = [
    {
        "name": "spawn_task",
        "description": "Spawn an independent background agent task. Useful for parallelising slow operations: research, data fetching, long analysis. Returns a task_id you can poll with task_status.",
        "parameters": {
            "type": "object",
            "properties": {
                "prompt":  {"type": "string", "description": "The task for the sub-agent to perform"},
                "context": {"type": "string", "description": "Optional background context to inject into the sub-agent's system prompt"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "task_status",
        "description": "Check the status of a background task spawned with spawn_task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task_id returned by spawn_task"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "task_result",
        "description": "Retrieve the full result text of a completed background task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task_id returned by spawn_task"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "task_list",
        "description": "List all active and completed background tasks with their statuses.",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "task_cancel",
        "description": "Cancel a pending or running background task.",
        "parameters": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "The task_id to cancel"},
            },
            "required": ["task_id"],
        },
    },
]
