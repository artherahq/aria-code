import unittest

from runtime import (
    AgentErrorPresentation,
    AgentTurnState,
    AgentTurnResult,
    AgentTurnEnvelope,
    ToolExecutor,
    ToolBatchState,
    ToolTurnPlan,
    build_next_turn_messages,
    build_tool_followup,
    collect_parallel_done,
    record_tool_result,
    run_parallel_tools,
    run_serial_tool,
    split_tool_calls,
)


class RuntimeAgentLoopTests(unittest.TestCase):
    def test_agent_error_presentation_no_provider(self):
        presentation = AgentErrorPresentation.from_error("no_provider")

        self.assertEqual(presentation.error, "no_provider")
        self.assertEqual(presentation.level, "warning")
        self.assertFalse(presentation.use_generic_error_prefix)
        self.assertEqual(presentation.lines[0], "没有可用的 AI 模型")
        self.assertTrue(any("ollama serve" in line for line in presentation.lines))

    def test_agent_error_presentation_all_providers_failed(self):
        presentation = AgentErrorPresentation.from_error("all_providers_failed")

        self.assertEqual(presentation.level, "warning")
        self.assertEqual(
            presentation.lines,
            ["所有云端 Provider 均请求失败，请检查网络或 API Key 是否有效。"],
        )

    def test_agent_error_presentation_unknown_error(self):
        presentation = AgentErrorPresentation.from_error("boom")

        self.assertEqual(presentation.level, "error")
        self.assertTrue(presentation.use_generic_error_prefix)
        self.assertEqual(presentation.lines, ["Error: boom"])

    def test_agent_turn_state_accumulates_model_results(self):
        state = AgentTurnState(provider="aws")

        state.apply_model_result({
            "response": "hello",
            "provider": "deepseek",
            "tools_used": ["read_file", "read_file", "run_command"],
            "sources": [{"url": "x"}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "thinking_tokens": 2,
            },
        })
        state.apply_model_result({
            "response": " world",
            "usage": {"prompt_tokens": 3, "completion_tokens": 4},
        })

        self.assertEqual(state.total_response, "hello world")
        self.assertEqual(state.provider, "deepseek")
        self.assertEqual(state.sources, [{"url": "x"}])
        self.assertEqual(state.usage["prompt_tokens"], 13)
        self.assertEqual(state.usage["completion_tokens"], 9)
        self.assertEqual(state.usage["thinking_tokens"], 2)
        self.assertEqual(state.unique_tools(), ["read_file", "run_command"])

    def test_agent_turn_state_token_counts_use_fallbacks(self):
        state = AgentTurnState()

        self.assertEqual(
            state.token_counts(token_count=7, thinking_tokens=3),
            (0, 7, 3, 10),
        )

        state.add_usage({"prompt_tokens": 2, "completion_tokens": 5, "thinking_tokens": 1})

        self.assertEqual(
            state.token_counts(token_count=7, thinking_tokens=3),
            (2, 5, 1, 8),
        )

    def test_agent_turn_state_tool_time_and_final_text(self):
        state = AgentTurnState()

        state.add_tool_time(1.5)
        state.append_response("")

        self.assertEqual(state.generation_time(5.0), 3.5)
        self.assertEqual(state.final_text("fallback"), "fallback")

        state.append_response("done")
        self.assertEqual(state.final_text("fallback"), "done")
        state.reset_response()
        self.assertEqual(state.total_response, "")

    def test_agent_turn_state_builds_metadata_from_usage(self):
        state = AgentTurnState(provider="deepseek")
        state.add_usage({"prompt_tokens": 100, "completion_tokens": 50, "thinking_tokens": 10})
        state.add_tool_time(1.0)
        state.tools_used.extend(["read_file", "read_file", "run_command"])

        metadata = state.build_metadata(elapsed=3.0)

        self.assertEqual(metadata.prompt_tokens, 100)
        self.assertEqual(metadata.completion_tokens, 50)
        self.assertEqual(metadata.thinking_tokens, 10)
        self.assertEqual(metadata.total_tokens, 160)
        self.assertEqual(metadata.generation_time, 2.0)
        self.assertEqual(metadata.provider, "deepseek")
        self.assertEqual(metadata.tools, ["read_file", "run_command"])
        self.assertEqual(metadata.system_prompt_estimate("hello"), 99)
        self.assertEqual(metadata.parts, [
            "3.0s",
            "160 tokens (in: 100, out: 50, think: 10)",
            "25 t/s",
            "tools: 1.0s",
            "deepseek",
            "read_file run_command",
        ])

    def test_agent_turn_state_builds_metadata_from_token_fallback(self):
        state = AgentTurnState()

        metadata = state.build_metadata(elapsed=2.0, token_count=20)

        self.assertEqual(metadata.prompt_tokens, 0)
        self.assertEqual(metadata.completion_tokens, 20)
        self.assertEqual(metadata.total_tokens, 20)
        self.assertEqual(metadata.parts, ["2.0s", "20 tokens (out: 20)", "10 t/s"])

    def test_agent_turn_state_builds_result(self):
        state = AgentTurnState(provider="openai")
        state.append_response("final")
        state.sources.append({"id": "source-1"})
        state.tools_used.extend(["read_file", "read_file"])
        state.add_usage({"prompt_tokens": 4, "completion_tokens": 6})

        result = state.build_result(elapsed=2.0)

        self.assertTrue(result.success)
        self.assertFalse(result.cancelled)
        self.assertEqual(result.error, "")
        self.assertEqual(result.final_text, "final")
        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.tools, ["read_file"])
        self.assertEqual(result.sources, [{"id": "source-1"}])
        self.assertEqual(result.metadata.total_tokens, 10)
        self.assertEqual(result.to_dict()["metadata"]["parts"], result.metadata.parts)

    def test_agent_turn_state_builds_cancelled_result(self):
        state = AgentTurnState(provider="ollama")
        state.append_response("partial")
        state.add_usage({"completion_tokens": 3})

        result = state.build_cancelled_result(elapsed=1.0)

        self.assertTrue(result.success)
        self.assertTrue(result.cancelled)
        self.assertEqual(result.error, "")
        self.assertEqual(result.final_text, "partial")
        self.assertEqual(result.provider, "ollama")
        self.assertEqual(result.metadata.parts, ["1.0s", "3 tokens (out: 3)", "3 t/s", "ollama"])

    def test_agent_turn_state_builds_error_result(self):
        state = AgentTurnState()

        result = state.build_error_result(None, elapsed=0.25, fallback_response="partial")

        self.assertFalse(result.success)
        self.assertFalse(result.cancelled)
        self.assertEqual(result.error, "Unknown error")
        self.assertEqual(result.final_text, "partial")

    def test_agent_turn_result_factories(self):
        cancelled = AgentTurnResult.cancelled_result(final_text="partial")
        failed = AgentTurnResult.error_result("no_provider")

        self.assertTrue(cancelled.success)
        self.assertTrue(cancelled.cancelled)
        self.assertEqual(cancelled.final_text, "partial")
        self.assertFalse(failed.success)
        self.assertEqual(failed.error, "no_provider")

    def test_agent_turn_result_to_envelope_is_stable(self):
        state = AgentTurnState(provider="deepseek")
        state.append_response("done")
        state.add_usage({"prompt_tokens": 2, "completion_tokens": 4})

        result = state.build_result(elapsed=1.2)
        env = result.to_envelope()

        self.assertIsInstance(env, AgentTurnEnvelope)
        self.assertEqual(env.status, "ok")
        self.assertEqual(env.provider, "deepseek")
        self.assertIn("1.2s", env.summary)
        self.assertIn("6 tokens (in: 2, out: 4)", env.summary)
        self.assertIn("deepseek", env.summary)
        self.assertTrue(env.to_dict()["success"])

    def test_split_tool_calls_keeps_write_tools_serial(self):
        read = {"tool": "read_file", "params": {}}
        search = {"tool": "search_code", "params": {}}
        write = {"tool": "write_file", "params": {}}
        run = {"tool": "run_command", "params": {}}

        parallel, serial = split_tool_calls([read, write, search, run])

        self.assertEqual(parallel, [read, search])
        self.assertEqual(serial, [write, run])

    def test_collect_parallel_done_preserves_original_indices(self):
        read = {"tool": "read_file", "params": {}}
        write = {"tool": "write_file", "params": {}}
        search = {"tool": "search_code", "params": {}}
        result_read = {"success": True, "data": "read"}
        result_search = {"success": True, "data": "search"}

        done = collect_parallel_done(
            [read, write, search],
            [(read, result_read), (search, result_search)],
        )

        self.assertEqual(done, {0: result_read, 2: result_search})

    def test_build_tool_followup(self):
        followup = build_tool_followup([
            {"tool": "read_file", "result": "OK: 10 lines"},
            {"tool": "run_command", "result": "exit_code=0"},
        ])
        self.assertIn("[read_file]: OK: 10 lines", followup)
        self.assertIn("[run_command]: exit_code=0", followup)
        self.assertTrue(followup.endswith("Please continue your analysis using these results."))

    def test_record_tool_result_uses_formatter(self):
        records = []

        def formatter(tool, result):
            return f"{tool}:{result['data']}"

        record = record_tool_result(records, "read_file", {"success": True, "data": "ok"}, formatter)

        self.assertEqual(record, {"tool": "read_file", "result": "read_file:ok"})
        self.assertEqual(records, [record])

    def test_build_next_turn_messages(self):
        assistant, user, followup = build_next_turn_messages(
            "assistant text",
            [{"tool": "read_file", "result": "OK"}],
        )

        self.assertEqual(assistant, {"role": "assistant", "content": "assistant text"})
        self.assertEqual(user["role"], "user")
        self.assertEqual(user["content"], followup)
        self.assertIn("[read_file]: OK", followup)

    def test_tool_batch_state_records_results_and_elapsed_time(self):
        batch = ToolBatchState()

        def formatter(tool, result):
            return f"{tool}:{result['data']}"

        record = batch.add_result(
            "run_command",
            {"success": True, "data": "ok"},
            formatter,
            elapsed=1.25,
        )

        self.assertEqual(record, {"tool": "run_command", "result": "run_command:ok"})
        self.assertEqual(batch.tool_results, [record])
        self.assertEqual(batch.elapsed_total, 1.25)
        self.assertFalse(batch.cancelled)

    def test_tool_batch_state_cancel_and_next_turn(self):
        batch = ToolBatchState()
        batch.cancel()
        batch.add_result("read_file", {"success": True, "data": "ok"}, lambda _tool, _result: "OK")

        assistant, user, followup = batch.build_next_turn("assistant text")

        self.assertTrue(batch.cancelled)
        self.assertEqual(assistant, {"role": "assistant", "content": "assistant text"})
        self.assertEqual(user["content"], followup)
        self.assertIn("[read_file]: OK", followup)

    def test_tool_turn_plan_preserves_order_and_parallel_results(self):
        read = {"tool": "read_file", "params": {"path": "a.py"}}
        write = {"tool": "write_file", "params": {"path": "a.py"}}
        search = {"tool": "search_code", "params": {"query": "x"}}
        read_result = {"success": True, "data": "read"}
        search_result = {"success": True, "data": "search"}

        plan = ToolTurnPlan(
            pending=[read, write, search],
            parallel_done={0: read_result, 2: search_result},
        )
        tasks = plan.tasks()

        self.assertEqual([task.tool_name for task in tasks], ["read_file", "write_file", "search_code"])
        self.assertIs(tasks[0].parallel_result, read_result)
        self.assertTrue(tasks[0].has_parallel_result)
        self.assertFalse(tasks[1].has_parallel_result)
        self.assertIs(tasks[2].parallel_result, search_result)
        self.assertIs(tasks[1].params, write["params"])

    def test_tool_call_task_progress_label(self):
        plan = ToolTurnPlan(pending=[
            {"tool": "read_file", "params": {}},
            {"tool": "run_command", "params": {}},
        ])
        first, second = plan.tasks()

        self.assertEqual(first.progress_label(2), "  [1/2] Running read_file...")
        self.assertEqual(second.progress_label(2), "  [2/2] Running run_command...")
        self.assertEqual(first.progress_label(1), "  Running read_file...")


class RuntimeAgentLoopAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_parallel_tools_executes_only_parallel_safe_tools(self):
        calls = []

        def read_tool(params):
            calls.append(("local", params["path"]))
            return {"success": True, "data": {"path": params["path"]}}

        async def remote_runner(tool, params):
            calls.append(("remote", tool))
            return {"success": True, "data": {"tool": tool}}

        read = {"tool": "read_file", "params": {"path": "a.py"}}
        write = {"tool": "write_file", "params": {"path": "a.py", "content": "x"}}
        remote = {"tool": "remote_tool", "params": {}}
        executor = ToolExecutor({"read_file": (read_tool, "Read")})

        done = await run_parallel_tools(
            [read, write, remote],
            executor,
            remote_runner=remote_runner,
        )

        self.assertEqual(set(done.keys()), {0, 2})
        self.assertEqual(done[0]["data"]["path"], "a.py")
        self.assertEqual(done[2]["data"]["tool"], "remote_tool")
        self.assertNotIn(("local", "write_file"), calls)

    async def test_run_parallel_tools_converts_remote_exception_to_result(self):
        async def remote_runner(_tool, _params):
            raise RuntimeError("remote failed")

        remote = {"tool": "remote_tool", "params": {}}
        executor = ToolExecutor({})

        done = await run_parallel_tools([remote], executor, remote_runner=remote_runner)

        self.assertFalse(done[0]["success"])
        self.assertIn("remote failed", done[0]["error"])

    async def test_run_serial_tool_local(self):
        def echo(params):
            return {"success": True, "data": params}

        executor = ToolExecutor({"echo": (echo, "Echo")})
        result, elapsed = await run_serial_tool("echo", {"x": 1}, executor)

        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["x"], 1)
        self.assertGreaterEqual(elapsed, 0)

    async def test_run_serial_tool_remote_with_hooks(self):
        hook_events = []

        async def remote_runner(tool, params):
            self.assertEqual(tool, "remote_tool")
            return {"success": True, "data": params}

        def hook(event, tool, params, result=None):
            hook_events.append((event, tool, bool(result)))

        executor = ToolExecutor({})
        result, _elapsed = await run_serial_tool(
            "remote_tool",
            {"y": 2},
            executor,
            remote_runner=remote_runner,
            hook=hook,
        )

        self.assertTrue(result["success"])
        self.assertEqual(hook_events, [("pre_tool", "remote_tool", False), ("post_tool", "remote_tool", True)])

    async def test_run_serial_tool_remote_exception(self):
        async def remote_runner(_tool, _params):
            raise RuntimeError("remote failed")

        executor = ToolExecutor({})
        result, _elapsed = await run_serial_tool("remote_tool", {}, executor, remote_runner=remote_runner)

        self.assertFalse(result["success"])
        self.assertIn("remote failed", result["error"])


if __name__ == "__main__":
    unittest.main()
