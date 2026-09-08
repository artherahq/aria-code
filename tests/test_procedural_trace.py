"""Tests for runtime.procedural_trace — the adapters and consent-gated
wiring that turn local runtime records into execution traces for Arthera's
procedural memory.
"""

from __future__ import annotations

from unittest import mock

import pytest

from aria_code.runtime import procedural_trace as pt


# ==================== Adapters ====================


class TestSubagentSnapshotAdapter:
    def test_pending_task_produces_no_trace(self):
        assert pt.trace_from_subagent_snapshot({"status": "pending"}) is None

    def test_running_task_produces_no_trace(self):
        assert pt.trace_from_subagent_snapshot({"status": "running"}) is None

    def test_cancelled_task_produces_no_trace(self):
        assert pt.trace_from_subagent_snapshot({"status": "cancelled"}) is None

    def test_done_task_maps_to_success(self):
        contract = pt.trace_from_subagent_snapshot({
            "task_id": "abc123", "prompt": "inspect code", "status": "done",
            "result": "looks fine", "error": "", "backend": "aria",
        })
        assert contract["outcome"] == "success"
        assert contract["goal"] == "inspect code"
        assert contract["source"] == "aria_code.task_ledger"
        assert contract["source_ref"] == "abc123"
        assert contract["steps"][0]["error"] is None

    def test_failed_task_maps_to_failure_with_error_on_step(self):
        contract = pt.trace_from_subagent_snapshot({
            "task_id": "abc124", "prompt": "deploy", "status": "failed",
            "result": "", "error": "quota exceeded",
        })
        assert contract["outcome"] == "failure"
        assert contract["steps"][0]["error"] == "quota exceeded"

    def test_done_task_with_leftover_error_field_is_still_failure(self):
        # status="done" with a non-empty error is inconsistent data — treat
        # it as a failure rather than silently reporting a false success.
        contract = pt.trace_from_subagent_snapshot({
            "task_id": "x", "prompt": "p", "status": "done", "error": "boom",
        })
        assert contract["outcome"] == "failure"


class TestVerificationSummaryAdapter:
    def test_all_passed_maps_to_success(self):
        summary = {
            "all_passed": True,
            "checks": [{"name": "pytest", "command": "pytest -q", "passed": True, "extracted_errors": []}],
        }
        contract = pt.trace_from_verification_summary(summary, goal="verify change")
        assert contract["outcome"] == "success"
        assert contract["source"] == "aria_code.verify_loop"

    def test_mixed_results_map_to_partial(self):
        summary = {
            "all_passed": False,
            "checks": [
                {"name": "pytest", "command": "pytest -q", "passed": True, "extracted_errors": []},
                {"name": "mypy", "command": "mypy .", "passed": False, "extracted_errors": ["type error"]},
            ],
        }
        contract = pt.trace_from_verification_summary(summary, goal="verify change")
        assert contract["outcome"] == "partial"
        assert "type error" in contract["steps"][1]["error"]

    def test_all_failed_maps_to_failure(self):
        summary = {
            "all_passed": False,
            "checks": [{"name": "pytest", "command": "pytest -q", "passed": False, "extracted_errors": ["boom"]}],
        }
        contract = pt.trace_from_verification_summary(summary, goal="verify change")
        assert contract["outcome"] == "failure"


class TestSelfHealingResultAdapter:
    def test_success_without_retries_is_plain_success(self):
        result = {"success": True, "retries_used": 0, "final_output": "ok", "patches_applied": []}
        contract = pt.trace_from_self_healing_result(result, goal="run strategy")
        assert contract["outcome"] == "success"

    def test_success_after_retries_is_recovery_shape(self):
        result = {
            "success": True, "retries_used": 2, "final_output": "ok",
            "patches_applied": [
                {"round": 1, "type": "syntax_fix", "line": 12},
                {"round": 2, "type": "import_fix"},
            ],
        }
        contract = pt.trace_from_self_healing_result(result, goal="run strategy")
        assert contract["outcome"] == "success_after_recovery"
        assert contract["metadata"]["retries_used"] == 2
        # one step per patch plus the final attempt
        assert len(contract["steps"]) == 3
        assert "syntax_fix" in contract["steps"][0]["action"]

    def test_failure_carries_the_error_on_the_final_step(self):
        result = {"success": False, "retries_used": 3, "final_output": "", "error": "gave up", "patches_applied": []}
        contract = pt.trace_from_self_healing_result(result, goal="run strategy")
        assert contract["outcome"] == "failure"
        assert contract["steps"][-1]["error"] == "gave up"


# ==================== Consent-gated wiring ====================


