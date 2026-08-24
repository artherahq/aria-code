from apps.cli.providers.llm.sse_stream import build_chat_payload


def test_react_gateway_payload_uses_shared_conversation_contract():
    endpoint, payload = build_chat_payload(
        "Review this repository",
        [{"role": "assistant", "content": "Earlier context"}],
        model="gemini-2.5-flash",
        thinking_mode="medium",
        user_context={"workspace_mode": "code", "locale": "zh-CN"},
        project_context="Follow ARIA.md",
        use_react_gateway=True,
    )

    assert endpoint == "/api/v2/chat/react"
    assert payload["surface"] == "aria_code"
    assert payload["mode"] == "code"
    assert payload["message"]["content"] == [{"type": "text", "text": "Review this repository"}]
    assert payload["model"] == {"id": "gemini-2.5-flash", "effort": "medium"}
    assert payload["context"]["project_context"] == "Follow ARIA.md"
    assert "workspace_mode" not in payload["context"]


def test_legacy_gateway_payload_remains_compatible():
    endpoint, payload = build_chat_payload(
        "hello", [], model="qwen", thinking_mode="auto", user_context=None,
        project_context="", use_react_gateway=False,
    )

    assert endpoint == "/api/v2/ai/chat/stream"
    assert payload["message"] == "hello"
    assert payload["stream"] is True
