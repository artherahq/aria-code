"""Deterministic completion gates for multi-agent research reports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable


class ReportQualityDecision(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ReportQualityPolicy:
    min_agent_coverage: float = 0.60
    min_core_coverage: float = 2 / 3
    core_agents: tuple[str, ...] = ("technical", "fundamental", "risk")
    max_degraded_ratio: float = 0.50
    require_quote: bool = True
    allow_stale: bool = False
    max_market_cap: float = 100_000_000_000_000.0
    max_implied_shares: float = 100_000_000_000.0


@dataclass(frozen=True)
class ReportQualityAssessment:
    decision: ReportQualityDecision
    requested_agents: tuple[str, ...]
    usable_agents: tuple[str, ...]
    degraded_agents: tuple[str, ...]
    failed_agents: tuple[str, ...]
    agent_coverage: float
    core_coverage: float
    reasons: tuple[str, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    blocking_reasons: tuple[str, ...] = field(default_factory=tuple)
    assessed_at: str = field(default_factory=_utc_timestamp)

    @property
    def metadata_status(self) -> str:
        if self.decision is ReportQualityDecision.BLOCKED:
            return "data_unavailable"
        return self.decision.value

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        data["metadata_status"] = self.metadata_status
        data["core_agent_coverage"] = data["core_coverage"]
        data["_contract_type"] = "ResearchQualityAssessment"
        data["_contract_version"] = "1.0.0"
        return data


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


def assess_team_report(
    team_result: Any,
    data_bundle: Any = None,
    policy: ReportQualityPolicy | None = None,
) -> ReportQualityAssessment:
    """Assess whether a report is complete, usable with caveats, or blocked."""
    policy = policy or ReportQualityPolicy()
    requested = _unique(
        str(name)
        for name in (getattr(team_result, "agents_run", None) or [])
        if name not in {"synthesis", "debate"}
    )
    results = list(getattr(team_result, "results", None) or [])
    if not requested:
        requested = _unique(
            str(getattr(result, "agent", ""))
            for result in results
            if getattr(result, "agent", "") not in {"synthesis", "debate"}
        )

    result_by_agent = {
        str(getattr(result, "agent", "")): result
        for result in results
        if getattr(result, "agent", "")
    }
    usable = _unique(
        name
        for name in requested
        if name in result_by_agent
        and bool(getattr(result_by_agent[name], "success", False))
        and bool(str(getattr(result_by_agent[name], "analysis", "") or "").strip())
    )
    degraded = _unique(
        name for name in usable if bool(getattr(result_by_agent[name], "degraded", False))
    )
    failed = _unique(name for name in requested if name not in usable)
    coverage = len(usable) / len(requested) if requested else 0.0
    expected_core = tuple(name for name in policy.core_agents if name in requested)
    core_coverage = (
        sum(1 for name in expected_core if name in usable) / len(expected_core)
        if expected_core else 1.0
    )

    warnings: list[str] = []
    blocking: list[str] = []
    reasons: list[str] = []

    quote = dict(getattr(data_bundle, "quote", {}) or {}) if data_bundle else {}
    price = _as_float(quote.get("price") or quote.get("current_price"))
    volume = _as_float(quote.get("volume"))
    market_cap = _as_float(quote.get("market_cap") or quote.get("marketCap"))
    data_status = str(getattr(data_bundle, "status", "") or "").lower() if data_bundle else ""
    stale = bool((getattr(data_bundle, "quality", {}) or {}).get("stale", False)) if data_bundle else False

    if policy.require_quote and (price is None or price <= 0):
        blocking.append("missing or invalid reference price")
    if volume is not None and volume < 0:
        blocking.append("negative volume")
    if market_cap is not None:
        if market_cap <= 0:
            warnings.append("invalid market capitalization")
        elif market_cap > policy.max_market_cap:
            warnings.append("market capitalization exceeds plausibility limit")
        if price and price > 0 and market_cap / price > policy.max_implied_shares:
            warnings.append("implied share count exceeds plausibility limit")

    if data_status in {"data_unavailable", "unavailable", "failed"}:
        blocking.append("market data bundle unavailable")
    elif data_status in {"partial", "stale"}:
        warnings.append(f"market data status is {data_status}")
    if stale and not policy.allow_stale:
        warnings.append("market data is stale")
    if not usable:
        blocking.append("no agent produced usable analysis")
    if coverage < policy.min_agent_coverage:
        warnings.append(
            f"agent coverage {coverage:.0%} is below {policy.min_agent_coverage:.0%}"
        )
    if core_coverage < policy.min_core_coverage:
        warnings.append(
            f"core-agent coverage {core_coverage:.0%} is below {policy.min_core_coverage:.0%}"
        )
    degraded_ratio = len(degraded) / len(usable) if usable else 0.0
    if degraded_ratio > policy.max_degraded_ratio:
        warnings.append(f"degraded-agent ratio {degraded_ratio:.0%} is too high")

    if blocking:
        decision = ReportQualityDecision.BLOCKED
    elif warnings:
        decision = ReportQualityDecision.PARTIAL
    else:
        decision = ReportQualityDecision.COMPLETE

    reasons.append(f"usable agents: {len(usable)}/{len(requested)}")
    if degraded:
        reasons.append(f"degraded agents: {', '.join(degraded)}")
    if failed:
        reasons.append(f"failed agents: {', '.join(failed)}")

    return ReportQualityAssessment(
        decision=decision,
        requested_agents=requested,
        usable_agents=usable,
        degraded_agents=degraded,
        failed_agents=failed,
        agent_coverage=round(coverage, 4),
        core_coverage=round(core_coverage, 4),
        reasons=_unique(reasons),
        warnings=_unique(warnings),
        blocking_reasons=_unique(blocking),
    )
