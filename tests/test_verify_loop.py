"""Tests for runtime.verify_loop — the automated post-edit verification tool
and its (optional) execution-trace reporting hook."""

from __future__ import annotations

import pathlib

import pytest

from aria_code.runtime.verify_loop import tool_verify_changes


@pytest.fixture(autouse=True)
def clear_reporter():
    import aria_code.runtime.verify_loop as vl
    orig = vl._TRACE_REPORTER
    vl._TRACE_REPORTER = None
    yield
    vl._TRACE_REPORTER = orig


class TestToolVerifyChangesContract:
    def test_rejects_non_list_modified_files(self):
        result = tool_verify_changes({"modified_files": "not-a-list"})
        assert result["success"] is False
        assert "modified_files" in result["error"]

    def test_empty_project_with_no_checks_passes_trivially(self, tmp_path):
        result = tool_verify_changes({
            "modified_files": [], "workspace_root": str(tmp_path),
        })
        assert result["success"] is True
        assert result["all_passed"] is True
        assert result["checks"] == []

    def test_syntax_check_on_a_broken_python_file_fails(self, tmp_path):
        bad_file = tmp_path / "broken.py"
        bad_file.write_text("def f(:\n    pass\n")

        result = tool_verify_changes({
            "modified_files": [str(bad_file)], "workspace_root": str(tmp_path),
        })

        assert result["success"] is False
        assert any(c["name"] == "python-syntax" for c in result["checks"])
        assert result["repair_directive"]

    def test_syntax_check_on_a_valid_python_file_passes(self, tmp_path):
        good_file = tmp_path / "ok.py"
        good_file.write_text("def f():\n    return 1\n")

        result = tool_verify_changes({
            "modified_files": [str(good_file)], "workspace_root": str(tmp_path),
        })

        assert result["success"] is True
        assert result["all_passed"] is True


class TestToolIsRegisteredWithTheModel:
    """Guards against the exact bug class this tool started as: a real,
    tested handler that the model can never actually select because it was
    never added to aria_cli.py's LOCAL_TOOLS dispatch dict / LOCAL_TOOL_SCHEMAS
    list (see tests/test_aria_cli_core.py for the sibling guard on commands)."""

    def test_verify_changes_is_dispatchable(self):
        from aria_code import aria_cli as _ac
        assert "verify_changes" in _ac.LOCAL_TOOLS
        handler, _description = _ac.LOCAL_TOOLS["verify_changes"]
        assert handler is tool_verify_changes

    def test_verify_changes_schema_is_visible_to_the_model(self):
        from aria_code import aria_cli as _ac
        names = [
            s.get("name") or s.get("function", {}).get("name")
            for s in _ac.LOCAL_TOOL_SCHEMAS
        ]
        assert "verify_changes" in names


class TestTraceReporting:
    def test_reporter_is_called_with_a_goal_naming_the_files(self, tmp_path):
        import aria_code.runtime.verify_loop as vl
        received = []
        vl.set_trace_reporter(lambda summary, goal: received.append((summary, goal)))

        good_file = tmp_path / "ok.py"
        good_file.write_text("x = 1\n")
        tool_verify_changes({"modified_files": [str(good_file)], "workspace_root": str(tmp_path)})

        assert len(received) == 1
        summary, goal = received[0]
        assert "ok.py" in goal
        assert summary["all_passed"] is True

    def test_no_reporter_registered_is_a_silent_no_op(self, tmp_path):
        result = tool_verify_changes({"modified_files": [], "workspace_root": str(tmp_path)})
        assert result["success"] is True  # must not raise despite no reporter

    def test_a_broken_reporter_never_breaks_verification(self, tmp_path):
        import aria_code.runtime.verify_loop as vl

        def _boom(summary, goal):
            raise RuntimeError("reporter backend unreachable")

        vl.set_trace_reporter(_boom)
        result = tool_verify_changes({"modified_files": [], "workspace_root": str(tmp_path)})
        assert result["success"] is True  # verification result still returned
