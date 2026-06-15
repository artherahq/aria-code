"""Write/edit tool implementations extracted from aria_cli.py.

These functions depend on aria_cli globals (GLOBAL_CHANGE_STORE, console, etc.).
They use lazy runtime imports to avoid circular imports at load time — aria_cli
is already fully initialised by the time any tool executes.
"""
from __future__ import annotations

import pathlib
import re as _re


# ── Lazy access to aria_cli singletons ───────────────────────────────────────

def _ac():
    """Return the aria_cli module (already loaded, never reimported from scratch)."""
    import aria_cli
    return aria_cli


def _ui():
    """Return (console, HAS_RICH) from the live aria_cli namespace."""
    m = _ac()
    return getattr(m, "console", None), getattr(m, "HAS_RICH", False)


def _change_store():
    return _ac().GLOBAL_CHANGE_STORE


def _write_policy():
    return _ac()._ACTIVE_WRITE_POLICY


def _is_safe(p: pathlib.Path) -> bool:
    return _ac()._is_safe_path(p)


def _config_dir() -> str:
    return str(_ac().CONFIG_DIR)


def _sessions_dir() -> str:
    return str(_ac().SESSIONS_DIR)


def _ChangeConflictError():
    return _ac().ChangeConflictError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_markdown_fences(content: str) -> str:
    """Strip markdown code fences that LLMs sometimes wrap around file content."""
    stripped = content.strip()
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl >= 0:
            stripped = stripped[first_nl + 1:]
        else:
            return content
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3].rstrip()
    if stripped != content.strip():
        return stripped + "\n"
    return content


def _auto_fix_python(content: str, path: str) -> str:
    """Auto-inject missing imports and validate syntax for Python files."""
    if not path.endswith(".py"):
        return content

    lines = content.split("\n")
    imports_present: set[str] = set()
    first_non_comment = 0
    for i, line in enumerate(lines):
        s = line.strip()
        if s and not s.startswith("#") and not s.startswith('"""') and not s.startswith("'''"):
            first_non_comment = i
            break

    for line in lines:
        s = line.strip()
        if s.startswith("import "):
            parts = s.split()
            if len(parts) >= 2:
                imports_present.add(parts[1].split(".")[0].split(",")[0])
        elif s.startswith("from "):
            parts = s.split()
            if len(parts) >= 2:
                imports_present.add(parts[1].split(".")[0])

    code = content
    needed: list[str] = []

    if ("os.path" in code or "os.expanduser" in code or "os.getcwd" in code
            or "os.makedirs" in code) and "os" not in imports_present:
        needed.append("import os")
    if ("sys." in code or "sys.exit" in code) and "sys" not in imports_present:
        needed.append("import sys")
    if "np." in code and "numpy" not in imports_present and "np" not in imports_present:
        needed.append("import numpy as np")
    if "pd." in code and "pandas" not in imports_present and "pd" not in imports_present:
        needed.append("import pandas as pd")
    if "yf." in code and "yfinance" not in imports_present and "yf" not in imports_present:
        needed.append("import yfinance as yf")

    has_plt = "plt." in code
    has_matplotlib_use = "matplotlib.use" in code
    if has_plt and "matplotlib" not in imports_present:
        needed.append("import matplotlib; matplotlib.use('Agg')")
        needed.append("import matplotlib.pyplot as plt")
    elif has_plt and not has_matplotlib_use:
        for i, line in enumerate(lines):
            if "import matplotlib.pyplot" in line and "matplotlib.use" not in "\n".join(lines[:i]):
                lines.insert(i, "import matplotlib; matplotlib.use('Agg')")
                content = "\n".join(lines)
                break

    if "mpf." in code and "mplfinance" not in imports_present and "mpf" not in imports_present:
        needed.append("import mplfinance as mpf")
    if "re." in code and "re" not in imports_present:
        needed.append("import re")
    if "json." in code and "json" not in imports_present:
        needed.append("import json")
    if "datetime" in code and "datetime" not in imports_present:
        needed.append("from datetime import datetime, timedelta")
    if (_re.search(r'\bta\.(?:sma|ema|rsi|macd|bbands|stoch|atr|adx|obv|vwap)\b', code)
            or "pandas_ta" in code) and "ta" not in imports_present and "pandas_ta" not in imports_present:
        needed.append("import pandas_ta as ta")
    if (_re.search(r'\bgo\.(?:Figure|Candlestick|Scatter|Bar|Heatmap|Layout|Table)', code)
            or "px." in code or "plotly" in code) and "plotly" not in imports_present:
        if "go.Figure" in code or "go.Candlestick" in code:
            needed.append("import plotly.graph_objects as go")
        if "px." in code:
            needed.append("import plotly.express as px")
        if "make_subplots" in code:
            needed.append("from plotly.subplots import make_subplots")
    if "scipy" in code and "scipy" not in imports_present:
        needed.append("import scipy")

    has_warnings_in_needed = any("warnings" in n for n in needed)
    if ("yf." in code or "pd." in code) and "warnings" not in imports_present and not has_warnings_in_needed:
        needed.insert(0, "import warnings; warnings.filterwarnings('ignore')")
    elif "warnings" in code and "warnings" not in imports_present and not has_warnings_in_needed:
        needed.append("import warnings")

    if needed:
        for imp in reversed(needed):
            lines.insert(first_non_comment, imp)
        content = "\n".join(lines)

    try:
        import ast
        ast.parse(content)
    except SyntaxError as e:
        console, has_rich = _ui()
        if has_rich and console:
            console.print(f"  [dim]Warning: syntax issue at line {e.lineno}: {e.msg}[/dim]")

    return content


