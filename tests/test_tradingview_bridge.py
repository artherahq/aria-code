import sqlite3

from apps.cli.tradingview_bridge import (
    build_tradingview_order_preview,
    enqueue_tradingview_alert,
    export_pine_strategy,
    generate_pine_strategy,
    parse_tradingview_alert,
    tradingview_symbol,
    tradingview_url,
)
from brokers.paper_broker import PaperBroker


def _patch_trade_paths(monkeypatch, tmp_path):
    import brokers.paper_broker as paper_mod
    import brokers.trading as trading_mod

    monkeypatch.setattr(paper_mod, "PAPER_LEDGER_PATH", tmp_path / "paper_ledger.json")
    monkeypatch.setattr(trading_mod, "TRADE_PREVIEWS_PATH", tmp_path / "trade_previews.json")
    monkeypatch.setattr(trading_mod, "TRADE_AUDIT_PATH", tmp_path / "trade_audit.jsonl")


def test_tradingview_symbol_maps_common_global_assets():
    assert tradingview_symbol("^IXIC") == "NASDAQ:IXIC"
    assert tradingview_symbol("QQQ") == "NASDAQ:QQQ"
    assert tradingview_symbol("0700.HK") == "HKEX:700"
    assert tradingview_symbol("600519") == "SSE:600519"
    assert tradingview_symbol("300750") == "SZSE:300750"
    assert tradingview_symbol("BTC-USD") == "BINANCE:BTCUSDT"
    assert tradingview_symbol("GC=F") == "COMEX:GC1!"
    assert tradingview_symbol("EURUSD=X") == "FX:EURUSD"


def test_tradingview_url_encodes_symbol():
    assert tradingview_url("^IXIC").endswith("symbol=NASDAQ%3AIXIC")


def test_tradingview_url_supports_interval():
    assert tradingview_url("NVDA", interval="60").endswith("symbol=NASDAQ%3ANVDA&interval=60")


def test_parse_tradingview_alert_normalizes_symbol_and_action():
    alert = parse_tradingview_alert({"ticker": "NASDAQ:NVDA", "side": "long", "price": 210.5})

    assert alert["symbol"] == "NVDA"
    assert alert["action"] == "BUY"
    assert alert["price"] == 210.5


def test_parse_tradingview_strategy_order_fields():
    alert = parse_tradingview_alert({
        "syminfo.tickerid": "NASDAQ:NVDA",
        "strategy.order.action": "sell",
        "strategy.order.price": "210.5",
    })

    assert alert["symbol"] == "NVDA"
    assert alert["action"] == "SELL"
    assert alert["price"] == "210.5"


def test_tradingview_buy_alert_creates_paper_trade_preview(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("paper_tv", {
        "id": "paper_tv",
        "type": "paper",
        "label": "TV Paper",
        "mode": "paper",
        "starting_cash": 10000,
        "currency": "USD",
    })
    broker.connect()

    result = build_tradingview_order_preview(
        {"ticker": "NASDAQ:NVDA", "action": "BUY", "quantity": 2, "price": 100},
        broker=broker,
    )

    assert result["success"] is True
    assert result["trade_preview_created"] is True
    assert result["preview_id"].startswith("tp_")
    assert result["mode"] == "paper"
    assert result["can_execute"] is True
    assert result["trade_preview"]["order_plan"]["estimated_order"]["quantity"] == 2


def test_tradingview_buy_alert_without_size_does_not_create_order(monkeypatch, tmp_path):
    _patch_trade_paths(monkeypatch, tmp_path)
    broker = PaperBroker("paper_tv_safe", {
        "id": "paper_tv_safe",
        "type": "paper",
        "label": "TV Paper Safe",
        "mode": "paper",
        "starting_cash": 10000,
        "currency": "USD",
    })
    broker.connect()

    result = build_tradingview_order_preview(
        {"ticker": "NASDAQ:NVDA", "action": "BUY", "price": 100},
        broker=broker,
    )

    assert result["success"] is True
    assert result["trade_preview_created"] is False
    assert result["reason"] == "missing_quantity"


def test_enqueue_tradingview_alert_writes_daemon_job(tmp_path):
    db_path = tmp_path / "daemon.db"
    result = enqueue_tradingview_alert(
        {"symbol": "NASDAQ:NVDA", "action": "SELL", "channels": ["telegram"]},
        db_path=db_path,
    )

    assert result["success"] is True
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT command, source, status, payload FROM webhook_jobs").fetchone()
    assert row[0] == "tradingview_alert"
    assert row[1] == "tradingview"
    assert row[2] == "pending"
    assert '"symbol": "NVDA"' in row[3]
    assert '"action": "SELL"' in row[3]


def test_generate_and_export_pine_strategy(tmp_path):
    script = generate_pine_strategy("NVDA")
    assert 'strategy("Aria NVDA EMA RSI Strategy"' in script
    assert '\\"symbol\\":\\"NVDA\\"' in script
    assert '\\"quantity\\":1' in script
    assert "{{close}}" in script

    path = export_pine_strategy("NVDA", output_dir=tmp_path)
    assert path.suffix == ".pine"
    assert "Aria NVDA EMA RSI Strategy" in path.read_text(encoding="utf-8")
