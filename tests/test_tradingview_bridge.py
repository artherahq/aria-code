from apps.cli.tradingview_bridge import tradingview_symbol, tradingview_url


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

