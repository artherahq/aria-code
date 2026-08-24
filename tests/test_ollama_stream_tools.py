from aria_code.apps.cli.providers.llm.ollama_stream import (
    _finance_tool_schema_allowed,
    _normalize_requested_tool_call,
    _response_cache_eligible,
    _routing_message_for_turn,
    _skill_tool_schema_allowed,
)
from aria_code.packages.aria_mcp.tools import mcp_tool_to_spec
from aria_code.apps.cli.prompts.system_prompts import build_analysis_system_prompt


def test_tool_capable_turn_never_uses_prose_response_cache():
    assert not _response_cache_eligible("分析苹果股票走势", [], enable_tools=True)
    assert _response_cache_eligible("你好", [], enable_tools=False)
    assert not _response_cache_eligible("你好", [{"role": "user"}], enable_tools=False)


def test_tool_protocol_sanitizer_removes_brtc_tail_and_local_path():
    from apps.cli.message_processing import strip_tool_call_tags

    text = (
        "行情已获取，正在计算指标。?brtc "
        '{"name":"get_market_data","arguments":{"symbol":"AAPL",'
        '"source":{"path":"/Users/mac/Desktop/aria-code/generated/tmp_ohlc.csv"}}}'
    )

    assert strip_tool_call_tags(text) == "行情已获取，正在计算指标。"


def test_tool_protocol_sanitizer_removes_nested_bare_json_call():
    from apps.cli.message_processing import strip_tool_call_tags

    text = (
        "结果如下： "
        '{"name":"get_market_data","arguments":{"symbol":"AAPL",'
        '"options":{"period":"1y","adjusted":true}}} '
        "请注意风险。"
    )

    cleaned = strip_tool_call_tags(text)
    assert "get_market_data" not in cleaned
    assert "AAPL" not in cleaned
    assert cleaned == "结果如下：  请注意风险。"


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


def test_finance_routing_keeps_approved_arthera_mcp_tools_without_magic_word():
    schema = {
        "type": "function",
        "function": {
            "name": "mcp__arthera_quant_engine__research_report_assess",
            "parameters": {"type": "object"},
        },
    }

    assert _finance_tool_schema_allowed(
        schema,
        {"research_report_assess"},
        explicit_mcp=False,
    )
    assert not _finance_tool_schema_allowed(
        schema,
        {"get_market_data"},
        explicit_mcp=False,
    )


def test_skill_policy_matches_local_and_namespaced_mcp_tools_only():
    local_schema = {
        "function": {"name": "get_market_data"},
    }
    mcp_schema = {
        "function": {"name": "mcp__arthera_quant_engine__research_report_assess"},
    }
    denied_schema = {
        "function": {"name": "run_command"},
    }
    allowed = {"get_market_data", "research_report_assess"}

    assert _skill_tool_schema_allowed(local_schema, allowed)
    assert _skill_tool_schema_allowed(mcp_schema, allowed)
    assert not _skill_tool_schema_allowed(denied_schema, allowed)


def test_core_file_tools_stay_available_despite_narrow_skill_policy():
    """Regression for the 2026-07-15 incident: equity-research-report's policy
    listed only research tools; a request to convert an *existing* file to
    PDF matched on the term "报告" and activated it anyway, which then
    silently stripped read_file/write_file from the model's tool schema —
    the model could only say "please paste the file contents". Core file I/O
    and deliverable-export tools must stay available no matter what a
    skill's allowed_tools declares.
    """
    narrow_policy = {"get_market_data", "web_search"}  # no read_file, no write_file

    for tool_name in (
        "read_file", "write_file", "edit_file", "multi_edit",
        "analyze_file", "write_spreadsheet", "export_markdown_pdf",
    ):
        schema = {"function": {"name": tool_name}}
        assert _skill_tool_schema_allowed(schema, narrow_policy), (
            f"{tool_name} must stay available even when a skill's policy omits it"
        )

    # The filter should still deny tools that are genuinely outside both the
    # core set and the skill's declared policy — this isn't a blanket bypass.
    assert not _skill_tool_schema_allowed({"function": {"name": "run_command"}}, narrow_policy)


def test_report_quality_mcp_tool_gets_research_quality_capability():
    spec = mcp_tool_to_spec(
        {
            "name": "research_report_assess",
            "description": "Assess report quality and completion gates",
            "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True},
        },
        "arthera_quant_engine",
    )

    assert "research.quality" in spec.capabilities
