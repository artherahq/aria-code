"""FileCommandsMixin — Claude Code-style file ops: read/write/edit/ls/search + staged changes.

Method bodies use aria_cli module globals (self.context.console, self.context.has_rich, the _tool_* file
tools, _display_path, pathlib, _get_Syntax(), _get__SYNTAX_THEME(), _get_GLOBAL_CHANGE_STORE()), which
are bound at import time by aria_cli._rebind_mixin_globals(FileCommandsMixin).
Note cmd_read uses `pathlib.Path` (a bound global), not a bare `Path`, so no
local import is required.
"""

from __future__ import annotations


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional

def _display_path(*args, **kwargs):
    from aria_cli import _display_path as fn
    return fn(*args, **kwargs)
def _tool_read_file(*args, **kwargs):
    from aria_cli import _tool_read_file as fn
    return fn(*args, **kwargs)
def _get_GLOBAL_CHANGE_STORE():
    from aria_cli import GLOBAL_CHANGE_STORE as val
    return val
def _get_Syntax():
    from aria_cli import Syntax as val
    return val
def _get__SYNTAX_THEME():
    from aria_cli import _SYNTAX_THEME as val
    return val
def _tool_list_files(*args, **kwargs):
    from aria_cli import _tool_list_files as fn
    return fn(*args, **kwargs)
import pathlib
def _tool_write_file(*args, **kwargs):
    from aria_cli import _tool_write_file as fn
    return fn(*args, **kwargs)
def _tool_search_code(*args, **kwargs):
    from aria_cli import _tool_search_code as fn
    return fn(*args, **kwargs)

import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


