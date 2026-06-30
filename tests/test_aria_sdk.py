import pytest
import sys
import types

from apps.cli.providers.base import (
    AriaSSEProvider,
    LLMDone,
    LLMStatus,
    LLMToken,
    LLMToolCall,
    LLMToolResult,
    OllamaProvider,
)
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
    stream_provider_result,
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


def test_ollama_stream_resolver_supports_direct_script_entrypoint(monkeypatch):
    import apps.cli.providers.base as provider_base

    sentinel = lambda: None
    fake_main = types.SimpleNamespace(stream_ollama=sentinel)
    monkeypatch.delitem(sys.modules, "aria_cli", raising=False)
    monkeypatch.setitem(sys.modules, "__main__", fake_main)

    assert provider_base._resolve_ollama_stream() is sentinel


@pytest.mark.asyncio
async def test_callback_provider_streams_events_before_done(monkeypatch):
    import apps.cli.providers.base as provider_base

    async def fake_stream_ollama(*args, **kwargs):
        assert kwargs["tool_schemas"] == [{"type": "function", "function": {"name": "echo"}}]
        assert kwargs["defer_tool_execution"] is True
        kwargs["on_token"]("hello")
        kwargs["on_thinking"]("thinking")
        kwargs["on_tool_call"]("echo", {"x": 1})
        kwargs["on_tool_result"]("echo", "done")
        return {"success": True, "response": "hello", "provider": "ollama"}

    monkeypatch.setattr(provider_base, "_resolve_ollama_stream", lambda: fake_stream_ollama)

    events = [event async for event in OllamaProvider("http://ollama", "fake").stream(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "echo"}}],
    )]

    assert [type(event).__name__ for event in events] == [
        "LLMToken",
        "LLMThinking",
        "LLMToolCall",
        "LLMToolResult",
        "LLMDone",
    ]
    assert events[0].text == "hello"
    assert events[2].tool == "echo"
    assert events[3].summary == "done"
    assert events[-1].success is True


@pytest.mark.asyncio
async def test_stream_provider_result_forwards_callbacks():
    class FakeProvider:
        async def stream(self, messages, tools, *, cancel_event=None):
            assert messages[-1] == {"role": "user", "content": "hi"}
            assert tools == [{"name": "echo"}]
            yield LLMToken("A")
            yield LLMToolCall("echo", {"value": 3})
            yield LLMToolResult("echo", "ok")
            yield LLMStatus("retry", "again")
            yield LLMDone(response="AB", provider="fake", success=True)

    seen = []
    result = await stream_provider_result(
        FakeProvider(),
        "hi",
        [],
        tools=[{"name": "echo"}],
        on_token=lambda token: seen.append(("token", token)),
        on_tool_call=lambda tool, params: seen.append(("tool", tool, params)),
        on_tool_result=lambda tool, summary: seen.append(("result", tool, summary)),
        on_status=lambda state, message: seen.append(("status", state, message)),
    )

    assert seen == [
        ("token", "A"),
        ("tool", "echo", {"value": 3}),
        ("result", "echo", "ok"),
        ("status", "retry", "again"),
    ]
    assert result["success"] is True
    assert result["response"] == "AB"
    assert result["provider"] == "fake"
    assert result["tool_calls_pending"] == [{"tool": "echo", "params": {"value": 3}}]


@pytest.mark.asyncio
async def test_stream_provider_result_rejects_empty_success_without_tools():
    class EmptyProvider:
        async def stream(self, messages, tools, *, cancel_event=None):
            yield LLMDone(response="", provider="empty", success=True)

    result = await stream_provider_result(EmptyProvider(), "hi", [])

    assert result["success"] is False
    assert result["error"] == "empty_response"


@pytest.mark.asyncio
async def test_stream_provider_result_allows_tool_only_turn():
    class ToolProvider:
        async def stream(self, messages, tools, *, cancel_event=None):
            yield LLMToolCall("read_file", {"path": "README.md"})
            yield LLMDone(response="", provider="tool", success=True)

    result = await stream_provider_result(ToolProvider(), "hi", [])

    assert result["success"] is True
    assert result["tool_calls_pending"] == [
        {"tool": "read_file", "params": {"path": "README.md"}},
    ]


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
            yield LLMToolResult("search", "ok")
            yield LLMStatus("retry", "again")
            yield LLMDone(response="hi", provider="fake_provider", success=True)

    monkeypatch.setattr(
        sdk_client,
        "build_llm_provider",
        lambda options: ProviderSelection("fake_provider", FakeProvider()),
    )

    client = AriaSDKClient(AriaAgentOptions(deterministic=False))
    events = await _collect(client.query("hello"))

    assert [event.kind for event in events] == [
        "system",
        "user",
        "token",
        "tool_result",
        "status",
        "assistant",
        "result",
    ]
    assert events[3].data == {"tool": "search", "summary": "ok"}
    assert events[4].data == {"state": "retry"}
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


def test_tool_capable_model_skips_blocking_deterministic_market_lookup(monkeypatch):
    import apps.cli.deterministic as deterministic

    market_calls = []
    monkeypatch.setattr(deterministic, "handle_strategy_advice", lambda _message: {"success": False})
    monkeypatch.setattr(deterministic, "_handle_realty_query", lambda _message: {"success": False})
    monkeypatch.setattr(deterministic, "_handle_stock_chart_analysis", lambda _message: {"success": False})
    monkeypatch.setattr(
        deterministic,
        "_try_handle_market_overview",
        lambda _message: market_calls.append("overview") or {"success": False},
    )
    monkeypatch.setattr(
        deterministic,
        "_try_handle_market_snapshot_analysis",
        lambda _message, history=None: market_calls.append("snapshot") or {"success": True},
    )

    result = deterministic.run_deterministic_chain(
        "分析苹果股票走势和成交量",
        model_has_tools=True,
    )

    assert result == {"success": False}
    assert market_calls == []
