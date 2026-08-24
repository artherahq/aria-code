"""Tests for isolated Git worktrees used by write-capable subagents."""

from __future__ import annotations

import subprocess

import pytest

from aria_code.runtime import CheckpointStore, WorktreeError, WorktreeManager


def _git(repo, *args):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def clean_repository(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Aria Test")
    _git(repo, "config", "user.email", "aria@example.test")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    _git(repo, "commit", "-m", "initial")
    return repo


def test_create_isolated_worktree_and_report_diff(clean_repository, tmp_path):
    manager = WorktreeManager(tmp_path / "worktrees")

    spec = manager.create(task_id="task1234", workspace=clean_repository)

    worktree = tmp_path / "worktrees" / "repo" / "task1234"
    assert spec.path == str(worktree)
    assert spec.branch == "aria/task-task1234"
    assert (worktree / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert _git(worktree, "branch", "--show-current") == spec.branch

    (worktree / "app.py").write_text("value = 2\n", encoding="utf-8")
    assert "app.py" in manager.diff(spec)
    assert (clean_repository / "app.py").read_text(encoding="utf-8") == "value = 1\n"

    manager.remove(spec, force=True)
    assert not worktree.exists()


def test_write_worktree_rejects_dirty_repository(clean_repository, tmp_path):
    (clean_repository / "app.py").write_text("dirty = True\n", encoding="utf-8")
    manager = WorktreeManager(tmp_path / "worktrees")

    with pytest.raises(WorktreeError, match="uncommitted changes"):
        manager.create(task_id="task5678", workspace=clean_repository)


def test_apply_worktree_copies_tracked_and_untracked_changes(clean_repository, tmp_path):
    manager = WorktreeManager(tmp_path / "worktrees")
    spec = manager.create(task_id="taskapply", workspace=clean_repository)
    worktree = tmp_path / "worktrees" / "repo" / "taskapply"
    (worktree / "app.py").write_text("value = 3\n", encoding="utf-8")
    (worktree / "new.py").write_text("created = True\n", encoding="utf-8")

    result = manager.apply(spec)

    assert result.paths == ("app.py", "new.py")
    assert (clean_repository / "app.py").read_text(encoding="utf-8") == "value = 3\n"
    assert (clean_repository / "new.py").read_text(encoding="utf-8") == "created = True\n"
    manager.remove(spec, force=True)
    manager.delete_branch(spec, force=True)


def test_apply_refuses_when_target_workspace_changed(clean_repository, tmp_path):
    manager = WorktreeManager(tmp_path / "worktrees")
    spec = manager.create(task_id="taskconflict", workspace=clean_repository)
    worktree = tmp_path / "worktrees" / "repo" / "taskconflict"
    (worktree / "app.py").write_text("agent = True\n", encoding="utf-8")
    (clean_repository / "app.py").write_text("user = True\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="target workspace changed"):
        manager.apply(spec)

    assert (clean_repository / "app.py").read_text(encoding="utf-8") == "user = True\n"
    manager.remove(spec, force=True)
    manager.delete_branch(spec, force=True)


def test_apply_includes_commits_created_inside_worktree(clean_repository, tmp_path):
    manager = WorktreeManager(tmp_path / "worktrees")
    spec = manager.create(task_id="taskcommit", workspace=clean_repository)
    worktree = tmp_path / "worktrees" / "repo" / "taskcommit"
    (worktree / "app.py").write_text("committed = True\n", encoding="utf-8")
    _git(worktree, "add", "app.py")
    _git(worktree, "commit", "-m", "subagent change")

    result = manager.apply(spec)

    assert result.paths == ("app.py",)
    assert (clean_repository / "app.py").read_text(encoding="utf-8") == "committed = True\n"
    manager.remove(spec, force=True)
    manager.delete_branch(spec, force=True)


def test_completed_subagent_apply_creates_rewind_checkpoint(
    clean_repository,
    tmp_path,
    monkeypatch,
):
    from aria_code.runtime.subagent import _TASKS, SubagentTask, apply_task_worktree

    monkeypatch.setenv("ARIA_HOME", str(tmp_path / "aria-home"))
    manager = WorktreeManager(tmp_path / "worktrees")
    spec = manager.create(task_id="subapply", workspace=clean_repository)
    worktree = tmp_path / "worktrees" / "repo" / "subapply"
    (worktree / "app.py").write_text("value = 9\n", encoding="utf-8")
    task = SubagentTask(
        task_id="subapply",
        prompt="edit app",
        status="done",
        mode="workspace-write",
        isolation="worktree",
        workspace=str(worktree),
        session_id="subagent-session",
        branch=spec.branch,
        worktree_spec=spec,
    )
    _TASKS[task.task_id] = task
    try:
        result = apply_task_worktree(task.task_id)

        assert result["success"] is True
        assert (clean_repository / "app.py").read_text(encoding="utf-8") == "value = 9\n"
        assert task.applied is True
        checkpoints = CheckpointStore().list(session_id="subagent-session")
        assert len(checkpoints) == 1
        CheckpointStore().restore_checkpoint(checkpoints[0].checkpoint_id)
        assert (clean_repository / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    finally:
        _TASKS.pop(task.task_id, None)


def test_repository_root_accepts_nested_workspace(clean_repository):
    nested = clean_repository / "src" / "nested"
    nested.mkdir(parents=True)

    assert WorktreeManager.repository_root(nested) == clean_repository.resolve()
