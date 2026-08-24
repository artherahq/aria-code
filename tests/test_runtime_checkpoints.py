"""Tests for durable, conflict-aware code checkpoints."""

from __future__ import annotations

import stat

import pytest

from aria_code.runtime import CheckpointConflictError, CheckpointStore, RunStatus, RunStore


@pytest.fixture
def stores(tmp_path):
    database = tmp_path / "runtime" / "runs.sqlite3"
    return RunStore(database), CheckpointStore(database)


def _run(run_store: RunStore, session_id: str = "session-1"):
    run = run_store.create_run(
        session_id=session_id,
        prompt="edit files",
        workspace="/tmp/project",
    )
    run_store.transition(run.run_id, RunStatus.RUNNING)
    return run


def _record(
    store: CheckpointStore,
    path,
    before: str,
    after: str,
    *,
    run_id: str | None = None,
    session_id: str = "session-1",
    existed_before: bool = True,
):
    path.write_text(after, encoding="utf-8")
    return store.record_change(
        path=path,
        before_content=before,
        after_content=after,
        existed_before=existed_before,
        source="edit_file",
        run_id=run_id,
        session_id=session_id,
        before_mode=0o640 if existed_before else None,
    )


def test_restore_existing_file_content_and_mode(stores, tmp_path):
    _, checkpoint_store = stores
    target = tmp_path / "strategy.py"
    target.write_text("before\n", encoding="utf-8")
    target.chmod(0o640)
    checkpoint = _record(checkpoint_store, target, "before\n", "after\n")
    target.chmod(0o600)

    result = checkpoint_store.restore_checkpoint(checkpoint.checkpoint_id)

    assert target.read_text(encoding="utf-8") == "before\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert result.restored_paths == (str(target),)
    restored = checkpoint_store.get(checkpoint.checkpoint_id)
    assert restored is not None and restored.status == "restored"


def test_restore_removes_file_created_by_agent(stores, tmp_path):
    _, checkpoint_store = stores
    target = tmp_path / "new_file.py"
    checkpoint = _record(
        checkpoint_store,
        target,
        "",
        "print('complete implementation')\n",
        existed_before=False,
    )

    checkpoint_store.restore_checkpoint(checkpoint.checkpoint_id)

    assert not target.exists()


def test_restore_refuses_to_overwrite_later_user_change(stores, tmp_path):
    _, checkpoint_store = stores
    target = tmp_path / "model.py"
    checkpoint = _record(checkpoint_store, target, "v1\n", "v2\n")
    target.write_text("user edit\n", encoding="utf-8")

    with pytest.raises(CheckpointConflictError, match="changed after"):
        checkpoint_store.restore_checkpoint(checkpoint.checkpoint_id)

    assert target.read_text(encoding="utf-8") == "user edit\n"
    active = checkpoint_store.get(checkpoint.checkpoint_id)
    assert active is not None and active.status == "active"


def test_restore_run_rewinds_repeated_edits_in_reverse_order(stores, tmp_path):
    run_store, checkpoint_store = stores
    run = _run(run_store)
    target = tmp_path / "pipeline.py"
    first = _record(checkpoint_store, target, "A\n", "B\n", run_id=run.run_id)
    second = _record(checkpoint_store, target, "B\n", "C\n", run_id=run.run_id)

    result = checkpoint_store.restore_run(run.run_id)

    assert target.read_text(encoding="utf-8") == "A\n"
    assert result.checkpoint_ids == (second.checkpoint_id, first.checkpoint_id)
    assert checkpoint_store.list(run_id=run.run_id, status="active") == []
    assert run_store.events(run.run_id)[-1].event_type == "checkpoint_restored"


def test_restore_run_preflights_all_files_before_mutating(stores, tmp_path):
    run_store, checkpoint_store = stores
    run = _run(run_store)
    first_path = tmp_path / "first.py"
    second_path = tmp_path / "second.py"
    _record(checkpoint_store, first_path, "one\n", "two\n", run_id=run.run_id)
    _record(checkpoint_store, second_path, "x\n", "y\n", run_id=run.run_id)
    first_path.write_text("user change\n", encoding="utf-8")

    with pytest.raises(CheckpointConflictError):
        checkpoint_store.restore_run(run.run_id)

    assert first_path.read_text(encoding="utf-8") == "user change\n"
    assert second_path.read_text(encoding="utf-8") == "y\n"


def test_latest_restore_groups_checkpoints_by_run(stores, tmp_path):
    run_store, checkpoint_store = stores
    older = _run(run_store, "session-1")
    old_path = tmp_path / "old.py"
    _record(checkpoint_store, old_path, "old-a\n", "old-b\n", run_id=older.run_id)

    latest = _run(run_store, "session-1")
    latest_path = tmp_path / "latest.py"
    _record(checkpoint_store, latest_path, "new-a\n", "new-b\n", run_id=latest.run_id)

    result = checkpoint_store.restore_latest(session_id="session-1")

    assert result.run_id == latest.run_id
    assert latest_path.read_text(encoding="utf-8") == "new-a\n"
    assert old_path.read_text(encoding="utf-8") == "old-b\n"


def test_standalone_checkpoint_can_be_restored_as_latest(stores, tmp_path):
    _, checkpoint_store = stores
    target = tmp_path / "manual.py"
    _record(checkpoint_store, target, "before\n", "after\n", session_id="manual")

    result = checkpoint_store.restore_latest(session_id="manual")

    assert result.run_id is None
    assert target.read_text(encoding="utf-8") == "before\n"


def test_edit_tool_records_checkpoint_when_session_context_is_present(tmp_path, monkeypatch):
    monkeypatch.setenv("ARIA_HOME", str(tmp_path / "aria-home"))
    from aria_code.apps.cli.tools.write_tools import tool_edit_file

    target = tmp_path / "tool_edit.py"
    target.write_text("value = 1\nprint(value)\n", encoding="utf-8")

    result = tool_edit_file({
        "path": str(target),
        "old_string": "value = 1",
        "new_string": "value = 2",
        "_session_id": "tool-session",
    })

    assert result["success"] is True
    checkpoint_id = result["data"]["checkpoint_id"]
    store = CheckpointStore()
    store.restore_checkpoint(checkpoint_id)
    assert target.read_text(encoding="utf-8") == "value = 1\nprint(value)\n"
