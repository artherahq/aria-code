from __future__ import annotations

from pathlib import Path


def test_resolve_config_dir_prefers_aria_home(monkeypatch, tmp_path):
    from apps.cli.config_paths import resolve_config_dir

    monkeypatch.setenv("ARIA_HOME", str(tmp_path / "aria-home"))

    assert resolve_config_dir() == tmp_path / "aria-home"


def test_resolve_config_dir_falls_back_to_legacy_when_present(monkeypatch, tmp_path):
    from apps.cli.config_paths import resolve_config_dir

    monkeypatch.delenv("ARIA_HOME", raising=False)
    legacy = Path.home() / ".arthera"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".arthera").mkdir()

    assert resolve_config_dir() == tmp_path / ".arthera"


def test_config_snapshot_is_serializable(monkeypatch, tmp_path):
    from apps.cli.config_paths import config_snapshot

    monkeypatch.setenv("ARIA_HOME", str(tmp_path / "aria-home"))
    monkeypatch.setenv("ARIA_USER_OUTPUT_ROOT", str(tmp_path / "user-output"))

    snap = config_snapshot()

    assert snap["config_dir"] == str(tmp_path / "aria-home")
    assert snap["user_generated_dir"] == str(tmp_path / "user-output" / "generated")
    assert snap["user_projects_dir"] == str(tmp_path / "user-output" / "projects")
