"""Intelligent tool-result routing — artifact-backed context budgeting.

Why this exists
---------------
Before this module, every tool result was hard-truncated to 6000 characters via
``_truncate_tool_result``.  That works for small outputs, but a ``npm test`` or
``cargo build`` easily produces 60 000+ characters, and the head+tail slice
drops exactly the lines the model needs to diagnose a failure — the middle,
where the actual errors and stack traces are.

This module replaces that with *routing*: small results go inline as before;
large results are saved to a timestamped artifact file in ``.aria/artifacts/``
and the model receives a *structured summary* (test counts, failure names,
error lines) plus a reference it can ``read_file`` if it needs more detail.

The distinction is format-aware: test output is parsed differently from build
output, and both differently from search results.  Unknown formats fall back
to head+tail with an artifact reference — strictly better than the old
truncation because the full output is never lost.

Integration
-----------
The ``ToolResultRouter.route()`` method is a drop-in replacement for
``_truncate_tool_result()`` in ``agent_loop.py``.  The returned
``RoutedResult.inline_text`` goes into the follow-up message; the artifact
path is available for tracing.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import pathlib
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

@dataclasses.dataclass
class RoutedResult:
    """Represents the routed outcome of a tool execution."""
    inline_text: str
    artifact_path: str | None
    was_truncated: bool
    original_length: int
    summary: str | None


def summarize_test_output(text: str) -> str:
    """Summarizes test framework output."""
    lines = text.splitlines()
    summary_lines = []
    
    # Simple regexes for common test outputs
    pytest_summary_re = re.compile(r"==.*(passed|failed|skipped|error).*==")
    jest_summary_re = re.compile(r"(Tests|Test Suites):")
    go_test_fail_re = re.compile(r"--- FAIL:")
    
    failures = []
    last_20 = lines[-20:] if len(lines) > 20 else lines
    
    for i, line in enumerate(lines):
        if 'FAIL' in line or 'ERROR' in line or go_test_fail_re.match(line) or 'FAILED' in line:
            # try to capture some context
            if len(failures) < 10:
                failures.append(line)
                if i + 1 < len(lines) and lines[i+1].strip():
                    failures.append("  " + lines[i+1])
                    
    summary_text = ""
    for line in reversed(lines):
        if pytest_summary_re.search(line) or jest_summary_re.search(line):
            summary_text = line
            break
            
    res = []
    if summary_text:
        res.append(f"Summary: {summary_text.strip()}")
        
    if failures:
        res.append("\nSample Failures:")
        res.extend(failures[:20]) # Limit failure lines
        
    res.append("\nLast 20 lines (context):")
    res.extend(last_20)
    
    return "\n".join(res)


def summarize_build_output(text: str) -> str:
    """Summarizes compiler or bundler output."""
    lines = text.splitlines()
    errors = []
    error_count = 0
    warning_count = 0
    
    for line in lines:
        lower_line = line.lower()
        if 'error:' in lower_line or 'error ' in lower_line or 'fail' in lower_line:
            error_count += 1
            if len(errors) < 5:
                errors.append(line)
        elif 'warning:' in lower_line or 'warn ' in lower_line:
            warning_count += 1
            
    res = [f"Build Summary: {error_count} errors, {warning_count} warnings"]
    if errors:
        res.append("\nFirst 5 errors:")
        res.extend(errors)
        
    res.append("\nLast 20 lines:")
    res.extend(lines[-20:] if len(lines) > 20 else lines)
    
    return "\n".join(res)


class ToolResultRouter:
    """Intelligently routes tool execution results based on size and content."""
    
    def __init__(self, artifact_dir: pathlib.Path | str | None = None, budget: int = 6000):
        if artifact_dir is None:
            self.artifact_dir = pathlib.Path.cwd() / ".aria" / "artifacts"
        else:
            self.artifact_dir = pathlib.Path(artifact_dir)
        self.budget = budget
        
    def _save_artifact(self, tool_name: str, text: str) -> str:
        """Saves text to an artifact file and returns the path."""
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        timestamp = hex(int(time.time() * 1000))[2:]
        filename = f"{tool_name}-{timestamp}.log"
        file_path = self.artifact_dir / filename
        try:
            file_path.write_text(text, encoding="utf-8")
            return str(file_path)
        except Exception as e:
            logger.error(f"Failed to write artifact {file_path}: {e}")
            return f"<failed to write artifact: {e}>"
            
    def route(self, tool_name: str, result: Any) -> RoutedResult:
        """Decide how to handle a tool result based on size and tool type."""
        text = str(result)
        original_length = len(text)
        
        if original_length <= self.budget:
            return RoutedResult(
                inline_text=text,
                artifact_path=None,
                was_truncated=False,
                original_length=original_length,
                summary=None
            )
            
        artifact_path = self._save_artifact(tool_name, text)
        
        summary = None
        inline_text = ""
        
        if tool_name == "run_command":
            # Heuristic to detect if it's a test or build
            lower_text = text.lower()
            if "test" in lower_text and ("pass" in lower_text or "fail" in lower_text):
                summary = summarize_test_output(text)
            elif "error:" in lower_text or "warning:" in lower_text or "build" in lower_text:
                summary = summarize_build_output(text)
                
            if summary:
                inline_text = (
                    f"{summary}\n\n"
                    f"... Output truncated (original length: {original_length} chars).\n"
                    f"Full output saved to: {artifact_path}"
                )
                
        elif tool_name in ("search_code", "list_files", "read_file"):
            # Head + Tail approach
            head_budget = int(self.budget * (2/3))
            tail_budget = int(self.budget * (1/3))
            
            head = text[:head_budget]
            tail = text[-tail_budget:]
            
            inline_text = (
                f"{head}\n"
                f"\n... [TRUNCATED {original_length - head_budget - tail_budget} chars] ...\n"
                f"Full output saved to: {artifact_path}\n\n"
                f"{tail}"
            )
            summary = "Head and tail included."
            
        if not inline_text:
            # Default fallback for unknown tools
            head_budget = int(self.budget * (2/3))
            tail_budget = int(self.budget * (1/3))
            head = text[:head_budget]
            tail = text[-tail_budget:]
            inline_text = (
                f"{head}\n"
                f"\n... [TRUNCATED {original_length - head_budget - tail_budget} chars] ...\n"
                f"Full output saved to: {artifact_path}\n\n"
                f"{tail}"
            )
            summary = "Default head/tail truncation."
            
        return RoutedResult(
            inline_text=inline_text,
            artifact_path=artifact_path,
            was_truncated=True,
            original_length=original_length,
            summary=summary
        )
