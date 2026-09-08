import asyncio

import pytest

from aria_code.agents.financial.technical import TechnicalAgent
from aria_code.agents.team import AgentTeam
from aria_code.agents.base import AgentResult


class _SlowTechnicalAgent(TechnicalAgent):
    async def analyze(self, symbol, data):
        await asyncio.sleep(0.05)
        return await super().analyze(symbol, data)


@pytest.mark.asyncio
async def test_timeout_uses_prefetched_deterministic_fallback():
    team = AgentTeam(timeout_per_agent=0.001)
    data = {
        "quote": {"price": 100.0},
        "history": {
            "ma5": 99.0,
            "ma20": 95.0,
            "ma60": 90.0,
            "rsi": 58.0,
            "macd": 1.0,
            "macd_signal": 0.5,
            "signal_strength": 0.75,
        },
    }

    result = await team._run_one(_SlowTechnicalAgent(), "TEST", data)

    assert result.success is True
    assert result.degraded is True
    assert result.confidence == 0.45
    assert "deterministic_template" in result.provenance
    assert result.data_used["fallback_reason"] == "timeout"


@pytest.mark.asyncio
async def test_timeout_without_prefetched_evidence_remains_failed():
    team = AgentTeam(timeout_per_agent=0.001)

    result = await team._run_one(_SlowTechnicalAgent(), "TEST", None)

    assert result.success is False
    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_all_failed_agents_skip_slow_llm_synthesis(monkeypatch):
    import aria_code.agents.team as team_module

    synthesis_calls = []

    class _FailedA:
        name = "failed_a"

        def __init__(self, **_kwargs):
            pass

        async def run(self, symbol):
            return AgentResult(self.name, symbol, "", 0.0, error="unavailable")

    class _FailedB(_FailedA):
        name = "failed_b"

    class _Synthesis:
        def __init__(self, **_kwargs):
            pass

        async def analyze(self, _symbol, _data):
            synthesis_calls.append(True)
            await asyncio.sleep(1)

    monkeypatch.setattr(
        team_module,
        "get_registry",
        lambda: {"failed_a": _FailedA, "failed_b": _FailedB, "synthesis": _Synthesis},
    )

    result = await AgentTeam(timeout_per_agent=0.1, synthesis_timeout=0.1).run(
        "TEST",
        agents=["failed_a", "failed_b"],
    )

    assert synthesis_calls == []
    assert result.final_signal == "HOLD"
    assert "所有 agent 均未成功" in result.synthesis


@pytest.mark.asyncio
async def test_completion_callback_failure_does_not_fail_agent():
    team = AgentTeam(
        on_agent_done=lambda *_args: (_ for _ in ()).throw(RuntimeError("render failed")),
    )
    data = {
        "quote": {"price": 100.0},
        "history": {"ma5": 99.0, "ma20": 95.0, "rsi": 55.0},
    }

    result = await team._run_one(TechnicalAgent(), "TEST", data)

    assert result.success is True
