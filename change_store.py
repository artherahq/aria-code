"""Staged file-change store for Aria Code.

The CLI still supports direct writes for existing workflows, but every write can
now be represented as a hash-checked change first. This gives us Codex/Claude
Code style review/apply/reject primitives without coupling the logic to the REPL.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import pathlib
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


class ChangeConflictError(RuntimeError):
    """Raised when the file changed after a staged change was created."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


@dataclass(frozen=True)
class StagedChange:
    change_id: str
    path: str
    before_content: str
    after_content: str
    before_hash: str
    after_hash: str
    diff: str
    created_at: float
    source: str = "aria-code"
    applied: bool = False
    rejected: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class ChangeStore:
    """In-memory staged change store with conflict-aware apply."""

    def __init__(self) -> None:
        self._changes: Dict[str, StagedChange] = {}

    def stage(self, path: str | pathlib.Path, after_content: str, source: str = "aria-code") -> StagedChange:
        target = pathlib.Path(path).expanduser().resolve()
        before = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        rel = str(target)
        diff = "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after_content.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        ))
        change = StagedChange(
            change_id=uuid.uuid4().hex[:12],
            path=rel,
            before_content=before,
            after_content=after_content,
            before_hash=sha256_text(before),
            after_hash=sha256_text(after_content),
            diff=diff,
            created_at=time.time(),
            source=source,
        )
        self._changes[change.change_id] = change
        return change

    def list(self, include_closed: bool = False) -> List[StagedChange]:
        changes = list(self._changes.values())
        if not include_closed:
            changes = [c for c in changes if not c.applied and not c.rejected]
        return sorted(changes, key=lambda c: c.created_at)

    def get(self, change_id: str) -> Optional[StagedChange]:
        return self._changes.get(change_id)

    def apply(self, change_id: str) -> StagedChange:
        change = self._require_open(change_id)
        target = pathlib.Path(change.path)
        current = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
        if sha256_text(current) != change.before_hash:
            raise ChangeConflictError(f"File changed since staging: {change.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.aria-tmp-{uuid.uuid4().hex[:8]}")
        tmp.write_text(change.after_content, encoding="utf-8")
        os.replace(tmp, target)
        applied = StagedChange(**{**change.to_dict(), "applied": True})
        self._changes[change.change_id] = applied
        return applied

    def reject(self, change_id: str) -> StagedChange:
        change = self._require_open(change_id)
        rejected = StagedChange(**{**change.to_dict(), "rejected": True})
        self._changes[change.change_id] = rejected
        return rejected

    def clear_closed(self) -> int:
        closed = [cid for cid, c in self._changes.items() if c.applied or c.rejected]
        for cid in closed:
            del self._changes[cid]
        return len(closed)

    def _require_open(self, change_id: str) -> StagedChange:
        key = (change_id or "").strip()
        change = self._changes.get(key)
        if change is None:
            matches = [c for cid, c in self._changes.items() if cid.startswith(key)]
            if len(matches) == 1:
                change = matches[0]
        if change is None:
            raise KeyError(f"Unknown change id: {change_id}")
        if change.applied:
            raise ValueError(f"Change already applied: {change.change_id}")
        if change.rejected:
            raise ValueError(f"Change already rejected: {change.change_id}")
        return change


GLOBAL_CHANGE_STORE = ChangeStore()
