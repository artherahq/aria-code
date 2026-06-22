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
