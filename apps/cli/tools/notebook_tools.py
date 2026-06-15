"""Notebook and glob tools — stateless helpers.

No console/write-policy state involved; all I/O is through pathlib.
WorkspaceSecurity is imported from the aria-code root to reuse the
same path-safety rules as the main CLI.
"""
from __future__ import annotations

import json
import pathlib
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent.parent  # aria-code/
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from workspace import WorkspaceSecurity  # noqa: E402


def _is_safe(p: pathlib.Path) -> bool:
    return WorkspaceSecurity().is_safe_path(p)


def tool_glob(params: dict) -> dict:
    """Fast file-pattern search across a directory tree."""
    pattern = params.get("pattern", "**/*")
    root    = (params.get("path", ".") or ".").strip()
    limit   = min(int(params.get("limit", 200)), 1000)
    try:
        p = pathlib.Path(root).expanduser().resolve()
        if not p.is_dir():
            return {"success": False, "error": f"Directory not found: {p}"}
        results = sorted(
            str(fp.relative_to(p) if fp.is_relative_to(p) else fp)
            for fp in p.glob(pattern)
            if fp.is_file()
        )[:limit]
        return {"success": True, "data": {
            "pattern": pattern, "root": str(p),
            "count": len(results), "files": results,
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_notebook_read(params: dict) -> dict:
    """Read a Jupyter notebook (.ipynb) and return cells as text."""
    path = params.get("path", "")
    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "error": f"File not found: {p}"}
        if p.suffix != ".ipynb":
            return {"success": False, "error": f"Not a notebook: {p}"}
        if not _is_safe(p):
            return {"success": False, "error": f"Access denied: {p}"}
        nb    = json.loads(p.read_text(errors="replace"))
        cells = nb.get("cells", [])
        lines = []
        for i, cell in enumerate(cells):
            ct  = cell.get("cell_type", "code")
            src = "".join(cell.get("source", []))
            lines.append(f"[Cell {i+1} | {ct}]\n{src}")
            if ct == "code":
                for out in cell.get("outputs", []):
                    text = out.get("text") or out.get("data", {}).get("text/plain", [])
                    if isinstance(text, list):
                        text = "".join(text)
                    if text:
                        lines.append(f"  # Output: {text[:300].strip()}")
        content = "\n\n".join(lines)
        return {"success": True, "data": {
            "path": str(p), "cell_count": len(cells),
            "content": content[:30000],
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def tool_notebook_edit(params: dict) -> dict:
    """Edit a cell in a Jupyter notebook by index (0-based)."""
    path       = params.get("path", "")
    cell_index = int(params.get("cell_index", 0))
    new_source = params.get("new_source") or params.get("source", "")
    if not path:
        return {"success": False, "error": "Missing 'path'"}
    if not new_source:
        return {"success": False, "error": "Missing 'new_source'"}
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not _is_safe(p):
            return {"success": False, "error": f"Access denied: {p}"}
        nb    = json.loads(p.read_text(errors="replace"))
        cells = nb.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return {"success": False,
                    "error": f"Cell index {cell_index} out of range (0–{len(cells)-1})"}
        cells[cell_index]["source"] = [new_source]
        if cells[cell_index].get("cell_type") == "code":
            cells[cell_index]["outputs"] = []
            cells[cell_index]["execution_count"] = None
        p.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
        return {"success": True, "data": {
            "path": str(p), "cell_index": cell_index,
            "message": f"Cell {cell_index} updated successfully",
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}
