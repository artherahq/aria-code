import sqlite3

from apps.cli.tradingview_bridge import (
    enqueue_tradingview_alert,
    export_pine_strategy,
    generate_pine_strategy,
    parse_tradingview_alert,
    tradingview_symbol,
    tradingview_url,
)


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
    assert "{{close}}" in script

    path = export_pine_strategy("NVDA", output_dir=tmp_path)
    assert path.suffix == ".pine"
    assert "Aria NVDA EMA RSI Strategy" in path.read_text(encoding="utf-8")
