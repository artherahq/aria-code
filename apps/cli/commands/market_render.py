"""Backwards-compatibility shim — canonical code lives in ui/render/market.py."""
from ui.render.market import *  # noqa: F401, F403
from ui.render.market import (
    print_quote_result, print_ta_result,
    render_quote_plain, render_ta_plain,
    compact_quote_market_cap,
)
