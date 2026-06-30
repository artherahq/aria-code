"""Shared deterministic routing for Aria agent entrypoints.

This module is intentionally UI-free.  The legacy CLI, future daemon/webhook
entrypoints, and the public SDK can all use the same routing order without
importing the terminal implementation from ``aria_cli.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from apps.cli.handlers.broker_handlers import handle_broker_query
from apps.cli.handlers.chart_handlers import handle_stock_chart_analysis
from apps.cli.handlers.market_handlers import (
    _try_handle_market_overview,
    _try_handle_market_snapshot_analysis,
)
from apps.cli.handlers.realty_handlers import handle_realty_query
from apps.cli.handlers.strategy_advice import handle_strategy_advice
from apps.cli.utils.market_detect import (
    _CN_CITIES,
    _INTL_CITIES,
    _extract_market_symbol,
    _is_broker_intent,
    _is_realty_query,
    _is_stock_chart_analysis_request,
)


BrokerRegistryFactory = Callable[[], Any]


def _missing_broker_registry() -> None:
    return None


@dataclass(frozen=True)
class DeterministicRouterConfig:
    """Configuration for deterministic routing outside the model loop."""

    model_has_tools: bool = True
    has_brokers: bool = False
    get_broker_registry: BrokerRegistryFactory | None = None


def _handle_broker_query(message: str, config: DeterministicRouterConfig) -> dict:
    return handle_broker_query(
        message,
        has_brokers=config.has_brokers,
        is_broker_intent=_is_broker_intent,
        get_broker_registry=config.get_broker_registry or _missing_broker_registry,
    )


def _handle_realty_query(message: str) -> dict:
    return handle_realty_query(
        message,
        is_realty_query=_is_realty_query,
        cn_cities=_CN_CITIES,
        intl_cities=_INTL_CITIES,
    )


def _handle_stock_chart_analysis(message: str) -> dict:
    return handle_stock_chart_analysis(
        message,
        is_chart_request=_is_stock_chart_analysis_request,
        extract_symbol=_extract_market_symbol,
    )


def run_deterministic_chain(
    message: str,
    *,
    model_has_tools: bool,
    history: list | None = None,
    has_brokers: bool = False,
    get_broker_registry: BrokerRegistryFactory | None = None,
) -> dict:
    """Run the deterministic routing chain used before LLM fallback.

    Order matters:
    - broker account reads are only used when the model cannot call tools;
    - realty must run before market parsing, so housing questions do not inherit
      a ticker;
    - chart requests run before snapshots;
    - whole-market overview runs before single-symbol snapshot parsing, so
      "分析A股" is treated as the A-share market instead of ticker ``A``.
    """

    config = DeterministicRouterConfig(
        model_has_tools=model_has_tools,
        has_brokers=has_brokers,
        get_broker_registry=get_broker_registry,
    )

    deterministic: dict = {"success": False}
    if not config.model_has_tools:
        deterministic = _handle_broker_query(message, config)

    for handler in (
        handle_strategy_advice,
        _handle_realty_query,
        _handle_stock_chart_analysis,
    ):
        if deterministic.get("success"):
            break
        deterministic = handler(message)

    # Tool-capable models should resolve symbols and fetch market data through
    # the audited tool loop. The deterministic market handlers can refresh a
    # large remote symbol universe, which blocks the REPL before the model ever
    # receives the request. Keep them as the fallback for text-only models.
    if not deterministic.get("success") and not config.model_has_tools:
        deterministic = _try_handle_market_overview(message)

    if not deterministic.get("success") and not config.model_has_tools:
        deterministic = _try_handle_market_snapshot_analysis(message, history=history)

    return deterministic


__all__ = [
    "BrokerRegistryFactory",
    "DeterministicRouterConfig",
    "run_deterministic_chain",
]
