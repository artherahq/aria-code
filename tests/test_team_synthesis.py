from agents.financial.synthesis import _template_synthesis


def test_template_synthesis_uses_real_market_context_without_no_real_data():
    text = _template_synthesis(
        "AAPL",
        [
            {
                "agent": "technical",
                "signal": "BUY",
                "confidence": 0.7,
                "key_points": ["RSI 39.1, price below MA20"],
            },
            {
                "agent": "fundamental",
                "signal": "HOLD",
                "confidence": 0.6,
                "key_points": ["PE available"],
            },
        ],
        {
            "consensus_signal": "HOLD",
            "consensus_confidence": 0.61,
            "market_snapshot": {
                "price": 298.01,
                "currency": "USD",
                "analyst_target": 310.0,
                "ma60": 282.91,
                "provider_chain": ["finnhub", "yfinance"],
                "missing_fields": [],
            },
        },
    )

    assert "当前价: USD 298.01" in text
    assert "数据源: finnhub, yfinance" in text
    assert "FINAL: HOLD | Target: USD 310.00 | Stop: below MA60 USD 282.91" in text
    assert "no real data" not in text


def test_template_synthesis_uses_support_stop_when_price_is_below_ma60():
    text = _template_synthesis(
        "NFLX",
        [
            {
                "agent": "technical",
                "signal": "SELL",
                "confidence": 0.5,
                "key_points": ["RSI 22.2, price below MA60"],
            },
            {
                "agent": "risk",
                "signal": "HOLD",
                "confidence": 0.7,
                "key_points": ["risk metrics unavailable"],
            },
        ],
        {
            "consensus_signal": "HOLD",
            "consensus_confidence": 0.64,
            "market_snapshot": {
                "price": 77.38,
                "currency": "USD",
                "ma60": 90.35,
                "support": [75.89],
                "provider_chain": ["finnhub", "yahoo_chart"],
                "missing_fields": ["volume", "analyst_target", "risk_metrics"],
            },
        },
    )

    assert "缺失: volume, analyst_target, risk_metrics" in text
    assert "Stop: below support USD 75.89" in text
    assert "Stop: below MA60 USD 90.35" not in text
