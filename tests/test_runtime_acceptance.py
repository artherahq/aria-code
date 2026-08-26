"""Tests for the acceptance gate.

The behaviours pinned here are the ones that decide whether the gate is an
asset or a tax: it must not fire on turns that changed nothing, it must not
report a green it did not observe, and it must stop.
"""

import asyncio
import unittest

from aria_code.runtime import (
    AcceptanceGate,
    AcceptanceReport,
    AgentEventComplete,
    AgentEventStatus,
    AgentOptions,
    CheckResult,
    ToolExecutor,
    extract_mutated_paths,
    run_agent,
)


class _StubPlanner:
    """Stands in for VerificationPlanner so tests never touch a real project."""

    def __init__(self, commands, reason="stub"):
        self.commands = list(commands)
        self.reason = reason
        self.calls = []

    def infer(self, paths):
        self.calls.append(list(paths))

        class _Plan:
            pass

        plan = _Plan()
        plan.commands = list(self.commands)
        plan.reason = self.reason
        return plan


def _ok(stdout=""):
    return {"success": True, "data": {"exit_code": 0, "stdout": stdout, "stderr": ""}}


def _fail(stderr="boom", code=1):
    return {"success": True, "data": {"exit_code": code, "stdout": "", "stderr": stderr}}


def _write_result(path="app.py"):
    return {"success": True, "data": {"path": path, "applied": True, "lines": 3}}


class ExtractMutatedPathsTests(unittest.TestCase):
    def test_applied_write_reports_its_path(self):
        self.assertEqual(
            extract_mutated_paths("write_file", _write_result("/tmp/app.py")),
            ("/tmp/app.py",),
        )

    def test_mcp_prefixed_tool_name_is_canonicalised(self):
        self.assertEqual(
            extract_mutated_paths("server__edit_file", _write_result("a.py")),
            ("a.py",),
        )

    def test_read_only_tool_reports_nothing(self):
        self.assertEqual(extract_mutated_paths("read_file", _write_result()), ())

    def test_failed_write_reports_nothing(self):
        self.assertEqual(
            extract_mutated_paths("write_file", {"success": False, "error": "denied"}),
            (),
        )

    def test_staged_write_reports_nothing(self):
        # Nothing reached disk, so verifying now would check the old state and
        # report a green that means nothing.
        staged = {"success": True, "data": {"path": "a.py", "applied": False, "staged": True}}
        self.assertEqual(extract_mutated_paths("write_file", staged), ())

    def test_run_command_never_arms_the_gate(self):
        # Otherwise each check the gate runs would re-arm it, forever.
        self.assertEqual(
            extract_mutated_paths("run_command", {"success": True, "data": {"path": "a.py"}}),
            (),
        )

    def test_multi_edit_reports_every_edited_path(self):
        result = {
            "success": True,
            "data": {"edits": [{"path": "a.py"}, {"path": "b.py"}, {"path": "a.py"}]},
        }
        self.assertEqual(extract_mutated_paths("multi_edit", result), ("a.py", "b.py"))


