from packages.aria_services.research_protocol import (
    grounding_tool_names,
    requires_financial_evidence,
)


def test_financial_evidence_intent_is_conservative():
    assert requires_financial_evidence("分析苹果股票走势和成交量")
    assert requires_financial_evidence("What is the current AAPL price?")
    assert not requires_financial_evidence("Explain what a Sharpe ratio is")


def test_grounding_tool_names_support_local_and_mcp_schemas():
    schemas = [
        {"name": "get_market_data"},
        {"name": "read_file"},
        {
            "type": "function",
            "function": {"name": "mcp__arthera_quant_engine__run_backtest"},
        },
    ]

    assert grounding_tool_names(schemas) == frozenset({
        "get_market_data",
        "mcp__arthera_quant_engine__run_backtest",
    })
