from brokers.base import AccountInfo, Position
from brokers.planning import (
    RiskRuleSet,
    StrategyIntent,
    infer_intent_from_backtest,
    plan_order,
    plans_from_strategy_results,
    snapshot_from_broker,
)


class _FakeBroker:
    broker_id = "paper"
    label = "Paper Broker"

    def account_info(self):
        return AccountInfo(
            broker_id="paper",
            broker_type="paper",
            label="Paper Broker",
            account_id="12345678",
            currency="USD",
            total_assets=100000,
            cash=20000,
            market_value=80000,
        )

    def positions(self):
        return [
            Position(
                symbol="AAPL",
                quantity=100,
                cost_price=150,
                current_price=200,
                market_value=20000,
                currency="USD",
            )
        ]


def test_snapshot_from_broker_normalizes_account_and_positions():
    snapshot = snapshot_from_broker(_FakeBroker())

    assert snapshot.broker_id == "paper"
    assert snapshot.currency == "USD"
    assert snapshot.cash == 20000
    assert round(snapshot.current_weight("AAPL"), 4) == 0.2


def test_plan_order_rebalances_to_target_weight():
    snapshot = snapshot_from_broker(_FakeBroker())
    intent = StrategyIntent(symbol="MSFT", action="rebalance", target_weight=0.05)
    plan = plan_order(snapshot, intent, price=250)

    assert plan.estimated_order is not None
    assert plan.estimated_order.side == "buy"
    assert plan.estimated_order.quantity == 20
    assert plan.cash_after == 15000
    assert plan.risk["passed"] is True


def test_plan_order_rejects_cash_shortfall():
    snapshot = snapshot_from_broker(_FakeBroker())
    intent = StrategyIntent(symbol="NVDA", action="rebalance", target_weight=0.50)
    plan = plan_order(snapshot, intent, price=100, rules=RiskRuleSet(max_single_position_weight=0.60))

    assert plan.estimated_order is not None
    assert plan.risk["passed"] is False
    assert "可用现金不足" in plan.risk["violations"]


def test_infer_intent_from_backtest_and_create_plan():
    backtest = {
        "symbol": "TSLA",
        "strategy": "momentum",
        "total_return": 0.22,
        "alpha": 0.08,
        "max_drawdown": -0.12,
        "equity_curve": [{"close": 100}, {"close": 125}],
    }
    intent = infer_intent_from_backtest(backtest)
    assert intent.symbol == "TSLA"
    assert intent.target_weight == 0.10
    assert intent.source == "backtest"

    plans = plans_from_strategy_results(snapshot_from_broker(_FakeBroker()), [backtest])
    assert len(plans) == 1
    assert plans[0].symbol == "TSLA"
    assert plans[0].estimated_order is not None
