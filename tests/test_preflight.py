from apps.cli.preflight import build_install_plan, build_intent_preflight, format_preflight_plain


def _modules_available(*present):
    present_set = set(present)
    return lambda module: module in present_set


def _commands_available(*present):
    present_set = set(present)
    return lambda command: command in present_set


def test_chart_png_preflight_detects_market_and_rendering_deps():
    report = build_intent_preflight(
        "生成 AAPL 近一年 K线图 png",
        module_available=_modules_available("pandas", "numpy"),
        command_available=_commands_available(),
    )

    assert "chart" in report.intents
    assert "market_data" in report.services
    assert "chart_renderer" in report.services
    missing = {req.package for req in report.missing_python}
    assert {"yfinance", "matplotlib", "mplfinance"} <= missing
    assert "python3 -m pip install" in report.pip_install_command()


def test_plain_chat_has_no_preflight_noise():
    report = build_intent_preflight(
        "你好，今天我们聊什么",
        module_available=_modules_available(),
        command_available=_commands_available(),
    )

    assert report.intents == ()
    assert not report.has_findings
    assert format_preflight_plain(report) == ""


def test_vision_and_browser_require_different_capabilities():
    report = build_intent_preflight(
        "/browser screenshot https://example.com",
        module_available=_modules_available("PIL"),
        command_available=_commands_available(),
    )

    assert "browser" in report.intents
    assert "vision" in report.intents
    missing_modules = {req.package for req in report.missing_python}
    missing_commands = {req.command for req in report.missing_commands}
    assert "playwright" in missing_modules
    assert "playwright" in missing_commands


def test_broker_preflight_uses_specific_connector_package():
    report = build_intent_preflight(
        "帮我连接 IBKR 账户并查看持仓",
        module_available=_modules_available("pandas", "numpy", "yfinance"),
        command_available=_commands_available(),
    )

    assert "broker_connector" in report.services
    assert [req.package for req in report.missing_python] == ["ib_insync"]


def test_file_analysis_preflight_lists_parser_packages_without_paths():
    report = build_intent_preflight(
        "/file load report.xlsx",
        module_available=_modules_available("pandas", "PIL"),
        command_available=_commands_available(),
    )

    text = format_preflight_plain(report)
    assert "file_parser" in text
    assert "openpyxl" in text
    assert "/Users/" not in text


def test_install_plan_deduplicates_packages_and_keeps_tool_hints_structured():
    report = build_intent_preflight(
        "/browser screenshot https://example.com",
        module_available=_modules_available(),
        command_available=_commands_available(),
    )

    plan = build_install_plan(report)

    assert plan.has_actions
    assert plan.pip_packages.count("playwright") == 1
    assert "Pillow" in plan.pip_packages
    assert plan.pip_command.startswith("python3 -m pip install ")
    assert any(hint.startswith("playwright:") for hint in plan.command_hints)
    assert plan.has_required_items


def test_cloud_preflight_install_plan_keeps_env_setup_separate():
    report = build_intent_preflight(
        "检查阿里云云端服务",
        module_available=_modules_available(),
        command_available=_commands_available(),
        env_get=lambda _name: None,
    )

    plan = build_install_plan(report)

    assert plan.pip_packages == ()
    assert plan.pip_command == ""
    assert plan.env_hints == ("ALIYUN_ACCESS_KEY_ID: 阿里云访问密钥",)