class FileCommandsMixin:
    """Mixin: file read/write/edit/list/search and staged-change commands."""

    def cmd_read(self, args: str):
        """Read a file: /read <path> [offset] [limit]"""
        parts = args.split()
        if not parts:
            self.context.console.print("[dim]Usage: /read <file_path> [start_line] [num_lines][/dim]" if self.context.has_rich
                          else "Usage: /read <path> [offset] [limit]")
            return
        params = {"path": parts[0]}
        if len(parts) > 1:
            try:
                params["offset"] = int(parts[1])
            except ValueError:
                pass
        if len(parts) > 2:
            try:
                params["limit"] = int(parts[2])
            except ValueError:
                pass
        result = _tool_read_file(params)
        if result["success"]:
            content = result["data"]["content"]
            if self.context.has_rich:
                # Use _get_Syntax() for code files
                path = result["data"]["path"]
                ext = pathlib.Path(path).suffix
                lang_map = {".py": "python", ".js": "javascript", ".ts": "typescript",
                            ".tsx": "typescript", ".jsx": "javascript", ".json": "json",
                            ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
                            ".swift": "swift", ".html": "html", ".css": "css",
                            ".sh": "bash", ".sql": "sql", ".rs": "rust", ".go": "go"}
                lang = lang_map.get(ext, "text")
                # Strip line numbers we added, use _get_Syntax()'s own
                raw = "\n".join(line.split("│ ", 1)[1] if "│ " in line else line
                                for line in content.split("\n"))
                self.context.console.print(f"\n[dim]{_display_path(path)} ({result['data']['lines']} lines)[/dim]")
                self.context.console.print(_get_Syntax()(raw, lang, line_numbers=True, theme=_get__SYNTAX_THEME()))
            else:
                print(f"\n{_display_path(result['data']['path'])} ({result['data']['lines']} lines)")
                print(content)
        else:
            self.context.console.print(f"[red]{result['error']}[/red]" if self.context.has_rich else result["error"])

    def cmd_write(self, args: str):
        """Write a file: /write [--stage] <path> then paste content, end with EOF line."""
        parts = args.strip().split()
        stage_only = False
        if "--stage" in parts:
            stage_only = True
            parts = [p for p in parts if p != "--stage"]
        path = " ".join(parts).strip()
        if not path:
            self.context.console.print("[dim]Usage: /write [--stage] <file_path>[/dim]" if self.context.has_rich
                          else "Usage: /write [--stage] <path>")
            self.context.console.print("[dim]Then paste content, end with a line containing only 'EOF'[/dim]" if self.context.has_rich
                          else "Paste content, end with EOF")
            return
        if self.context.has_rich:
            mode = "Staging" if stage_only else "Writing"
            self.context.console.print(f"[dim]{mode} {_display_path(path)} — paste content, end with 'EOF' on a new line:[/dim]")
        else:
            print(f"{'Staging' if stage_only else 'Writing'} {_display_path(path)} — paste content, end with EOF:")
        lines = []
        try:
            while True:
                line = input()
                if line.strip() == "EOF":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            self.context.console.print("[dim]Cancelled[/dim]" if self.context.has_rich else "Cancelled")
            return
        content = "\n".join(lines) + "\n"
        result = _tool_write_file({"path": path, "content": content, "stage_only": stage_only})
        if not result["success"]:
            self.context.console.print(f"[red]{result['error']}[/red]" if self.context.has_rich else result["error"])
        elif stage_only:
            change_id = result.get("data", {}).get("change_id", "")
            msg = f"Staged change {change_id}. Review with /changes, apply with /apply-change {change_id}."
            self.context.console.print(f"[dim]{msg}[/dim]" if self.context.has_rich else msg)

    async def cmd_edit(self, args: str):
        """Edit a file interactively: /edit <path> — AI edits based on instruction."""
        parts = args.strip().split(maxsplit=1)
        if not parts:
            self.context.console.print("[dim]Usage: /edit <file_path> <instruction>[/dim]" if self.context.has_rich
                          else "Usage: /edit <path> <instruction>")
            return
        path = parts[0]
        instruction = parts[1] if len(parts) > 1 else None

        # Read the file first
        read_result = _tool_read_file({"path": path})
        if not read_result["success"]:
            self.context.console.print(f"[red]{read_result['error']}[/red]" if self.context.has_rich else read_result["error"])
            return

        if not instruction:
            # Show file and ask for instruction
            if self.context.has_rich:
                self.context.console.print(f"[dim]{_display_path(read_result['data']['path'])} ({read_result['data']['lines']} lines)[/dim]")
            try:
                instruction = (self.context.console.input("[bold]>[/bold] What to change: ") if self.context.has_rich
                               else input("What to change: ")).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not instruction:
                return

        # Send to AI with file context and ask for edit
        file_content = read_result["data"]["content"]
        prompt = (
            f"I need you to edit the file `{path}`.\n\n"
            f"Current file content:\n```\n{file_content[:8000]}\n```\n\n"
            f"Instruction: {instruction}\n\n"
            f"Use the edit_file tool to make the changes. Remember to use the exact old_string from the file."
        )
        await self.terminal.send_message(prompt)

    def cmd_ls(self, args: str):
        """List files: /ls [path] [pattern]"""
        parts = args.split()
        path = parts[0] if parts else "."
        pattern = parts[1] if len(parts) > 1 else "*"
        result = _tool_list_files({"path": path, "pattern": pattern})
        if result["success"]:
            items = result["data"]["items"]
            if self.context.has_rich:
                self.context.console.print(f"\n[dim]{_display_path(result['data']['path'], fallback='directory')} ({result['data']['count']} items)[/dim]\n")
                for item in items:
                    if item["type"] == "dir":
                        self.context.console.print(f"  [bold]{item['name']}/[/bold]")
                    else:
                        size = item["size"]
                        size_str = f"{size:,}" if size < 10000 else f"{size/1024:.1f}K"
                        self.context.console.print(f"  {item['name']}  [dim]{size_str}[/dim]")
            else:
                for item in items:
                    suffix = "/" if item["type"] == "dir" else ""
                    print(f"  {item['name']}{suffix}")
        else:
            self.context.console.print(f"[red]{result['error']}[/red]" if self.context.has_rich else result["error"])

    def cmd_search(self, args: str):
        """Search code: /search <pattern> [path] [glob]

        If the second word doesn't look like a file path (no / or .), the whole
        args string is treated as the pattern and CWD is searched.
        """
        args = args.strip().strip('"\'')
        parts = args.split()
        if not parts:
            self.context.console.print("[dim]Usage: /search <pattern> [path] [file_glob][/dim]" if self.context.has_rich
                          else "Usage: /search <pattern> [path] [glob]")
            return

        # Determine if second token looks like a file path or directory
        def _looks_like_path(s: str) -> bool:
            return bool(s) and any(c in s for c in "/\\.")

        _QUOTES = '"\'`'
        if len(parts) == 1:
            # Single token: use as pattern, search CWD
            params = {"pattern": parts[0].strip(_QUOTES)}
        elif len(parts) >= 2 and _looks_like_path(parts[1]):
            # Second token is a path
            params = {"pattern": parts[0].strip(_QUOTES)}
            params["path"] = parts[1]
            if len(parts) > 2:
                params["glob"] = parts[2]
        else:
            # Multi-word pattern with no path (e.g. /search def cmd_model)
            # Find where the path arg starts (if any)
            path_idx = None
            for i, p in enumerate(parts[1:], 1):
                if _looks_like_path(p):
                    path_idx = i
                    break
            if path_idx:
                params = {"pattern": " ".join(parts[:path_idx]).strip(_QUOTES)}
                params["path"] = parts[path_idx]
                if path_idx + 1 < len(parts):
                    params["glob"] = parts[path_idx + 1]
            else:
                # Whole args is the pattern
                params = {"pattern": args.strip(_QUOTES)}
        result = _tool_search_code(params)
        if result["success"]:
            matches = result["data"]["matches"]
            if self.context.has_rich:
                self.context.console.print(f"\n[dim]{result['data']['count']} matches for '{result['data']['pattern']}'[/dim]\n")
                for m in matches[:30]:
                    self.context.console.print(f"  [dim]{m['file']}:{m['line']}[/dim]  {m['content'][:100]}")
            else:
                print(f"\n{result['data']['count']} matches:")
                for m in matches[:30]:
                    print(f"  {m['file']}:{m['line']}  {m['content'][:100]}")
        else:
            self.context.console.print(f"[red]{result['error']}[/red]" if self.context.has_rich else result["error"])

    def cmd_changes(self, args: str):
        """List staged file changes."""
        include_closed = "--all" in args.split()
        changes = _get_GLOBAL_CHANGE_STORE().list(include_closed=include_closed)
        if not changes:
            msg = "No staged changes."
            self.context.console.print(f"[dim]{msg}[/dim]" if self.context.has_rich else msg)
            return
        if self.context.has_rich:
            self.context.console.print()
            for change in changes:
                added = sum(1 for line in change.diff.splitlines() if line.startswith("+") and not line.startswith("+++"))
                removed = sum(1 for line in change.diff.splitlines() if line.startswith("-") and not line.startswith("---"))
                status = "applied" if change.applied else "rejected" if change.rejected else "pending"
                color = "green" if change.applied else "red" if change.rejected else "yellow"
                self.context.console.print(f"[{color}]{change.change_id}[/{color}] [bold]{change.path}[/bold] [dim]{status} +{added}/-{removed}[/dim]")
                preview = "\n".join(change.diff.splitlines()[:18])
                if preview:
                    self.context.console.print(_get_Syntax()(preview, "diff", theme=_get__SYNTAX_THEME()))
            self.context.console.print()
        else:
            for change in changes:
                status = "applied" if change.applied else "rejected" if change.rejected else "pending"
                print(f"{change.change_id} {status} {change.path}")
                print("\n".join(change.diff.splitlines()[:18]))

    def cmd_apply_change(self, args: str):
        """Apply a staged file change."""
        change_id = args.strip()
        if not change_id:
            self.context.console.print("[dim]Usage: /apply-change <change_id>[/dim]" if self.context.has_rich
                          else "Usage: /apply-change <change_id>")
            return
        try:
            change = _get_GLOBAL_CHANGE_STORE().apply(change_id)
            msg = f"Applied change {change.change_id}: {change.path}"
            self.context.console.print(f"[green]{msg}[/green]" if self.context.has_rich else msg)
        except Exception as exc:
            self.context.console.print(f"[red]{exc}[/red]" if self.context.has_rich else str(exc))

    def cmd_reject_change(self, args: str):
        """Reject a staged file change."""
        change_id = args.strip()
        if not change_id:
            self.context.console.print("[dim]Usage: /reject-change <change_id>[/dim]" if self.context.has_rich
                          else "Usage: /reject-change <change_id>")
            return
        try:
            change = _get_GLOBAL_CHANGE_STORE().reject(change_id)
            msg = f"Rejected change {change.change_id}: {change.path}"
            self.context.console.print(f"[dim]{msg}[/dim]" if self.context.has_rich else msg)
        except Exception as exc:
            self.context.console.print(f"[red]{exc}[/red]" if self.context.has_rich else str(exc))
