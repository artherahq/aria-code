"""JSON-backed session persistence for Aria Code."""

from __future__ import annotations

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional


def _resolve_config_dir() -> Path:
    if "ARIA_HOME" in os.environ:
        return Path(os.environ["ARIA_HOME"]).expanduser()
    legacy = Path.home() / ".arthera"
    if legacy.exists():
        return legacy
    return Path.home() / ".aria-code"


class SessionManager:
    """Manage chat sessions with local file persistence."""

    def __init__(self, sessions_dir: Optional[Path] = None):
        self.root = sessions_dir or (_resolve_config_dir() / "sessions")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def save_session(self, session_id: str, conversation: list, metadata: dict = None):
        meta = dict(metadata or {})
        if not meta.get("created_at"):
            meta["created_at"] = datetime.now().isoformat()
        for msg in conversation:
            if msg.get("role") == "user":
                meta.setdefault("title", str(msg.get("content", ""))[:60])
                break
        data = {
            "id": session_id,
            "messages": conversation,
            "metadata": meta,
            "updated_at": datetime.now().isoformat(),
        }
        path = self._path(session_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_session(self, session_id: str) -> Optional[dict]:
        path = self._path(session_id)
        if path.exists():
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    def list_sessions(self, limit: int = 20) -> list:
        sessions = []
        for path in sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                sessions.append({
                    "id": data.get("id", path.stem),
                    "title": data.get("metadata", {}).get("title", "Untitled"),
                    "messages": len(data.get("messages", [])),
                    "updated": data.get("updated_at", ""),
                })
            except Exception:
                continue
            if len(sessions) >= limit:
                break
        return sessions

    def delete_session(self, session_id: str) -> bool:
        path = self._path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False
