"""Local-first feedback storage and privacy settings."""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class PrivacySettings:
    """User-controlled data sharing settings."""

    data_sharing: bool = False
    feedback_upload: bool = False

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "PrivacySettings":
        return cls(
            data_sharing=bool(config.get("data_sharing", False)),
            feedback_upload=bool(config.get("feedback_upload", False)),
        )

    def apply_to_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        config["data_sharing"] = self.data_sharing
        config["feedback_upload"] = self.feedback_upload
        return config


@dataclass(frozen=True)
class FeedbackRecord:
    """One local feedback event for a model response."""

    rating: str
    message: str
    comment: str = ""
    model: str = ""
    session_id: str = ""
    timestamp: str = ""
    message_index: int | None = None
    shared: bool = False

    @classmethod
    def create(
        cls,
        *,
        rating: str,
        message: str,
        comment: str = "",
        model: str = "",
        session_id: str = "",
        message_index: int | None = None,
        shared: bool = False,
    ) -> "FeedbackRecord":
        return cls(
            rating=rating,
            message=message,
            comment=comment,
            model=model,
            session_id=session_id,
            timestamp=datetime.now().isoformat(),
            message_index=message_index,
            shared=shared,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


class FeedbackStore:
    """Append-only local feedback store with explicit export/delete actions."""

    def __init__(self, config_dir: str | Path) -> None:
        self.config_dir = Path(config_dir).expanduser()
        self.feedback_dir = self.config_dir / "feedback"
        self.feedback_file = self.feedback_dir / "feedback.jsonl"
        self.export_dir = self.feedback_dir / "exports"

    def append(self, record: FeedbackRecord) -> Path:
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        with self.feedback_file.open("a", encoding="utf-8") as handle:
            handle.write(record.to_json() + "\n")
        return self.feedback_file

    def iter_records(self) -> Iterable[Dict[str, Any]]:
        if not self.feedback_file.exists():
            return []
        rows: List[Dict[str, Any]] = []
        with self.feedback_file.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows

    def count(self) -> int:
        return sum(1 for _ in self.iter_records())

    def export_jsonl(self, destination: str | Path | None = None) -> Path:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        if destination is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            destination = self.export_dir / f"feedback_export_{stamp}.jsonl"
        dest = Path(destination).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self.feedback_file.exists():
            shutil.copyfile(self.feedback_file, dest)
        else:
            dest.write_text("", encoding="utf-8")
        return dest

    def delete_all(self) -> int:
        count = self.count()
        if self.feedback_file.exists():
            self.feedback_file.unlink()
        return count
