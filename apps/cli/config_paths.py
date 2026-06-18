"""Shared config path resolution for Aria CLI components."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class AriaConfigPaths:
    config_dir: Path
    config_file: Path
    history_file: Path
    sessions_dir: Path
    providers_file: Path
    hooks_file: Path
    user_output_root: Path
    user_generated_dir: Path
    user_projects_dir: Path


def resolve_config_dir() -> Path:
    """Resolve the user config directory with stable precedence."""
    if "ARIA_HOME" in os.environ:
        return Path(os.environ["ARIA_HOME"]).expanduser()
    legacy = Path.home() / ".arthera"
    if legacy.exists():
        return legacy
    return Path.home() / ".aria-code"


def resolve_user_output_root() -> Path:
    """Resolve the per-user output root for generated code and user artifacts."""
    configured = os.getenv("ARIA_USER_OUTPUT_ROOT")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Documents" / "Aria Code"


def resolve_paths(config_dir: Optional[Path] = None) -> AriaConfigPaths:
    root = Path(config_dir).expanduser() if config_dir else resolve_config_dir()
    user_output_root = resolve_user_output_root()
    return AriaConfigPaths(
        config_dir=root,
        config_file=root / "config.json",
        history_file=root / "history",
        sessions_dir=root / "sessions",
        providers_file=root / "providers.json",
        hooks_file=root / "hooks.json",
        user_output_root=user_output_root,
        user_generated_dir=user_output_root / "generated",
        user_projects_dir=user_output_root / "projects",
    )


def config_snapshot(config_dir: Optional[Path] = None) -> dict[str, str]:
    """Return a JSON-serializable snapshot of resolved config paths."""
    paths = resolve_paths(config_dir)
    return {
        "config_dir": str(paths.config_dir),
        "config_file": str(paths.config_file),
        "history_file": str(paths.history_file),
        "sessions_dir": str(paths.sessions_dir),
        "providers_file": str(paths.providers_file),
        "hooks_file": str(paths.hooks_file),
        "user_output_root": str(paths.user_output_root),
        "user_generated_dir": str(paths.user_generated_dir),
        "user_projects_dir": str(paths.user_projects_dir),
    }
