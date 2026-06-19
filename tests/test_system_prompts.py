from apps.cli.prompts.system_prompts import build_finance_prompt


def test_finance_prompt_routes_visual_artifacts_to_services_not_scripts():
    prompt = build_finance_prompt("生成 AAPL 近一年 K线图")

    assert "调用已有 chart/backtest/report 服务" in prompt
    assert "调用 dashboard 服务生成 HTML" in prompt
    assert "保存 PNG 到桌面" not in prompt
    assert "保存 HTML 图表到桌面" not in prompt
    assert "画K线图/生成图表/回测策略' → 写 Python 脚本" not in prompt
