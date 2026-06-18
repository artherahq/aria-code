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


GLOBAL_PROVIDER_HEALTH = ProviderHealthRegistry()
