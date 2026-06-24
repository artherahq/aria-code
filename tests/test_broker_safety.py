"""Tests for broker safety/reliability additions:
- global ARIA_DRY_RUN kill-switch (forces all brokers read-only)
- broker heartbeat / auto-reconnect (ensure_connected, registry.ensure/health)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from brokers.paper_broker import PaperBroker
from brokers.registry import BrokerRegistry
from brokers.trading import (
    OrderIntent,
    build_order_preview,
    global_dry_run,
    resolve_trading_mode,
)


def _patch_trade_paths(monkeypatch, tmp_path: Path):
    import brokers.paper_broker as paper_mod
    import brokers.trading as trading_mod
    monkeypatch.setattr(paper_mod, "PAPER_LEDGER_PATH", tmp_path / "paper_ledger.json")
    monkeypatch.setattr(trading_mod, "TRADE_PREVIEWS_PATH", tmp_path / "trade_previews.json")
    monkeypatch.setattr(trading_mod, "TRADE_AUDIT_PATH", tmp_path / "trade_audit.jsonl")


# ── Global dry-run kill-switch ─────────────────────────────────────────────────

def test_dry_run_off_by_default(monkeypatch):
    monkeypatch.delenv("ARIA_DRY_RUN", raising=False)
    assert global_dry_run() is False
    assert resolve_trading_mode({"mode": "live"}, "ibkr") == "live"


@pytest.mark.parametrize("val", ["1", "true", "YES", "on"])
def test_dry_run_forces_read_only(monkeypatch, val):
    monkeypatch.setenv("ARIA_DRY_RUN", val)
    assert global_dry_run() is True
    # Even an explicit live config is forced read-only.
    assert resolve_trading_mode({"mode": "live"}, "ibkr") == "read_only"
    assert resolve_trading_mode({"mode": "paper"}, "paper") == "read_only"


def test_dry_run_blocks_paper_execution(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("paper_dry", {"type": "paper", "mode": "paper",
                                       "starting_cash": 10000, "currency": "USD"})
    broker.connect()

    # Baseline: paper preview is executable.
    monkeypatch.delenv("ARIA_DRY_RUN", raising=False)
    p_ok = build_order_preview(broker, OrderIntent(symbol="AAPL", side="buy", quantity=10, price=100))
    assert p_ok["can_execute"] is True

    # With dry-run on, the same preview is blocked.
    monkeypatch.setenv("ARIA_DRY_RUN", "1")
    p_blocked = build_order_preview(broker, OrderIntent(symbol="AAPL", side="buy", quantity=10, price=100))
    assert p_blocked["can_execute"] is False
    assert any("ARIA_DRY_RUN" in b for b in p_blocked.get("execution_blockers", []))


# ── Heartbeat / auto-reconnect ────────────────────────────────────────────────

def test_paper_ping_reflects_ledger(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("hb", {"type": "paper", "starting_cash": 1000})
    assert broker.ping() is False          # not connected → no account yet
    broker.connect()
    assert broker.ping() is True


def test_ensure_connected_reconnects_after_drop(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("hb", {"type": "paper", "starting_cash": 1000})
    broker.connect()
    broker._connected = False              # simulate a silent socket drop
    assert broker.ensure_connected() is True
    assert broker.is_connected is True


def test_registry_ensure_and_health(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("hb", {"type": "paper", "starting_cash": 1000})
    broker.connect()
    reg = BrokerRegistry()
    reg._instances["hb"] = broker

    broker._connected = False
    inst = reg.ensure("hb")                # transparent reconnect
    assert inst.is_connected is True

    snap = reg.health()
    assert snap and snap[0]["broker_id"] == "hb"
    assert snap[0]["healthy"] is True
