from __future__ import annotations

import io
import zipfile

import pytest

from aria_code.workspace_changes import WorkspaceChangeError, WorkspaceChangeService
from aria_code.workspace_service import WorkspaceService


def _zip(entries: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)
    return output.getvalue()


def _workspace(tmp_path):
    service = WorkspaceService(tmp_path)
    manifest = service.create_from_archive(
        _zip({"src/app.py": "value = 1\n"}), "project.zip", owner_id="owner-a", run_id="run-a"
    )
    return service, manifest


def test_change_requires_proposal_then_applies_and_rolls_back(tmp_path):
    workspaces, manifest = _workspace(tmp_path)
    changes = WorkspaceChangeService(workspaces)
    change = changes.propose(
        manifest.workspace_id,
        owner_id="owner-a",
        path="src/app.py",
        content="value = 2\n",
    )
    target = tmp_path / manifest.workspace_id / "source" / "src" / "app.py"
    assert target.read_text() == "value = 1\n"
    assert "-value = 1" in change.diff
    assert "+value = 2" in change.diff

    applied = changes.apply(manifest.workspace_id, change.change_id, owner_id="owner-a")
    assert applied.status == "applied"
    assert target.read_text() == "value = 2\n"

    rolled_back = changes.rollback(manifest.workspace_id, change.change_id, owner_id="owner-a")
    assert rolled_back.status == "rolled_back"
    assert target.read_text() == "value = 1\n"


def test_stale_change_cannot_apply(tmp_path):
    workspaces, manifest = _workspace(tmp_path)
    changes = WorkspaceChangeService(workspaces)
    change = changes.propose(
        manifest.workspace_id, owner_id="owner-a", path="src/app.py", content="value = 2\n"
    )
    target = tmp_path / manifest.workspace_id / "source" / "src" / "app.py"
    target.write_text("user = 'newer'\n")
    with pytest.raises(WorkspaceChangeError, match="stale"):
        changes.apply(manifest.workspace_id, change.change_id, owner_id="owner-a")


def test_change_rejects_sensitive_and_cross_tenant_paths(tmp_path):
    workspaces, manifest = _workspace(tmp_path)
    changes = WorkspaceChangeService(workspaces)
    with pytest.raises(WorkspaceChangeError, match="Sensitive"):
        changes.propose(manifest.workspace_id, owner_id="owner-a", path=".env", content="TOKEN=x")
    with pytest.raises(WorkspaceChangeError, match="not found"):
        changes.propose(manifest.workspace_id, owner_id="owner-b", path="src/app.py", content="x")
    with pytest.raises(WorkspaceChangeError, match="relative"):
        changes.propose(manifest.workspace_id, owner_id="owner-a", path="../escape.py", content="x")


def test_created_file_is_removed_by_rollback(tmp_path):
    workspaces, manifest = _workspace(tmp_path)
    changes = WorkspaceChangeService(workspaces)
    change = changes.propose(
        manifest.workspace_id, owner_id="owner-a", path="tests/test_app.py", content="def test_ok():\n    assert True\n"
    )
    target = tmp_path / manifest.workspace_id / "source" / "tests" / "test_app.py"
    changes.apply(manifest.workspace_id, change.change_id, owner_id="owner-a")
    assert target.exists()
    changes.rollback(manifest.workspace_id, change.change_id, owner_id="owner-a")
    assert not target.exists()
