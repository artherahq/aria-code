"""Unit tests for runtime.gateway.run_turn (P0 — the neutral single entry).

Drives the real ``run_agent`` with a fake ``provider_fn`` and an empty
``ToolExecutor`` (no network, no real model). Confirms: tokens stream live AND
are returned; provider errors surface; cancellation is reported; the final-text
fallback works for non-streaming providers.
"""

from runtime import ToolExecutor
from runtime.gateway import TurnResult, run_turn


def _executor():
    return ToolExecutor({})


async def test_streams_tokens_live_and_returns_text():
    async def provider(message, history, *, on_token=None, **kw):
        for ch in "hello world":
            if on_token:
                on_token(ch)
        return {"success": True, "response": "hello world", "tool_calls_pending": []}

    seen: list[str] = []
    res = await run_turn(
        "hi", [], provider_fn=provider, tool_executor=_executor(), on_token=seen.append
    )
    assert isinstance(res, TurnResult)
    assert res.text == "hello world"
    assert "".join(seen) == "hello world"  # forwarded live, not only at the end
    assert res.ok and res.error is None and res.cancelled is False


async def test_final_text_fallback_when_not_streamed():
    # A provider that returns a response without streaming any tokens.
    async def provider(message, history, *, on_token=None, **kw):
        return {"success": True, "response": "batched answer", "tool_calls_pending": []}

    res = await run_turn("hi", [], provider_fn=provider, tool_executor=_executor())
    assert res.text == "batched answer"  # recovered from the turn result's final_text
    assert res.ok


async def test_provider_error_surfaces():
    async def provider(message, history, *, on_token=None, **kw):
        return {"success": False, "error": "boom"}

    res = await run_turn("hi", [], provider_fn=provider, tool_executor=_executor())
    assert res.error == "boom"
    assert not res.ok


async def test_cancellation_reported():
    async def provider(message, history, *, on_token=None, **kw):
        return {"success": False, "cancelled": True, "response": "half"}

    res = await run_turn("hi", [], provider_fn=provider, tool_executor=_executor())
    assert res.cancelled is True
    assert not res.ok
