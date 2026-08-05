"""Tests for packages/aria_mcp/server.py — the safety-critical properties:
the _WRITE_SAFE allowlist boundary, the video-generation cost-confirm gate
(submission is the moment real money is spent, so it must hard-refuse
without confirmed=true), and the two-gate chat-confirmed trade execution
path (aria.broker.confirm_order): it must hard-refuse unless the broker was
separately opted in via /trade allow-chat-confirm at the terminal AND the
call itself passes confirmed=true.
"""
from __future__ import annotations

import pytest

from packages.aria_mcp.server import TOOLS, _call_broker_confirm_order, _call_video_generate_submit


def test_no_skill_backed_exposures_reach_tools_list():
    # skill: targets aren't clean callables in this server — must never
    # silently appear as a callable tool.
    from packages.aria_mcp.bridge import default_exposures

    tool_names = {t["name"] for t in TOOLS}
    for exposure in default_exposures():
        if exposure.target.startswith("skill:"):
            assert exposure.name not in tool_names


def test_execute_order_preview_is_not_bound_at_module_level():
    # execute_order_preview is real and reachable now (via
    # _call_broker_confirm_order's gated path), but only as a function-local
    # import inside that one handler — never a module-level attribute a
    # careless import elsewhere in this file could pick up unguarded.
    import packages.aria_mcp.server as server_mod

    assert not hasattr(server_mod, "execute_order_preview")
    assert "_call_broker_confirm_order" in dir(server_mod)


@pytest.mark.asyncio
async def test_confirm_order_refuses_without_confirmed():
    result = await _call_broker_confirm_order({"preview_id": "tp_x", "broker_id": "some_broker"})
    assert result["success"] is False
    assert "confirmed" in result["error"]


@pytest.mark.asyncio
async def test_confirm_order_refuses_with_confirmed_false():
    result = await _call_broker_confirm_order(
        {"preview_id": "tp_x", "broker_id": "some_broker", "confirmed": False}
    )
    assert result["success"] is False
    assert "confirmed" in result["error"]


@pytest.mark.asyncio
async def test_confirm_order_refuses_missing_preview_id():
    result = await _call_broker_confirm_order({"confirmed": True})
    assert result["success"] is False
    assert "preview_id" in result["error"]


@pytest.mark.asyncio
async def test_confirm_order_refuses_when_chat_confirm_not_enabled(monkeypatch):
    import packages.aria_mcp.server as server_mod

    class _FakeBroker:
        broker_id = "some_broker"

    monkeypatch.setattr(server_mod, "_get_broker", lambda broker_id="": _FakeBroker())
    monkeypatch.setattr("brokers.config.is_chat_confirm_enabled", lambda broker_id: False)

    result = await _call_broker_confirm_order(
        {"preview_id": "tp_x", "broker_id": "some_broker", "confirmed": True}
    )
    assert result["success"] is False
    assert "allow-chat-confirm" in result["error"]


@pytest.mark.asyncio
async def test_confirm_order_executes_only_when_both_gates_pass(monkeypatch):
    import packages.aria_mcp.server as server_mod

    class _FakeBroker:
        broker_id = "some_broker"

    monkeypatch.setattr(server_mod, "_get_broker", lambda broker_id="": _FakeBroker())
    monkeypatch.setattr("brokers.config.is_chat_confirm_enabled", lambda broker_id: True)

    called = {}

    def fake_execute(broker, preview_id, *, confirmed, source):
        called["preview_id"] = preview_id
        called["confirmed"] = confirmed
        called["source"] = source
        return {"success": True, "order_id": "o1"}

    monkeypatch.setattr("brokers.trading.execute_order_preview", fake_execute)

    result = await _call_broker_confirm_order(
        {"preview_id": "tp_x", "broker_id": "some_broker", "confirmed": True}
    )
    assert result["success"] is True
    assert called["preview_id"] == "tp_x"
    assert called["confirmed"] is True
    assert called["source"] == "chat_mcp"


@pytest.mark.asyncio
async def test_generate_submit_refuses_without_confirmed():
    result = await _call_video_generate_submit({"provider": "kling", "prompt": "a sunset"})
    assert result["success"] is False
    assert "confirmed" in result["error"]


@pytest.mark.asyncio
async def test_generate_submit_refuses_with_confirmed_false():
    result = await _call_video_generate_submit(
        {"provider": "kling", "prompt": "a sunset", "confirmed": False}
    )
    assert result["success"] is False
    assert "confirmed" in result["error"]


@pytest.mark.asyncio
async def test_generate_submit_rejects_unknown_provider_even_when_confirmed():
    result = await _call_video_generate_submit(
        {"provider": "not-a-real-provider", "prompt": "a sunset", "confirmed": True}
    )
    assert result["success"] is False
    assert "provider" in result["error"]


@pytest.mark.asyncio
async def test_generate_submit_calls_provider_only_when_confirmed(monkeypatch):
    import kling_video_client

    called = {}

    def fake_submit_video(prompt, **kwargs):
        called["prompt"] = prompt
        called["kwargs"] = kwargs
        return {"success": True, "task_id": "t1", "provider": "kling"}

    monkeypatch.setattr(kling_video_client, "submit_video", fake_submit_video)
    result = await _call_video_generate_submit(
        {"provider": "kling", "prompt": "a sunset", "confirmed": True, "duration": 5}
    )
    assert result["success"] is True
    assert called["prompt"] == "a sunset"
