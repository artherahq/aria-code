"""Verifiable evaluation for Aria Code.

The existing harness in ``evals/run_evals.py`` scores a turn by looking for
strings in the transcript and by asking a judge model whether the answer seems
right.  That measures whether the agent *said* something plausible.  It cannot
measure whether the agent *did* the job, and with two cases it could not
measure much of anything.

This package scores the other thing.  A task here is a workspace plus a
prompt plus one command, and the score is the command's exit code after the
agent has had its turn.  No judge, no string matching, no partial credit: the
tests pass or they do not, the ledger balances or it does not.

That is deliberately the same shape as the acceptance gate, and for the same
reason — every domain expresses "correct" as a command that exits non-zero
when the work is wrong.  A logistics suite and a Python suite differ only in
what that command is, so adding an industry to the scoreboard costs a fixture
and a line of YAML rather than a new scoring strategy.
"""

from .harness import (
    SuiteResult,
    TaskResult,
    TaskSpec,
    load_suite,
    run_suite,
    run_task,
)

__all__ = [
    "SuiteResult",
    "TaskResult",
    "TaskSpec",
    "load_suite",
    "run_suite",
    "run_task",
]
