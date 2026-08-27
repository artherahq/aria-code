"""Read-only, token-bounded tools for the Aria research ADK agent."""

from __future__ import annotations

import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Callable, Dict


logger = logging.getLogger(__name__)


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _compact(value: Any, *, depth: int = 0) -> Any:
    """Make a small JSON-safe representation suitable for an LLM tool result."""
    if depth > 3:
        return str(value)[:240]
    if isinstance(value, dict):
        return {str(k): _compact(v, depth=depth + 1) for k, v in list(value.items())[:32]}
    if isinstance(value, (list, tuple)):
        return [_compact(v, depth=depth + 1) for v in list(value)[:12]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:240]


class MarketResearchTools:
    """Expose data-service capabilities without exposing its mutable internals.

    ``service_factory`` is injectable so tests and future deployments can use a
    remote Arthera service instead of importing the local market client.
    """

    def __init__(self, service_factory: Callable[[], Any] | None = None) -> None:
        self._service_factory = service_factory or self._default_service

    @staticmethod
    def _default_service() -> Any:
        from aria_code.data_service import DataService

        return DataService()

    def get_market_snapshot(self, symbol: str) -> Dict[str, Any]:
        """Return a current, read-only market snapshot for one symbol.

        Args:
            symbol: Ticker, A-share code, or crypto pair, e.g. ``AAPL``,
                ``000300.SS``, or ``BTC/USDT``.
        """
        symbol = str(symbol or "").strip().upper()
        if not symbol or len(symbol) > 32:
            return {"success": False, "error": "A valid symbol of up to 32 characters is required."}

        try:
            # ``bundle`` is Aria's public normalized aggregate.  Keep the ADK
            # contract independent of the underlying service method name.
            result = self._service_factory().bundle(symbol)
            payload = _as_dict(result)
            status = str(payload.get("status") or payload.get("quality", {}).get("status") or "")
            return {
                "success": status in {"complete", "partial", "stale"},
                "symbol": symbol,
                "as_of": payload.get("as_of") or payload.get("timestamp"),
                "quote": _compact(payload.get("quote", {})),
                "fundamentals": _compact(payload.get("fundamentals", {})),
                "technical": _compact(payload.get("technical", {})),
                "quality": _compact(payload.get("quality", {})),
                "status": status or "data_unavailable",
                "warnings": _compact(payload.get("warnings", [])),
                "errors": _compact(payload.get("errors", [])),
                "disclaimer": "Market data may be delayed or unavailable; verify before acting.",
            }
        except Exception as exc:  # Tool errors must be factual and non-fatal to the agent turn.
            # Provider exceptions can include URLs, headers or other diagnostics.
            # Keep those in the local log; the agent gets a stable public error.
            logger.warning("Market snapshot unavailable for %s: %s", symbol, exc)
            return {
                "success": False,
                "symbol": symbol,
                "error": "Market snapshot is temporarily unavailable.",
                "retryable": True,
            }

    def get_market_data_health(self) -> Dict[str, Any]:
        """Return product-safe current data-provider health.

        This endpoint intentionally exposes only an aggregate health summary.
        It can be rendered directly in the UI or used by an agent to decide
        whether to retry, without leaking credentials, endpoints, provider
        names, or raw provider exceptions.
        """
        try:
            from aria_code.packages.aria_services.provider_health import GLOBAL_PROVIDER_HEALTH

            public_status = GLOBAL_PROVIDER_HEALTH.public_status("market_data")
            summary = GLOBAL_PROVIDER_HEALTH.summary()

            return {
                "success": True,
                "status": _compact(public_status.to_dict()),
                "health": {
                    "total": summary.total,
                    "available": summary.ok,
                    "degraded": summary.warn,
                    "unavailable": summary.err,
                    "cooldown": summary.cooldown,
                },
                "note": "Status updates after the first market-data request in this process.",
            }
        except Exception as exc:
            logger.warning("Provider health unavailable: %s", exc)
            return {
                "success": False,
                "error": "Provider health is temporarily unavailable.",
                "retryable": True,
            }
