"""
brokers/intl/alpaca_broker.py — Alpaca Markets 适配器
======================================================
支持市场：美股（实盘 + 模拟盘）

安装：pip install alpaca-py
文档：https://docs.alpaca.markets/

配置示例::

    {
      "id":         "alpaca_paper",
      "type":       "alpaca",
      "label":      "Alpaca 模拟盘",
      "api_key":    "PKxxx",
      "api_secret": "xxx",
      "paper":      true
    }
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BrokerBase, AccountInfo, Position, Order, OrderResult


class AlpacaBroker(BrokerBase):
    broker_type = "alpaca"
    broker_name = "Alpaca Markets"
    market      = "US"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._api_key    = config.get("api_key",    "")
        self._api_secret = config.get("api_secret", "")
        self._paper      = bool(config.get("paper", False))
        self._trading    = None

    def connect(self) -> bool:
        try:
            from alpaca.trading.client import TradingClient
        except ImportError:
            raise ImportError("alpaca-py 未安装。请运行: pip install alpaca-py")

        try:
            client = TradingClient(self._api_key, self._api_secret, paper=self._paper)
            client.get_account()          # 测试连通性
            self._trading   = client
            self._connected = True
            return True
        except Exception as e:
            raise RuntimeError(f"Alpaca 连接失败: {e}") from e

    def account_info(self) -> AccountInfo:
        self._require_connected()
        try:
            a = self._trading.get_account()
        except Exception as e:
            raise RuntimeError(f"Alpaca 账户查询失败: {e}") from e

        equity = float(getattr(a, "equity",        0))
        cash   = float(getattr(a, "cash",           0))
        mv     = float(getattr(a, "long_market_value", 0)) - float(getattr(a, "short_market_value", 0))
        pnl    = float(getattr(a, "unrealized_pl",  0))
        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=str(getattr(a, "account_number", getattr(a, "id", ""))),
            currency="USD",
            total_assets=equity,
            cash=cash,
            market_value=mv,
            pnl_today=float(getattr(a, "unrealized_pl", 0)),
            pnl_total=pnl,
            extra={"buying_power": float(getattr(a, "buying_power", 0)),
                   "paper": self._paper},
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        try:
            raw = self._trading.get_all_positions()
        except Exception as e:
            raise RuntimeError(f"Alpaca 持仓查询失败: {e}") from e

        result = []
        for p in (raw or []):
            sym   = str(getattr(p, "symbol", ""))
            qty   = float(getattr(p, "qty",             0))
            avail = float(getattr(p, "qty_available",   qty))
            cost  = float(getattr(p, "avg_entry_price", 0))
            price = float(getattr(p, "current_price",   0))
            mv    = float(getattr(p, "market_value",    0))
            pnl   = float(getattr(p, "unrealized_pl",   0))
            pnl_pct = float(getattr(p, "unrealized_plpc", 0)) * 100
            result.append(Position(
                symbol=sym, name=sym,
                quantity=qty, available_qty=avail,
                cost_price=cost, current_price=price,
                market_value=mv, pnl=pnl, pnl_pct=pnl_pct,
                currency="USD", market="us",
            ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        try:
            from alpaca.trading.requests import GetOrdersRequest
            from alpaca.trading.enums import QueryOrderStatus
            qstatus = {
                "open":      QueryOrderStatus.OPEN,
                "filled":    QueryOrderStatus.CLOSED,
                "cancelled": QueryOrderStatus.CLOSED,
            }.get(status, QueryOrderStatus.ALL)
            raw = self._trading.get_orders(GetOrdersRequest(status=qstatus, limit=limit))
        except Exception as e:
            raise RuntimeError(f"Alpaca 订单查询失败: {e}") from e

        result = []
        for o in (raw or []):
            side   = "buy" if str(getattr(o, "side", "")).lower() == "buy" else "sell"
            mapped = _alpaca_order_status(str(getattr(o, "status", "")))
            if status not in ("all",) and mapped != status:
                continue
            result.append(Order(
                order_id=str(getattr(o, "id", "")),
                symbol=str(getattr(o, "symbol", "")),
                name=str(getattr(o, "symbol", "")),
                side=side,
                order_type=str(getattr(o, "order_type", "limit")).lower(),
                quantity=float(getattr(o, "qty",         0) or 0),
                filled_qty=float(getattr(o, "filled_qty", 0) or 0),
                price=float(getattr(o, "limit_price",    0) or 0),
                avg_price=float(getattr(o, "filled_avg_price", 0) or 0),
                status=mapped,
                created_at=str(getattr(o, "created_at", "")),
                currency="USD",
            ))
        return result

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "limit", price: float = 0.0, **kwargs) -> OrderResult:
        self._require_connected()
        try:
            from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            alpaca_side = OrderSide.BUY if side == "buy" else OrderSide.SELL
            if order_type == "market":
                req = MarketOrderRequest(
                    symbol=symbol, qty=quantity, side=alpaca_side, time_in_force=TimeInForce.DAY)
            else:
                req = LimitOrderRequest(
                    symbol=symbol, qty=quantity, side=alpaca_side,
                    limit_price=price, time_in_force=TimeInForce.DAY)
            o = self._trading.submit_order(req)
            return OrderResult(success=True, order_id=str(o.id), broker_id=self.broker_id)
        except Exception as e:
            return OrderResult(success=False, message=str(e), broker_id=self.broker_id)

    def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        try:
            self._trading.cancel_order_by_id(order_id)
            return True
        except Exception:
            return False

    def _require_connected(self):
        if not self._connected or not self._trading:
            raise RuntimeError("Alpaca 未连接，请先调用 connect()")


def _alpaca_order_status(raw: str) -> str:
    raw = raw.lower()
    if raw == "filled":
        return "filled"
    if raw == "partially_filled":
        return "partial"
    if raw in ("canceled", "cancelled", "expired", "rejected"):
        return "cancelled"
    return "open"
