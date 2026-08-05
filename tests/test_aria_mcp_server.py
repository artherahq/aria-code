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

from packages.aria_mcp.server import (
    TOOLS,
    _call_allocation_chart,
    _call_broker_confirm_order,
    _call_comparison_chart,
    _call_edit_image,
    _call_generate_image,
    _call_indicator_chart,
    _call_video_generate_submit,
)


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


@pytest.mark.asyncio
async def test_generate_image_refuses_without_confirmed():
    result = await _call_generate_image({"prompt": "a poster"})
    assert result["success"] is False
    assert "confirmed" in result["error"]


@pytest.mark.asyncio
async def test_generate_image_refuses_with_confirmed_false():
    result = await _call_generate_image({"prompt": "a poster", "confirmed": False})
    assert result["success"] is False
    assert "confirmed" in result["error"]


@pytest.mark.asyncio
async def test_generate_image_calls_client_only_when_confirmed(monkeypatch):
    import openai_image_client

    called = {}

    def fake_generate_image(prompt, **kwargs):
        called["prompt"] = prompt
        called["kwargs"] = kwargs
        return {"success": True, "path": "/tmp/out.png"}

    monkeypatch.setattr(openai_image_client, "generate_image", fake_generate_image)
    result = await _call_generate_image({"prompt": "a poster", "confirmed": True})
    assert result["success"] is True
    assert called["prompt"] == "a poster"
    assert called["kwargs"]["confirmed"] is True


@pytest.mark.asyncio
async def test_edit_image_refuses_without_confirmed():
    result = await _call_edit_image({"image_path": "/tmp/x.jpg", "prompt": "make it duotone"})
    assert result["success"] is False
    assert "confirmed" in result["error"]


@pytest.mark.asyncio
async def test_edit_image_calls_client_only_when_confirmed(monkeypatch):
    import openai_image_client

    called = {}

    def fake_edit_image(image_path, prompt, **kwargs):
        called["image_path"] = image_path
        called["kwargs"] = kwargs
        return {"success": True, "path": "/tmp/out.png"}

    monkeypatch.setattr(openai_image_client, "edit_image", fake_edit_image)
    result = await _call_edit_image({"image_path": "/tmp/x.jpg", "prompt": "make it duotone", "confirmed": True})
    assert result["success"] is True
    assert called["image_path"] == "/tmp/x.jpg"
    assert called["kwargs"]["confirmed"] is True


@pytest.mark.asyncio
async def test_edit_image_omits_mask_path_by_default(monkeypatch):
    import openai_image_client

    called = {}

    def fake_edit_image(image_path, prompt, **kwargs):
        called["kwargs"] = kwargs
        return {"success": True, "path": "/tmp/out.png"}

    monkeypatch.setattr(openai_image_client, "edit_image", fake_edit_image)
    await _call_edit_image({"image_path": "/tmp/x.jpg", "prompt": "make it duotone", "confirmed": True})
    assert called["kwargs"]["mask_path"] is None


@pytest.mark.asyncio
async def test_edit_image_passes_through_mask_path_for_inpainting(monkeypatch):
    import openai_image_client

    called = {}

    def fake_edit_image(image_path, prompt, **kwargs):
        called["kwargs"] = kwargs
        return {"success": True, "path": "/tmp/out.png"}

    monkeypatch.setattr(openai_image_client, "edit_image", fake_edit_image)
    await _call_edit_image({
        "image_path": "/tmp/x.jpg", "prompt": "fill in the sky", "confirmed": True,
        "mask_path": "/tmp/mask.png",
    })
    assert called["kwargs"]["mask_path"] == "/tmp/mask.png"


# ── New chart tools: indicator/comparison/allocation ────────────────────────

def _fake_ohlcv_df():
    import numpy as np
    import pandas as pd

    idx = pd.date_range("2025-01-01", periods=60, freq="D")
    close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 1, 60))
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1000},
        index=idx,
    )


@pytest.mark.asyncio
async def test_indicator_chart_requires_symbol():
    result = await _call_indicator_chart({})
    assert result["success"] is False
    assert "symbol" in result["error"]


@pytest.mark.asyncio
async def test_indicator_chart_writes_artifact_on_success(monkeypatch, tmp_path):
    df = _fake_ohlcv_df()
    import report_generator
    monkeypatch.setattr(report_generator, "_fetch_report_data_sync", lambda symbol: (df, None, {}))

    fake_path = tmp_path / "out.png"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("artifacts.create_user_artifact", lambda *a, **kw: type("A", (), {"path": fake_path})())
        result = await _call_indicator_chart({"symbol": "aapl"})
    assert result["success"] is True
    assert result["path"] == str(fake_path)
    assert fake_path.exists()


@pytest.mark.asyncio
async def test_indicator_chart_no_data_reports_error(monkeypatch):
    import pandas as pd
    import report_generator
    monkeypatch.setattr(report_generator, "_fetch_report_data_sync", lambda symbol: (pd.DataFrame(), None, {}))

    result = await _call_indicator_chart({"symbol": "AAPL"})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_comparison_chart_requires_at_least_two_symbols():
    result = await _call_comparison_chart({"symbols": ["AAPL"]})
    assert result["success"] is False
    assert "symbols" in result["error"]


@pytest.mark.asyncio
async def test_comparison_chart_writes_artifact_on_success(monkeypatch, tmp_path):
    df = _fake_ohlcv_df()
    import report_generator
    monkeypatch.setattr(report_generator, "_fetch_report_data_sync", lambda symbol: (df, None, {}))

    fake_path = tmp_path / "out.png"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("artifacts.create_user_artifact", lambda *a, **kw: type("A", (), {"path": fake_path})())
        result = await _call_comparison_chart({"symbols": ["AAPL", "MSFT"]})
    assert result["success"] is True
    assert fake_path.exists()


@pytest.mark.asyncio
async def test_comparison_chart_no_usable_data_reports_error(monkeypatch):
    import pandas as pd
    import report_generator
    monkeypatch.setattr(report_generator, "_fetch_report_data_sync", lambda symbol: (pd.DataFrame(), None, {}))

    result = await _call_comparison_chart({"symbols": ["AAPL", "MSFT"]})
    assert result["success"] is False


@pytest.mark.asyncio
async def test_allocation_chart_writes_artifact_on_success(monkeypatch, tmp_path):
    import packages.aria_mcp.server as server_mod
    from brokers.base import Position

    class _FakeBroker:
        def positions(self):
            return [Position(symbol="AAPL", market_value=15000)]

    monkeypatch.setattr(server_mod, "_get_broker", lambda broker_id="": _FakeBroker())

    fake_path = tmp_path / "out.png"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("artifacts.create_user_artifact", lambda *a, **kw: type("A", (), {"path": fake_path})())
        result = await _call_allocation_chart({})
    assert result["success"] is True
    assert fake_path.exists()


@pytest.mark.asyncio
async def test_allocation_chart_no_positions_reports_error(monkeypatch):
    import packages.aria_mcp.server as server_mod

    class _FakeBroker:
        def positions(self):
            return []

    monkeypatch.setattr(server_mod, "_get_broker", lambda broker_id="": _FakeBroker())
    result = await _call_allocation_chart({})
    assert result["success"] is False
