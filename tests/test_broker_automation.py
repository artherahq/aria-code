"""Safety tests for controlled auto-execution (brokers/automation.py).

The contract: an order auto-executes ONLY when every gate passes. These tests
prove it stays blocked by default and under each individual failing gate.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from brokers.automation import (
    AutoExecutePolicy,
    evaluate_auto_execute,
    run_auto_execute,
)
from brokers.paper_broker import PaperBroker
from brokers.trading import OrderIntent, build_order_preview


def _patch_trade_paths(monkeypatch, tmp_path: Path):
    import brokers.paper_broker as paper_mod
    import brokers.trading as trading_mod
    monkeypatch.setattr(paper_mod, "PAPER_LEDGER_PATH", tmp_path / "paper_ledger.json")
    monkeypatch.setattr(trading_mod, "TRADE_PREVIEWS_PATH", tmp_path / "trade_previews.json")
    monkeypatch.setattr(trading_mod, "TRADE_AUDIT_PATH", tmp_path / "trade_audit.jsonl")
    # automation reads the same audit path object — re-bind it too
    import brokers.automation as auto_mod
    monkeypatch.setattr(auto_mod, "TRADE_AUDIT_PATH", tmp_path / "trade_audit.jsonl")


_EXEC_PREVIEW = {"can_execute": True, "symbol": "AAPL",
                 "order_plan": {"estimated_order": {"symbol": "AAPL", "side": "buy",
                                                    "quantity": 10, "price": 100}}}


# ── Pure gate logic ────────────────────────────────────────────────────────────

def test_disabled_by_default_blocks():
    d = evaluate_auto_execute(_EXEC_PREVIEW, AutoExecutePolicy(), dry_run=False)
    assert d.allowed is False
    assert any("enabled=false" in r for r in d.reasons)


def test_dry_run_blocks_even_when_enabled():
    d = evaluate_auto_execute(_EXEC_PREVIEW, AutoExecutePolicy(enabled=True), dry_run=True)
    assert d.allowed is False
    assert any("ARIA_DRY_RUN" in r for r in d.reasons)


def test_risk_blockers_block():
    pv = {"can_execute": False, "execution_blockers": ["违反单票上限"], "symbol": "AAPL",
          "order_plan": {"estimated_order": {"symbol": "AAPL", "side": "buy", "quantity": 1, "price": 1}}}
    d = evaluate_auto_execute(pv, AutoExecutePolicy(enabled=True), dry_run=False)
    assert d.allowed is False
    assert any("风控" in r for r in d.reasons)


def test_value_cap_blocks():
    d = evaluate_auto_execute(_EXEC_PREVIEW, AutoExecutePolicy(enabled=True, max_order_value=500),
                              dry_run=False)
    assert d.allowed is False  # 10 * 100 = 1000 > 500
    assert any("超过上限" in r for r in d.reasons)


def test_side_and_symbol_whitelist():
    d_side = evaluate_auto_execute(_EXEC_PREVIEW,
                                   AutoExecutePolicy(enabled=True, allowed_sides=("sell",)), dry_run=False)
    assert d_side.allowed is False
    d_sym = evaluate_auto_execute(_EXEC_PREVIEW,
                                  AutoExecutePolicy(enabled=True, allowed_symbols=("MSFT",)), dry_run=False)
    assert d_sym.allowed is False


def test_daily_limit_blocks():
    d = evaluate_auto_execute(_EXEC_PREVIEW, AutoExecutePolicy(enabled=True, max_orders_per_day=2),
                              dry_run=False, executed_today=2)
    assert d.allowed is False
    assert any("当日" in r for r in d.reasons)


def test_all_gates_pass_allows():
    d = evaluate_auto_execute(_EXEC_PREVIEW,
                              AutoExecutePolicy(enabled=True, max_order_value=100000),
                              dry_run=False)
    assert d.allowed is True
    assert d.reasons == []


def test_policy_from_config():
    p = AutoExecutePolicy.from_config({"automation": {"enabled": True, "max_order_value": 5000,
                                                      "allowed_symbols": ["aapl"]}})
    assert p.enabled is True and p.max_order_value == 5000
    assert p.allowed_symbols == ("AAPL",)
    assert AutoExecutePolicy.from_config({}).enabled is False


# ── End-to-end with paper broker ───────────────────────────────────────────────

def _paper(automation=None):
    cfg = {"type": "paper", "mode": "paper", "starting_cash": 100000, "currency": "USD"}
    if automation is not None:
        cfg["automation"] = automation
    b = PaperBroker("auto_test", cfg)
    b.connect()
    return b


def test_run_auto_execute_skips_when_disabled(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    b = _paper()  # no automation block → disabled
    pv = build_order_preview(b, OrderIntent(symbol="AAPL", side="buy", quantity=5, price=100))
    r = run_auto_execute(b, pv["preview_id"])
    assert r["success"] is False and r["skipped"] is True
    assert any("enabled=false" in x for x in r["reasons"])


def test_run_auto_execute_executes_when_enabled(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    b = _paper({"enabled": True, "max_order_value": 100000})
    pv = build_order_preview(b, OrderIntent(symbol="AAPL", side="buy", quantity=5, price=100))
    assert pv["can_execute"] is True
    r = run_auto_execute(b, pv["preview_id"])
    assert r.get("auto_executed") is True and r.get("success") is True


def test_run_auto_execute_respects_dry_run(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("ARIA_DRY_RUN", "1")
    b = _paper({"enabled": True, "max_order_value": 100000})
    pv = build_order_preview(b, OrderIntent(symbol="AAPL", side="buy", quantity=5, price=100))
    r = run_auto_execute(b, pv["preview_id"])
    assert r["success"] is False and r["skipped"] is True
