"""Approval-ready text changes for isolated Aria Code workspaces."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import secrets
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from aria_code.project_review import _is_sensitive
from aria_code.workspace_service import WorkspaceService, WorkspaceServiceError


MAX_CHANGE_BYTES = 1 * 1024 * 1024
MAX_DIFF_CHARS = 120_000


class WorkspaceChangeError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class WorkspaceChange:
    schema_version: str
    change_id: str
    workspace_id: str
    path: str
    operation: str
    status: str
    base_sha256: str
    proposed_sha256: str
    created_at: str
    updated_at: str
    diff: str

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorkspaceChangeService:
    """Stages text changes outside the source tree until approval is verified."""

    def __init__(self, workspaces: WorkspaceService) -> None:
        self.workspaces = workspaces

    def _change_root(self, workspace_id: str) -> Path:
        root = self.workspaces._workspace_dir(workspace_id) / ".aria-changes"
        root.mkdir(mode=0o700, exist_ok=True)
        return root

    def _directory(self, workspace_id: str, change_id: str) -> Path:
        if not change_id.startswith("chg_") or len(change_id) != 28:
            raise WorkspaceChangeError("Invalid change id.")
        root = self._change_root(workspace_id).resolve()
        directory = (root / change_id).resolve()
        if directory.parent != root:
            raise WorkspaceChangeError("Invalid change path.")
        return directory

    @staticmethod
    def _metadata(directory: Path) -> Path:
        return directory / "change.json"

    def propose(
        self,
        workspace_id: str,
        *,
        owner_id: str,
        path: str,
        content: str,
        expected_sha256: str | None = None,
    ) -> WorkspaceChange:
        relative = PurePosixPath(str(path or "").replace("\\", "/"))
        if not relative.parts or relative.is_absolute() or ".." in relative.parts:
            raise WorkspaceChangeError("Change path must be relative to the workspace.")
        if _is_sensitive(relative):
            raise WorkspaceChangeError("Sensitive configuration and key files cannot be written.")
        proposed = content.encode("utf-8")
        if len(proposed) > MAX_CHANGE_BYTES:
            raise WorkspaceChangeError("Proposed file exceeds the 1 MB text-change limit.")
        try:
            target = self.workspaces.source_path(workspace_id, str(relative), owner_id=owner_id)
        except WorkspaceServiceError as exc:
            raise WorkspaceChangeError(str(exc)) from exc
        if target.exists() and not target.is_file():
            raise WorkspaceChangeError("Change target is not a regular file.")
        try:
            before = target.read_bytes() if target.exists() else b""
            before_text = before.decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise WorkspaceChangeError("Only UTF-8 text files can be changed.") from exc
        base_hash = _digest(before)
        if expected_sha256 and not secrets.compare_digest(expected_sha256, base_hash):
            raise WorkspaceChangeError("File changed since it was reviewed; refresh before proposing a patch.")
        if before == proposed:
            raise WorkspaceChangeError("Proposed content is identical to the current file.")
        diff = "".join(difflib.unified_diff(
            before_text.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        ))
        if len(diff) > MAX_DIFF_CHARS:
            diff = diff[:MAX_DIFF_CHARS] + "\n... diff truncated ...\n"
        change_id = f"chg_{secrets.token_hex(12)}"
        directory = self._directory(workspace_id, change_id)
        directory.mkdir(mode=0o700)
        (directory / "before.bin").write_bytes(before)
        (directory / "proposed.txt").write_bytes(proposed)
        created = _now()
        change = WorkspaceChange(
            schema_version="aria.workspace-change.v1",
            change_id=change_id,
            workspace_id=workspace_id,
            path=str(relative),
            operation="update" if target.exists() else "create",
            status="proposed",
            base_sha256=base_hash,
            proposed_sha256=_digest(proposed),
            created_at=created,
            updated_at=created,
            diff=diff,
        )
        self._metadata(directory).write_text(json.dumps(asdict(change), indent=2), encoding="utf-8")
        return change

    def get(self, workspace_id: str, change_id: str, *, owner_id: str) -> WorkspaceChange:
        self.workspaces.get(workspace_id, owner_id=owner_id)
        try:
            raw = json.loads(self._metadata(self._directory(workspace_id, change_id)).read_text(encoding="utf-8"))
            return WorkspaceChange(**raw)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WorkspaceChangeError("Change proposal not found.") from exc

    def _store(self, change: WorkspaceChange) -> None:
        directory = self._directory(change.workspace_id, change.change_id)
        self._metadata(directory).write_text(json.dumps(asdict(change), indent=2), encoding="utf-8")

    def apply(self, workspace_id: str, change_id: str, *, owner_id: str) -> WorkspaceChange:
        change = self.get(workspace_id, change_id, owner_id=owner_id)
        if change.status != "proposed":
            raise WorkspaceChangeError("Only a proposed change can be applied.")
        target = self.workspaces.source_path(workspace_id, change.path, owner_id=owner_id)
        current = target.read_bytes() if target.exists() else b""
        if not secrets.compare_digest(_digest(current), change.base_sha256):
            raise WorkspaceChangeError("File changed after the proposal; approval is stale.")
        proposed = (self._directory(workspace_id, change_id) / "proposed.txt").read_bytes()
        if not secrets.compare_digest(_digest(proposed), change.proposed_sha256):
            raise WorkspaceChangeError("Stored proposal failed its integrity check.")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(proposed)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        updated = WorkspaceChange(**{**asdict(change), "status": "applied", "updated_at": _now()})
        self._store(updated)
        return updated

    def rollback(self, workspace_id: str, change_id: str, *, owner_id: str) -> WorkspaceChange:
        change = self.get(workspace_id, change_id, owner_id=owner_id)
        if change.status != "applied":
            raise WorkspaceChangeError("Only an applied change can be rolled back.")
        target = self.workspaces.source_path(workspace_id, change.path, owner_id=owner_id)
        current = target.read_bytes() if target.exists() else b""
        if not secrets.compare_digest(_digest(current), change.proposed_sha256):
            raise WorkspaceChangeError("File changed after apply; automatic rollback would overwrite newer work.")
        before = (self._directory(workspace_id, change_id) / "before.bin").read_bytes()
        if change.operation == "create":
            target.unlink(missing_ok=True)
        else:
            target.write_bytes(before)
            try:
                target.chmod(0o600)
            except OSError:
                pass
        updated = WorkspaceChange(**{**asdict(change), "status": "rolled_back", "updated_at": _now()})
        self._store(updated)
        return updated
