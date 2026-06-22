import inspect
import unittest

from apps.cli.providers.runtime_bridge import build_tool_executor, make_provider_fn


class RuntimeBridgeTests(unittest.TestCase):
    def test_tool_executor_dispatches_local_tool(self):
        registry = {"echo": (lambda p: {"success": True, "echo": p.get("x")}, {})}
        ex = build_tool_executor(registry)
        self.assertEqual(ex.execute_local("echo", {"x": 7}), {"success": True, "echo": 7})

    def test_tool_executor_unknown_tool_is_graceful(self):
        ex = build_tool_executor({})
        result = ex.execute_local("nope", {})
        self.assertFalse(result["success"])

    def test_make_provider_fn_returns_coroutine_fn(self):
        pf = make_provider_fn(model="m", config={}, api_url=None,
                              ollama_url="http://x", tool_schemas=[])
        self.assertTrue(inspect.iscoroutinefunction(pf))


if __name__ == "__main__":
    unittest.main()
