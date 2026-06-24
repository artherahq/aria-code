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


def _render(tool_name, result):
    con = Console(record=True, width=80)
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
