"""apps/cli/handlers — deterministic pre-LLM response handlers extracted from aria_cli.py."""
from .broker_handlers import handle_broker_query
from .realty_handlers import handle_realty_query
from .chart_handlers import handle_stock_chart_analysis_direct, handle_stock_chart_analysis

__all__ = [
    "handle_broker_query",
    "handle_realty_query",
    "handle_stock_chart_analysis_direct",
    "handle_stock_chart_analysis",
]
