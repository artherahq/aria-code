import asyncio

import pytest

from agents.financial.technical import TechnicalAgent
from agents.team import AgentTeam


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