class AcceptanceGateTests(unittest.TestCase):
    def _gate(self, runner, commands=("pytest -q",), **kwargs):
        return AcceptanceGate(
            runner=runner,
            planner=_StubPlanner(commands),
            **kwargs,
        )

    def test_unarmed_gate_does_not_run(self):
        gate = self._gate(lambda cmd: _ok())
        self.assertFalse(gate.should_run())
        self.assertIsNone(asyncio.run(gate.run()))
        self.assertIsNone(gate.summary()["verified"])

    def test_applied_write_arms_and_passes(self):
        seen = []

        def runner(command):
            seen.append(command)
            return _ok("2 passed")

        gate = self._gate(runner)
        gate.record_tool("write_file", _write_result())
        self.assertTrue(gate.should_run())

        report = asyncio.run(gate.run())
        self.assertTrue(report.passed)
        self.assertEqual(seen, ["pytest -q"])
        self.assertTrue(gate.summary()["verified"])

    def test_failing_check_is_not_passed_and_carries_output(self):
        gate = self._gate(lambda cmd: _fail('File "app.py", line 12\nNameError: x'))
        gate.record_tool("write_file", _write_result())

        report = asyncio.run(gate.run())
        self.assertFalse(report.passed)
        self.assertTrue(report.ran)
        self.assertEqual(report.failures[0].exit_code, 1)
        self.assertIn("app.py:12", report.failures[0].anchors)
        self.assertIs(gate.summary()["verified"], False)

    def test_repair_directive_carries_command_output_and_anchor(self):
        gate = self._gate(lambda cmd: _fail('File "app.py", line 12\nNameError: x'))
        gate.record_tool("write_file", _write_result())
        directive = asyncio.run(gate.run()).repair_directive()

        self.assertIn("pytest -q", directive)
        self.assertIn("NameError", directive)
        self.assertIn("app.py:12", directive)
        self.assertIn("edit_file", directive)

    def test_runner_timeout_is_a_failure_not_a_pass(self):
        # tool_run_command reports a timeout as success:False with no exit
        # code; reading that as exit 0 would turn "never finished" into green.
        gate = self._gate(lambda cmd: {"success": False, "error": "Command timed out (300s)"})
        gate.record_tool("write_file", _write_result())

        report = asyncio.run(gate.run())
        self.assertFalse(report.passed)
        self.assertIn("timed out", report.failures[0].error)

    def test_runner_exception_is_contained(self):
        def runner(command):
            raise RuntimeError("no shell")

        gate = self._gate(runner)
        gate.record_tool("write_file", _write_result())

        report = asyncio.run(gate.run())
        self.assertFalse(report.passed)
        self.assertIn("no shell", report.failures[0].error)

    def test_async_runner_is_awaited(self):
        async def runner(command):
            return _ok()

        gate = self._gate(runner)
        gate.record_tool("write_file", _write_result())
        self.assertTrue(asyncio.run(gate.run()).passed)

    def test_no_inferable_command_is_unverified_not_verified(self):
        gate = self._gate(lambda cmd: _ok(), commands=())
        gate.record_tool("write_file", _write_result())

        report = asyncio.run(gate.run())
        self.assertFalse(report.ran)
        self.assertFalse(report.passed)
        self.assertIsNone(gate.summary()["verified"])

    def test_first_red_stops_the_remaining_checks(self):
        seen = []

        def runner(command):
            seen.append(command)
            return _fail()

        gate = self._gate(runner, commands=("lint", "pytest -q", "build"))
        gate.record_tool("write_file", _write_result())
        asyncio.run(gate.run())
        self.assertEqual(seen, ["lint"])

    def test_gate_disarms_after_running(self):
        gate = self._gate(lambda cmd: _fail())
        gate.record_tool("write_file", _write_result())
        asyncio.run(gate.run())
        self.assertFalse(gate.should_run())

    def test_a_repair_write_re_arms_the_gate(self):
        gate = self._gate(lambda cmd: _fail())
        gate.record_tool("write_file", _write_result())
        asyncio.run(gate.run())
        gate.record_tool("edit_file", _write_result())
        self.assertTrue(gate.should_run())

    def test_attempts_are_bounded(self):
        gate = self._gate(lambda cmd: _fail(), max_attempts=2)
        for _ in range(5):
            gate.record_tool("write_file", _write_result())
            asyncio.run(gate.run())
        self.assertEqual(gate.attempts, 2)
        self.assertTrue(gate.exhausted)
        self.assertFalse(gate.should_run())

    def test_last_attempt_directive_forbids_claiming_success(self):
        gate = self._gate(lambda cmd: _fail(), max_attempts=1)
        gate.record_tool("write_file", _write_result())
        directive = asyncio.run(gate.run()).repair_directive()
        self.assertIn("最后一次", directive)

    def test_configured_commands_override_inference(self):
        seen = []
        gate = AcceptanceGate(
            runner=lambda cmd: (seen.append(cmd), _ok())[1],
            planner=_StubPlanner(["pytest -q"]),
            commands=["make check"],
        )
        gate.record_tool("write_file", _write_result())
        asyncio.run(gate.run())
        self.assertEqual(seen, ["make check"])

    def test_disabled_gate_never_arms(self):
        gate = self._gate(lambda cmd: _ok(), enabled=False)
        gate.record_tool("write_file", _write_result())
        self.assertFalse(gate.should_run())

    def test_huge_failure_output_is_trimmed(self):
        noise = "\n".join(f"line {i}" for i in range(4000))
        gate = self._gate(lambda cmd: _fail(noise))
        gate.record_tool("write_file", _write_result())
        directive = asyncio.run(gate.run()).repair_directive()
        self.assertLess(len(directive), 8000)
        self.assertIn("omitted", directive)


class AcceptanceReportTests(unittest.TestCase):
    def test_report_with_no_checks_is_not_passed(self):
        self.assertFalse(AcceptanceReport(attempt=1).passed)

    def test_headline_names_the_failing_command(self):
        report = AcceptanceReport(
            attempt=1,
            checks=(CheckResult(command="pytest -q", exit_code=2, passed=False),),
        )
        self.assertIn("pytest -q", report.headline())


