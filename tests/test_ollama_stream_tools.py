from apps.cli.providers.llm.ollama_stream import (
    _normalize_requested_tool_call,
    _response_cache_eligible,
    _routing_message_for_turn,
)
from apps.cli.prompts.system_prompts import build_analysis_system_prompt


def test_tool_capable_turn_never_uses_prose_response_cache():
    assert not _response_cache_eligible("分析苹果股票走势", [], enable_tools=True)
    assert _response_cache_eligible("你好", [], enable_tools=False)
    assert not _response_cache_eligible("你好", [{"role": "user"}], enable_tools=False)


def test_analysis_prompt_does_not_force_text_tool_markup():
    prompt = build_analysis_system_prompt()

    assert "<tool_call>" not in prompt
    assert "ALWAYS call get_market_data" in prompt


def test_tool_followup_keeps_original_user_intent_for_routing():
    history = [
        {"role": "user", "content": "分析苹果股票走势和成交量"},
        {"role": "assistant", "content": ""},
        {"role": "tool", "content": "market data"},
    ]

    routed = _routing_message_for_turn(
        "## Tool Results\n\n### [get_market_data] ✓ Success",
        history,
    )

    assert routed == "分析苹果股票走势和成交量"


def test_market_tool_alias_and_ticker_parameter_are_normalized():
    name, params, requested = _normalize_requested_tool_call(
        "get_stock_data",
        {"ticker": "AAPL"},
        {"get_market_data", "get_market_history"},
    )

    assert name == "get_market_data"
    assert params == {"symbol": "AAPL"}
    assert requested == "get_stock_data"


def test_unknown_tool_name_is_not_silently_redirected():
    name, params, requested = _normalize_requested_tool_call(
        "delete_everything",
        {"force": True},
        {"get_market_data"},
    )

    assert name == requested == "delete_everything"
    assert params == {"force": True}
