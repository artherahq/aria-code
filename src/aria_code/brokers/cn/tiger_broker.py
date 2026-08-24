"""
brokers/cn/tiger_broker.py — 老虎证券 OpenAPI 适配器
======================================================
支持市场：美股、港股、A股

安装：pip install tigeropen
文档：https://quant.tigerfintech.com/

配置示例::

    {
      "id":               "tiger_us",
      "type":             "tiger",
      "label":            "老虎美股",
      "tiger_id":         "YOUR_TIGER_ID",
      "private_key_path": "~/.arthera/tiger_rsa.pem",
      "account":          "YOUR_ACCOUNT_ID",
      "sandbox":          false
    }
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BrokerBase, AccountInfo, Position, Order, OrderResult


class TigerBroker(BrokerBase):
    broker_type = "tiger"
    broker_name = "老虎证券"
    market      = "US"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._tiger_id       = config.get("tiger_id", "")
        self._private_key_path = config.get("private_key_path", "")
        self._account        = config.get("account", "")
        self._sandbox        = bool(config.get("sandbox", False))
        self._client_config  = None
        self._trade_client   = None
        self._quote_client   = None

    def connect(self) -> bool:
        try:
            from tigeropen.tiger_open_config import TigerOpenClientConfig
            from tigeropen.common.consts import TigerApiConstants
            from tigeropen.trade.trade_client import TradeClient
            from tigeropen.quote.quote_client import QuoteClient
        except ImportError:
            raise ImportError("tigeropen 未安装。请运行: pip install tigeropen")

        import os
        pk_path = os.path.expanduser(self._private_key_path)
        if not os.path.exists(pk_path):
            raise FileNotFoundError(f"老虎证券私钥文件不存在: {pk_path}")

        try:
            cfg = TigerOpenClientConfig(sandbox_debug=self._sandbox)
            cfg.tiger_id    = self._tiger_id
            cfg.private_key = open(pk_path).read()
            cfg.account     = self._account
            self._client_config  = cfg
            self._trade_client   = TradeClient(cfg)
            self._quote_client   = QuoteClient(cfg)
            # 测试连接
            self._trade_client.get_managed_accounts()
            self._connected = True
            return True
        except Exception as e:
            raise RuntimeError(f"老虎证券连接失败: {e}") from e

    def account_info(self) -> AccountInfo:
        self._require_connected()
        try:
            assets = self._trade_client.get_assets(account=self._account)
            a = assets[0] if assets else None
            if not a:
                raise RuntimeError("老虎证券返回空账户数据")
            return AccountInfo(
                broker_id=self.broker_id,
                broker_type=self.broker_type,
                label=self.label,
                account_id=self._account,
                currency=str(getattr(a, "currency", "USD")),
                total_assets=float(getattr(a, "net_liquidation", 0)),
                cash=float(getattr(a, "cash_balance", 0)),
                market_value=float(getattr(a, "gross_position_value", 0)),
                pnl_today=float(getattr(a, "realized_pnl", 0)),
                pnl_total=float(getattr(a, "unrealized_pnl", 0)),
            )
        except Exception as e:
            raise RuntimeError(f"老虎证券账户查询失败: {e}") from e

    def positions(self) -> List[Position]:
        self._require_connected()
        try:
            raw = self._trade_client.get_positions(account=self._account)
        except Exception as e:
            raise RuntimeError(f"老虎证券持仓查询失败: {e}") from e

        result = []
        for p in (raw or []):
            sym   = str(getattr(p, "contract", {}).symbol if hasattr(p, "contract") else getattr(p, "symbol", ""))
            qty   = float(getattr(p, "quantity",         0))
            avail = float(getattr(p, "available_qty",    qty))
            cost  = float(getattr(p, "average_cost",     0))
            price = float(getattr(p, "market_price",     0))
            mv    = float(getattr(p, "position_value",   0))
            pnl   = float(getattr(p, "unrealized_pnl",   0))
            pnl_pct = float(getattr(p, "unrealized_pnl_ratio", 0)) * 100
            currency = str(getattr(p, "currency", "USD"))
            result.append(Position(
                symbol=sym, name=sym,
                quantity=qty, available_qty=avail,
                cost_price=cost, current_price=price,
                market_value=mv, pnl=pnl, pnl_pct=pnl_pct,
                currency=currency, market="us",
            ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        try:
            from tigeropen.common.consts import OrderStatus
            raw = self._trade_client.get_orders(account=self._account, limit=limit)
        except Exception as e:
            raise RuntimeError(f"老虎证券订单查询失败: {e}") from e

        result = []
        for o in (raw or []):
            side   = "buy" if str(getattr(o, "action", "")).upper() == "BUY" else "sell"
            mapped = _tiger_order_status(str(getattr(o, "status", "")))
            if status != "all" and mapped != status:
                continue
            contract = getattr(o, "contract", None)
            sym = str(contract.symbol if contract else getattr(o, "symbol", ""))
            result.append(Order(
                order_id=str(getattr(o, "id", getattr(o, "order_id", ""))),
                symbol=sym, name=sym,
                side=side,
                order_type=str(getattr(o, "order_type", "limit")).lower(),
                quantity=float(getattr(o, "quantity", 0)),
                filled_qty=float(getattr(o, "filled", 0)),
                price=float(getattr(o, "limit_price", 0)),
                avg_price=float(getattr(o, "avg_fill_price", 0)),
                status=mapped,
                created_at=str(getattr(o, "order_time", "")),
                currency=str(getattr(o, "currency", "USD")),
            ))
        return result

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "limit", price: float = 0.0, **kwargs) -> OrderResult:
        self._require_connected()
        try:
            from tigeropen.trade.domain.order import market_order, limit_order
            from tigeropen.common.consts import Currency, Market

            if order_type == "market":
                order = market_order(
                    account=self._account, symbol=symbol,
                    action=side.upper(), quantity=int(quantity),
                )
            else:
                order = limit_order(
                    account=self._account, symbol=symbol,
                    action=side.upper(), quantity=int(quantity), limit_price=price,
                )
            ret = self._trade_client.place_order(order)
            return OrderResult(success=True, order_id=str(ret), broker_id=self.broker_id)
        except Exception as e:
            return OrderResult(success=False, message=str(e), broker_id=self.broker_id)

    def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        try:
            self._trade_client.cancel_order(account=self._account, id=int(order_id))
            return True
        except Exception:
            return False

    def _require_connected(self):
        if not self._connected:
            raise RuntimeError("老虎证券未连接，请先调用 connect()")


def _tiger_order_status(raw: str) -> str:
    raw = raw.upper()
    if raw in ("FILLED",):
        return "filled"
    if raw in ("PARTIALLY_FILLED",):
        return "partial"
    if raw in ("CANCELLED", "EXPIRED", "REJECTED"):
        return "cancelled"
    return "open"
