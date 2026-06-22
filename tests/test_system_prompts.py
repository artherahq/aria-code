from apps.cli.prompts.system_prompts import (
    build_finance_prompt,
    build_prefetched_analysis_prompt,
    build_response_style_rule,
)


def test_finance_prompt_routes_visual_artifacts_to_services_not_scripts():
    prompt = build_finance_prompt("生成 AAPL 近一年 K线图")

    assert "调用已有 chart/backtest/report 服务" in prompt
    assert "调用 dashboard 服务生成 HTML" in prompt
    assert "保存 PNG 到桌面" not in prompt
    assert "保存 HTML 图表到桌面" not in prompt
    assert "画K线图/生成图表/回测策略' → 写 Python 脚本" not in prompt


def test_response_style_rule_is_bilingual_and_terminal_first():
    zh = build_response_style_rule("zh")
    en = build_response_style_rule("en")

    assert "先给结论" in zh
    assert "错误或缺数据" in zh
    assert "Lead with the answer" in en
    assert "missing data" in en


def test_prefetched_analysis_prompt_matches_user_language():
    en = build_prefetched_analysis_prompt(user_message="analysis Apple volume")
    zh = build_prefetched_analysis_prompt(user_message="分析苹果成交量")

    assert "Data Already Fetched" in en
    assert "Use four compact sections" in en
    assert "数据已经预取完毕" in zh
    assert "终端回答风格" in zh
