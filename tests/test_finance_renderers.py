"""Safety net for the finance renderer dispatcher.

`render_finance_result` is a long if/elif over tool_name. It's easy to add a
finance tool to FINANCE_TOOL_NAMES (so it's routed here) but forget a render
branch — the result then dumps as a raw dict. These tests make sure every
registered finance tool is handled without crashing on representative,
minimal, and empty payloads.
"""
import pathlib
import sys

import pytest

_CLI_DIR = str(pathlib.Path(__file__).parents[1])
if _CLI_DIR not in sys.path:
    sys.path.insert(0, _CLI_DIR)

from rich.console import Console  # noqa: E402

from ui.render.finance import render_finance_result  # noqa: E402
from ui.render.output import FINANCE_TOOL_NAMES  # noqa: E402


def _render(tool_name, result, *, width=80):
    con = Console(record=True, width=width)
    render_finance_result(tool_name, result, console=con, has_rich=True)
    return con.export_text()


@pytest.mark.parametrize("tool_name", sorted(FINANCE_TOOL_NAMES))
def test_renderer_handles_minimal_success_without_crash(tool_name):
    # A minimal success payload must never raise, regardless of tool.
    _render(tool_name, {"success": True, "symbol": "TEST", "provider": "unit"})


@pytest.mark.parametrize("tool_name", sorted(FINANCE_TOOL_NAMES))
def test_renderer_handles_failure_without_crash(tool_name):
    out = _render(tool_name, {"success": False, "error": "unit-failure",
                              "provider_chain": ["a", "b"]})
    # Every finance tool shares the failure preamble → must surface the error.
    assert "unit-failure" in out


@pytest.mark.parametrize("tool_name", sorted(FINANCE_TOOL_NAMES))
def test_renderer_handles_empty_without_crash(tool_name):
    # None / empty dict must be tolerated (no output, no exception).
    _render(tool_name, None)
    _render(tool_name, {})


def test_market_data_and_history_emit_output():
    # The two most-used tools must produce visible output (not a silent
    # fall-through), guarding against an accidental routing/branch regression.
    md = _render("get_market_data", {
        "success": True, "symbol": "AAPL", "price": 200.0, "change_pct": 1.5,
        "currency": "USD", "provider": "unit",
    })
    assert "AAPL" in md

    mh = _render("get_market_history", {
        "success": True, "symbol": "AAPL", "provider": "unit",
        "summary": {"start_date": "2026-01-01", "end_date": "2026-02-01",
                    "start_close": 100, "end_close": 110, "change_pct": 10.0},
        "recent_candles": [{"close": c} for c in [100, 105, 110]],
    })
    assert "AAPL" in mh


def test_broker_tool_views_are_stacked_at_80_and_reduced_at_100():
    positions = {
        "success": True,
        "query": "positions",
        "broker": "模拟账户",
        "positions": [{
            "symbol": "AAPL", "name": "Apple Inc.", "quantity": 10,
            "cost": 190.0, "price": 200.0, "market_value": 2000.0,
            "pnl": 100.0, "pnl_pct": 5.26,
        }],
    }

    narrow = _render("broker_query", positions, width=80)
    compact = _render("broker_query", positions, width=100)
    full = _render("broker_query", positions, width=120)

    assert "#1  AAPL  Apple Inc." in narrow
    assert "成本 190.000" in narrow
    assert "#1" not in compact
    assert "成本" not in compact
    assert "成本" in full


def test_screen_and_limit_up_results_stack_at_80_columns():
    screen = _render("screen_ashare", {
        "success": True,
        "count": 1,
        "stocks": [{
            "code": "000518", "name": "四环生物", "price": 3.99,
            "change_pct": 4.45, "pe_dynamic": 18.2, "market_cap_yi": 41,
        }],
    }, width=80)
    limit_up = _render("get_limit_up_pool", {
        "success": True,
        "date": "2026-07-10",
        "count": 1,
        "stocks": [{
            "code": "000518", "name": "四环生物", "consecutive": 2,
            "first_lock_time": "09:35:10", "limit_type": "换手板",
        }],
    }, width=80)

    assert "#1  000518  四环生物" in screen
    assert "价格 3.99" in screen
    assert "PE 18.2" in screen
    assert "#1  000518  四环生物" in limit_up
    assert "连板 2" in limit_up
    assert "首封时间 09:35:10" in limit_up
