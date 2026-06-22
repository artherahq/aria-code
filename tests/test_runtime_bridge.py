import asyncio
import inspect
import unittest

import apps.cli.providers.runtime_bridge as rb
from apps.cli.providers.runtime_bridge import build_tool_executor, make_provider_fn


def test_run_chat_via_runtime_streams_tokens_and_returns_text(monkeypatch):
    """Verify the run_agent driver consumes token events and accumulates text,
    independent of a real LLM (the live REPL path differs only in the provider)."""
    async def fake_provider_fn(message, history, *, on_token=None, **kwargs):
        for tok in ("Hello", " ", "world"):
            if on_token:
                on_token(tok)
        return {"success": True, "response": "Hello world", "provider": "fake"}

    monkeypatch.setattr(rb, "make_provider_fn", lambda **kw: fake_provider_fn)

    seen: list = []
    text = asyncio.run(rb.run_chat_via_runtime(
        prompt="hi", history=[], local_tools={}, tool_schemas=[],
        model="m", config={}, api_url=None, ollama_url="http://x",
        on_token=seen.append, max_rounds=2,
    ))
    # The driver returns the turn's text (streamed tokens, or the result's
    # authoritative final_text when the provider returns a whole response).
    assert text == "Hello world"


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
