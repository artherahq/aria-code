import pytest

from apps.cli.providers.base import AriaSSEProvider, LLMDone, LLMToken, LLMToolCall, OllamaProvider
from apps.cli.deterministic import run_deterministic_chain
from runtime import ToolExecutor
from packages.aria_sdk import (
    AriaAgentOptions,
    AriaMessage,
    AriaResult,
    AriaSDKClient,
    ProviderSelection,
    build_llm_provider,
    normalize_provider_name,
    query,
    run,
)


async def _collect(async_iterable):
    return [event async for event in async_iterable]


def test_sdk_public_exports_are_importable():
    options = AriaAgentOptions(model="test-model", provider="local", cwd="/tmp/aria")
    message = AriaMessage(kind="result", content="ok", data={"success": True})
    result = AriaResult(success=True, content="ok")

    assert options.to_dict()["model"] == "test-model"
    assert options.to_dict()["provider"] == "local"
    assert options.to_dict()["get_broker_registry"] is None
    assert message.to_dict()["data"] == {"success": True}
    assert result.to_dict()["success"] is True


def test_sdk_provider_factory_normalizes_local_and_cloud_modes():
    assert normalize_provider_name("local") == "ollama"
    assert normalize_provider_name("cloud") == "aria_sse"

    local = build_llm_provider(AriaAgentOptions(provider="auto", local_mode=True))
    cloud = build_llm_provider(AriaAgentOptions(provider="auto", local_mode=False, api_url="https://example.com"))

    assert local.name == "ollama"
    assert isinstance(local.provider, OllamaProvider)
    assert cloud.name == "aria_sse"
    assert isinstance(cloud.provider, AriaSSEProvider)


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
    assert events[0].data["provider"] == "auto"
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
async def test_sdk_llm_path_uses_provider_factory(monkeypatch):
    import packages.aria_sdk.client as sdk_client

    class FakeProvider:
        async def stream(self, messages, tools, *, cancel_event=None):
            assert messages[-1] == {"role": "user", "content": "hello"}
            yield LLMToken("hi")
            yield LLMDone(response="hi", provider="fake_provider", success=True)

    monkeypatch.setattr(
        sdk_client,
        "build_llm_provider",
        lambda options: ProviderSelection("fake_provider", FakeProvider()),
    )

    client = AriaSDKClient(AriaAgentOptions(deterministic=False))
    events = await _collect(client.query("hello"))

    assert [event.kind for event in events] == ["system", "user", "token", "assistant", "result"]
    assert events[-1].data["provider"] == "fake_provider"
    assert client.messages[-1] == {"role": "assistant", "content": "hi"}


@pytest.mark.asyncio
async def test_sdk_agent_path_emits_tool_events(monkeypatch):
    import packages.aria_sdk.client as sdk_client

    class FakeProvider:
        def __init__(self):
            self.calls = 0

        async def stream(self, messages, tools, *, cancel_event=None):
            self.calls += 1
            if self.calls == 1:
                yield LLMToolCall("echo", {"value": 7})
                yield LLMDone(response="need echo", provider="fake_provider", success=True)
            else:
                assert "Tool Results" in messages[-1]["content"]
                yield LLMToken("done")
                yield LLMDone(response="done", provider="fake_provider", success=True)

    fake_provider = FakeProvider()
    monkeypatch.setattr(
        sdk_client,
        "build_llm_provider",
        lambda options: ProviderSelection("fake_provider", fake_provider),
    )

    def echo(params):
        return {"success": True, "data": {"value": params["value"]}}

    client = AriaSDKClient(
        AriaAgentOptions(deterministic=False, max_turns=3),
        tool_executor=ToolExecutor({"echo": (echo, "Echo")}),
        tool_result_formatter=lambda tool, result: f"{tool}:{result['data']['value']}",
    )
    events = await _collect(client.query("use a tool"))
    kinds = [event.kind for event in events]

    assert "tool_use" in kinds
    assert "tool_result" in kinds
    assert events[-1].kind == "result"
    assert events[-1].data["provider"] == "fake_provider"
    assert events[-1].data["tools"] == ["echo"]
    assert client.messages[-1] == {"role": "assistant", "content": "done"}


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
