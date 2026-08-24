import asyncio

from aria_code.apps.cli.providers.collaboration import (
    collaboration_readiness,
    consult,
    resolve_collaborator,
)


def test_collaboration_aliases_and_configured_model():
    assert resolve_collaborator("chatgpt").provider == "openai"
    assert resolve_collaborator("claude").provider == "anthropic"
    assert resolve_collaborator("unknown") is None
    assert resolve_collaborator("chatgpt", {"collab_chatgpt_model": "gpt-test"}).default_model == "gpt-test"


def test_collaboration_readiness_does_not_expose_keys():
    rows = collaboration_readiness({}, lambda provider: provider == "openai")
    assert [(row["provider"], row["configured"]) for row in rows] == [
        ("openai", True), ("anthropic", False),
    ]
    assert all("key" not in row for row in rows)


def test_consult_runs_targets_concurrently_and_keeps_answers_attributed():
    class FakeProvider:
        async def complete(self, messages):
            await asyncio.sleep(0)
            return {"success": True, "response": f"answer:{messages[0].content}"}

    class Message:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    targets = [resolve_collaborator("chatgpt"), resolve_collaborator("claude")]
    results = asyncio.run(consult(
        "review this", [target for target in targets if target],
        get_provider=lambda _spec: FakeProvider(), message_factory=Message,
    ))

    assert [result["alias"] for result in results] == ["chatgpt", "claude"]
    assert all(result["success"] for result in results)
    assert all(result["response"] == "answer:review this" for result in results)


def test_consult_turns_provider_errors_into_attributed_results():
    class FakeProvider:
        async def complete(self, _messages):
            return {"success": False, "error": "quota reached"}

    class Message:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    target = resolve_collaborator("claude")
    results = asyncio.run(consult(
        "review", [target], get_provider=lambda _spec: FakeProvider(), message_factory=Message,
    ))
    assert results[0]["alias"] == "claude"
    assert results[0]["success"] is False
    assert results[0]["error"] == "quota reached"
