"""
brokers/cn/longbridge_broker.py — 长桥证券 OpenAPI 适配器
===========================================================
支持市场：港股、美股、A股

安装：pip install longbridge
文档：https://open.longportapp.com/

配置示例::

    {
      "id":           "lb_main",
      "type":         "longbridge",
      "label":        "长桥主账户",
      "app_key":      "YOUR_APP_KEY",
      "app_secret":   "YOUR_APP_SECRET",
      "access_token": "YOUR_ACCESS_TOKEN"
    }
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BrokerBase, AccountInfo, Position, Order, OrderResult


class LongbridgeBroker(BrokerBase):
    broker_type = "longbridge"
    broker_name = "长桥证券"
    market      = "GLOBAL"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._app_key      = config.get("app_key", "")
        self._app_secret   = config.get("app_secret", "")
        self._access_token = config.get("access_token", "")
        self._trade_ctx    = None
        self._quote_ctx    = None

    def connect(self) -> bool:
        try:
            from longbridge.openapi import TradeContext, QuoteContext, Config
        except ImportError:
            raise ImportError("longbridge 未安装。请运行: pip install longbridge")

        try:
            cfg = Config(
                app_key=self._app_key,
                app_secret=self._app_secret,
                access_token=self._access_token,
            )
            self._trade_ctx = TradeContext(cfg)
            self._quote_ctx = QuoteContext(cfg)
            # 测试连接
            self._trade_ctx.account_balance()
            self._connected = True
            return True
        except Exception as e:
            raise RuntimeError(f"长桥证券连接失败: {e}") from e

    def account_info(self) -> AccountInfo:
        self._require_connected()
        try:
            resp = self._trade_ctx.account_balance()
            bal  = resp[0] if resp else None
            if not bal:
                raise RuntimeError("长桥账户余额为空")
            currency = str(getattr(bal, "currency", "HKD"))
            total = float(getattr(bal, "total_cash", 0))
            cash  = float(getattr(bal, "available_cash", 0))
            mv    = float(getattr(bal, "market_value", 0))
            pnl   = float(getattr(bal, "unrealized_pnl", 0))
        except Exception as e:
            raise RuntimeError(f"长桥账户查询失败: {e}") from e

        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=self.config.get("account_id", self._app_key[-6:]),
            currency=currency,
            total_assets=total + mv,
            cash=cash,
            market_value=mv,
            pnl_total=pnl,
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        try:
            raw = self._trade_ctx.stock_positions()
        except Exception as e:
            raise RuntimeError(f"长桥持仓查询失败: {e}") from e

        result = []
        for ch in getattr(raw, "channels", []):
            for p in getattr(ch, "positions", []):
                sym   = str(getattr(p, "symbol", ""))
                name  = str(getattr(p, "symbol_name", ""))
                qty   = float(getattr(p, "quantity", 0))
                avail = float(getattr(p, "available_quantity", qty))
                cost  = float(getattr(p, "cost_price", 0))
                price = float(getattr(p, "current_price", 0))
                mv    = float(getattr(p, "market_value", 0))
                pnl   = float(getattr(p, "unrealized_pnl", 0))
                pnl_pct = float(getattr(p, "unrealized_pnl_ratio", 0))
                currency = str(getattr(p, "currency", "HKD"))
                result.append(Position(
                    symbol=sym, name=name,
                    quantity=qty, available_qty=avail,
                    cost_price=cost, current_price=price,
                    market_value=mv, pnl=pnl, pnl_pct=pnl_pct * 100,
                    currency=currency,
                ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        try:
            from longbridge.openapi import OrderStatus as LBOrderStatus
            raw = self._trade_ctx.today_orders()
        except Exception as e:
            raise RuntimeError(f"长桥订单查询失败: {e}") from e

        result = []
        for o in list(raw or [])[:limit]:
            side   = "buy" if "BUY" in str(getattr(o, "side", "")).upper() else "sell"
            mapped = _lb_order_status(str(getattr(o, "status", "")))
            if status != "all" and mapped != status:
                continue
            result.append(Order(
                order_id=str(getattr(o, "order_id", "")),
                symbol=str(getattr(o, "symbol", "")),
                name=str(getattr(o, "stock_name", "")),
                side=side,
                order_type=str(getattr(o, "order_type", "")).lower(),
                quantity=float(getattr(o, "quantity", 0)),
                filled_qty=float(getattr(o, "executed_quantity", 0)),
                price=float(getattr(o, "price", 0)),
                avg_price=float(getattr(o, "executed_price", 0)),
                status=mapped,
                created_at=str(getattr(o, "submitted_at", "")),
                currency=str(getattr(o, "currency", "HKD")),
            ))
        return result

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "limit", price: float = 0.0, **kwargs) -> OrderResult:
        self._require_connected()
        try:
            from longbridge.openapi import OrderSide, OrderType, TimeInForceType
            from decimal import Decimal
            lb_side = OrderSide.Buy if side == "buy" else OrderSide.Sell
            lb_type = OrderType.MO if order_type == "market" else OrderType.LO
            resp = self._trade_ctx.submit_order(
                symbol=symbol,
                order_type=lb_type,
                side=lb_side,
                submitted_quantity=int(quantity),
                time_in_force=TimeInForceType.Day,
                submitted_price=Decimal(str(price)) if price else None,
            )
            oid = str(getattr(resp, "order_id", ""))
            return OrderResult(success=True, order_id=oid, broker_id=self.broker_id)
        except Exception as e:
            return OrderResult(success=False, message=str(e), broker_id=self.broker_id)

    def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        try:
            self._trade_ctx.cancel_order(order_id)
            return True
        except Exception:
            return False

    def _require_connected(self):
        if not self._connected:
            raise RuntimeError("长桥证券未连接，请先调用 connect()")


def _lb_order_status(raw: str) -> str:
    raw = raw.upper()
    if "FILLED" in raw and "PARTIAL" not in raw:
        return "filled"
    if "PARTIAL" in raw:
        return "partial"
    if any(x in raw for x in ("CANCELLED", "EXPIRED", "REJECTED")):
        return "cancelled"
    return "open"
