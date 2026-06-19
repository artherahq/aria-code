from apps.cli.market_universe import (
    MarketSymbol,
    looks_like_unresolved_market_name,
    resolve_market_mentions,
    resolve_market_symbol,
)


def test_static_a_share_name_resolves_sidike():
    assert resolve_market_symbol("斯迪克的走势和预测") == "300806"


def test_static_global_assets_resolve_common_classes():
    assert resolve_market_symbol("黄金走势") == "GC=F"
    assert resolve_market_symbol("标普500今天怎么样") == "^GSPC"
    assert resolve_market_symbol("纳斯达克的走势和预测") == "^IXIC"
    assert resolve_market_symbol("欧元美元汇率") == "EURUSD=X"
    assert resolve_market_symbol("比特币行情") == "BTC-USD"
    assert resolve_market_symbol("分析lvmh股票和成交量") == "MC.PA"
    assert resolve_market_symbol("路易斯威登股价") == "MC.PA"


def test_market_detect_prefers_universe_index_alias_over_old_etf_alias():
    from apps.cli.utils.market_detect import _extract_market_symbol, _extract_market_symbols

    assert _extract_market_symbol("纳斯达克的走势和预测") == "^IXIC"
    assert _extract_market_symbols("纳斯达克的走势和预测") == ["^IXIC"]


def test_dynamic_loader_can_resolve_full_universe_names():
    def fake_loader():
        return [
            MarketSymbol("测试股份", "301234", "CN", "test"),
            MarketSymbol("测试港股", "1234.HK", "HK", "test"),
        ]

    hits = resolve_market_mentions("测试股份和测试港股走势", load_universe=fake_loader)

    assert [item.symbol for _, item in hits] == ["301234", "1234.HK"]


def test_unresolved_market_name_heuristic_blocks_history_inheritance():
    assert looks_like_unresolved_market_name("不存在公司走势") is True
    assert looks_like_unresolved_market_name("今天天气怎么样") is False
