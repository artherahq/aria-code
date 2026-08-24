"""State machine for durable agent runs.

The model may suggest what to do next, but the runtime owns lifecycle state.
Keeping transitions explicit prevents a failed or interrupted turn from being
reported as successful merely because the generated text says it is done.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet


class RunStatus(str, Enum):
    QUEUED = "queued"
    PLANNING = "planning"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_USER = "waiting_user"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL_RUN_STATUSES: FrozenSet[RunStatus] = frozenset({
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
})

ACTIVE_RUN_STATUSES: FrozenSet[RunStatus] = frozenset({
    RunStatus.QUEUED,
    RunStatus.PLANNING,
    RunStatus.RUNNING,
    RunStatus.WAITING_APPROVAL,
    RunStatus.WAITING_USER,
    RunStatus.VERIFYING,
})

_TRANSITIONS: Dict[RunStatus, FrozenSet[RunStatus]] = {
    RunStatus.QUEUED: frozenset({
        RunStatus.PLANNING,
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    }),
    RunStatus.PLANNING: frozenset({
        RunStatus.RUNNING,
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_USER,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    }),
    RunStatus.RUNNING: frozenset({
        RunStatus.WAITING_APPROVAL,
        RunStatus.WAITING_USER,
        RunStatus.VERIFYING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    }),
    RunStatus.WAITING_APPROVAL: frozenset({
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    }),
    RunStatus.WAITING_USER: frozenset({
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    }),
    RunStatus.VERIFYING: frozenset({
        RunStatus.RUNNING,
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
    }),
    RunStatus.INTERRUPTED: frozenset({
        RunStatus.PLANNING,
        RunStatus.RUNNING,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
    }),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


class InvalidRunTransition(ValueError):
    """Raised when code attempts an unsupported lifecycle transition."""


def normalize_run_status(status: RunStatus | str) -> RunStatus:
    if isinstance(status, RunStatus):
        return status
    try:
        return RunStatus(str(status))
    except ValueError as exc:
        raise InvalidRunTransition(f"Unknown run status: {status!r}") from exc


def can_transition(current: RunStatus | str, target: RunStatus | str) -> bool:
    source = normalize_run_status(current)
    destination = normalize_run_status(target)
    return source == destination or destination in _TRANSITIONS[source]


def require_transition(current: RunStatus | str, target: RunStatus | str) -> None:
    source = normalize_run_status(current)
    destination = normalize_run_status(target)
    if not can_transition(source, destination):
        raise InvalidRunTransition(
            f"Invalid run transition: {source.value} -> {destination.value}"
        )


def is_terminal(status: RunStatus | str) -> bool:
    return normalize_run_status(status) in TERMINAL_RUN_STATUSES

