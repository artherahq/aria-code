"""Tests for apps.cli.plan_mode interactive plan mode."""

import pytest
from apps.cli.plan_mode import PlanModeState, _format_params


class TestPlanModeState:
    def test_initial_state(self):
        plan = PlanModeState()
        assert plan.active is False

    def test_enter_sets_active(self):
        plan = PlanModeState()
        plan.enter()
        assert plan.active is True

    def test_exit_clears_active(self):
        plan = PlanModeState()
        plan.enter()
        plan.exit()
        assert plan.active is False

    def test_record_step(self):
        plan = PlanModeState()
        plan.enter()
        step = plan.record_step("read_file", {"path": "/foo.py"}, approved=True)
        assert step.tool == "read_file"
        assert step.approved is True
        assert step.index == 1

    def test_multiple_steps_increments_index(self):
        plan = PlanModeState()
        plan.enter()
        s1 = plan.record_step("read_file", {}, approved=True)
        s2 = plan.record_step("write_file", {}, approved=False)
        assert s1.index == 1
        assert s2.index == 2

    def test_summary(self):
        plan = PlanModeState()
        plan.enter()
        plan.record_step("read_file", {}, approved=True)
        plan.record_step("write_file", {}, approved=True)
        plan.record_step("run_command", {}, approved=False)
        summary = plan.summary()
        assert summary["total"] == 3
        assert summary["approved"] == 2
        assert summary["rejected"] == 1

    def test_exit_resets_steps(self):
        plan = PlanModeState()
        plan.enter()
        plan.record_step("read_file", {}, approved=True)
        plan.exit()
        assert plan.summary()["total"] == 0

    def test_enter_resets_previous_steps(self):
        plan = PlanModeState()
        plan.enter()
        plan.record_step("read_file", {}, approved=True)
        plan.enter()  # re-enter clears state
        assert plan.summary()["total"] == 0


class TestFormatParams:
    def test_path_param(self):
        result = _format_params({"path": "/foo/bar.py"})
        assert "path=" in result

    def test_command_param(self):
        result = _format_params({"command": "ls -la"})
        assert "command=" in result

    def test_long_value_truncated(self):
        result = _format_params({"path": "x" * 100})
        assert "…" in result

    def test_empty_params(self):
        result = _format_params({})
        assert result == ""
