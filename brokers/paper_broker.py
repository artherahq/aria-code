"""Local paper-trading broker.

The paper broker implements the same BrokerBase contract as live adapters, but
all orders are filled into a local JSON ledger. It is meant for simulation,
strategy rehearsal, and TradingView alert dry-runs.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List

from .base import AccountInfo, BrokerBase, Order, OrderResult, Position
from .config import BROKERS_CONFIG_PATH


PAPER_LEDGER_PATH = BROKERS_CONFIG_PATH.parent / "paper_ledger.json"


def _now_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


def _load_ledger() -> Dict[str, Any]:
    if not PAPER_LEDGER_PATH.exists():
        return {"accounts": {}}
    try:
        data = json.loads(PAPER_LEDGER_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("accounts", {})
            return data
    except Exception:
        pass
    return {"accounts": {}}


def _save_ledger(data: Dict[str, Any]) -> None:
    PAPER_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    PAPER_LEDGER_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class PaperBroker(BrokerBase):
    broker_type = "paper"
    broker_name = "Aria Paper Broker"
    market = "GLOBAL"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self.currency = str(config.get("currency", "USD") or "USD").upper()
        self.starting_cash = float(config.get("starting_cash", 100000.0) or 100000.0)

    def connect(self) -> bool:
        ledger = _load_ledger()
        accounts = ledger.setdefault("accounts", {})
        if self.broker_id not in accounts:
            accounts[self.broker_id] = {
                "broker_id": self.broker_id,
                "label": self.label,
                "currency": self.currency,
                "starting_cash": self.starting_cash,
                "cash": self.starting_cash,
                "positions": {},
                "orders": [],
                "created_at": int(time.time()),
            }
            _save_ledger(ledger)
        self._connected = True
        return True

    def ping(self) -> bool:
        """Real liveness probe: the local ledger account must exist."""
        try:
            return self.broker_id in (_load_ledger().get("accounts") or {})
        except Exception:
            return False

    def reset(self, starting_cash: float | None = None, currency: str | None = None) -> None:
        ledger = _load_ledger()
        accounts = ledger.setdefault("accounts", {})
        cash = float(starting_cash if starting_cash is not None else self.starting_cash)
        curr = str(currency or self.currency).upper()
        accounts[self.broker_id] = {
            "broker_id": self.broker_id,
            "label": self.label,
            "currency": curr,
            "starting_cash": cash,
            "cash": cash,
            "positions": {},
            "orders": [],
            "created_at": int(time.time()),
        }
        _save_ledger(ledger)
        self.currency = curr
        self.starting_cash = cash
        self._connected = True

    def _account(self) -> Dict[str, Any]:
        if not self._connected:
            self.connect()
        ledger = _load_ledger()
        return ledger.setdefault("accounts", {}).setdefault(self.broker_id, {
            "broker_id": self.broker_id,
            "label": self.label,
            "currency": self.currency,
            "starting_cash": self.starting_cash,
            "cash": self.starting_cash,
            "positions": {},
            "orders": [],
        })

    def _save_account(self, account: Dict[str, Any]) -> None:
        ledger = _load_ledger()
        ledger.setdefault("accounts", {})[self.broker_id] = account
        _save_ledger(ledger)

    def account_info(self) -> AccountInfo:
        account = self._account()
        positions = self._positions_from_account(account)
        market_value = sum(p.market_value for p in positions)
        cost_basis = sum(p.cost_price * p.quantity for p in positions)
        pnl_total = market_value - cost_basis
        cash = float(account.get("cash", 0.0) or 0.0)
        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=f"PAPER-{self.broker_id}",
            currency=str(account.get("currency", self.currency)),
            total_assets=cash + market_value,
            cash=cash,
            market_value=market_value,
            pnl_total=pnl_total,
            pnl_pct=(pnl_total / cost_basis * 100) if cost_basis > 0 else 0.0,
            extra={"mode": "paper", "ledger_path": str(PAPER_LEDGER_PATH)},
        )

    def positions(self) -> List[Position]:
        return self._positions_from_account(self._account())

    def _positions_from_account(self, account: Dict[str, Any]) -> List[Position]:
        out: List[Position] = []
        for symbol, raw in sorted((account.get("positions") or {}).items()):
            qty = float(raw.get("quantity", 0.0) or 0.0)
            if qty <= 0:
                continue
            price = float(raw.get("current_price", raw.get("cost_price", 0.0)) or 0.0)
            cost = float(raw.get("cost_price", 0.0) or 0.0)
            market_value = qty * price
            pnl = (price - cost) * qty
            out.append(Position(
                symbol=symbol,
                name=symbol,
                quantity=qty,
                available_qty=qty,
                cost_price=cost,
                current_price=price,
                market_value=market_value,
                pnl=pnl,
                pnl_pct=(pnl / (cost * qty) * 100) if cost > 0 and qty > 0 else 0.0,
                currency=str(account.get("currency", self.currency)),
                market="paper",
            ))
        return out

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        raw_orders = list(self._account().get("orders") or [])
        raw_orders = list(reversed(raw_orders))[: max(0, int(limit or 50))]
        out: List[Order] = []
        for raw in raw_orders:
            mapped = str(raw.get("status", "filled"))
            if status != "all" and mapped != status:
                continue
            out.append(Order(
                order_id=str(raw.get("order_id", "")),
                symbol=str(raw.get("symbol", "")),
                name=str(raw.get("symbol", "")),
                side=str(raw.get("side", "")),
                order_type=str(raw.get("order_type", "")),
                quantity=float(raw.get("quantity", 0.0) or 0.0),
                filled_qty=float(raw.get("filled_qty", raw.get("quantity", 0.0)) or 0.0),
                price=float(raw.get("price", 0.0) or 0.0),
                avg_price=float(raw.get("avg_price", raw.get("price", 0.0)) or 0.0),
                status=mapped,
                created_at=str(raw.get("created_at", "")),
                currency=str(raw.get("currency", self.currency)),
            ))
        return out

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "limit",
        price: float = 0.0,
        **kwargs: Any,
    ) -> OrderResult:
        symbol = str(symbol or "").strip().upper()
        side = str(side or "").lower()
        qty = float(quantity or 0.0)
        if not symbol:
            return OrderResult(False, message="symbol is required", broker_id=self.broker_id)
        if side not in ("buy", "sell"):
            return OrderResult(False, message="side must be buy or sell", broker_id=self.broker_id)
        if qty <= 0:
            return OrderResult(False, message="quantity must be positive", broker_id=self.broker_id)

        account = self._account()
        positions = account.setdefault("positions", {})
        pos = dict(positions.get(symbol) or {})
        current_price = float(price or pos.get("current_price", pos.get("cost_price", 0.0)) or 0.0)
        if current_price <= 0:
            return OrderResult(False, message="paper order requires a positive price", broker_id=self.broker_id)

        cash = float(account.get("cash", 0.0) or 0.0)
        notional = qty * current_price
        existing_qty = float(pos.get("quantity", 0.0) or 0.0)
        existing_cost = float(pos.get("cost_price", current_price) or current_price)

        if side == "buy":
            if notional > cash:
                return OrderResult(False, message="paper cash insufficient", broker_id=self.broker_id)
            new_qty = existing_qty + qty
            new_cost = ((existing_qty * existing_cost) + notional) / new_qty if new_qty > 0 else current_price
            pos.update({"quantity": new_qty, "cost_price": new_cost, "current_price": current_price})
            account["cash"] = cash - notional
            positions[symbol] = pos
        else:
            if qty > existing_qty:
                return OrderResult(False, message="paper position insufficient", broker_id=self.broker_id)
            new_qty = existing_qty - qty
            account["cash"] = cash + notional
            if new_qty <= 0:
                positions.pop(symbol, None)
            else:
                pos.update({"quantity": new_qty, "cost_price": existing_cost, "current_price": current_price})
                positions[symbol] = pos

        order_id = _now_id("paper")
        account.setdefault("orders", []).append({
            "order_id": order_id,
            "symbol": symbol,
            "side": side,
            "order_type": order_type,
            "quantity": qty,
            "filled_qty": qty,
            "price": current_price,
            "avg_price": current_price,
            "status": "filled",
            "created_at": int(time.time()),
            "currency": str(account.get("currency", self.currency)),
        })
        self._save_account(account)
        return OrderResult(True, order_id=order_id, message="paper order filled", broker_id=self.broker_id)


def reset_paper_account(broker_id: str = "paper_main", starting_cash: float = 100000.0, currency: str = "USD") -> None:
    PaperBroker(broker_id, {
        "id": broker_id,
        "type": "paper",
        "label": "Aria 仿盘账户",
        "starting_cash": starting_cash,
        "currency": currency,
    }).reset(starting_cash=starting_cash, currency=currency)
