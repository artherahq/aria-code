"""Backwards-compatibility shim — canonical code lives in ui/render/finance.py."""
from ui.render.finance import *  # noqa: F401, F403
from ui.render.finance import (
    render_finance_result,
    render_macro_result, render_cb_rates, render_econ_calendar,
    render_options_chain, render_quality_scores, render_ichimoku,
    render_fear_greed, render_funding_rates, render_peer_comparison,
    render_house_price, render_reits_list, render_rental_yield,
    render_property_val, render_multi_city, render_asset_score,
    render_corr_matrix, render_portfolio_bt, render_sql_result,
    render_alerts, format_backtest_output,
)
