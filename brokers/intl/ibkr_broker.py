"""
brokers/intl/ibkr_broker.py — Interactive Brokers 适配器
==========================================================
通过 ib_insync 连接 TWS 或 IB Gateway。

安装：pip install ib_insync
文档：https://ib-insync.readthedocs.io/

配置示例::

    {
      "id":        "ibkr_us",
      "type":      "ibkr",
      "label":     "盈透美股",
      "host":      "127.0.0.1",
      "port":      7496,
      "client_id": 1,
      "readonly":  false
    }

端口说明：
    TWS 实盘:  7496
    TWS 模拟:  7497
    Gateway 实盘: 4001
    Gateway 模拟: 4002
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BrokerBase, AccountInfo, Position, Order, OrderResult


class IBKRBroker(BrokerBase):
    broker_type = "ibkr"
    broker_name = "Interactive Brokers"
    market      = "US"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._host      = config.get("host",      "127.0.0.1")
        self._port      = int(config.get("port",  7496))
        self._client_id = int(config.get("client_id", 1))
        self._readonly  = bool(config.get("readonly", False))
        self._ib        = None

    def connect(self) -> bool:
        try:
            from ib_insync import IB
        except ImportError:
            raise ImportError("ib_insync 未安装。请运行: pip install ib_insync  并启动 TWS / IB Gateway")

        try:
            ib = IB()
            ib.connect(self._host, self._port, clientId=self._client_id, readonly=self._readonly)
            self._ib        = ib
            self._connected = True
            return True
        except Exception as e:
            raise RuntimeError(f"IBKR 连接失败: {e}") from e

    def disconnect(self) -> None:
        if self._ib:
            try:
                self._ib.disconnect()
            except Exception:
                pass
        self._connected = False

    def account_info(self) -> AccountInfo:
        self._require_connected()
        try:
            summary = {v.tag: v.value for v in self._ib.accountSummary()}
            account_id = summary.get("AccountCode", self._ib.managedAccounts()[0] if self._ib.managedAccounts() else "")
            currency = summary.get("BaseCurrency", "USD")
            netliq   = float(summary.get("NetLiquidation", 0))
            cash     = float(summary.get("TotalCashValue",  0))
            mv       = float(summary.get("GrossPositionValue", 0))
            pnl      = float(summary.get("UnrealizedPnL",   0))
        except Exception as e:
            raise RuntimeError(f"IBKR 账户查询失败: {e}") from e

        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=account_id,
            currency=currency,
            total_assets=netliq,
            cash=cash,
            market_value=mv,
            pnl_total=pnl,
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        try:
            raw = self._ib.positions()
        except Exception as e:
            raise RuntimeError(f"IBKR 持仓查询失败: {e}") from e

        # Request market data for all contracts in batch to get current prices
        _tick_map: dict = {}
        try:
            contracts = [p.contract for p in (raw or [])]
            if contracts:
                tickers = self._ib.reqTickers(*contracts)
                for tk in tickers:
                    _sym = tk.contract.symbol
                    # Use last price, fall back to close, then mark price
                    _px = tk.last or tk.close or tk.marketPrice() or 0.0
                    if _px and _px > 0:
                        _tick_map[_sym] = float(_px)
        except Exception:
            pass  # tick fetch best-effort; fall back to cost-based estimates below

        result = []
        for p in (raw or []):
            contract = p.contract
            sym  = contract.symbol
            qty  = float(p.position)
            cost = float(p.avgCost)
            price = _tick_map.get(sym, 0.0)
            # If tick fetch failed, estimate from unrealizedPNL + cost basis
            if price == 0.0 and qty != 0 and cost > 0:
                upnl = float(getattr(p, "unrealizedPNL", 0))
                price = cost + upnl / qty
                price = max(price, 0.0)
            mv   = qty * price
            pnl  = float(getattr(p, "unrealizedPNL", 0))
            currency = contract.currency or "USD"
            result.append(Position(
                symbol=sym, name=contract.localSymbol or sym,
                quantity=qty, available_qty=qty,
                cost_price=cost, current_price=price,
                market_value=mv, pnl=pnl,
                pnl_pct=round(pnl / (cost * qty) * 100, 2) if cost and qty else 0.0,
                currency=currency, market="us",
            ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        try:
            raw = self._ib.openOrders() + self._ib.reqCompletedOrders(apiOnly=False)
        except Exception as e:
            raise RuntimeError(f"IBKR 订单查询失败: {e}") from e

        result = []
        for trade in list(raw or [])[:limit]:
            order    = trade.order
            contract = trade.contract
            side     = "buy" if order.action.upper() == "BUY" else "sell"
            mapped   = _ibkr_order_status(trade.orderStatus.status)
            if status != "all" and mapped != status:
                continue
            result.append(Order(
                order_id=str(order.orderId),
                symbol=contract.symbol,
                name=contract.localSymbol or contract.symbol,
                side=side,
                order_type=order.orderType.lower(),
                quantity=float(order.totalQuantity),
                filled_qty=float(trade.orderStatus.filled),
                price=float(getattr(order, "lmtPrice", 0)),
                avg_price=float(trade.orderStatus.avgFillPrice),
                status=mapped,
                currency=contract.currency or "USD",
            ))
        return result

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "limit", price: float = 0.0, **kwargs) -> OrderResult:
        if self._readonly:
            return OrderResult(success=False, message="IBKR 账户配置为只读模式", broker_id=self.broker_id)
        self._require_connected()
        try:
            from ib_insync import Stock, MarketOrder, LimitOrder
            contract = Stock(symbol, "SMART", "USD")
            if order_type == "market":
                order = MarketOrder(action=side.upper(), totalQuantity=int(quantity))
            else:
                order = LimitOrder(action=side.upper(), totalQuantity=int(quantity), lmtPrice=price)
            trade = self._ib.placeOrder(contract, order)
            return OrderResult(success=True, order_id=str(trade.order.orderId), broker_id=self.broker_id)
        except Exception as e:
            return OrderResult(success=False, message=str(e), broker_id=self.broker_id)

    def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        try:
            open_trades = self._ib.openTrades()
            for t in open_trades:
                if str(t.order.orderId) == order_id:
                    self._ib.cancelOrder(t.order)
                    return True
            return False
        except Exception:
            return False

    def _require_connected(self):
        if not self._connected or not self._ib:
            raise RuntimeError("IBKR 未连接，请先调用 connect()")


def _ibkr_order_status(raw: str) -> str:
    raw = raw.lower()
    if raw == "filled":
        return "filled"
    if "partial" in raw:
        return "partial"
    if raw in ("cancelled", "inactive"):
        return "cancelled"
    return "open"
