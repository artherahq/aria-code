"""
tests/test_portfolio_ledger.py — PortfolioLedger.positions_by_strategy

Covers the strategy-attribution primitive shared by /portfolio holdings and
/deploy: trades are tied to a strategy via the free-text `reason`, longest name
wins, fully-closed positions drop out, and unattributed trades bucket last.
Uses an isolated temp db_path (no HOME monkeypatching needed).
"""
from __future__ import annotations

import pytest

from portfolio_ledger import PortfolioLedger


@pytest.fixture
def ledger(tmp_path):
    return PortfolioLedger(db_path=tmp_path / "ledger.db")


def test_attributes_trades_to_strategy_by_reason(ledger):
    ledger.add_trade("AAPL", "BUY", 10, 150, reason="deploy value @v2")
    ledger.add_trade("MSFT", "BUY", 5, 300, reason="value entry")
    groups = ledger.positions_by_strategy(["value"])
    assert "value" in groups
    syms = {p["symbol"] for p in groups["value"]}
    assert syms == {"AAPL", "MSFT"}


def test_average_cost_is_buy_weighted(ledger):
    ledger.add_trade("AAPL", "BUY", 10, 150, reason="value")
    ledger.add_trade("AAPL", "BUY", 10, 170, reason="value")  # avg 160, net 20
    pos = ledger.positions_by_strategy(["value"])["value"][0]
    assert pos["symbol"] == "AAPL"
    assert pos["net_qty"] == 20
    assert pos["avg_cost"] == 160.0
    assert pos["cost_basis"] == 3200.0


def test_longest_strategy_name_wins(ledger):
    # reason contains both 'value' and the longer 'value_v2'; longer should win
    ledger.add_trade("MSFT", "BUY", 5, 300, reason="deploy value_v2 entry")
    groups = ledger.positions_by_strategy(["value", "value_v2"])
    assert "value_v2" in groups
    assert "value" not in groups  # the trade was claimed by the longer name


def test_fully_closed_position_drops_out(ledger):
    ledger.add_trade("NVDA", "BUY", 4, 100, reason="momo signal")
    ledger.add_trade("NVDA", "SELL", 4, 120, reason="momo exit")
    groups = ledger.positions_by_strategy(["momo"])
    assert "momo" not in groups  # net zero -> no group created


def test_partial_close_keeps_buy_weighted_avg(ledger):
    ledger.add_trade("X", "BUY", 10, 100, reason="s")
    ledger.add_trade("X", "BUY", 10, 120, reason="s")  # avg 110
    ledger.add_trade("X", "SELL", 8, 140, reason="s")  # net 12, avg stays 110
    pos = ledger.positions_by_strategy(["s"])["s"][0]
    assert pos["net_qty"] == 12
    assert pos["avg_cost"] == 110.0


def test_unattributed_bucket_is_last(ledger):
    ledger.add_trade("AAPL", "BUY", 10, 150, reason="value")
    ledger.add_trade("TSLA", "BUY", 2, 200, reason="gut feel")
    groups = ledger.positions_by_strategy(["value"])
    assert PortfolioLedger.UNATTRIBUTED in groups
    assert groups[PortfolioLedger.UNATTRIBUTED][0]["symbol"] == "TSLA"
    assert list(groups.keys())[-1] == PortfolioLedger.UNATTRIBUTED


def test_named_groups_sorted_before_unattributed(ledger):
    ledger.add_trade("AAPL", "BUY", 1, 100, reason="beta strat")
    ledger.add_trade("MSFT", "BUY", 1, 100, reason="alpha strat")
    ledger.add_trade("TSLA", "BUY", 1, 100, reason="none")
    keys = list(ledger.positions_by_strategy(["alpha strat", "beta strat"]).keys())
    assert keys == ["alpha strat", "beta strat", PortfolioLedger.UNATTRIBUTED]


def test_empty_ledger_returns_empty(ledger):
    assert ledger.positions_by_strategy(["value"]) == {}


def test_no_strategy_names_all_unattributed(ledger):
    ledger.add_trade("AAPL", "BUY", 10, 150, reason="deploy value")
    groups = ledger.positions_by_strategy([])
    assert list(groups.keys()) == [PortfolioLedger.UNATTRIBUTED]
    assert groups[PortfolioLedger.UNATTRIBUTED][0]["symbol"] == "AAPL"
