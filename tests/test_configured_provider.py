import pytest

from apps.cli.providers.base import ConfiguredProvider, LLMDone, LLMToken
from providers.llm.openai_compat import chat_completions_url


@pytest.mark.asyncio
async def test_configured_provider_uses_non_ollama_local_runtime(monkeypatch):
    import local_llm_provider

    seen = {}

    class FakeRuntime:
        @classmethod
        def from_config(cls, config):
            seen["config"] = config
            return cls()

        async def stream(self, messages, tools=None, cancel_event=None):
            seen["messages"] = messages
            seen["tools"] = tools
            yield {"type": "token", "text": "ok"}
            yield {"type": "done", "usage": {"completion_tokens": 1}}

    monkeypatch.setattr(local_llm_provider, "LocalLLMProvider", FakeRuntime)
    provider = ConfiguredProvider(
        {"local_provider": "lmstudio", "lmstudio_url": "http://localhost:1234"},
        "loaded-model",
        system_override="ROLE",
    )

    events = [event async for event in provider.stream(
        [{"role": "user", "content": "hello"}],
        [{"type": "function", "function": {"name": "echo"}}],
    )]

    assert seen["config"]["model"] == "loaded-model"
    assert seen["messages"][0] == {"role": "system", "content": "ROLE"}
    assert isinstance(events[0], LLMToken)
    assert isinstance(events[-1], LLMDone)
    assert events[-1].provider == "lmstudio"


@pytest.mark.asyncio
async def test_configured_cloud_provider_normalizes_tool_schema(monkeypatch):
    import providers.llm.registry as registry

    seen = {}

    class FakeCloud:
        async def stream(self, messages, tools=None, cancel_event=None):
            seen["tools"] = tools
            yield {"type": "token", "text": "cloud"}
            yield {"type": "done"}

    monkeypatch.setattr(registry, "get_provider", lambda spec: FakeCloud())
    provider = ConfiguredProvider(
        {"local_provider": "deepseek"}, "deepseek-chat"
    )
    events = [event async for event in provider.stream(
        [{"role": "user", "content": "hello"}],
        [{
            "type": "function",
            "function": {"name": "echo", "parameters": {"type": "object"}},
        }],
    )]

    assert seen["tools"] == [
        {"name": "echo", "parameters": {"type": "object"}}
    ]
    assert events[-1].provider == "deepseek"


def test_local_provider_uses_backend_default_not_application_local_url():
    from local_llm_provider import LocalLLMProvider

    provider = LocalLLMProvider.from_config({
        "local_provider": "lmstudio",
        "local_url": "http://localhost:8000",
        "model": "loaded-model",
    })
    assert provider.base_url == "http://localhost:1234"


def test_openai_compatible_url_joining():
    from local_llm_provider import openai_models_url

    assert chat_completions_url("https://api.openai.com") == (
        "https://api.openai.com/v1/chat/completions"
    )
    assert chat_completions_url("https://api.moonshot.cn/v1") == (
        "https://api.moonshot.cn/v1/chat/completions"
    )
    assert chat_completions_url("https://example.test/v4/") == (
        "https://example.test/v4/chat/completions"
    )
    assert openai_models_url("http://localhost:1234") == (
        "http://localhost:1234/v1/models"
    )
    assert openai_models_url("http://localhost:1234/v1/") == (
        "http://localhost:1234/v1/models"
    )
