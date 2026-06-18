"""Local artifact paths for reports, charts, projects, and strategy output."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


def slugify_topic(topic: Optional[str], fallback: str = "general") -> str:
    raw = str(topic or "").strip()
    if not raw:
        raw = fallback
    raw = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "-", raw)
    raw = raw.strip("-._")
    return raw[:80] or fallback


@dataclass(frozen=True)
class ArtifactRecord:
    """Resolved paths for one generated artifact bundle."""

    category: str
    topic: str
    directory: Path
    path: Path
    metadata_path: Path
    raw_data_path: Path


def _project_artifact_root() -> Optional[Path]:
    """Return project-level artifact root from .ariarc when configured."""
    try:
        from ariarc import AriaRC

        rc = AriaRC.load()
        source = rc.source_path
        if not source:
            return None
        data = getattr(rc, "_data", None)
        if not isinstance(data, dict):
            text = source.read_text(encoding="utf-8")
            import json as _json
            import re as _re
            text = _re.sub(r"/\*.*?\*/", "", text, flags=_re.DOTALL)
            text = _re.sub(r'(?<!:)(?<!https)//[^\n]*', "", text)
            data = _json.loads(text)
        configured = data.get("artifact_root") or data.get("output_dir")
        if configured:
            path = Path(str(configured)).expanduser()
            if not path.is_absolute():
                path = source.parent / path
            return path
        return source.parent / "aria-output"
    except Exception:
        return None
    return None


def artifact_root() -> Path:
    """Return the per-user local artifact root.

    Override with ARIA_ARTIFACT_ROOT when a user wants reports/projects under a
    specific workspace. Defaults to a product-owned folder under that user's
    Documents directory, not the developer's Arthera repo.
    """
    configured = os.getenv("ARIA_ARTIFACT_ROOT")
    if configured:
        return Path(configured).expanduser()
    project_root = _project_artifact_root()
    if project_root:
        return project_root
    return Path.home() / "Documents" / "Aria Code"


def user_output_root() -> Path:
    """Return a user-owned output root that never falls back to project cwd.

    Use this for generated code, strategies, and project scaffolds. Reports and
    charts may still honor project `.ariarc` via `artifact_root()`, but user
    code should not silently land in the Aria source checkout.
    """
    configured = os.getenv("ARIA_USER_OUTPUT_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Documents" / "Aria Code"


def user_projects_dir(create: bool = True) -> Path:
    path = user_output_root() / "projects"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def user_generated_dir(create: bool = True) -> Path:
    path = user_output_root() / "generated"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_dir(category: str, topic: Optional[str] = None, create: bool = True) -> Path:
    parts = [slugify_topic(part) for part in str(category or "artifacts").split("/") if part]
    base = artifact_root().joinpath(*parts)
    if topic:
        base = base / slugify_topic(topic)
    if create:
        base.mkdir(parents=True, exist_ok=True)
    return base


def create_artifact(
    category: str,
    topic: Optional[str],
    stem: str,
    suffix: str,
    *,
    timestamp: Optional[datetime] = None,
    create: bool = True,
) -> ArtifactRecord:
    """Create a dated artifact bundle path and its sidecar metadata paths.

    Output layout:

        <root>/<category>/<topic>/YYYY-MM-DD/<HHMMSS>_<stem><suffix>
        <root>/<category>/<topic>/YYYY-MM-DD/<HHMMSS>_<stem>.metadata.json
        <root>/<category>/<topic>/YYYY-MM-DD/<HHMMSS>_<stem>.raw_data.json
    """

    ts = timestamp or datetime.now()
    topic_slug = slugify_topic(topic)
    stem_slug = slugify_topic(stem, fallback="artifact")
    suffix = suffix if suffix.startswith(".") else f".{suffix}"
    directory = artifact_dir(category, topic_slug, create=False) / ts.strftime("%Y-%m-%d")
    prefix = ts.strftime("%H%M%S")
    base_name = f"{prefix}_{stem_slug}"
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return ArtifactRecord(
        category=category,
        topic=topic_slug,
        directory=directory,
        path=directory / f"{base_name}{suffix}",
        metadata_path=directory / f"{base_name}.metadata.json",
        raw_data_path=directory / f"{base_name}.raw_data.json",
    )


def write_artifact_metadata(record: ArtifactRecord, metadata: Dict[str, Any]) -> Path:
    payload = {
        "artifact": {
            "category": record.category,
            "topic": record.topic,
            "path": str(record.path),
            "metadata_path": str(record.metadata_path),
            "raw_data_path": str(record.raw_data_path),
        },
        **metadata,
    }
    record.metadata_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return record.metadata_path


def write_artifact_raw_data(record: ArtifactRecord, data: Any) -> Path:
    record.raw_data_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return record.raw_data_path


def recent_artifacts(limit: int = 20, root: Optional[Path] = None) -> list[Dict[str, Any]]:
    """Return recent artifact metadata records, newest first."""
    base = root or artifact_root()
    if not base.exists():
        return []
    items: list[Dict[str, Any]] = []
    for path in base.rglob("*.metadata.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            artifact = data.get("artifact") or {}
            output_path = Path(str(artifact.get("path") or ""))
            mtime = output_path.stat().st_mtime if output_path.exists() else path.stat().st_mtime
            items.append({
                "kind": data.get("kind") or artifact.get("category") or "artifact",
                "status": data.get("status") or "unknown",
                "topic": artifact.get("topic") or data.get("symbol") or "",
                "path": str(output_path) if str(output_path) else "",
                "metadata_path": str(path),
                "created_at": data.get("created_at") or "",
                "mtime": mtime,
            })
        except Exception:
            continue
    items.sort(key=lambda item: item.get("mtime") or 0, reverse=True)
    return items[: max(0, int(limit))]
