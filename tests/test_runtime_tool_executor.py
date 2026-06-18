import unittest

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