class AcceptanceInAgentLoopTests(unittest.TestCase):
    """The gate seen from the loop: a red check must buy another round."""

    @staticmethod
    def _provider(script):
        """Replay a scripted sequence of provider results, one per round."""
        rounds = list(script)
        seen = []

        async def provider_fn(message, history, **kwargs):
            seen.append(message)
            return rounds.pop(0) if rounds else {"success": True, "response": "done"}

        return provider_fn, seen

    def _run(self, provider_fn, gate, executor):
        async def drive():
            events = []
            async for event in run_agent(
                "fix the bug",
                [],
                provider_fn=provider_fn,
                tool_executor=executor,
                options=AgentOptions(acceptance=gate, max_rounds=6),
            ):
                events.append(event)
            return events

        return asyncio.run(drive())

    def test_red_check_feeds_the_failure_back_and_the_repair_passes(self):
        executor = ToolExecutor({"write_file": (lambda params: _write_result(), "Write")})
        provider_fn, seen = self._provider([
            {"success": True, "response": "writing",
             "tool_calls_pending": [{"tool": "write_file", "params": {"path": "app.py"}}]},
            {"success": True, "response": "任务完成"},          # premature claim
            {"success": True, "response": "patched",
             "tool_calls_pending": [{"tool": "write_file", "params": {"path": "app.py"}}]},
            {"success": True, "response": "任务完成"},          # now backed by green
        ])

        outcomes = iter([_fail('File "app.py", line 12\nNameError: x'), _ok("1 passed")])
        gate = AcceptanceGate(
            runner=lambda cmd: next(outcomes),
            planner=_StubPlanner(["pytest -q"]),
        )

        events = self._run(provider_fn, gate, executor)

        states = [e.state for e in events if isinstance(e, AgentEventStatus)]
        self.assertIn("acceptance_failed", states)
        self.assertIn("acceptance_passed", states)

        # The failing output actually reached the model.
        self.assertTrue(any("验收未通过" in msg for msg in seen))
        self.assertTrue(any("NameError" in msg for msg in seen))

        complete = [e for e in events if isinstance(e, AgentEventComplete)][-1]
        self.assertTrue(complete.result.acceptance["verified"])
        self.assertEqual(complete.result.acceptance["attempts"], 2)

    def test_read_only_turn_runs_no_checks(self):
        executor = ToolExecutor({"read_file": (lambda params: {"success": True, "data": {"content": "x"}}, "Read")})
        provider_fn, _ = self._provider([
            {"success": True, "response": "reading",
             "tool_calls_pending": [{"tool": "read_file", "params": {"path": "app.py"}}]},
            {"success": True, "response": "分析完成"},
        ])

        ran = []
        gate = AcceptanceGate(
            runner=lambda cmd: (ran.append(cmd), _ok())[1],
            planner=_StubPlanner(["pytest -q"]),
        )

        events = self._run(provider_fn, gate, executor)
        self.assertEqual(ran, [])
        complete = [e for e in events if isinstance(e, AgentEventComplete)][-1]
        self.assertIsNone(complete.result.acceptance)

    def test_persistently_red_turn_ends_unverified_rather_than_looping(self):
        executor = ToolExecutor({"write_file": (lambda params: _write_result(), "Write")})

        async def provider_fn(message, history, **kwargs):
            # Writes, then claims done, forever: the worst case for a gate.
            if "Tool Results" in message:
                return {"success": True, "response": "任务完成"}
            return {"success": True, "response": "writing",
                    "tool_calls_pending": [{"tool": "write_file", "params": {}}]}

        gate = AcceptanceGate(
            runner=lambda cmd: _fail(),
            planner=_StubPlanner(["pytest -q"]),
            max_attempts=2,
        )

        events = self._run(provider_fn, gate, executor)
        complete = [e for e in events if isinstance(e, AgentEventComplete)][-1]
        self.assertIs(complete.result.acceptance["verified"], False)
        self.assertEqual(gate.attempts, 2)


if __name__ == "__main__":
    unittest.main()


class CLIAcceptanceGateWiringTests(unittest.TestCase):
    """The CLI adapter decides *whether* a session gets a gate at all."""

    @staticmethod
    def _executor(**tools):
        return ToolExecutor({name: (fn, name) for name, fn in tools.items()})

    def test_gate_runs_checks_through_the_sessions_run_command(self):
        from aria_code.apps.cli.providers.runtime_bridge import build_acceptance_gate

        seen = []

        def run_command(params):
            seen.append(params)
            return _ok()

        gate = build_acceptance_gate(
            self._executor(run_command=run_command),
            {"permission_mode": "workspace-write"},
        )
        gate._planner = _StubPlanner(["pytest -q"])
        gate.record_tool("write_file", _write_result())
        asyncio.run(gate.run())

        self.assertEqual(seen[0]["command"], "pytest -q")
        self.assertEqual(seen[0]["permission_mode"], "workspace-write")

    def test_read_only_session_gets_no_gate(self):
        from aria_code.apps.cli.providers.runtime_bridge import build_acceptance_gate

        self.assertIsNone(
            build_acceptance_gate(
                self._executor(run_command=lambda params: _ok()),
                {"permission_mode": "read-only"},
            )
        )

    def test_gate_can_be_switched_off_by_config(self):
        from aria_code.apps.cli.providers.runtime_bridge import build_acceptance_gate

        self.assertIsNone(
            build_acceptance_gate(
                self._executor(run_command=lambda params: _ok()),
                {"acceptance_gate": False},
            )
        )

    def test_no_run_command_tool_means_no_gate(self):
        from aria_code.apps.cli.providers.runtime_bridge import build_acceptance_gate

        self.assertIsNone(build_acceptance_gate(self._executor(), {}))

    def test_configured_commands_reach_the_gate(self):
        from aria_code.apps.cli.providers.runtime_bridge import build_acceptance_gate

        gate = build_acceptance_gate(
            self._executor(run_command=lambda params: _ok()),
            {"acceptance_commands": ["make check"], "acceptance_max_attempts": 3},
        )
        self.assertEqual(gate.commands, ("make check",))
        self.assertEqual(gate.max_attempts, 3)
