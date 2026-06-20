import pytest

from apps.cli.deterministic import run_deterministic_chain
from packages.aria_sdk import AriaAgentOptions, AriaMessage, AriaResult, AriaSDKClient, query, run


async def _collect(async_iterable):
    return [event async for event in async_iterable]


def test_sdk_public_exports_are_importable():
    options = AriaAgentOptions(model="test-model", cwd="/tmp/aria")
    message = AriaMessage(kind="result", content="ok", data={"success": True})
    result = AriaResult(success=True, content="ok")

    assert options.to_dict()["model"] == "test-model"
    assert options.to_dict()["get_broker_registry"] is None
    assert message.to_dict()["data"] == {"success": True}
    assert result.to_dict()["success"] is True


@pytest.mark.asyncio
async def test_sdk_query_uses_deterministic_router(monkeypatch):
    import packages.aria_sdk.client as sdk_client

    calls = []

    def fake_deterministic_chain(message, *, model_has_tools, history, has_brokers, get_broker_registry):
        calls.append((message, model_has_tools, history, has_brokers, get_broker_registry))
        return {
            "success": True,
            "response": "deterministic answer",
            "tools_used": ["strategy_advice"],
        }

    monkeypatch.setattr(sdk_client, "run_deterministic_chain", fake_deterministic_chain)

    client = AriaSDKClient(AriaAgentOptions(model="sdk-test", model_has_tools=False))
    events = await _collect(client.query("如果我要写一个美股量化策略，你觉得要从几个角度去写"))

    assert [event.kind for event in events] == ["system", "user", "assistant", "result"]
    assert events[-1].data["success"] is True
    assert events[-1].data["provider"] == "deterministic"
    assert events[-1].data["tools_used"] == ["strategy_advice"]
    assert client.messages[-1] == {"role": "assistant", "content": "deterministic answer"}
    assert calls and calls[0][1] is False


@pytest.mark.asyncio
async def test_sdk_query_falls_back_to_llm_when_deterministic_misses(monkeypatch):
    import packages.aria_sdk.client as sdk_client

    monkeypatch.setattr(
        sdk_client,
        "run_deterministic_chain",
        lambda *args, **kwargs: {"success": False, "error": "miss"},
    )

    async def fake_run_llm(self, prompt, *, history, cancel_event=None):
        yield AriaMessage(kind="token", role="assistant", content="hello")
        yield AriaMessage(
            kind="result",
            role="assistant",
            content="hello",
            data={"success": True, "provider": "fake", "session_id": self.session_id},
        )

    monkeypatch.setattr(AriaSDKClient, "_run_llm", fake_run_llm)

    events = await _collect(query("你好", model="fake-model"))

    assert [event.kind for event in events] == ["system", "user", "token", "result"]
    assert events[-1].data["provider"] == "fake"


@pytest.mark.asyncio
async def test_sdk_run_collects_final_result(monkeypatch):
    import packages.aria_sdk.client as sdk_client

    monkeypatch.setattr(
        sdk_client,
        "run_deterministic_chain",
        lambda *args, **kwargs: {"success": True, "response": "done", "tools_used": ["test"]},
    )

    result = await run("anything", model="fake-model")

    assert result.success is True
    assert result.content == "done"
    assert result.provider == "deterministic"


def test_deterministic_router_preserves_strategy_advice_path():
    result = run_deterministic_chain(
        "如果我要写一个美股量化策略，你觉得要从几个角度去写",
        model_has_tools=True,
    )

    assert result["success"] is True
    assert result["tools_used"] == ["strategy_advice"]
    assert "不需要先写文件" in result["response"]
