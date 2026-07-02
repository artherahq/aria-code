import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from runtime import AgentTurnState, RuntimeTrace, ToolExecutor


def _echo_tool(params):
    return {"success": True, "data": {"params": params}}


class RuntimeToolExecutorTests(unittest.TestCase):
    def test_execute_local_records_trace(self):
        trace = RuntimeTrace()
        executor = ToolExecutor({"echo": (_echo_tool, "Echo")}, trace=trace)
        result = executor.execute_local("echo", {"x": 1})
        self.assertTrue(result["success"])
        self.assertEqual(len(trace.tool_calls), 1)
        self.assertEqual(trace.tool_calls[0].tool, "echo")
        self.assertTrue(any(event.type == "tool_call" for event in trace.events))

    def test_run_command_params_are_injected_from_config(self):
        captured = {}

        def run_tool(params):
            captured.update(params)
            return {"success": True, "data": {}}

        executor = ToolExecutor(
            {"run_command": (run_tool, "Run")},
            config={
                "command_policy": "balanced",
                "permission_mode": "read-only",
                "network_enabled": False,
            },
        )
        result = executor.execute_local("run_command", {"command": "pytest -q"})
        self.assertTrue(result["success"])
        self.assertEqual(captured["policy"], "balanced")
        self.assertEqual(captured["permission_mode"], "read-only")
        self.assertFalse(captured["network_enabled"])

    def test_local_tool_receives_runtime_execution_context(self):
        captured = {}

        def write_tool(params):
            captured.update(params)
            return {"success": True, "data": {}}

        executor = ToolExecutor(
            {"write_file": (write_tool, "Write")},
            execution_context=lambda: {
                "_run_id": "run-123",
                "_session_id": "session-456",
                "public": "must-not-be-injected",
            },
        )

        result = executor.execute_local("write_file", {"path": "x.py"})

        self.assertTrue(result["success"])
        self.assertEqual(captured["_run_id"], "run-123")
        self.assertEqual(captured["_session_id"], "session-456")
        self.assertNotIn("public", captured)

    def test_isolated_workspace_resolves_relative_paths(self):
        captured = {}
        with TemporaryDirectory() as temp_dir:
            executor = ToolExecutor(
                {"read_file": (lambda params: captured.update(params) or {"success": True}, "Read")},
                execution_context=lambda: {
                    "_workspace": temp_dir,
                    "_workspace_restricted": True,
                },
            )

            result = executor.execute_local("read_file", {"path": "src/app.py"})

            self.assertTrue(result["success"])
            self.assertEqual(captured["path"], str(Path(temp_dir, "src/app.py").resolve()))

    def test_isolated_workspace_denies_path_escape(self):
        called = []
        with TemporaryDirectory() as temp_dir:
            executor = ToolExecutor(
                {"write_file": (lambda params: called.append(params) or {"success": True}, "Write")},
                execution_context=lambda: {
                    "_workspace": temp_dir,
                    "_workspace_restricted": True,
                },
            )

            result = executor.execute_local("write_file", {"path": "../outside.py"})

            self.assertFalse(result["success"])
            self.assertIn("outside the isolated workspace", result["error"])
            self.assertEqual(called, [])

    def test_isolated_workspace_injects_command_cwd(self):
        captured = {}
        with TemporaryDirectory() as temp_dir:
            executor = ToolExecutor(
                {"run_command": (lambda params: captured.update(params) or {"success": True}, "Run")},
                execution_context=lambda: {
                    "_workspace": temp_dir,
                    "_workspace_restricted": True,
                },
            )

            result = executor.execute_local("run_command", {"command": "pytest -q"})

            self.assertTrue(result["success"])
            self.assertEqual(captured["cwd"], str(Path(temp_dir).resolve()))

    def test_unknown_tool_is_error(self):
        executor = ToolExecutor({})
        result = executor.execute_local("missing", {})
        self.assertFalse(result["success"])
        self.assertIn("Unknown local tool", result["error"])

    def test_trace_records_turn_results(self):
        trace = RuntimeTrace()
        state = AgentTurnState(provider="deepseek")
        state.append_response("done")
        state.add_usage({"prompt_tokens": 2, "completion_tokens": 4})
        turn = state.build_result(elapsed=1.0).to_envelope()

        record = trace.add_turn_result(turn.to_dict())

        self.assertEqual(len(trace.turn_results), 1)
        self.assertEqual(record.provider, "deepseek")
        self.assertEqual(trace.events[-1].type, "turn_complete")
        self.assertEqual(trace.to_dict()["turn_results"][0]["summary"], turn.summary)


if __name__ == "__main__":
    unittest.main()
