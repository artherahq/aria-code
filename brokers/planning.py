"""Broker-aware portfolio snapshot, order planning, and risk gates.

This module intentionally does not place orders. It converts strategy output or
user order intent into an auditable plan that a human must approve first.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .base import AccountInfo, Position


@dataclass(frozen=True)
class PortfolioSnapshot:
    broker_id: str
    broker_label: str
    currency: str
    total_assets: float
    cash: float
    market_value: float
    positions: List[Position] = field(default_factory=list)

    def position_for(self, symbol: str) -> Optional[Position]:
        sym = (symbol or "").upper()
        for pos in self.positions:
            if (pos.symbol or "").upper() == sym:
                return pos
        return None

    def current_weight(self, symbol: str) -> float:
        if self.total_assets <= 0:
            return 0.0
        pos = self.position_for(symbol)
        return float(pos.market_value / self.total_assets) if pos else 0.0


@dataclass(frozen=True)
class StrategyIntent:
    symbol: str
    action: str = "hold"  # buy | sell | hold | rebalance
    target_weight: Optional[float] = None
    confidence: Optional[float] = None
    reason: str = ""
    source: str = "manual"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RiskRuleSet:
    max_single_position_weight: float = 0.20
    min_cash_reserve_weight: float = 0.02
    max_order_value_weight: float = 0.10
    allow_short: bool = False
    allow_fractional: bool = False


@dataclass(frozen=True)
class PlannedOrder:
    symbol: str
    side: str
    quantity: float
    order_type: str
    price: float
    estimated_value: float


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    action: str
    current_weight: float
    target_weight: float
    current_quantity: float
    estimated_price: float
    estimated_order: Optional[PlannedOrder]
    cash_before: float
    cash_after: float
    requires_approval: bool
    reason: str = ""
    source: str = "manual"
    risk: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "current_weight": round(self.current_weight, 6),
            "target_weight": round(self.target_weight, 6),
            "current_quantity": self.current_quantity,
            "estimated_price": self.estimated_price,
            "estimated_order": self.estimated_order.__dict__ if self.estimated_order else None,
            "cash_before": round(self.cash_before, 4),
            "cash_after": round(self.cash_after, 4),
            "requires_approval": self.requires_approval,
            "reason": self.reason,
            "source": self.source,
            "risk": self.risk,
        }


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def snapshot_from_broker(broker: Any) -> PortfolioSnapshot:
    account: AccountInfo = broker.account_info()
    positions = list(broker.positions() or [])
    return PortfolioSnapshot(
        broker_id=getattr(broker, "broker_id", ""),
        broker_label=getattr(broker, "label", getattr(broker, "broker_id", "")),
        currency=account.currency,
        total_assets=_as_float(account.total_assets),
        cash=_as_float(account.cash),
        market_value=_as_float(account.market_value),
        positions=positions,
    )


def infer_intent_from_backtest(result: Dict[str, Any], target_weight: Optional[float] = None) -> StrategyIntent:
    total_return = _as_float(result.get("total_return"))
    alpha = _as_float(result.get("alpha"))
    max_dd = _as_float(result.get("max_drawdown"))
    symbol = str(result.get("symbol", "")).upper()
    if target_weight is None:
        if total_return > 0 and alpha >= 0:
            target_weight = 0.10
        elif total_return > 0:
            target_weight = 0.05
        else:
            target_weight = 0.0
    action = "rebalance" if target_weight > 0 else "sell"
    confidence = max(0.0, min(1.0, 0.5 + alpha - abs(max_dd) * 0.25))
    return StrategyIntent(
        symbol=symbol,
        action=action,
        target_weight=target_weight,
        confidence=round(confidence, 4),
        reason=f"backtest total={total_return:.2%}, alpha={alpha:.2%}, max_dd={max_dd:.2%}",
        source="backtest",
        metadata={
            "strategy": result.get("strategy"),
            "report_path": result.get("report_path"),
            "total_return": result.get("total_return"),
            "alpha": result.get("alpha"),
            "max_drawdown": result.get("max_drawdown"),
        },
    )


def _resolve_price(symbol: str, explicit_price: Optional[float], snapshot: PortfolioSnapshot) -> float:
    price = _as_float(explicit_price)
    if price > 0:
        return price
    pos = snapshot.position_for(symbol)
    return _as_float(pos.current_price if pos else 0.0)


def plan_order(
    snapshot: PortfolioSnapshot,
    intent: StrategyIntent,
    price: Optional[float] = None,
    quantity: Optional[float] = None,
    order_type: str = "limit",
    rules: Optional[RiskRuleSet] = None,
) -> OrderPlan:
    rules = rules or RiskRuleSet()
    symbol = intent.symbol.upper()
    current_pos = snapshot.position_for(symbol)
    current_qty = _as_float(current_pos.quantity if current_pos else 0.0)
    resolved_price = _resolve_price(symbol, price, snapshot)
    current_weight = snapshot.current_weight(symbol)
    target_weight = current_weight
    if intent.target_weight is not None:
        target_weight = max(0.0 if not rules.allow_short else -1.0, _as_float(intent.target_weight))

    if quantity is not None:
        qty = _as_float(quantity)
        side = "buy" if intent.action in ("buy", "rebalance") else "sell"
    else:
        if resolved_price <= 0 or snapshot.total_assets <= 0:
            qty = 0.0
            side = "hold"
        else:
            target_value = snapshot.total_assets * target_weight
            current_value = snapshot.total_assets * current_weight
            delta_value = target_value - current_value
            side = "buy" if delta_value > 0 else "sell" if delta_value < 0 else "hold"
            qty = abs(delta_value) / resolved_price

    if not rules.allow_fractional:
        qty = math.floor(qty)
    if side == "sell":
        qty = min(qty, max(current_qty, 0.0))

    estimated_value = max(qty, 0.0) * max(resolved_price, 0.0)
    order = None
    cash_after = snapshot.cash
    if side in ("buy", "sell") and qty > 0 and resolved_price > 0:
        order = PlannedOrder(
            symbol=symbol,
            side=side,
            quantity=qty,
            order_type=order_type,
            price=resolved_price,
            estimated_value=estimated_value,
        )
        cash_after = snapshot.cash - estimated_value if side == "buy" else snapshot.cash + estimated_value

    plan = OrderPlan(
        symbol=symbol,
        action=intent.action,
        current_weight=current_weight,
        target_weight=target_weight,
        current_quantity=current_qty,
        estimated_price=resolved_price,
        estimated_order=order,
        cash_before=snapshot.cash,
        cash_after=cash_after,
        requires_approval=order is not None,
        reason=intent.reason,
        source=intent.source,
    )
    risk = evaluate_risk(plan, snapshot, rules)
    return OrderPlan(**{**plan.__dict__, "risk": risk})


def evaluate_risk(plan: OrderPlan, snapshot: PortfolioSnapshot, rules: Optional[RiskRuleSet] = None) -> Dict[str, Any]:
    rules = rules or RiskRuleSet()
    violations: List[str] = []
    warnings: List[str] = []
    order = plan.estimated_order
    total_assets = max(snapshot.total_assets, 0.0)
    if total_assets <= 0:
        violations.append("账户总资产不可用，无法评估仓位")
    if plan.target_weight > rules.max_single_position_weight:
        violations.append(f"目标单票仓位 {plan.target_weight:.1%} 超过上限 {rules.max_single_position_weight:.1%}")
    if order and order.side == "buy":
        if order.estimated_value > snapshot.cash:
            violations.append("可用现金不足")
        reserve = total_assets * rules.min_cash_reserve_weight
        if plan.cash_after < reserve:
            warnings.append(f"交易后现金低于保留比例 {rules.min_cash_reserve_weight:.1%}")
    projected_position_value = 0.0
    projected_position_weight = 0.0
    if order and total_assets > 0:
        current_value = total_assets * plan.current_weight
        if order.side == "buy":
            projected_position_value = current_value + order.estimated_value
        else:
            projected_position_value = max(0.0, current_value - order.estimated_value)
        projected_position_weight = projected_position_value / total_assets
        if projected_position_weight > rules.max_single_position_weight:
            violations.append(
                f"成交后单票仓位 {projected_position_weight:.1%} 超过上限 {rules.max_single_position_weight:.1%}"
            )
    if order and total_assets > 0 and order.estimated_value / total_assets > rules.max_order_value_weight:
        warnings.append(f"单笔订单金额超过账户 {rules.max_order_value_weight:.1%}")
    if order and order.side == "sell" and order.quantity > plan.current_quantity and not rules.allow_short:
        violations.append("卖出数量超过当前持仓，且未允许做空")
    return {
        "passed": not violations,
        "requires_manual_review": bool(warnings or violations),
        "violations": violations,
        "warnings": warnings,
        "rules": {
            "max_single_position_weight": rules.max_single_position_weight,
            "min_cash_reserve_weight": rules.min_cash_reserve_weight,
            "max_order_value_weight": rules.max_order_value_weight,
            "allow_short": rules.allow_short,
            "allow_fractional": rules.allow_fractional,
        },
        "projected_position_weight": round(projected_position_weight, 6),
    }


def plans_from_strategy_results(
    snapshot: PortfolioSnapshot,
    results: Iterable[Dict[str, Any]],
    rules: Optional[RiskRuleSet] = None,
) -> List[OrderPlan]:
    plans: List[OrderPlan] = []
    for result in results:
        intent = infer_intent_from_backtest(result)
        last_price = None
        curve = result.get("equity_curve") or []
        if curve and isinstance(curve[-1], dict):
            last_price = curve[-1].get("close")
        plans.append(plan_order(snapshot, intent, price=last_price, rules=rules))
    return plans
