from apps.cli.finance_service_catalog import classify_finance_market, market_service_summary


def test_market_service_catalog_routes_each_major_market():
    assert classify_finance_market("分析四环生物 000518").key == "CN"
    assert classify_finance_market("港股恒生科技走势").key == "HK"
    assert classify_finance_market("分析 AAPL 财报").key == "US"
    assert classify_finance_market("BTC 资金费率和风险").key == "CRYPTO"
    assert classify_finance_market("美元兑人民币汇率").key == "FX"
    assert classify_finance_market("黄金期货走势").key == "FUTURES"


def test_a_share_contract_discloses_prediction_quality_requirements():
    summary = market_service_summary("A股明日预测")
    assert summary["selected"] == "CN"
    item = summary["markets"][0]
    assert "次交易日预测" in item["prediction"]
    assert "T+1/涨跌停约束回测" in item["analysis"]
