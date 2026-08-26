"""Tests for the verifiable eval harness.

The behaviour that matters most here is not that a passing task scores a pass.
It is that a task which cannot measure anything says so, loudly, instead of
inflating the number.
"""

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from aria_code.evals import SuiteResult, TaskSpec, load_suite, run_suite, run_task
from aria_code.evals.harness import ERROR, FAIL, INVALID, PASS, TaskResult, write_report


def _fixture(root: Path, name: str, files: dict) -> Path:
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# A check that fails until `fixed.txt` exists next to it.
_GUARD = '''
    import pathlib, sys
    sys.exit(0 if (pathlib.Path(__file__).parent / "fixed.txt").exists() else 1)
'''


class HarnessBase(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fixtures = self.root / "fixtures"
        self.fixtures.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def _task(self, **kwargs):
        base = dict(
            id="t1",
            prompt="fix it",
            verify="{python} check.py",
            fixture="broken",
        )
        base.update(kwargs)
        return TaskSpec(**base)

    def _run(self, task, solver):
        return run_task(task, solver=solver, fixtures_root=self.fixtures)


class ScoringTests(HarnessBase):
    def test_a_solver_that_fixes_it_passes(self):
        _fixture(self.fixtures, "broken", {"check.py": _GUARD})

        def solver(prompt, workspace):
            (workspace / "fixed.txt").write_text("done", encoding="utf-8")

        result = self._run(self._task(), solver)
        self.assertEqual(result.outcome, PASS)
        self.assertEqual(result.exit_code, 0)

    def test_a_solver_that_does_nothing_fails(self):
        _fixture(self.fixtures, "broken", {"check.py": _GUARD})
        result = self._run(self._task(), lambda p, w: None)
        self.assertEqual(result.outcome, FAIL)
        self.assertEqual(result.exit_code, 1)

    def test_a_confident_summary_is_not_a_pass(self):
        # The whole point of exit-code scoring: saying it is done does nothing.
        _fixture(self.fixtures, "broken", {"check.py": _GUARD})
        result = self._run(self._task(), lambda p, w: "任务完成，已修复")
        self.assertEqual(result.outcome, FAIL)


class PreflightTests(HarnessBase):
    def test_a_fixture_that_starts_green_is_invalid_not_a_pass(self):
        _fixture(self.fixtures, "broken", {
            "check.py": "import sys; sys.exit(0)\n",
        })
        result = self._run(self._task(), lambda p, w: None)
        self.assertEqual(result.outcome, INVALID)
        self.assertIn("measures nothing", result.detail)

    def test_an_invalid_task_is_excluded_from_the_pass_rate(self):
        suite = SuiteResult(name="s", results=[
            TaskResult(task_id="a", outcome=PASS),
            TaskResult(task_id="b", outcome=INVALID),
            TaskResult(task_id="c", outcome=ERROR),
        ])
        # 1 of 1 scored, not 1 of 3 — a broken fixture must neither look like
        # an agent regression nor hide one.
        self.assertEqual(suite.scored, 1)
        self.assertEqual(suite.pass_rate, 1.0)

    def test_a_regression_guard_may_opt_out_of_starting_red(self):
        _fixture(self.fixtures, "broken", {"check.py": "import sys; sys.exit(0)\n"})
        result = self._run(self._task(allow_green_start=True), lambda p, w: None)
        self.assertEqual(result.outcome, PASS)

    def test_the_solver_never_runs_on_an_invalid_task(self):
        _fixture(self.fixtures, "broken", {"check.py": "import sys; sys.exit(0)\n"})
        calls = []
        self._run(self._task(), lambda p, w: calls.append(p))
        self.assertEqual(calls, [])


class EnvironmentTests(HarnessBase):
    def test_a_missing_requirement_errors_rather_than_scoring_a_fail(self):
        # The failure this exists for: five tasks once reported red because
        # the python3 on PATH had no pytest. That is not the agent's score.
        _fixture(self.fixtures, "broken", {"check.py": _GUARD})
        result = self._run(self._task(requires=("definitely_not_installed_xyz",)), lambda p, w: None)
        self.assertEqual(result.outcome, ERROR)
        self.assertIn("definitely_not_installed_xyz", result.detail)

    def test_python_placeholder_resolves_to_the_running_interpreter(self):
        import sys

        _fixture(self.fixtures, "broken", {
            "check.py": f"import sys; sys.exit(0 if sys.executable == {sys.executable!r} else 1)\n",
        })
        result = self._run(self._task(), lambda p, w: None)
        # Green start, so INVALID — which proves the interpreter matched.
        self.assertEqual(result.outcome, INVALID)

    def test_a_check_that_never_finishes_is_a_failure(self):
        _fixture(self.fixtures, "broken", {"check.py": "import time; time.sleep(30)\n"})
        result = self._run(self._task(timeout=1), lambda p, w: None)
        self.assertEqual(result.outcome, FAIL)
        self.assertEqual(result.exit_code, 124)

    def test_a_missing_fixture_errors(self):
        result = self._run(self._task(fixture="nope"), lambda p, w: None)
        self.assertEqual(result.outcome, ERROR)
        self.assertIn("fixture not found", result.detail)

    def test_a_failing_setup_command_errors(self):
        _fixture(self.fixtures, "broken", {"check.py": _GUARD})
        result = self._run(self._task(setup=("exit 3",)), lambda p, w: None)
        self.assertEqual(result.outcome, ERROR)
        self.assertIn("setup command failed", result.detail)

    def test_a_solver_crash_is_an_error_not_a_failed_task(self):
        _fixture(self.fixtures, "broken", {"check.py": _GUARD})

        def solver(prompt, workspace):
            raise RuntimeError("provider outage")

        result = self._run(self._task(), solver)
        self.assertEqual(result.outcome, ERROR)
        self.assertIn("provider outage", result.detail)


class IsolationTests(HarnessBase):
    def test_the_agent_works_on_a_copy_not_the_fixture(self):
        source = _fixture(self.fixtures, "broken", {"check.py": _GUARD})

        def vandal(prompt, workspace):
            (workspace / "check.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")

        self._run(self._task(), vandal)
        self.assertIn("fixed.txt", source.joinpath("check.py").read_text(encoding="utf-8"))

    def test_two_runs_do_not_see_each_others_changes(self):
        _fixture(self.fixtures, "broken", {"check.py": _GUARD})
        first = self._run(self._task(), lambda p, w: (w / "fixed.txt").write_text("x"))
        second = self._run(self._task(), lambda p, w: None)
        self.assertEqual(first.outcome, PASS)
        self.assertEqual(second.outcome, FAIL)


class SuiteTests(HarnessBase):
    def _suite_tasks(self):
        _fixture(self.fixtures, "broken", {"check.py": _GUARD})
        return [
            self._task(id="a", tags=("software",)),
            self._task(id="b", tags=("logistics",)),
            self._task(id="c", tags=("logistics", "hard")),
        ]

    def _fix_only(self, *ids):
        def solver(prompt, workspace):
            if workspace.name in ids:
                (workspace / "fixed.txt").write_text("x", encoding="utf-8")
        return solver

    def test_pass_rate_and_per_tag_scoreboard(self):
        suite = run_suite(
            self._suite_tasks(), solver=self._fix_only("a", "b"),
            fixtures_root=self.fixtures, name="demo",
        )
        self.assertEqual((suite.passed, suite.failed), (2, 1))
        self.assertAlmostEqual(suite.pass_rate, 2 / 3)
        self.assertEqual(suite.by_tag()["logistics"], {"passed": 1, "scored": 2, "pass_rate": 0.5})
        self.assertEqual(suite.by_tag()["software"]["pass_rate"], 1.0)

    def test_tasks_can_be_filtered_by_tag(self):
        suite = run_suite(
            self._suite_tasks(), solver=lambda p, w: None,
            fixtures_root=self.fixtures, tags=["hard"],
        )
        self.assertEqual([r.task_id for r in suite.results], ["c"])

    def test_tasks_can_be_filtered_by_id(self):
        suite = run_suite(
            self._suite_tasks(), solver=lambda p, w: None,
            fixtures_root=self.fixtures, only=["b"],
        )
        self.assertEqual([r.task_id for r in suite.results], ["b"])

    def test_report_is_written_as_json(self):
        import json

        suite = run_suite(
            self._suite_tasks(), solver=self._fix_only("a"),
            fixtures_root=self.fixtures, name="demo",
        )
        path = write_report(suite, self.root / "out" / "report.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["suite"], "demo")
        self.assertEqual(data["passed"], 1)
        self.assertIn("by_tag", data)


class SuiteFileTests(HarnessBase):
    def _write(self, body: str) -> Path:
        path = self.root / "suite.yaml"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def test_loads_tasks(self):
        self._write("""
            suite: demo
            tasks:
              - id: one
                prompt: do it
                verify: "{python} check.py"
                tags: [software]
        """)
        name, tasks = load_suite(self.root / "suite.yaml")
        self.assertEqual(name, "demo")
        self.assertEqual(tasks[0].tags, ("software",))

    def test_a_task_missing_a_required_field_is_rejected(self):
        self._write("""
            suite: demo
            tasks:
              - id: one
                prompt: do it
        """)
        with self.assertRaises(ValueError) as ctx:
            load_suite(self.root / "suite.yaml")
        self.assertIn("verify", str(ctx.exception))

    def test_duplicate_task_ids_are_rejected(self):
        self._write("""
            suite: demo
            tasks:
              - {id: one, prompt: a, verify: "true"}
              - {id: one, prompt: b, verify: "true"}
        """)
        with self.assertRaises(ValueError) as ctx:
            load_suite(self.root / "suite.yaml")
        self.assertIn("duplicate", str(ctx.exception))


class ShippedSuiteTests(unittest.TestCase):
    """The suite that ships with the repo must keep measuring something."""

    def test_core_suite_loads_and_every_task_is_well_formed(self):
        repo_root = Path(__file__).resolve().parent.parent
        name, tasks = load_suite(repo_root / "evals" / "suites" / "core.yaml")
        self.assertEqual(name, "core")
        self.assertGreaterEqual(len(tasks), 5)
        for task in tasks:
            with self.subTest(task=task.id):
                self.assertTrue(task.tags, "every task needs a tag for the scoreboard")
                self.assertTrue((repo_root / "evals" / "fixtures" / task.fixture).is_dir())

    def test_the_suite_covers_more_than_software(self):
        repo_root = Path(__file__).resolve().parent.parent
        _, tasks = load_suite(repo_root / "evals" / "suites" / "core.yaml")
        tags = {tag for task in tasks for tag in task.tags}
        self.assertTrue({"logistics", "payments"} <= tags)


if __name__ == "__main__":
    unittest.main()
