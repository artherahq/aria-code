from apps.cli.commands.backtest_cmds import format_backtest_data_error


def test_format_backtest_data_error_for_short_history():
    msg = format_backtest_data_error(
        "AAPL",
        start_date="2024-01-01",
        end_date="2024-12-31",
        bars=3,
    )

    assert "仅 3 个交易日" in msg
    assert "缩短策略周期" in msg


def test_format_backtest_data_error_for_empty_range():
    msg = format_backtest_data_error(
        "AAPL",
        start_date="2024-01-01",
        end_date="2024-12-31",
    )

    assert "没有可用历史数据" in msg
    assert "未停牌" in msg


def test_format_backtest_data_error_includes_local_error_hint():
    msg = format_backtest_data_error(
        "AAPL",
        start_date="2024-01-01",
        end_date="2024-12-31",
        local_error="empty history dataframe",
    )

    assert "回测失败" in msg
    assert "/doctor /health" in msg
