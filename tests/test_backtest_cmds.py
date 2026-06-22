from apps.cli.commands.backtest_cmds import _bt_result_summary, _bt_trade_count, format_backtest_data_error


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


def test_bt_trade_count_prefers_local_engine_total_trades():
    assert _bt_trade_count({"total_trades": 4, "num_trades": 0}) == 4
    assert _bt_trade_count({"num_trades": 2}) == 2
    assert _bt_trade_count({}) == 0


def test_bt_result_summary_compares_strategy_with_buy_and_hold():
    msg = _bt_result_summary(
        {
            "total_return": 0.224,
            "buy_hold_return": 0.482,
            "sharpe_ratio": 1.19,
            "max_drawdown": -0.126,
        }
    )

    assert "策略收益 22.4%" in msg
    assert "低于买入持有 48.2%" in msg
    assert "Sharpe 1.19" in msg
