"""Compaction checkpoints — persist the pre-compaction conversation.

Resolves the context layer's long-standing blocker (artifact schema and
retention policy, decided 2026-07): every compaction first appends a
full-snapshot checkpoint, so the conversation state that compaction is about
to destroy becomes restorable instead of gone.

Schema  ``aria.context_checkpoint.v1`` — one JSON object per line:
    schema, ts, session_id, seq, kind ("compact" | "hard" | "fallback"),
    message_count, truncated, envelope (optional compaction metadata),
    messages (the full pre-compaction conversation; oldest messages dropped
    first if the serialized snapshot would exceed the per-record byte cap).

Storage  ``<root>/<session_id>.jsonl`` (append-only, crash-safe, greppable —
same conventions as the session JSONL store and the durable run store).

Retention (enforced on every write, config-overridable by the caller):
    keep the newest ``keep_per_session`` records per session, and delete
    sibling session files untouched for ``max_age_days``.

Pure besides filesystem writes under ``root``; clock injectable for tests.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from aria_code.packages.aria_core.paths import aria_home

CHECKPOINT_SCHEMA = "aria.context_checkpoint.v1"
DEFAULT_KEEP_PER_SESSION = 5
DEFAULT_MAX_AGE_DAYS = 30
DEFAULT_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


class ContextCheckpointStore:
    def __init__(
        self,
        root: Path,
        *,
        keep_per_session: int = DEFAULT_KEEP_PER_SESSION,
        max_age_days: int = DEFAULT_MAX_AGE_DAYS,
        max_snapshot_bytes: int = DEFAULT_MAX_SNAPSHOT_BYTES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root)
        self.keep_per_session = max(1, int(keep_per_session))
        self.max_age_days = max(1, int(max_age_days))
        self.max_snapshot_bytes = max(64 * 1024, int(max_snapshot_bytes))
        self.clock = clock

    # ── write path ───────────────────────────────────────────────────────────

    def record(
        self,
        session_id: str,
        messages: List[dict],
        *,
        kind: str = "compact",
        envelope: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Append one checkpoint for *session_id*; returns the written record."""
        self.root.mkdir(parents=True, exist_ok=True)
        existing = self.list(session_id)
        seq = (existing[-1]["seq"] + 1) if existing else 1

        snapshot, truncated = self._bounded_snapshot(list(messages or []))
        record = {
            "schema": CHECKPOINT_SCHEMA,
            "ts": self.clock(),
            "session_id": str(session_id),
            "seq": seq,
            "kind": kind,
            "message_count": len(messages or []),
            "truncated": truncated,
            "envelope": dict(envelope) if envelope else {},
            "messages": snapshot,
        }

        kept = (existing + [record])[-self.keep_per_session:]
        path = self._path(session_id)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text(
            "".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in kept),
            encoding="utf-8",
        )
        tmp.replace(path)
        self._expire_old_files()
        return record

    def _bounded_snapshot(self, messages: List[dict]) -> tuple:
        """Drop oldest messages until the serialized snapshot fits the cap."""
        truncated = False
        while messages:
            blob = json.dumps(messages, ensure_ascii=False, default=str)
            if len(blob.encode("utf-8")) <= self.max_snapshot_bytes:
                return messages, truncated
            drop = max(1, len(messages) // 10)
            messages = messages[drop:]
            truncated = True
        return [], truncated

    def _expire_old_files(self) -> None:
        cutoff = self.clock() - self.max_age_days * 86400
        try:
            for f in self.root.glob("*.jsonl"):
                if f.stat().st_mtime < cutoff:
                    f.unlink(missing_ok=True)
        except Exception:
            pass  # retention is best-effort; never break the write path

    # ── read path ────────────────────────────────────────────────────────────

    def list(self, session_id: str) -> List[Dict[str, Any]]:
        path = self._path(session_id)
        if not path.exists():
            return []
        records: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("schema") == CHECKPOINT_SCHEMA:
                records.append(obj)
        return records

    def latest(self, session_id: str) -> Optional[Dict[str, Any]]:
        records = self.list(session_id)
        return records[-1] if records else None

    @staticmethod
    def restore_messages(record: Dict[str, Any]) -> List[dict]:
        """The conversation as it stood before that compaction."""
        msgs = record.get("messages")
        return [dict(m) for m in msgs] if isinstance(msgs, list) else []

    def _path(self, session_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id)) or "session"
        return self.root / f"{safe}.jsonl"


def default_checkpoint_root() -> Path:
    """Same home-resolution rule as the durable run store."""
    import os
    configured = os.getenv("ARIA_HOME")
    if configured:
        root = Path(configured).expanduser()
    else:
        legacy = aria_home()
        root = legacy if legacy.exists() else Path.home() / ".aria-code"
    return root / "runtime" / "context_checkpoints"
