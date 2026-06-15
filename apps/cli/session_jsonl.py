"""JSONL-backed session persistence for Aria Code.

Why JSONL instead of JSON?
  • Append-per-turn — no need to rewrite the whole file on every message
  • Crash-safe — partial writes leave previous turns intact
  • Streamable — readers can tail -f a live session

File layout:  ~/.arthera/sessions/<session_id>.jsonl
Each line is one JSON object:
  {"type": "meta",    "id": "...", "title": "...", "created_at": "..."}
  {"type": "message", "role": "user",      "content": "...", "ts": "..."}
  {"type": "message", "role": "assistant", "content": "...", "ts": "..."}
  {"type": "meta",    "updated_at": "..."}   ← appended on each save

Reading: scan all lines, reconstruct conversation from "message" entries.
Last "meta" wins for title / timestamps.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SESSIONS_DIR = Path.home() / ".arthera" / "sessions"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_path(session_id: str) -> Path:
    return _SESSIONS_DIR / f"{session_id}.jsonl"


class JsonlSessionStore:
    """Read/write JSONL session files."""

    def __init__(self, sessions_dir: Optional[Path] = None) -> None:
        self.root = sessions_dir or _SESSIONS_DIR
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    # ── Write ─────────────────────────────────────────────────────────────────

    def init_session(self, session_id: str, title: str = "") -> None:
        """Write the opening meta line. Call once at session start."""
        p = self._path(session_id)
        if p.exists():
            return
        line = json.dumps({
            "type": "meta",
            "id": session_id,
            "title": title or "",
            "created_at": _now(),
        }, ensure_ascii=False)
        p.write_text(line + "\n", encoding="utf-8")

    def append_message(self, session_id: str, role: str, content: str) -> None:
        """Append one message turn. Thread-safe for single-process use."""
        p = self._path(session_id)
        line = json.dumps({
            "type": "message",
            "role": role,
            "content": content,
            "ts": _now(),
        }, ensure_ascii=False)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def flush_meta(self, session_id: str, title: str = "", extra: Optional[dict] = None) -> None:
        """Append an updated meta line (title, timestamps)."""
        p = self._path(session_id)
        meta: dict = {"type": "meta", "updated_at": _now()}
        if title:
            meta["title"] = title
        if extra:
            meta.update(extra)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(meta, ensure_ascii=False) + "\n")

    def save_conversation(
        self,
        session_id: str,
        conversation: list[dict],
        title: str = "",
    ) -> None:
        """Full rewrite — used when bulk-importing a JSON session into JSONL."""
        p = self._path(session_id)
        lines = []
        lines.append(json.dumps({
            "type": "meta",
            "id": session_id,
            "title": title or "",
            "created_at": _now(),
            "updated_at": _now(),
        }, ensure_ascii=False))
        for msg in conversation:
            lines.append(json.dumps({
                "type": "message",
                "role": msg.get("role", "user"),
                "content": str(msg.get("content", "")),
                "ts": _now(),
            }, ensure_ascii=False))
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ── Read ──────────────────────────────────────────────────────────────────

    def load_session(self, session_id: str) -> Optional[dict]:
        """Load a session; returns None if not found."""
        p = self._path(session_id)
        if not p.exists():
            return None

        messages: list[dict] = []
        meta: dict = {"id": session_id}

        for raw in p.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue

            t = obj.get("type")
            if t == "meta":
                meta.update({k: v for k, v in obj.items() if k != "type"})
            elif t == "message":
                messages.append({
                    "role": obj.get("role", "user"),
                    "content": obj.get("content", ""),
                })

        return {
            "id": session_id,
            "messages": messages,
            "metadata": {
                "title": meta.get("title", "Untitled"),
                "created_at": meta.get("created_at", ""),
                "updated_at": meta.get("updated_at", ""),
            },
        }

    def list_sessions(self, limit: int = 20) -> list[dict]:
        """Return recent sessions sorted by mtime, newest first."""
        sessions = []
        for p in sorted(
            self.root.glob("*.jsonl"),
            key=lambda x: x.stat().st_mtime,
            reverse=True,
        ):
            session_id = p.stem
            meta: dict = {}
            msg_count = 0
            try:
                for raw in p.read_text(encoding="utf-8").splitlines():
                    raw = raw.strip()
                    if not raw:
                        continue
                    obj = json.loads(raw)
                    if obj.get("type") == "meta":
                        meta.update(obj)
                    elif obj.get("type") == "message":
                        msg_count += 1
            except Exception:
                continue

            sessions.append({
                "id": session_id,
                "title": meta.get("title", "Untitled"),
                "messages": msg_count,
                "updated": meta.get("updated_at", ""),
                "created": meta.get("created_at", ""),
            })
            if len(sessions) >= limit:
                break

        return sessions

    def delete_session(self, session_id: str) -> bool:
        p = self._path(session_id)
        if p.exists():
            p.unlink()
            return True
        return False

    def search_sessions(self, keyword: str, limit: int = 10) -> list[dict]:
        """Full-text search across all session JSONL files."""
        kw = keyword.lower()
        matches = []
        for p in self.root.glob("*.jsonl"):
            try:
                text = p.read_text(encoding="utf-8")
                if kw not in text.lower():
                    continue
                result = self.load_session(p.stem)
                if result:
                    matches.append(result)
            except Exception:
                continue
            if len(matches) >= limit:
                break
        return matches
