import types

import pytest

from apps.cli.commands.backtest_cmds import (
    BacktestCommandsMixin,
    _bt_result_summary,
    _bt_trade_count,
    format_backtest_data_error,
)


# ── /deploy token parsing (SYM:qty[@price] | SYM:pct%[@price]) ──────────────

def test_parse_deploy_token_qty_only_defaults_price_to_none():
    assert BacktestCommandsMixin._parse_deploy_token("AAPL:10") == ("AAPL", 10.0, None, None)


def test_parse_deploy_token_with_explicit_price():
    assert BacktestCommandsMixin._parse_deploy_token("MSFT:5@320.5") == ("MSFT", 5.0, None, 320.5)


def test_parse_deploy_token_uppercases_symbol():
    sym, qty, weight, price = BacktestCommandsMixin._parse_deploy_token("aapl:3")
    assert sym == "AAPL" and qty == 3.0 and weight is None and price is None


def test_parse_deploy_token_weight_mode():
    assert BacktestCommandsMixin._parse_deploy_token("AAPL:30%") == ("AAPL", None, 0.30, None)


def test_parse_deploy_token_weight_with_price():
    sym, qty, weight, price = BacktestCommandsMixin._parse_deploy_token("MSFT:25%@320")
    assert sym == "MSFT" and qty is None and weight == 0.25 and price == 320.0


@pytest.mark.parametrize("bad", ["AAPL", "AAPL:", "AAPL:0", "AAPL:-5", "AAPL:10@-3",
                                 "AAPL:abc", "AAPL:0%", "AAPL:150%", "AAPL:-10%"])
def test_parse_deploy_token_rejects_bad_input(bad):
    with pytest.raises(ValueError):
        BacktestCommandsMixin._parse_deploy_token(bad)


# ── /deploy rebalance plan ──────────────────────────────────────────────────

def test_rebalance_plan_buys_to_reach_target_from_empty():
    # no holdings, target 100% AAPL with $10000 capital at $100 -> BUY 100
    plan = BacktestCommandsMixin._rebalance_plan({}, {"AAPL": 1.0}, {"AAPL": 100.0}, capital=10000)
    assert plan == [{"symbol": "AAPL", "cur_weight": 0.0, "target_weight": 1.0,
                     "side": "BUY", "shares": 100.0, "price": 100.0}]


def test_rebalance_plan_within_existing_value_no_capital():
    # hold 100 AAPL@100 (=10000) + 0 MSFT; target 50/50 -> sell 50 AAPL, buy 25 MSFT@200
    cur = {"AAPL": 100.0}
    plan = {p["symbol"]: p for p in BacktestCommandsMixin._rebalance_plan(
        cur, {"AAPL": 0.5, "MSFT": 0.5}, {"AAPL": 100.0, "MSFT": 200.0})}
    assert plan["AAPL"]["side"] == "SELL" and plan["AAPL"]["shares"] == 50.0
    assert plan["MSFT"]["side"] == "BUY" and plan["MSFT"]["shares"] == 25.0


def test_rebalance_plan_target_zero_sells_all_no_shorting():
    # hold 30 AAPL, target 0% -> SELL exactly 30 (never more)
    plan = BacktestCommandsMixin._rebalance_plan({"AAPL": 30.0}, {"AAPL": 0.0}, {"AAPL": 50.0})
    assert plan == [{"symbol": "AAPL", "cur_weight": 1.0, "target_weight": 0.0,
                     "side": "SELL", "shares": 30.0, "price": 50.0}]


def test_rebalance_plan_already_balanced_is_empty():
    # hold matches target -> no trades
    plan = BacktestCommandsMixin._rebalance_plan(
        {"AAPL": 50.0, "MSFT": 25.0}, {"AAPL": 0.5, "MSFT": 0.5},
        {"AAPL": 100.0, "MSFT": 200.0})
    assert plan == []


def test_rebalance_plan_zero_total_returns_empty():
    assert BacktestCommandsMixin._rebalance_plan({}, {"AAPL": 1.0}, {"AAPL": 100.0}) == []


# ── value weights (for `rebalance like` / `equal`) ──────────────────────────

def test_value_weights_basic():
    # 100 AAPL@100 (=10000) + 50 MSFT@200 (=10000) -> 50/50
    w = BacktestCommandsMixin._value_weights({"AAPL": 100.0, "MSFT": 50.0},
                                             {"AAPL": 100.0, "MSFT": 200.0})
    assert w == {"AAPL": 0.5, "MSFT": 0.5}


def test_value_weights_skips_symbols_without_price():
    w = BacktestCommandsMixin._value_weights({"AAPL": 10.0, "XXX": 5.0}, {"AAPL": 100.0, "XXX": 0})
    assert w == {"AAPL": 1.0}


def test_value_weights_empty_when_no_value():
    assert BacktestCommandsMixin._value_weights({"AAPL": 10.0}, {}) == {}


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


def test_backtest_helpers_survive_mixin_global_rebinding():
    """aria_cli rebinds mixin method globals; helpers must remain available via self."""

    def render_like_cmd(self, data):
        bh = self._bt_num(self._bt_value(data, "buy_hold_return", "benchmark_return", default=0))
        return self._bt_pct(bh), self._bt_trade_count(data), self._bt_result_summary(data)

    rebound = types.FunctionType(
        render_like_cmd.__code__,
        {"__builtins__": __builtins__},
        render_like_cmd.__name__,
        render_like_cmd.__defaults__,
        render_like_cmd.__closure__,
    )

    instance = BacktestCommandsMixin()
    pct, trades, summary = rebound(
        instance,
        {
            "total_return": 0.1,
            "benchmark_return": 0.2,
            "sharpe_ratio": 0.7,
            "max_drawdown": -0.05,
            "total_trades": 3,
        },
    )

    assert pct == "20.0%"
    assert trades == 3
    assert "低于买入持有 20.0%" in summary
