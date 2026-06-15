"""
brokers/intl/webull_broker.py — Webull 适配器
==============================================
支持市场：美股、港股（非官方 API，以查询为主）

安装：pip install webull
文档：https://github.com/tedchou12/webull

配置示例::

    {
      "id":        "webull_us",
      "type":      "webull",
      "label":     "Webull 美股",
      "username":  "your@email.com",
      "password":  "your_password",
      "device_id": "",
      "mfa":       ""
    }

注意：Webull 为非官方 API，下单功能可能不稳定。
建议仅用于查询（持仓/账户），下单在 App 内操作。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BrokerBase, AccountInfo, Position, Order, OrderResult


class WebullBroker(BrokerBase):
    broker_type = "webull"
    broker_name = "Webull"
    market      = "US"
    read_only   = True          # 默认只读，避免非官方 API 下单风险

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._username  = config.get("username",  "")
        self._password  = config.get("password",  "")
        self._device_id = config.get("device_id", "")
        self._mfa       = config.get("mfa",        "")
        self._wb        = None

    def connect(self) -> bool:
        try:
            from webull import webull as wb_cls
        except ImportError:
            raise ImportError("webull 未安装。请运行: pip install webull")

        try:
            wb = wb_cls()
            if self._device_id:
                wb._set_did(self._device_id)
            wb.login(self._username, self._password, mfa_code=self._mfa or None)
            self._wb        = wb
            self._connected = True
            return True
        except Exception as e:
            raise RuntimeError(f"Webull 登录失败: {e}") from e

    def account_info(self) -> AccountInfo:
        self._require_connected()
        try:
            data = self._wb.get_account()
        except Exception as e:
            raise RuntimeError(f"Webull 账户查询失败: {e}") from e

        total   = float(data.get("netLiquidation",  data.get("totalMarketValue", 0)))
        cash    = float(data.get("cashBalance",     data.get("availableBalance", 0)))
        mv      = float(data.get("totalMarketValue", 0))
        pnl_day = float(data.get("unrealizedProfitLoss", 0))
        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=str(data.get("accountId", "")),
            currency="USD",
            total_assets=total,
            cash=cash,
            market_value=mv,
            pnl_today=pnl_day,
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        try:
            raw = self._wb.get_positions()
        except Exception as e:
            raise RuntimeError(f"Webull 持仓查询失败: {e}") from e

        result = []
        for p in (raw or []):
            ticker = p.get("ticker", {})
            sym    = str(ticker.get("symbol",    p.get("symbol", "")))
            name   = str(ticker.get("name",      ""))
            qty    = float(p.get("position",     p.get("quantity", 0)))
            cost   = float(p.get("costPrice",    p.get("avgCost",  0)))
            price  = float(p.get("lastPrice",    p.get("price",    0)))
            mv     = float(p.get("marketValue",  qty * price))
            pnl    = float(p.get("unrealizedProfitLoss", 0))
            pnl_pct= float(p.get("unrealizedProfitLossRate", 0)) * 100
            result.append(Position(
                symbol=sym, name=name,
                quantity=qty, available_qty=qty,
                cost_price=cost, current_price=price,
                market_value=mv, pnl=pnl, pnl_pct=pnl_pct,
                currency="USD", market="us",
            ))
        return result

    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        self._require_connected()
        try:
            raw = self._wb.get_history_orders(status="All")
        except Exception as e:
            raise RuntimeError(f"Webull 订单查询失败: {e}") from e

        result = []
        for o in list(raw or [])[:limit]:
            side   = "buy" if str(o.get("action", "")).upper() == "BUY" else "sell"
            mapped = _wb_order_status(str(o.get("status", "")))
            if status != "all" and mapped != status:
                continue
            ticker = o.get("ticker", {})
            result.append(Order(
                order_id=str(o.get("orderId", "")),
                symbol=str(ticker.get("symbol", o.get("symbol", ""))),
                name=str(ticker.get("name", "")),
                side=side,
                order_type=str(o.get("orderType", "limit")).lower(),
                quantity=float(o.get("totalQuantity", 0)),
                filled_qty=float(o.get("filledQuantity", 0)),
                price=float(o.get("lmtPrice", 0) or 0),
                avg_price=float(o.get("avgFilledPrice", 0) or 0),
                status=mapped,
                created_at=str(o.get("createTime", "")),
                currency="USD",
            ))
        return result

    def _require_connected(self):
        if not self._connected or not self._wb:
            raise RuntimeError("Webull 未连接，请先调用 connect()")


def _wb_order_status(raw: str) -> str:
    raw = raw.upper()
    if raw == "FILLED":
        return "filled"
    if raw == "PARTIALLY_FILLED":
        return "partial"
    if raw in ("CANCELLED", "EXPIRED", "REJECTED"):
        return "cancelled"
    return "open"
