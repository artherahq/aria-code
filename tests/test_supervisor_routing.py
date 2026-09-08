import asyncio
import json

from aria_code.agents.supervisor import SupervisorAgent


def test_supervisor_uses_only_registered_agents_when_no_llm_response(monkeypatch):
    class Registry:
        def list(self):
            return [
                {"name": "technical", "description": "technical", "builtin": True},
                {"name": "news", "description": "news", "builtin": True},
                {"name": "supervisor", "description": "router", "builtin": True},
            ]

    monkeypatch.setattr("aria_code.agents.registry.get_registry", lambda: Registry())

    agent = SupervisorAgent()
    result = asyncio.run(agent.analyze("AAPL", {}))

    assert json.loads(result.analysis) == ["technical", "news"]


def test_supervisor_discards_unknown_and_duplicate_model_choices():
    chosen = SupervisorAgent._normalize_selection(
        ["technical", "unknown", "technical", "news"],
        ["technical", "fundamental", "news"],
    )

    assert chosen == ["technical", "news"]
