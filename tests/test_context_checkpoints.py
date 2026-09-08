"""Tests for aria.context_checkpoint.v1 — the compaction checkpoint store and
its wiring into cmd_compact/_smart_compact_async. Pins the decided schema
(full snapshot, 2MB cap) and retention (keep-5 per session, 30-day expiry),
plus the never-break-compaction guarantee of the recording helper."""

import json
from pathlib import Path
from types import SimpleNamespace

from aria_code.packages.aria_services.context_checkpoints import (
    CHECKPOINT_SCHEMA,
    ContextCheckpointStore,
    default_checkpoint_root,
)


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now
    def __call__(self):
        return self.now


def _msgs(n, size=20):
    return [{"role": "user", "content": "x" * size, "i": i} for i in range(n)]


# ── store ─────────────────────────────────────────────────────────────────────

def test_record_and_restore_roundtrip(tmp_path):
    store = ContextCheckpointStore(tmp_path, clock=FakeClock())
    rec = store.record("sess1", _msgs(6), kind="compact", envelope={"old_message_count": 6})
    assert rec["schema"] == CHECKPOINT_SCHEMA
    assert rec["seq"] == 1
    assert rec["message_count"] == 6
    restored = ContextCheckpointStore.restore_messages(store.latest("sess1"))
    assert len(restored) == 6
    assert restored[0]["i"] == 0


def test_seq_increments_and_keep_per_session_trims(tmp_path):
    store = ContextCheckpointStore(tmp_path, keep_per_session=3, clock=FakeClock())
    for _ in range(5):
        store.record("s", _msgs(2))
    records = store.list("s")
    assert len(records) == 3                      # trimmed to newest 3
    assert [r["seq"] for r in records] == [3, 4, 5]


def test_snapshot_cap_drops_oldest_first_and_flags_truncated(tmp_path):
    store = ContextCheckpointStore(tmp_path, max_snapshot_bytes=64 * 1024, clock=FakeClock())
    big = _msgs(40, size=4000)                    # ~160KB serialized
    rec = store.record("s", big)
    assert rec["truncated"] is True
    kept = rec["messages"]
    assert kept                                    # something survived
    assert kept[-1]["i"] == 39                     # newest kept
    assert kept[0]["i"] > 0                        # oldest dropped


def test_expiry_deletes_stale_session_files(tmp_path):
    clk = FakeClock()
    store = ContextCheckpointStore(tmp_path, max_age_days=30, clock=clk)
    store.record("old_sess", _msgs(2))
    Path(store._path("old_sess")).touch()          # mtime = real now
    import os
    old = clk.now - 40 * 86400
    os.utime(store._path("old_sess"), (old, old))
    store.record("new_sess", _msgs(2))             # write triggers expiry
    assert not store._path("old_sess").exists()
    assert store._path("new_sess").exists()


def test_corrupt_lines_are_skipped(tmp_path):
    store = ContextCheckpointStore(tmp_path, clock=FakeClock())
    store.record("s", _msgs(1))
    path = store._path("s")
    path.write_text(path.read_text() + "{not json\n", encoding="utf-8")
    assert len(store.list("s")) == 1


def test_session_id_is_sanitized_for_filenames(tmp_path):
    store = ContextCheckpointStore(tmp_path, clock=FakeClock())
    store.record("../evil/../../id", _msgs(1))
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert "/" not in files[0].name and ".." not in files[0].name


def test_default_root_honors_aria_home(monkeypatch, tmp_path):
    monkeypatch.setenv("ARIA_HOME", str(tmp_path))
    assert default_checkpoint_root() == tmp_path / "runtime" / "context_checkpoints"


# ── compaction wiring ─────────────────────────────────────────────────────────

def _mixin_self(conversation, tmp_path, monkeypatch):
    """A minimal SlashCommands-like object exposing the mixin helper."""
    from aria_code.apps.cli.commands.session_ux_cmds import SessionUxCommandsMixin
    monkeypatch.setenv("ARIA_HOME", str(tmp_path))
    obj = SimpleNamespace(terminal=SimpleNamespace(
        conversation=conversation,
        config={"context_checkpoint_keep": 5, "context_checkpoint_max_age_days": 30},
        session_id="wired",
    ))
    obj._record_context_checkpoint = (
        SessionUxCommandsMixin._record_context_checkpoint.__get__(obj)
    )
    return obj


def test_helper_records_current_conversation(tmp_path, monkeypatch):
    obj = _mixin_self(_msgs(4), tmp_path, monkeypatch)
    obj._record_context_checkpoint("compact", envelope={"old_message_count": 4})
    store = ContextCheckpointStore(tmp_path / "runtime" / "context_checkpoints")
    rec = store.latest("wired")
    assert rec is not None
    assert rec["kind"] == "compact"
    assert rec["message_count"] == 4
    assert rec["envelope"]["old_message_count"] == 4


def test_helper_never_raises_even_when_store_breaks(tmp_path, monkeypatch):
    obj = _mixin_self(_msgs(2), tmp_path, monkeypatch)
    # Point ARIA_HOME at a path that cannot be a directory (a file), so the
    # store's mkdir fails — the helper must swallow it.
    blocker = tmp_path / "blocked"
    blocker.write_text("file, not dir")
    monkeypatch.setenv("ARIA_HOME", str(blocker / "sub"))
    obj._record_context_checkpoint("hard")   # must not raise