def _write_policy_confirm(p: pathlib.Path, content: str, existed: bool) -> tuple:
    """Prompt user to confirm a write. Returns (approved: bool, final_path: Path)."""
    import difflib
    console, has_rich = _ui()
    lines_new = content.count("\n") + 1
    desktop = pathlib.Path.home() / "Desktop"
    is_desktop = str(p).startswith(str(desktop))

    if has_rich and console:
        console.print()
        if existed:
            old_content = p.read_text(errors="replace")
            diff = list(difflib.unified_diff(
                old_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"current/{p.name}",
                tofile=f"new/{p.name}",
                n=2,
            ))
            added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            console.print(f"  [yellow]⚠ Overwrite[/yellow]  [bold]{p}[/bold]")
            console.print(f"  [dim]  +{added} lines  -{removed} lines  ({lines_new} total)[/dim]")
            for line in diff[:8]:
                if line.startswith("+") and not line.startswith("+++"):
                    console.print(f"  [green]{line.rstrip()}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    console.print(f"  [red]{line.rstrip()}[/red]")
        else:
            loc = "[dim cyan](Desktop)[/dim cyan]" if is_desktop else "[yellow](outside Desktop)[/yellow]"
            console.print(f"  [cyan]New file[/cyan] {loc}  [bold]{p}[/bold]  ({lines_new} lines)")
        console.print()
        choice = console.input("  [bold]Write this file?[/bold] [dim]\\[y/n/r=redirect path][/dim] ").strip().lower()
    else:
        print()
        print(f"  {'Overwrite' if existed else 'New file'}: {p}  ({lines_new} lines)")
        choice = input("  Write this file? [y/n/r=redirect path] ").strip().lower()

    if choice == "r":
        if has_rich and console:
            new_path_str = console.input("  [dim]Enter new path: [/dim]").strip()
        else:
            new_path_str = input("  Enter new path: ").strip()
        if new_path_str:
            new_p = pathlib.Path(new_path_str).expanduser().resolve()
            if _is_safe(new_p):
                return True, new_p
            if has_rich and console:
                console.print(f"  [red]Path not allowed: {new_p}[/red]")
            else:
                print(f"  Path not allowed: {new_p}")
        return False, p

    return choice in ("y", "yes", ""), p


# ── Public tool functions ─────────────────────────────────────────────────────

def tool_write_file(params: dict) -> dict:
    """Write content to a file (create or overwrite)."""
    path = params.get("path", "")
    content = params.get("content", "")
    skip_confirm = params.get("_skip_confirm", False)
    stage_only = bool(params.get("stage_only", False))

    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    if not content:
        return {"success": False, "error": "Missing 'content' parameter"}

    content = _strip_markdown_fences(content)
    stripped_check = content.strip()

    if len(stripped_check) < 20:
        return {"success": False,
                "error": f"Content too short ({len(stripped_check)} chars). "
                "You must write the COMPLETE script code, not a placeholder."}

    if (stripped_check.startswith("<") and stripped_check.endswith(">")
            and "\n" not in stripped_check and len(stripped_check) < 200
            and not stripped_check.lower().startswith("<!doctype")
            and not stripped_check.lower().startswith("<html")):
        return {"success": False,
                "error": f"Content appears to be a placeholder tag: '{stripped_check[:120]}'. "
                "Write the complete code with imports, data fetching, computation, and output."}

    content = _auto_fix_python(content, path)

    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not _is_safe(p):
            return {"success": False, "error": f"Access denied: path '{p}' is outside allowed directories"}

        existed = p.exists()
        desktop = pathlib.Path.home() / "Desktop"
        import tempfile as _tf
        _auto_trusted_prefixes = (
            str(desktop),
            str(pathlib.Path(_tf.gettempdir()).resolve()),
            "/tmp", "/private/tmp", "/private/var/folders",
            _config_dir(), _sessions_dir(),
        )
        is_auto_trusted = any(str(p).startswith(pfx) for pfx in _auto_trusted_prefixes)
        policy = _write_policy()[0]

        needs_confirm = (
            not skip_confirm
            and not is_auto_trusted
            and (
                policy == "always_confirm"
                or policy in ("desktop_only", "confirm_outside")
                or existed
            )
        )

        if needs_confirm:
            approved, p = _write_policy_confirm(p, content, existed)
            if not approved:
                return {"success": False, "error": "Write cancelled by user.",
                        "data": {"cancelled": True}}

        console, has_rich = _ui()
        store = _change_store()
        change = store.stage(p, content, source="write_file")
        lines = content.count("\n") + 1
        action = "Updated" if existed else "Created"

        if stage_only:
            label = "Staged update" if existed else "Staged create"
            if has_rich and console:
                console.print(f"  [dim]{label} {p} ({lines} lines, change {change.change_id})[/dim]")
            else:
                print(f"  {label} {p} ({lines} lines, change {change.change_id})")
            return {"success": True, "data": {
                "path": str(p), "action": "staged", "lines": lines,
                "change_id": change.change_id,
                "before_hash": change.before_hash, "after_hash": change.after_hash,
                "diff": change.diff, "staged": True, "applied": False,
            }}

        try:
            applied = store.apply(change.change_id)
        except _ChangeConflictError() as exc:
            return {"success": False, "error": str(exc), "data": {"change_id": change.change_id}}

        if has_rich and console:
            console.print(f"  [dim]{action} {p} ({lines} lines)[/dim]")
        else:
            print(f"  {action} {p} ({lines} lines)")

        try:
            size_bytes = p.stat().st_size
        except Exception:
            size_bytes = len(content.encode("utf-8"))

        return {"success": True, "data": {
            "path": str(p), "action": action.lower(), "lines": lines,
            "size_bytes": size_bytes,
            "change_id": applied.change_id,
            "before_hash": applied.before_hash, "after_hash": applied.after_hash,
            "diff": applied.diff, "staged": True, "applied": True,
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_edit_file(params: dict) -> dict:
    """Edit a file by replacing old_string with new_string (first occurrence)."""
    path = params.get("path", "")
    old_str = params.get("old_string", params.get("old_str", ""))
    new_str = params.get("new_string", params.get("new_str", ""))
    stage_only = bool(params.get("stage_only", False))

    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    if not old_str:
        return {"success": False, "error": "Missing 'old_string' parameter"}

    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "error": f"File not found: {p}"}
        if not _is_safe(p):
            return {"success": False, "error": f"Access denied: path '{p}' is outside allowed directories"}

        content = p.read_text(errors="replace")
        if content.count(old_str) == 0:
            preview = "\n".join(content.splitlines()[:10])
            return {"success": False,
                    "error": f"old_string not found in file. "
                    f"The file starts with:\n{preview}\n\n"
                    f"HINT: Use read_file to see the actual content, then retry edit_file "
                    f"with the correct old_string. Or use write_file to overwrite the entire file."}

        new_content = content.replace(old_str, new_str, 1)
        store = _change_store()
        change = store.stage(p, new_content, source="edit_file")
        added = len(new_str.splitlines())
        removed = len(old_str.splitlines())
        console, has_rich = _ui()

        if stage_only:
            if has_rich and console:
                console.print(f"  [dim]Staged edit {p} (change {change.change_id})[/dim]")
            else:
                print(f"  Staged edit {p} (change {change.change_id})")
            return {"success": True, "data": {
                "path": str(p), "replacements": 1,
                "lines": new_content.count("\n") + 1,
                "change_id": change.change_id,
                "before_hash": change.before_hash, "after_hash": change.after_hash,
                "diff": change.diff, "staged": True, "applied": False,
            }}

        try:
            applied = store.apply(change.change_id)
        except _ChangeConflictError() as exc:
            return {"success": False, "error": str(exc), "data": {"change_id": change.change_id}}

        if has_rich and console:
            parts = []
            if added > 0:
                parts.append(f"[green]+{added}[/green]")
            if removed > 0:
                parts.append(f"[red]-{removed}[/red]")
            console.print(f"  [dim]Applied ({', '.join(parts)} lines)[/dim]")
        else:
            print(f"  Applied (+{added}, -{removed} lines)")

        return {"success": True, "data": {
            "path": str(p), "replacements": 1,
            "lines": new_content.count("\n") + 1,
            "change_id": applied.change_id,
            "before_hash": applied.before_hash, "after_hash": applied.after_hash,
            "diff": applied.diff, "staged": True, "applied": True,
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}
