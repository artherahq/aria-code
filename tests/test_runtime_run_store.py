"""Tests for durable agent run state and event persistence."""

from __future__ import annotations

import socket

import pytest

from runtime import (
    InvalidRunTransition,
    RunStatus,
    RunStore,
    RuntimeTrace,
    can_transition,
)


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "runtime" / "runs.sqlite3")


def _create_run(store: RunStore, **overrides):
    values = {
        "session_id": "session-1",
        "prompt": "inspect the project",
        "workspace": "/tmp/project",
        "provider": "ollama",
    }
    values.update(overrides)
    return store.create_run(**values)


def test_run_state_machine_requires_verification_before_success():
    assert can_transition(RunStatus.QUEUED, RunStatus.RUNNING)
    assert can_transition(RunStatus.RUNNING, RunStatus.VERIFYING)
    assert can_transition(RunStatus.VERIFYING, RunStatus.SUCCEEDED)
    assert not can_transition(RunStatus.RUNNING, RunStatus.SUCCEEDED)


def test_persists_run_and_ordered_state_events(store):
    run = _create_run(store)
    store.transition(run.run_id, RunStatus.PLANNING, reason="decompose")
    store.transition(run.run_id, RunStatus.RUNNING, reason="execute")
    store.append_event(run.run_id, "tool_call", {"tool": "read_file"})
    store.transition(run.run_id, RunStatus.VERIFYING, reason="validate")
    final = store.transition(run.run_id, RunStatus.SUCCEEDED, reason="done")

    reloaded = RunStore(store.database_path).get_run(run.run_id)
    assert reloaded == final
    assert reloaded is not None
    assert reloaded.status is RunStatus.SUCCEEDED
    assert reloaded.started_at is not None
    assert reloaded.finished_at is not None

    events = store.events(run.run_id)
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert [event.event_type for event in events] == [
        "run_created",
        "run_state_changed",
        "run_state_changed",
        "tool_call",
        "run_state_changed",
        "run_state_changed",
    ]


def test_invalid_transition_is_rejected_without_changing_run(store):
    run = _create_run(store)
    store.transition(run.run_id, RunStatus.RUNNING)

    with pytest.raises(InvalidRunTransition, match="running -> succeeded"):
        store.transition(run.run_id, RunStatus.SUCCEEDED)

    current = store.get_run(run.run_id)
    assert current is not None
    assert current.status is RunStatus.RUNNING
    assert current.finished_at is None


def test_runtime_trace_streams_events_to_durable_store(store):
    run = _create_run(store)
    store.transition(run.run_id, RunStatus.RUNNING)
    trace = RuntimeTrace(event_sink=lambda event: store.append_event(
        run.run_id,
        event.type,
        event.data,
        event_id=event.event_id,
        timestamp=event.timestamp,
    ))

    event = trace.emit("tool_call", {"tool": "search_code", "query": "RunStore"})

    persisted = store.events(run.run_id)[-1]
    assert persisted.event_id == event.event_id
    assert persisted.event_type == "tool_call"
    assert persisted.data["query"] == "RunStore"


def test_event_payload_redacts_credentials(store):
    run = _create_run(store)
    store.append_event(run.run_id, "provider_request", {
        "provider": "openai",
        "api_key": "secret-value",
        "headers": {"authorization": "Bearer secret"},
        "usage": {"completion_tokens": 42},
    })

    event = store.events(run.run_id)[-1]
    assert event.data["api_key"] == "[REDACTED]"
    assert event.data["headers"]["authorization"] == "[REDACTED]"
    assert event.data["usage"]["completion_tokens"] == 42


def test_recovers_active_run_owned_by_dead_process(store):
    run = _create_run(
        store,
        owner_pid=999_999_999,
        owner_host=socket.gethostname(),
    )
    store.transition(run.run_id, RunStatus.RUNNING)

    recovered = store.recover_orphaned_runs()

    assert recovered == [run.run_id]
    current = store.get_run(run.run_id)
    assert current is not None
    assert current.status is RunStatus.INTERRUPTED
    assert "process ended" in current.error.lower()

    resumed = store.transition(run.run_id, RunStatus.RUNNING, reason="resume")
    assert resumed.status is RunStatus.RUNNING
    assert resumed.error == ""


def test_list_runs_can_filter_by_session_and_status(store):
    first = _create_run(store, session_id="one", prompt="first")
    second = _create_run(store, session_id="two", prompt="second")
    store.transition(first.run_id, RunStatus.RUNNING)
    store.transition(second.run_id, RunStatus.CANCELLED)

    active = store.list_runs(session_id="one", statuses=[RunStatus.RUNNING])
    cancelled = store.list_runs(statuses=[RunStatus.CANCELLED])

    assert [record.run_id for record in active] == [first.run_id]
    assert [record.run_id for record in cancelled] == [second.run_id]
