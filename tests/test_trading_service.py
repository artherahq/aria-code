from __future__ import annotations

from pathlib import Path

from brokers.base import AccountInfo
from brokers.paper_broker import PaperBroker
from brokers.trading import OrderIntent, build_order_preview, execute_order_preview


def _patch_trade_paths(monkeypatch, tmp_path: Path):
    import brokers.paper_broker as paper_mod
    import brokers.trading as trading_mod

    monkeypatch.setattr(paper_mod, "PAPER_LEDGER_PATH", tmp_path / "paper_ledger.json")
    monkeypatch.setattr(trading_mod, "TRADE_PREVIEWS_PATH", tmp_path / "trade_previews.json")
    monkeypatch.setattr(trading_mod, "TRADE_AUDIT_PATH", tmp_path / "trade_audit.jsonl")


def test_paper_broker_preview_and_confirm_executes_locally(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("paper_test", {
        "id": "paper_test",
        "type": "paper",
        "label": "Test Paper",
        "mode": "paper",
        "starting_cash": 10000,
        "currency": "USD",
    })
    broker.connect()

    preview = build_order_preview(
        broker,
        OrderIntent(symbol="AAPL", side="buy", quantity=10, price=100),
    )

    assert preview["mode"] == "paper"
    assert preview["can_execute"] is True
    assert preview["preview_id"].startswith("tp_")

    result = execute_order_preview(broker, preview["preview_id"], confirmed=True)

    assert result["success"] is True
    assert result["mode"] == "paper"
    assert broker.account_info().cash == 9000
    positions = broker.positions()
    assert len(positions) == 1
    assert positions[0].symbol == "AAPL"
    assert positions[0].quantity == 10


def test_read_only_mode_blocks_execution(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("paper_read_only", {
        "id": "paper_read_only",
        "type": "paper",
        "label": "Read Only",
        "mode": "read_only",
        "starting_cash": 10000,
        "currency": "USD",
    })
    broker.connect()

    preview = build_order_preview(
        broker,
        OrderIntent(symbol="AAPL", side="buy", quantity=1, price=100),
    )
    result = execute_order_preview(broker, preview["preview_id"], confirmed=True)

    assert preview["can_execute"] is False
    assert "read_only" in preview["execution_blockers"][0]
    assert result["success"] is False
    assert result["risk_rejected"] is True


class _LiveStubBroker:
    broker_id = "live_stub"
    broker_type = "alpaca"
    label = "Live Stub"
    config = {"mode": "live", "allow_live_trade": False}

    def account_info(self):
        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id="LIVE1234",
            currency="USD",
            total_assets=10000,
            cash=10000,
            market_value=0,
        )

    def positions(self):
        return []

    def place_order(self, **_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("live order should be blocked before place_order")


def test_live_mode_requires_explicit_allow_live_trade(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = _LiveStubBroker()

    preview = build_order_preview(
        broker,
        OrderIntent(symbol="AAPL", side="buy", quantity=1, price=100),
    )
    result = execute_order_preview(broker, preview["preview_id"], confirmed=True)

    assert preview["mode"] == "live"
    assert preview["can_execute"] is False
    assert any("allow_live_trade=true" in item for item in preview["execution_blockers"])
    assert result["success"] is False
    assert result["risk_rejected"] is True


def test_preview_blocks_projected_single_position_limit(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("paper_limit", {
        "id": "paper_limit",
        "type": "paper",
        "label": "Limit Paper",
        "mode": "paper",
        "starting_cash": 10000,
        "currency": "USD",
        "max_single_position_weight": 0.20,
        "allow_fractional": True,
    })
    broker.connect()

    preview = build_order_preview(
        broker,
        OrderIntent(symbol="AAPL", side="buy", quantity=30, price=100),
    )
    result = execute_order_preview(broker, preview["preview_id"], confirmed=True)

    assert preview["can_execute"] is False
    assert any("成交后单票仓位" in item for item in preview["execution_blockers"])
    assert preview["order_plan"]["risk"]["projected_position_weight"] == 0.3
    assert result["success"] is False
    assert broker.account_info().cash == 10000
