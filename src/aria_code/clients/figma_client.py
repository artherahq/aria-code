"""figma_client.py — read-only Figma REST API client.

Figma has no public API for creating/editing designs from a server-side
script — only the in-app Plugin API can do that, which requires a human
running it inside Figma itself. So this module only reads: file structure
(page/frame names, not the full multi-MB node tree) and comments. Nothing
here writes to Figma.

Auth: a Personal Access Token, stored the same way as every other data-service
key in this codebase — /apikey set figma <token> (see aria_cli.py's
_DATA_KEY_MAP), read back via _get_provider_key("figma")/env FIGMA_API_KEY.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

API_BASE = "https://api.figma.com/v1"


def _token() -> str:
    import os

    token = os.getenv("FIGMA_API_KEY", "")
    if token:
        return token
    try:
        from apps.cli.config_paths import resolve_paths
        import json

        path = resolve_paths().providers_file
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            entry = raw.get("data", {}).get("figma", {})
            if entry.get("api_key"):
                return entry["api_key"]
    except Exception:
        pass
    return ""


def _headers() -> Dict[str, str]:
    token = _token()
    if not token:
        raise RuntimeError("Figma not configured — run /apikey set figma <personal_access_token> first.")
    return {"X-Figma-Token": token}


def _summarize_node(node: Dict[str, Any], depth: int) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"name": node.get("name", ""), "type": node.get("type", "")}
    children = node.get("children") or []
    if children and depth > 0:
        summary["children"] = [_summarize_node(c, depth - 1) for c in children]
    elif children:
        summary["child_count"] = len(children)
    return summary


def get_file_summary(file_key: str, *, depth: int = 2) -> Dict[str, Any]:
    """Return {name, last_modified, pages: [{name, top_level_nodes: [...]}]} for a Figma file.

    Depth-limited on purpose: a raw Figma file's `document` tree can be
    megabytes of deeply nested nodes — this returns page names and the
    first `depth` levels of each page's structure, not the full document.
    """
    import requests

    resp = requests.get(f"{API_BASE}/files/{file_key}", headers=_headers(), timeout=20)
    if resp.status_code != 200:
        return {"success": False, "error": f"Figma API error {resp.status_code}: {resp.text[:300]}"}
    data = resp.json()
    document = data.get("document", {})
    pages = [_summarize_node(page, depth) for page in document.get("children", [])]
    return {
        "success": True,
        "name": data.get("name", ""),
        "last_modified": data.get("lastModified", ""),
        "pages": pages,
    }


def list_comments(file_key: str) -> Dict[str, Any]:
    import requests

    resp = requests.get(f"{API_BASE}/files/{file_key}/comments", headers=_headers(), timeout=20)
    if resp.status_code != 200:
        return {"success": False, "error": f"Figma API error {resp.status_code}: {resp.text[:300]}"}
    comments = resp.json().get("comments", [])
    return {
        "success": True,
        "comments": [
            {
                "message": c.get("message", ""),
                "user": (c.get("user") or {}).get("handle", ""),
                "created_at": c.get("created_at", ""),
                "resolved_at": c.get("resolved_at"),
            }
            for c in comments
        ],
    }
