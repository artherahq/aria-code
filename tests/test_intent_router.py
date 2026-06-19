from apps.cli.intent_router import build_intent_route, detect_intents


def test_visual_market_chart_routes_to_chart_without_code_autorun():
    route = build_intent_route("生成 Apple 公司近一年 K线图 png")

    assert "chart" in route.intents
    assert route.primary == "chart"
    assert route.visual_artifact is True
    assert route.wants_market_prefetch is False
    assert route.allows_code_autorun is False


def test_explicit_python_script_allows_code_autorun():
    route = build_intent_route("写一个 Python 脚本分析 AAPL 并保存为 aapl.py")

    assert route.primary == "code"
    assert route.explicit_code is True
    assert route.allows_code_autorun is True


def test_browser_screenshot_keeps_browser_and_vision_intents():
    intents = detect_intents("/browser screenshot https://example.com")

    assert "browser" in intents
    assert "vision" in intents


def test_plain_greeting_has_no_service_intents():
    route = build_intent_route("你好")

    assert route.intents == ()
    assert route.services == ()
    assert route.primary in {"general", "finance"}
