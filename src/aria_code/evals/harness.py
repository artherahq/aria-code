"""Run verifiable tasks and score them by exit code.

The shape of one task
---------------------
A fixture directory, a prompt, and a command::

    - id: fix-failing-test
      prompt: "tests/test_math.py 有一个测试失败了，修好它"
      fixture: fix_failing_test
      verify: "{python} -m pytest -q"
      requires: [pytest]

The fixture is copied to a scratch directory, the agent is turned loose in the
copy, and ``verify`` decides the outcome.  Exit 0 is a pass.  Nothing else
counts — not a confident summary, not a diff that looks right.

The property that makes a suite trustworthy
-------------------------------------------
**Every task must start red.**  Before the agent runs, the harness runs
``verify`` on the untouched fixture.  If it already passes, the task is
reported ``INVALID`` and scored as neither pass nor fail.

This check is worth more than any individual task.  A suite that silently
accumulates already-green tasks reports a rising pass rate while measuring
less and less, and the failure is invisible precisely because the number looks
good — you cannot tell a task the agent solved from a task that was never
broken.  Running the check every time costs one command per task and makes the
number mean something.

"Red" has to mean the check ran
------------------------------
The pre-flight alone is not enough, and the first run of this harness proved
it: every task reported red, and every task was red because the ``python3`` on
PATH had no pytest.  The suite looked healthy while measuring nothing at all —
the same silent-inflation failure the pre-flight exists to prevent, arriving
through the back door.

Two things close it.  ``{python}`` in a command resolves to the interpreter
running the harness, so a suite verifies against the environment it was
launched with rather than whatever ``python3`` happens to mean on this
machine.  And a task may declare ``requires: [pytest]``; a missing import is
reported as ``ERROR`` — excluded from the score — instead of being counted as
a red test the agent is expected to fix.

Isolation
---------
Tasks run in a copy under a scratch root, never in the fixture and never in
the repository.  An agent that deletes the workspace, writes outside it, or
leaves it in a broken state affects nothing but its own run's directory, which
is what makes it safe to keep destructive tasks in the suite — those are the
ones worth having.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional, Sequence

__all__ = [
    "SuiteResult",
    "TaskResult",
    "TaskSpec",
    "Solver",
    "load_suite",
    "run_suite",
    "run_task",
]

# Outcomes.  Kept as four values rather than a boolean because "the agent
# failed" and "the task was broken" call for opposite responses, and collapsing
# them is how a rotten suite goes unnoticed.
PASS = "pass"
FAIL = "fail"
INVALID = "invalid"   # the fixture was already green: the task measures nothing
ERROR = "error"       # the harness or the solver blew up; not the agent's score

_MAX_LOG_CHARS = 4000


def _trim(text: str, limit: int = _MAX_LOG_CHARS) -> str:
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    half = limit // 2
    return f"{body[:half]}\n… [truncated] …\n{body[-half:]}"


@dataclass(frozen=True)
class TaskSpec:
    """One verifiable task."""

    id: str
    prompt: str
    verify: str
    fixture: str = ""
    timeout: int = 300
    solve_timeout: int = 900
    tags: tuple[str, ...] = ()
    setup: tuple[str, ...] = ()
    # Importable modules the check needs. Missing ones make the task ERROR
    # rather than FAIL: an absent pytest is not a bug for the agent to fix.
    requires: tuple[str, ...] = ()
    # A task may legitimately start green when it is a regression guard: the
    # point is that the agent must not *break* it. Opting out is explicit so
    # that it is a decision someone made, not a fixture that quietly rotted.
    allow_green_start: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "TaskSpec":
        missing = [key for key in ("id", "prompt", "verify") if not data.get(key)]
        if missing:
            raise ValueError(f"task is missing required field(s): {', '.join(missing)}")
        return cls(
            id=str(data["id"]),
            prompt=str(data["prompt"]),
            verify=str(data["verify"]),
            fixture=str(data.get("fixture") or ""),
            timeout=int(data.get("timeout") or 300),
            solve_timeout=int(data.get("solve_timeout") or 900),
            tags=tuple(str(t) for t in (data.get("tags") or ())),
            setup=tuple(str(c) for c in (data.get("setup") or ())),
            requires=tuple(str(m) for m in (data.get("requires") or ())),
            allow_green_start=bool(data.get("allow_green_start", False)),
        )


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    outcome: str
    seconds: float = 0.0
    exit_code: Optional[int] = None
    detail: str = ""
    log: str = ""
    tags: tuple[str, ...] = ()

    @property
    def counted(self) -> bool:
        """Whether this result belongs in the pass rate at all."""
        return self.outcome in (PASS, FAIL)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "outcome": self.outcome,
            "seconds": round(self.seconds, 2),
            "exit_code": self.exit_code,
            "detail": self.detail,
            "tags": list(self.tags),
        }


@dataclass
class SuiteResult:
    name: str
    results: List[TaskResult] = field(default_factory=list)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.outcome == PASS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.outcome == FAIL)

    @property
    def invalid(self) -> int:
        return sum(1 for r in self.results if r.outcome == INVALID)

    @property
    def errored(self) -> int:
        return sum(1 for r in self.results if r.outcome == ERROR)

    @property
    def scored(self) -> int:
        return sum(1 for r in self.results if r.counted)

    @property
    def pass_rate(self) -> float:
        """Passes over *scored* tasks.

        Invalid and errored tasks are excluded rather than counted as
        failures. Counting them down would let one broken fixture look like an
        agent regression; counting them up would let it hide one.
        """
        return (self.passed / self.scored) if self.scored else 0.0

    def by_tag(self) -> dict:
        """Pass rate per tag — the per-industry scoreboard."""
        buckets: dict[str, list[TaskResult]] = {}
        for result in self.results:
            for tag in result.tags:
                buckets.setdefault(tag, []).append(result)
        out = {}
        for tag, results in sorted(buckets.items()):
            scored = [r for r in results if r.counted]
            out[tag] = {
                "passed": sum(1 for r in scored if r.outcome == PASS),
                "scored": len(scored),
                "pass_rate": round(
                    sum(1 for r in scored if r.outcome == PASS) / len(scored), 4
                ) if scored else 0.0,
            }
        return out

    def to_dict(self) -> dict:
        return {
            "suite": self.name,
            "pass_rate": round(self.pass_rate, 4),
            "passed": self.passed,
            "failed": self.failed,
            "invalid": self.invalid,
            "errored": self.errored,
            "scored": self.scored,
            "by_tag": self.by_tag(),
            "results": [r.to_dict() for r in self.results],
        }

    def summary_line(self) -> str:
        parts = [f"{self.name}: {self.passed}/{self.scored} ({self.pass_rate:.0%})"]
        if self.invalid:
            parts.append(f"{self.invalid} invalid")
        if self.errored:
            parts.append(f"{self.errored} error")
        return " · ".join(parts)


# ``(prompt, workspace) -> anything``.  Injected so the harness can be tested,
# and so the same suite can score a different agent — the CLI, a subagent
# backend, or a competitor — without the suite knowing which.
Solver = Callable[[str, Path], Any]


def _resolve(command: str) -> str:
    """Substitute ``{python}`` with the interpreter running the harness.

    Without this a suite silently verifies against whatever ``python3`` means
    on PATH, which is how five tasks once reported red because that
    interpreter had no pytest installed.
    """
    return (command or "").replace("{python}", sys.executable)


def _missing_modules(names: Iterable[str]) -> list[str]:
    missing = []
    for name in names:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError):
            missing.append(name)
    return missing


def _run(command: str, cwd: Path, timeout: int) -> tuple[int, str]:
    command = _resolve(command)
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # A check that never finishes is a failed check. Reporting it as
        # anything else would let an infinite loop score as a pass.
        return 124, f"verification timed out after {timeout}s"
    except Exception as exc:
        return 125, f"could not run command: {exc}"
    output = "\n".join(part for part in (proc.stdout, proc.stderr) if part.strip())
    return proc.returncode, output


def load_suite(path: str | Path) -> tuple[str, list[TaskSpec]]:
    """Read a YAML suite file into task specs."""
    import yaml

    file_path = Path(path).expanduser().resolve()
    data = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    name = str(data.get("suite") or file_path.stem)
    tasks = [TaskSpec.from_dict(entry) for entry in (data.get("tasks") or [])]

    duplicates = {t.id for t in tasks if [x.id for x in tasks].count(t.id) > 1}
    if duplicates:
        raise ValueError(f"duplicate task id(s) in {file_path.name}: {', '.join(sorted(duplicates))}")
    return name, tasks


def _prepare_workspace(task: TaskSpec, fixtures_root: Path, scratch: Path) -> Path:
    workspace = scratch / task.id
    if workspace.exists():
        shutil.rmtree(workspace)
    if task.fixture:
        source = fixtures_root / task.fixture
        if not source.is_dir():
            raise FileNotFoundError(f"fixture not found: {source}")
        shutil.copytree(source, workspace)
    else:
        workspace.mkdir(parents=True)
    return workspace


def run_task(
    task: TaskSpec,
    *,
    solver: Solver,
    fixtures_root: str | Path,
    scratch_root: str | Path | None = None,
    keep_workspace: bool = False,
) -> TaskResult:
    """Prepare, pre-check, solve, and score one task."""
    started = time.time()
    fixtures = Path(fixtures_root).expanduser().resolve()
    scratch = Path(scratch_root).expanduser().resolve() if scratch_root else Path(
        tempfile.mkdtemp(prefix="aria-eval-")
    )
    scratch.mkdir(parents=True, exist_ok=True)

    def _result(outcome: str, **kwargs) -> TaskResult:
        return TaskResult(
            task_id=task.id, outcome=outcome,
            seconds=time.time() - started, tags=task.tags, **kwargs,
        )

    try:
        workspace = _prepare_workspace(task, fixtures, scratch)
    except Exception as exc:
        return _result(ERROR, detail=f"workspace setup failed: {exc}")

    missing = _missing_modules(task.requires)
    if missing:
        return _result(
            ERROR,
            detail=f"environment is missing {', '.join(missing)} — the check cannot run",
        )

    try:
        for command in task.setup:
            code, output = _run(command, workspace, task.timeout)
            if code != 0:
                return _result(ERROR, exit_code=code,
                               detail=f"setup command failed: {command}",
                               log=_trim(output))

        # ── the pre-flight ────────────────────────────────────────────────
        # Confirm the task is actually broken before asking anyone to fix it.
        before_code, before_log = _run(task.verify, workspace, task.timeout)
        if before_code == 0 and not task.allow_green_start:
            return _result(
                INVALID, exit_code=0,
                detail="fixture already passes its own check — this task measures nothing",
                log=_trim(before_log),
            )

        # ── the agent's turn ──────────────────────────────────────────────
        try:
            solver(task.prompt, workspace)
        except Exception as exc:
            # A solver crash is not the agent failing the task; scoring it as
            # a fail would blame the model for a harness or provider outage.
            return _result(ERROR, detail=f"solver raised: {exc}")

        # ── the score ─────────────────────────────────────────────────────
        after_code, after_log = _run(task.verify, workspace, task.timeout)
        if after_code == 0:
            return _result(PASS, exit_code=0)
        return _result(
            FAIL, exit_code=after_code,
            detail=f"`{task.verify}` exited {after_code}",
            log=_trim(after_log),
        )
    finally:
        if not keep_workspace and scratch_root is None:
            shutil.rmtree(scratch, ignore_errors=True)


def run_suite(
    tasks: Sequence[TaskSpec],
    *,
    solver: Solver,
    fixtures_root: str | Path,
    name: str = "suite",
    scratch_root: str | Path | None = None,
    only: Iterable[str] = (),
    tags: Iterable[str] = (),
    on_result: Optional[Callable[[TaskResult], None]] = None,
    keep_workspace: bool = False,
) -> SuiteResult:
    """Run every task and collect the scoreboard."""
    wanted_ids = {str(i) for i in only if i}
    wanted_tags = {str(t) for t in tags if t}

    selected = [
        task for task in tasks
        if (not wanted_ids or task.id in wanted_ids)
        and (not wanted_tags or wanted_tags & set(task.tags))
    ]

    suite = SuiteResult(name=name)
    for task in selected:
        result = run_task(
            task,
            solver=solver,
            fixtures_root=fixtures_root,
            scratch_root=scratch_root,
            keep_workspace=keep_workspace,
        )
        suite.results.append(result)
        if on_result is not None:
            on_result(result)
    return suite


def write_report(suite: SuiteResult, path: str | Path) -> Path:
    """Persist the scoreboard as JSON so runs can be compared over time."""
    out = Path(path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(suite.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return out
