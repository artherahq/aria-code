"""Provider error classification and lightweight health state."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class ProviderIssue:
    provider: str
    category: str
    message: str
    retryable: bool = True
    cooldown_seconds: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderState:
    provider: str
    status: str = "ok"
    last_error_category: str = ""
    last_error: str = ""
    failures: int = 0
    cooldown_until: float = 0.0
    last_seen_at: float = 0.0
    last_success_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["cooldown_active"] = self.cooldown_until > time.time()
        data["cooldown_remaining_seconds"] = max(0, int(self.cooldown_until - time.time()))
        return data


@dataclass(frozen=True)
class ProviderHealthSummary:
    schema: str
    total: int
    ok: int
    warn: int
    err: int
    cooldown: int
    auth_errors: int
    providers: List[str]
    status: str
    detail: str
    suggestion: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def classify_provider_error(provider: str, error: Any) -> ProviderIssue:
    text = str(error or "").strip()
    low = text.lower()
    if not text:
        return ProviderIssue(provider, "unavailable", "provider returned no usable data", True, 30)
    if any(token in low for token in ("429", "rate", "too many", "limit")):
        return ProviderIssue(provider, "rate_limited", "provider rate limited the request", True, 60)
    if any(token in low for token in ("timeout", "timed out", "curl: (28)", "read timed out")):
        return ProviderIssue(provider, "timeout", "provider request timed out", True, 30)
    if any(token in low for token in ("connection", "network", "refused", "remote", "dns", "name resolution")):
        return ProviderIssue(provider, "network", "provider network connection failed", True, 30)
    if any(token in low for token in ("empty", "no data", "not found", "none", "null")):
        return ProviderIssue(provider, "no_data", "provider returned no market data", True, 15)
    if any(token in low for token in ("unauthorized", "forbidden", "api key", "401", "403")):
        return ProviderIssue(provider, "auth", "provider authentication failed", False, 0)
    return ProviderIssue(provider, "error", text[:240], True, 30)


class ProviderHealthRegistry:
    """In-process health state for data providers."""

    def __init__(self) -> None:
        self._states: Dict[str, ProviderState] = {}

    def mark_success(self, provider: str) -> None:
        if not provider:
            return
        state = self._states.setdefault(provider, ProviderState(provider=provider))
        state.status = "ok"
        state.last_error_category = ""
        state.last_error = ""
        state.failures = 0
        state.cooldown_until = 0.0
        now = time.time()
        state.last_seen_at = now
        state.last_success_at = now

    def mark_issue(self, issue: ProviderIssue) -> None:
        if not issue.provider:
            return
        state = self._states.setdefault(issue.provider, ProviderState(provider=issue.provider))
        now = time.time()
        state.status = issue.category
        state.last_error_category = issue.category
        state.last_error = issue.message
        state.failures += 1
        state.last_seen_at = now
        if issue.cooldown_seconds:
            state.cooldown_until = max(state.cooldown_until, now + issue.cooldown_seconds)

    def provider_in_cooldown(self, provider: str) -> bool:
        state = self._states.get(provider)
        return bool(state and state.cooldown_until > time.time())

    def snapshot(self) -> List[Dict[str, Any]]:
        return [self._states[name].to_dict() for name in sorted(self._states)]

    def summary(self) -> ProviderHealthSummary:
        return summarize_provider_health(self.snapshot())


GLOBAL_PROVIDER_HEALTH = ProviderHealthRegistry()


def summarize_provider_health(snapshot: List[Dict[str, Any]] | None = None) -> ProviderHealthSummary:
    rows = list(snapshot or [])
    if not rows:
        return ProviderHealthSummary(
            schema="aria.provider_health_summary.v1",
            total=0,
            ok=0,
            warn=0,
            err=0,
            cooldown=0,
            auth_errors=0,
            providers=[],
            status="warn",
            detail="no provider calls recorded in this session",
            suggestion="Run /quote, /ta, /analyze, or /report to populate provider health.",
        )

    ok = warn = err = cooldown = auth_errors = 0
    providers: list[str] = []
    for row in rows:
        provider = str(row.get("provider") or "provider")
        providers.append(provider)
        status = str(row.get("status") or "unknown")
        error_category = str(row.get("last_error_category") or "")
        if status == "ok":
            ok += 1
        elif error_category == "auth":
            err += 1
            auth_errors += 1
        else:
            warn += 1
        if row.get("cooldown_active"):
            cooldown += 1

    if err:
        status = "err"
    elif warn or cooldown:
        status = "warn"
    else:
        status = "ok"

    parts = [f"{len(rows)} providers"]
    if ok:
        parts.append(f"{ok} ok")
    if warn:
        parts.append(f"{warn} warn")
    if err:
        parts.append(f"{err} err")
    if cooldown:
        parts.append(f"{cooldown} cooldown")

    suggestion = "Run /doctor --network or inspect /apikey." if status != "ok" else "All providers healthy."
    if auth_errors:
        suggestion = "Fix API keys first, then retry /doctor or /cloud health."

    return ProviderHealthSummary(
        schema="aria.provider_health_summary.v1",
        total=len(rows),
        ok=ok,
        warn=warn,
        err=err,
        cooldown=cooldown,
        auth_errors=auth_errors,
        providers=providers,
        status=status,
        detail=", ".join(parts),
        suggestion=suggestion,
    )
