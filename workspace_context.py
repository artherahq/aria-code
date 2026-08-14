"""Safe, user-visible workspace context.

The context is deliberately a small allow-list.  It is suitable for a UI
summary, but is not a place for credentials, provider configuration, raw tool
responses, or data-source details.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


CONTEXT_VERSION = 1
CONTEXT_FILE_NAME = "workspace-context.json"
VALID_ACTIVITY_STATES = {
    "idle",
    "queued",
    "running",
    "needs_approval",
    "completed",
    "attention",
}


def _timestamp(now: datetime | None = None) -> str:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _text(value: Any, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _count(value: Any, *, maximum: int = 999_999_999) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, min(int(value), maximum))
    except (TypeError, ValueError):
        return 0


def context_file_path(workspace_root: str | Path | None = None) -> Path:
    """Return the private, workspace-local state path."""

    root = Path(workspace_root or Path.cwd()).expanduser().resolve()
    return root / ".aria" / CONTEXT_FILE_NAME


def default_workspace_context(
    workspace_root: str | Path | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    root = Path(workspace_root or Path.cwd()).expanduser().resolve()
    project_name = root.name or "Workspace"
    return {
        "schemaVersion": CONTEXT_VERSION,
        "updatedAt": _timestamp(now),
        "project": {"name": project_name},
        "activity": {
            "state": "idle",
            "requiresApproval": False,
        },
        "pendingApprovalCount": 0,
        "completedTaskCount": 0,
        "artifacts": [],
    }


def normalize_workspace_context(
    raw: Mapping[str, Any] | None,
    workspace_root: str | Path | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an allow-listed context, discarding unknown or sensitive input."""

    source: Mapping[str, Any] = raw if isinstance(raw, Mapping) else {}
    normalized = default_workspace_context(workspace_root, now=now)

    project = source.get("project")
    if isinstance(project, Mapping):
        name = _text(project.get("name"), limit=120)
        symbol = _text(project.get("symbol"), limit=24)
        if name:
            normalized["project"]["name"] = name
        if symbol:
            normalized["project"]["symbol"] = symbol

    activity = source.get("activity")
    if isinstance(activity, Mapping):
        state = _text(activity.get("state"), limit=32)
        title = _text(activity.get("title"), limit=160)
        if state in VALID_ACTIVITY_STATES:
            normalized["activity"]["state"] = state
        if title:
            normalized["activity"]["title"] = title
        if "progress" in activity:
            normalized["activity"]["progress"] = _count(activity.get("progress"), maximum=100)
        normalized["activity"]["requiresApproval"] = bool(
            activity.get("requiresApproval", False)
        )

    normalized["pendingApprovalCount"] = _count(source.get("pendingApprovalCount"))
    normalized["completedTaskCount"] = _count(source.get("completedTaskCount"))

    artifacts = source.get("artifacts")
    if isinstance(artifacts, list):
        safe_artifacts: list[dict[str, str]] = []
        for artifact in artifacts[:6]:
            if not isinstance(artifact, Mapping):
                continue
            artifact_id = _text(artifact.get("id"), limit=120)
            title = _text(artifact.get("title"), limit=160)
            kind = _text(artifact.get("kind"), limit=60)
            if not artifact_id or not title:
                continue
            entry: dict[str, str] = {
                "id": artifact_id,
                "title": title,
                "status": "ready",
            }
            if kind:
                entry["kind"] = kind
            safe_artifacts.append(entry)
        normalized["artifacts"] = safe_artifacts

    return normalized


def load_workspace_context(workspace_root: str | Path | None = None) -> dict[str, Any]:
    path = context_file_path(workspace_root)
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        raw = None
    return normalize_workspace_context(raw, workspace_root)


def save_workspace_context(
    context: Mapping[str, Any] | None,
    workspace_root: str | Path | None = None,
) -> Path:
    """Atomically persist only the normalized, user-visible context."""

    path = context_file_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_context = normalize_workspace_context(context, workspace_root)
    safe_context["updatedAt"] = _timestamp()

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".workspace-context-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            os.chmod(temp_path, 0o600)
            json.dump(safe_context, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        os.chmod(path, 0o600)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink(missing_ok=True)
    return path


def update_workspace_context(
    patch: Mapping[str, Any],
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """Apply a shallow, allow-listed patch and persist it."""

    current = load_workspace_context(workspace_root)
    merged: dict[str, Any] = {**current}
    for key in ("project", "activity"):
        candidate = patch.get(key)
        if isinstance(candidate, Mapping):
            merged[key] = {**current[key], **candidate}
    for key in ("pendingApprovalCount", "completedTaskCount", "artifacts"):
        if key in patch:
            merged[key] = patch[key]
    safe_context = normalize_workspace_context(merged, workspace_root)
    save_workspace_context(safe_context, workspace_root)
    return safe_context


def set_workspace_activity(
    *,
    state: str,
    title: str | None = None,
    progress: int | None = None,
    requires_approval: bool | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    activity: dict[str, Any] = {"state": state}
    if title is not None:
        activity["title"] = title
    if progress is not None:
        activity["progress"] = progress
    if requires_approval is not None:
        activity["requiresApproval"] = requires_approval
    return update_workspace_context({"activity": activity}, workspace_root)


def record_workspace_artifact(
    *,
    artifact_id: str,
    title: str,
    kind: str | None = None,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    current = load_workspace_context(workspace_root)
    artifacts = [*current["artifacts"], {"id": artifact_id, "title": title, "kind": kind or ""}]
    return update_workspace_context({"artifacts": artifacts[-6:]}, workspace_root)
