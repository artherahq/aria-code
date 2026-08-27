"""Read-only tool profile bound to one isolated remote workspace."""

from __future__ import annotations

from typing import Any

from aria_code.workspace_service import WorkspaceService, WorkspaceServiceError
from aria_code.workspace_changes import WorkspaceChangeError, WorkspaceChangeService


def build_workspace_read_tools(
    service: WorkspaceService,
    workspace_id: str,
    owner_id: str,
) -> tuple[dict[str, tuple[Any, dict[str, Any]]], list[dict[str, Any]]]:
    """Return ToolExecutor-compatible handlers without exposing host paths."""

    def read_file(params: dict[str, Any]) -> dict[str, Any]:
        relative = str(params.get("path") or "")
        if not relative:
            return {"success": False, "error": "path is required"}
        try:
            target = service.source_path(workspace_id, relative, owner_id=owner_id)
            result = service.files(workspace_id, owner_id=owner_id).read_file(
                str(target),
                offset=max(0, int(params.get("offset") or 0)),
                limit=max(0, min(int(params.get("limit") or 0), 2000)),
            )
            return {"success": True, "path": relative, "lines": result.lines, "content": result.content}
        except (WorkspaceServiceError, OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def list_files(params: dict[str, Any]) -> dict[str, Any]:
        relative = str(params.get("path") or ".")
        pattern = str(params.get("pattern") or "*")[:200]
        try:
            target = service.source_path(workspace_id, relative, owner_id=owner_id)
            result = service.files(workspace_id, owner_id=owner_id).list_files(str(target), pattern)
            result["path"] = relative
            return {"success": True, **result}
        except (WorkspaceServiceError, OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    def search_code(params: dict[str, Any]) -> dict[str, Any]:
        relative = str(params.get("path") or ".")
        pattern = str(params.get("pattern") or "")[:500]
        file_glob = str(params.get("file_glob") or "**/*")[:200]
        try:
            target = service.source_path(workspace_id, relative, owner_id=owner_id)
            result = service.files(workspace_id, owner_id=owner_id).search_code(
                pattern, str(target), file_glob
            )
            result["path"] = relative
            return {"success": True, **result}
        except (WorkspaceServiceError, OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    schemas = [
        {
            "type": "function",
            "function": {
                "name": "workspace_read_file",
                "description": "Read a UTF-8 text file inside the assigned project workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0},
                        "limit": {"type": "integer", "minimum": 0, "maximum": 2000},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "workspace_list_files",
                "description": "List files below a relative directory in the assigned workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "pattern": {"type": "string"}},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "workspace_search_code",
                "description": "Search project text using a regular expression without executing code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "file_glob": {"type": "string"},
                    },
                    "required": ["pattern"],
                },
            },
        },
    ]
    handlers = {
        "workspace_read_file": (read_file, schemas[0]),
        "workspace_list_files": (list_files, schemas[1]),
        "workspace_search_code": (search_code, schemas[2]),
    }
    return handlers, schemas


def build_workspace_proposal_tools(
    service: WorkspaceService,
    workspace_id: str,
    owner_id: str,
) -> tuple[dict[str, tuple[Any, dict[str, Any]]], list[dict[str, Any]]]:
    """Expose proposal creation only; applying still requires control-plane approval."""
    schema = {
        "type": "function",
        "function": {
            "name": "workspace_propose_change",
            "description": (
                "Stage a complete UTF-8 file replacement and return its diff. "
                "This does not modify the project; a user must approve the returned change id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    }

    def propose(params: dict[str, Any]) -> dict[str, Any]:
        try:
            change = WorkspaceChangeService(service).propose(
                workspace_id,
                owner_id=owner_id,
                path=str(params.get("path") or ""),
                content=str(params.get("content") or ""),
                expected_sha256=(str(params["expected_sha256"]) if params.get("expected_sha256") else None),
            )
            return {"success": True, **change.public_dict(), "requires_approval": True}
        except (WorkspaceChangeError, WorkspaceServiceError, OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    return {"workspace_propose_change": (propose, schema)}, [schema]
