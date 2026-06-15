"""Manifest export helpers for Aria package facades."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _manifest_dicts(items: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in items:
        if hasattr(item, "manifest"):
            item = item.manifest()
        if hasattr(item, "to_dict"):
            out.append(item.to_dict())
        elif isinstance(item, dict):
            out.append(dict(item))
    return out


def build_package_manifest(
    *,
    identity: Any,
    tools: Iterable[Any],
    agents: Iterable[Any],
    skills: Iterable[Any],
    mcp_exposures: Iterable[Any],
    services: Iterable[Any] = (),
    arthera_packages: Any = None,
    arthera_mcp_tools: Iterable[Any] = (),
) -> Dict[str, Any]:
    """Build one JSON-serializable package manifest."""

    return {
        "schema_version": "aria.package-manifest.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "product": identity.to_dict() if hasattr(identity, "to_dict") else identity,
        "capabilities": {
            "services": _manifest_dicts(services),
            "tools": _manifest_dicts(tools),
            "agents": _manifest_dicts(agents),
            "skills": _manifest_dicts(skills),
            "mcp_exposures": [
                item.to_tool_descriptor() if hasattr(item, "to_tool_descriptor") else dict(item)
                for item in mcp_exposures
            ],
            "arthera_mcp_tools": _manifest_dicts(arthera_mcp_tools),
        },
        "arthera_packages": (
            arthera_packages.to_dict()
            if hasattr(arthera_packages, "to_dict")
            else arthera_packages
        ),
    }


def write_package_manifest(path: Path, manifest: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
