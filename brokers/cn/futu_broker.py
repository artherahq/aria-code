"""
brokers/cn/futu_broker.py — 富途牛牛 OpenAPI 适配器
=====================================================
支持市场：港股、美股、A股（通过 OpenD 网关）

安装：pip install futu-api
文档：https://openapi.futunn.com/futu-api-doc/

配置示例::

    {
      "id":     "futu_hk",
      "type":   "futu",
      "label":  "富途港股",
      "host":   "127.0.0.1",
      "port":   11111,
      "market": "HK",
      "trd_env": "REAL"
    }

注意：需在本机启动 Futu OpenD 进程。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BrokerBase, AccountInfo, Position, Order, OrderResult


class FutuBroker(BrokerBase):
    broker_type = "futu"
    broker_name = "富途牛牛"
    market      = "HK"

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._host    = config.get("host", "127.0.0.1")
        self._port    = int(config.get("port", 11111))
        self._market  = config.get("market", "HK").upper()
        self._trd_env = config.get("trd_env", "REAL").upper()
        self._quote_ctx = None
        self._trd_ctx   = None
        self.market = self._market

    def connect(self) -> bool:
        try:
            import futu as ft
        except ImportError:
            raise ImportError("futu-api 未安装。请运行: pip install futu-api  并启动 Futu OpenD")

        try:
            self._quote_ctx = ft.OpenQuoteContext(host=self._host, port=self._port)
            trd_market = {
                "HK":  ft.TrdMarket.HK,
                "US":  ft.TrdMarket.US,
                "CN":  ft.TrdMarket.CN,
                "A":   ft.TrdMarket.CN,
            }.get(self._market, ft.TrdMarket.HK)
            trd_env = ft.TrdEnv.REAL if self._trd_env == "REAL" else ft.TrdEnv.SIMULATE
            self._trd_ctx = ft.OpenSecTradeContext(
                filter_trdmarket=trd_market,
                host=self._host, port=self._port,
                security_firm=ft.SecurityFirm.FUTUINC,
            )
            self._ft      = ft
            self._trd_env_val = trd_env
            self._connected   = True
            return True
        except Exception as e:
            raise RuntimeError(f"富途 OpenAPI 连接失败: {e}") from e

    def disconnect(self) -> None:
        for ctx in (self._quote_ctx, self._trd_ctx):
            try:
                if ctx:
                    ctx.close()
            except Exception:
                pass
        self._connected = False

    def account_info(self) -> AccountInfo:
        self._require_connected()
        ret, data = self._trd_ctx.accinfo_query(trd_env=self._trd_env_val)
        if ret != self._ft.RET_OK:
            raise RuntimeError(f"富途账户查询失败: {data}")
        row = data.iloc[0]
        currency = "HKD" if self._market == "HK" else "USD" if self._market == "US" else "CNY"
        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=str(row.get("acc_id", "")),
            currency=currency,
            total_assets=float(row.get("total_assets", 0)),
            cash=float(row.get("cash", 0)),
            market_value=float(row.get("market_val", 0)),
            frozen=float(row.get("frozen_cash", 0)),
            pnl_today=float(row.get("today_profit", 0)),
            pnl_pct=float(row.get("today_profit_ratio", 0)) * 100,
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        ret, data = self._trd_ctx.position_list_query(trd_env=self._trd_env_val)
        if ret != self._ft.RET_OK:
            raise RuntimeError(f"富途持仓查询失败: {data}")
        result = []
        currency = "HKD" if self._market == "HK" else "USD" if self._market == "US" else "CNY"
        for _, row in data.iterrows():
            sym  = str(row.get("code", ""))
            name = str(row.get("stock_name", ""))
            qty  = float(row.get("qty", 0))
            avail= float(row.get("can_sell_qty", 0))
            cost = float(row.get("cost_price", 0))
            price= float(row.get("price",      0))
            mv   = float(row.get("market_val", 0))
            pnl  = float(row.get("unrealized_pl", 0))
            pnl_pct = float(row.get("unrealized_pl_ratio", 0)) * 100
            result.append(Position(
                symbol=sym, name=name,
                quantity=qty, available_qty=avail,
                cost_price=cost, current_price=price,
                market_value=mv, pnl=pnl, pnl_pct=pnl_pct,
                currency=currency, market=self._market.lower(),
            ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        ret, data = self._trd_ctx.order_list_query(trd_env=self._trd_env_val)
        if ret != self._ft.RET_OK:
            raise RuntimeError(f"富途订单查询失败: {data}")
        result = []
        for _, row in data.head(limit).iterrows():
            side     = "buy" if "BUY" in str(row.get("trd_side", "")).upper() else "sell"
            raw_st   = str(row.get("order_status", ""))
            mapped   = _futu_order_status(raw_st)
            if status != "all" and mapped != status:
                continue
            result.append(Order(
                order_id=str(row.get("order_id", "")),
                symbol=str(row.get("code", "")),
                name=str(row.get("stock_name", "")),
                side=side,
                order_type=str(row.get("order_type", "")).lower(),
                quantity=float(row.get("qty", 0)),
                filled_qty=float(row.get("dealt_qty", 0)),
                price=float(row.get("price", 0)),
                avg_price=float(row.get("dealt_avg_price", 0)),
                status=mapped,
                created_at=str(row.get("create_time", "")),
            ))
        return result

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "limit", price: float = 0.0, **kwargs) -> OrderResult:
        self._require_connected()
        ft = self._ft
        trd_side = ft.TrdSide.BUY if side == "buy" else ft.TrdSide.SELL
        ot = ft.OrderType.NORMAL if order_type == "limit" else ft.OrderType.MARKET
        ret, data = self._trd_ctx.place_order(
            price=price, qty=quantity, code=symbol,
            trd_side=trd_side, order_type=ot,
            trd_env=self._trd_env_val,
        )
        if ret != ft.RET_OK:
            return OrderResult(success=False, message=str(data), broker_id=self.broker_id)
        oid = str(data.iloc[0].get("order_id", ""))
        return OrderResult(success=True, order_id=oid, broker_id=self.broker_id)

    def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        ret, _ = self._trd_ctx.modify_order(
            modify_order_op=self._ft.ModifyOrderOp.CANCEL,
            order_id=int(order_id), qty=0, price=0,
            trd_env=self._trd_env_val,
        )
        return ret == self._ft.RET_OK

    def _require_connected(self):
        if not self._connected or not self._trd_ctx:
            raise RuntimeError("富途 OpenAPI 未连接，请先调用 connect()")


def _futu_order_status(raw: str) -> str:
    raw = raw.upper()
    if "FILLED_ALL" in raw or "已全部成交" in raw:
        return "filled"
    if "FILLED_PART" in raw or "部分" in raw:
        return "partial"
    if "CANCELLED" in raw or "已撤" in raw:
        return "cancelled"
    return "open"
