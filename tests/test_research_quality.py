from types import SimpleNamespace

from agents.base import AgentResult
from agents.team import TeamResult
from packages.aria_services.research_quality import (
    ReportQualityDecision,
    assess_team_report,
)


def _bundle(status="complete", stale=False):
    return SimpleNamespace(
        quote={"price": 25.0, "volume": 1_000_000, "market_cap": 5_000_000_000},
        status=status,
        quality={"status": status, "stale": stale},
    )


def _result(agent, *, success=True, degraded=False):
    return AgentResult(
        agent=agent,
        symbol="TEST",
        analysis="usable analysis" if success else "",
        confidence=0.7 if success else 0.0,
        error=None if success else "failed",
        degraded=degraded,
    )


def test_quality_gate_marks_complete_when_core_evidence_is_usable():
    team = TeamResult(
        symbol="TEST",
        agents_run=["technical", "fundamental", "risk"],
        results=[_result("technical"), _result("fundamental"), _result("risk")],
    )

    assessment = assess_team_report(team, _bundle())

    assert assessment.decision is ReportQualityDecision.COMPLETE
    assert assessment.agent_coverage == 1.0
    assert assessment.metadata_status == "complete"
    assert assessment.to_dict()["core_agent_coverage"] == 1.0
    assert assessment.to_dict()["_contract_type"] == "ResearchQualityAssessment"


def test_quality_gate_marks_partial_for_low_coverage_or_partial_data():
    team = TeamResult(
        symbol="TEST",
        agents_run=["macro", "fundamental", "technical", "risk"],
        results=[
            _result("macro"),
            _result("fundamental", success=False),
            _result("technical", degraded=True),
            _result("risk", success=False),
        ],
    )

    assessment = assess_team_report(team, _bundle(status="partial"))

    assert assessment.decision is ReportQualityDecision.PARTIAL
    assert assessment.agent_coverage == 0.5
    assert assessment.degraded_agents == ("technical",)
    assert assessment.metadata_status == "partial"


def test_quality_gate_blocks_report_without_price_or_usable_agents():
    team = TeamResult(
        symbol="TEST",
        agents_run=["technical"],
        results=[_result("technical", success=False)],
    )
    bundle = SimpleNamespace(quote={}, status="data_unavailable", quality={})

    assessment = assess_team_report(team, bundle)

    assert assessment.decision is ReportQualityDecision.BLOCKED
    assert assessment.metadata_status == "data_unavailable"
    assert "no agent produced usable analysis" in assessment.blocking_reasons


def test_quality_gate_flags_implausible_market_cap_units():
    team = TeamResult(
        symbol="TEST",
        agents_run=["technical"],
        results=[_result("technical")],
    )
    bundle = SimpleNamespace(
        quote={"price": 10.0, "volume": 100, "market_cap": 1e16},
        status="complete",
        quality={"stale": False},
    )

    assessment = assess_team_report(team, bundle)

    assert assessment.decision is ReportQualityDecision.PARTIAL
    assert any("market capitalization" in warning for warning in assessment.warnings)
