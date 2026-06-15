"""
brokers/cn/xtquant_broker.py — 迅投 XTQuant 适配器
====================================================
支持券商：中信证券、华鑫证券、浙商证券（通过迅投QMT平台）

安装：pip install xtquant
文档：https://dict.thinktrader.net/nativeApi/

配置示例::

    {
      "id":         "xt_main",
      "type":       "xtquant",
      "label":      "中信主账户",
      "account_id": "1234567890",
      "path":       "C:\\国金QMT交易端模拟\\userdata_mini"
    }
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BrokerBase, AccountInfo, Position, Order, OrderResult


class XTQuantBroker(BrokerBase):
    broker_type = "xtquant"
    broker_name = "迅投 XTQuant"
    market      = "CN"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._account_id = config.get("account_id", "")
        self._path       = config.get("path", "")
        self._xt_trader  = None

    def connect(self) -> bool:
        try:
            from xtquant.xttrader import XtQuantTrader
            from xtquant import xtdata
            path = self._path or "."
            trader = XtQuantTrader(path, int(self.config.get("session_id", 1)))
            conn = trader.connect()
            if conn != 0:
                raise RuntimeError(f"XTQuant 连接失败: code={conn}")
            trader.subscribe_position(self._account_id)
            trader.subscribe_order(self._account_id)
            trader.subscribe_trade(self._account_id)
            self._xt_trader = trader
            self._connected = True
            return True
        except ImportError:
            raise ImportError("xtquant 未安装。请运行: pip install xtquant")
        except Exception as e:
            self._connected = False
            raise RuntimeError(f"XTQuant 连接失败: {e}") from e

    def disconnect(self) -> None:
        if self._xt_trader:
            try:
                self._xt_trader.disconnect()
            except Exception:
                pass
        self._xt_trader = None
        self._connected = False

    def account_info(self) -> AccountInfo:
        self._require_connected()
        acct = self._xt_trader.query_stock_asset(self._account_id)
        if not acct:
            raise RuntimeError("XTQuant 查询账户资金失败")
        # XTQuant StockAsset fields
        total   = float(getattr(acct, "total_asset", 0))
        cash    = float(getattr(acct, "cash", 0))
        frozen  = float(getattr(acct, "frozen_cash", 0))
        mv      = float(getattr(acct, "market_value", 0))
        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=self._account_id,
            currency="CNY",
            total_assets=total,
            cash=cash,
            market_value=mv,
            frozen=frozen,
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        raw = self._xt_trader.query_stock_positions(self._account_id)
        result = []
        for p in (raw or []):
            cost  = float(getattr(p, "open_price",    0))
            price = float(getattr(p, "market_price",  0))
            qty   = float(getattr(p, "volume",        0))
            avail = float(getattr(p, "can_use_volume",0))
            mv    = float(getattr(p, "market_value",  0))
            pnl   = float(getattr(p, "open_pnl",      0))
            sym   = str(getattr(p, "stock_code", ""))
            name  = str(getattr(p, "stock_name", ""))
            pnl_pct = (pnl / (mv - pnl) * 100) if (mv - pnl) > 0 else 0.0
            result.append(Position(
                symbol=sym, name=name,
                quantity=qty, available_qty=avail,
                cost_price=cost, current_price=price,
                market_value=mv, pnl=pnl, pnl_pct=pnl_pct,
                currency="CNY", market="a_share",
            ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        raw = self._xt_trader.query_stock_orders(self._account_id)
        result = []
        for o in (raw or [])[:limit]:
            raw_status = int(getattr(o, "order_status", 0))
            mapped = _xt_order_status(raw_status)
            if status != "all" and mapped != status:
                continue
            side = "buy" if int(getattr(o, "order_type", 23)) in (23, 90) else "sell"
            result.append(Order(
                order_id=str(getattr(o, "order_id", "")),
                symbol=str(getattr(o, "stock_code", "")),
                name=str(getattr(o, "stock_name", "")),
                side=side,
                order_type="limit",
                quantity=float(getattr(o, "order_volume", 0)),
                filled_qty=float(getattr(o, "traded_volume", 0)),
                price=float(getattr(o, "price", 0)),
                avg_price=float(getattr(o, "traded_price", 0)),
                status=mapped,
                created_at=str(getattr(o, "order_time", "")),
                currency="CNY",
            ))
        return result

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "limit", price: float = 0.0, **kwargs) -> OrderResult:
        self._require_connected()
        from xtquant.xttype import StockOrderType, StockPriceType
        xt_type  = 23 if side == "buy" else 24    # XTQuant buy/sell constants
        price_tp = StockPriceType.LATEST if order_type == "market" else StockPriceType.FIX
        oid = self._xt_trader.order_stock(
            self._account_id, symbol, xt_type, int(quantity), price_tp, price,
            strategy_name="aria_code", order_remark="",
        )
        if oid == -1:
            return OrderResult(success=False, message="XTQuant 下单失败（返回 -1）", broker_id=self.broker_id)
        return OrderResult(success=True, order_id=str(oid), broker_id=self.broker_id)

    def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        ret = self._xt_trader.cancel_order_stock(self._account_id, int(order_id))
        return ret == 0

    def _require_connected(self):
        if not self._connected or not self._xt_trader:
            raise RuntimeError("XTQuant 未连接，请先调用 connect()")


def _xt_order_status(code: int) -> str:
    _MAP = {
        48: "open", 49: "open", 50: "open",
        51: "filled", 52: "partial",
        53: "cancelled", 54: "cancelled", 55: "cancelled",
    }
    return _MAP.get(code, "open")