class TestWireTraceReporters:
    def setup_method(self):
        import aria_code.runtime.subagent as subagent
        import aria_code.runtime.verify_loop as verify_loop
        import aria_code.agents.engineering.tester as tester
        self._subagent = subagent
        self._tester = tester
        self._verify_loop = verify_loop
        self._orig_subagent_reporter = subagent._TRACE_REPORTER
        self._orig_tester_reporter = tester._TRACE_REPORTER
        self._orig_verify_reporter = verify_loop._TRACE_REPORTER
        subagent._TRACE_REPORTER = None
        tester._TRACE_REPORTER = None
        verify_loop._TRACE_REPORTER = None

    def teardown_method(self):
        self._subagent._TRACE_REPORTER = self._orig_subagent_reporter
        self._tester._TRACE_REPORTER = self._orig_tester_reporter
        self._verify_loop._TRACE_REPORTER = self._orig_verify_reporter

    def test_no_op_without_consent(self):
        wired = pt.wire_trace_reporters({"data_sharing": False, "feedback_upload": False})
        assert wired is False
        assert self._subagent._TRACE_REPORTER is None
        assert self._tester._TRACE_REPORTER is None

    def test_no_op_with_only_one_consent_flag(self):
        wired = pt.wire_trace_reporters({"data_sharing": True, "feedback_upload": False})
        assert wired is False

    def test_no_op_without_login_even_with_consent(self):
        wired = pt.wire_trace_reporters({
            "data_sharing": True, "feedback_upload": True,
            "api_url": "", "auth_token": None,
        })
        assert wired is False

    def test_wires_both_reporters_when_opted_in_and_logged_in(self):
        fake_client = mock.Mock()
        fake_client.available = True
        with mock.patch(
            "aria_code.cloud_memory.client_from_config", return_value=fake_client
        ):
            wired = pt.wire_trace_reporters({
                "data_sharing": True, "feedback_upload": True,
                "api_url": "https://api.example.com", "auth_token": "tok",
            })

        assert wired is True
        assert self._subagent._TRACE_REPORTER is not None
        assert self._tester._TRACE_REPORTER is not None
        assert self._verify_loop._TRACE_REPORTER is not None

        # A completed subagent task should reach the client.
        self._subagent._TRACE_REPORTER({
            "task_id": "t1", "prompt": "p", "status": "done", "result": "ok", "error": "",
        })
        fake_client.report_execution_trace.assert_called_once()
        assert fake_client.report_execution_trace.call_args[0][0]["source"] == "aria_code.task_ledger"

    def test_wired_self_healing_reporter_reaches_the_client(self):
        fake_client = mock.Mock()
        fake_client.available = True
        with mock.patch(
            "aria_code.cloud_memory.client_from_config", return_value=fake_client
        ):
            pt.wire_trace_reporters({
                "data_sharing": True, "feedback_upload": True,
                "api_url": "https://api.example.com", "auth_token": "tok",
            })

        self._tester._TRACE_REPORTER(
            {"success": True, "retries_used": 1, "final_output": "ok", "patches_applied": []},
            "test and self-heal AAPL",
        )
        fake_client.report_execution_trace.assert_called_once()
        sent = fake_client.report_execution_trace.call_args[0][0]
        assert sent["source"] == "aria_code.self_healing"
        assert sent["goal"] == "test and self-heal AAPL"

    def test_wired_verify_loop_reporter_reaches_the_client(self):
        fake_client = mock.Mock()
        fake_client.available = True
        with mock.patch(
            "aria_code.cloud_memory.client_from_config", return_value=fake_client
        ):
            pt.wire_trace_reporters({
                "data_sharing": True, "feedback_upload": True,
                "api_url": "https://api.example.com", "auth_token": "tok",
            })

        self._verify_loop._TRACE_REPORTER(
            {"all_passed": True, "checks": [], "repair_directive": None},
            "verify workspace",
        )
        fake_client.report_execution_trace.assert_called_once()
        sent = fake_client.report_execution_trace.call_args[0][0]
        assert sent["source"] == "aria_code.verify_loop"


class TestTesterAgentReportsRealRuns:
    """TesterAgent.analyze() must reach a registered reporter with the
    actual SelfHealingResult from a real (here: mocked) heal attempt."""

    def setup_method(self):
        import aria_code.agents.engineering.tester as tester
        self._tester = tester
        self._orig_reporter = tester._TRACE_REPORTER

    def teardown_method(self):
        self._tester._TRACE_REPORTER = self._orig_reporter

    def test_analyze_reports_the_heal_result(self, tmp_path):
        import asyncio
        from aria_code.agents.engineering.tester import TesterAgent
        from aria_code.runtime.self_healing import SelfHealingResult

        received = []
        self._tester.set_trace_reporter(lambda result, goal: received.append((result, goal)))

        target = tmp_path / "strategy.py"
        target.write_text("print('ok')")

        agent = TesterAgent()
        agent.engine.execute_and_heal = mock.AsyncMock(
            return_value=SelfHealingResult(
                success=True, retries_used=0, final_output="ok",
            )
        )

        asyncio.run(agent.analyze("AAPL", {"script_path": str(target)}))

        assert len(received) == 1
        result_dict, goal = received[0]
        assert result_dict["success"] is True
        assert "AAPL" in goal

    def test_no_reporter_registered_is_a_silent_no_op(self, tmp_path):
        import asyncio
        from aria_code.agents.engineering.tester import TesterAgent
        from aria_code.runtime.self_healing import SelfHealingResult

        self._tester.set_trace_reporter(None)
        target = tmp_path / "strategy.py"
        target.write_text("print('ok')")

        agent = TesterAgent()
        agent.engine.execute_and_heal = mock.AsyncMock(
            return_value=SelfHealingResult(success=True, retries_used=0, final_output="ok")
        )

        asyncio.run(agent.analyze("AAPL", {"script_path": str(target)}))  # must not raise
