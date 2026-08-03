"""Delegate a task to the Claude Code or Codex CLI as an external headless agent.

Complements runtime/subagent.py's spawn_task system: by default a spawned
task runs inside aria-code's own LLM loop, but a task can instead be
delegated to another agentic coding CLI already installed on the machine
(``backend="claude"`` / ``backend="codex"``), reusing whatever coding
capability that tool has. Both runners return plain text — a success/failure
string, never a raised exception — so a missing binary or a CLI-side failure
degrades the same way a normal subagent failure does (task.status="failed"
with .error set), and never crashes the background task loop.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from typing import Optional


async def _run_cli(
    binary: str,
    args: list[str],
    *,
    cwd: Optional[str],
    timeout: float,
) -> tuple[bool, str]:
    """Run one CLI invocation. Returns (ok, text) — never raises."""
    if not shutil.which(binary):
        return False, f"{binary} CLI not found on PATH. Install it first."
    try:
        proc = await asyncio.create_subprocess_exec(
            binary, *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return False, f"Failed to launch {binary}: {exc}"

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"{binary} CLI timed out after {timeout:.0f}s."

    out_text = stdout.decode("utf-8", errors="replace")
    if proc.returncode != 0:
        err_text = stderr.decode("utf-8", errors="replace")[:500]
        return False, f"{binary} CLI exited {proc.returncode}: {err_text or out_text[:500]}"
    return True, out_text


def _extract_json_result(raw: str, *keys: str) -> str:
    """Best-effort pull of the human-readable result out of a CLI's JSON output.

    Both CLIs' exact JSON shapes can vary by version, so this tries each
    candidate key and falls back to the raw text — a schema drift should
    degrade to "less clean output", not a crash or an empty result.
    """
    raw = raw.strip()
    if not raw:
        return "(no output)"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
    return raw


async def run_claude_code_headless(
    prompt: str,
    *,
    cwd: Optional[str] = None,
    timeout: float = 600.0,
) -> str:
    """Delegate `prompt` to `claude -p` (Claude Code headless mode)."""
    ok, text = await _run_cli(
        "claude",
        ["-p", prompt, "--output-format", "json"],
        cwd=cwd,
        timeout=timeout,
    )
    if not ok:
        return f"错误: {text}"
    return _extract_json_result(text, "result", "response")


async def run_codex_headless(
    prompt: str,
    *,
    cwd: Optional[str] = None,
    timeout: float = 600.0,
) -> str:
    """Delegate `prompt` to `codex exec` (OpenAI Codex CLI headless mode)."""
    ok, text = await _run_cli(
        "codex",
        ["exec", prompt, "--json"],
        cwd=cwd,
        timeout=timeout,
    )
    if not ok:
        return f"错误: {text}"
    return _extract_json_result(text, "result", "message", "output")


RUNNERS = {
    "claude": run_claude_code_headless,
    "codex": run_codex_headless,
}
