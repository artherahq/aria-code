"""
brokers/cn/easytrader_broker.py — EasyTrader 适配器
=====================================================
支持券商（通过客户端界面自动化）：
    同花顺(ths)、通达信(tdx)、华泰(huatai)、国泰君安(guojun)、
    银河证券(yh)、平安证券(pa)、招商证券(zszq)、雪球模拟(xq)

安装：pip install easytrader
文档：https://github.com/shidenggui/easytrader

配置示例::

    {
      "id":           "ht_main",
      "type":         "easytrader",
      "label":        "华泰账户",
      "broker_name":  "huatai",
      "exe_path":     "C:\\华泰证券\\xiadan.exe",
      "comm_password": "可选"
    }

注意：EasyTrader 通过操控客户端窗口，仅支持 Windows。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..base import BrokerBase, AccountInfo, Position, Order, OrderResult


class EasyTraderBroker(BrokerBase):
    broker_type = "easytrader"
    broker_name = "EasyTrader"
    market      = "CN"

    _BROKER_NAMES = {
        "huatai": "华泰证券",
        "guojun": "国泰君安",
        "ths":    "同花顺",
        "tdx":    "通达信",
        "yh":     "银河证券",
        "zszq":   "招商证券",
        "xq":     "雪球模拟",
    }

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        super().__init__(broker_id, config)
        self._broker_name  = config.get("broker_name", "ths")
        self._exe_path     = config.get("exe_path", "")
        self._comm_pwd     = config.get("comm_password", "")
        self._client       = None
        self.broker_name   = self._BROKER_NAMES.get(self._broker_name, self._broker_name)

    def connect(self) -> bool:
        try:
            import easytrader
        except ImportError:
            raise ImportError("easytrader 未安装。请运行: pip install easytrader")

        import sys
        if sys.platform != "win32":
            raise RuntimeError("EasyTrader 仅支持 Windows 系统")

        try:
            client = easytrader.use(self._broker_name)
            if self._exe_path:
                client.prepare(self._exe_path, comm_password=self._comm_pwd or None)
            else:
                client.prepare(comm_password=self._comm_pwd or None)
            self._client    = client
            self._connected = True
            return True
        except Exception as e:
            raise RuntimeError(f"EasyTrader 连接失败: {e}") from e

    def account_info(self) -> AccountInfo:
        self._require_connected()
        try:
            bal = self._client.balance
            total = float(bal.get("总资产", bal.get("资产", 0)))
            cash  = float(bal.get("可用金额", bal.get("可用资金", 0)))
            mv    = float(bal.get("证券市值", 0))
            frozen= float(bal.get("冻结金额", 0))
            pnl   = float(bal.get("盈亏金额", bal.get("参考盈亏", 0)))
        except Exception as e:
            raise RuntimeError(f"EasyTrader 账户资金查询失败: {e}") from e

        return AccountInfo(
            broker_id=self.broker_id,
            broker_type=self.broker_type,
            label=self.label,
            account_id=self.config.get("account_id", ""),
            currency="CNY",
            total_assets=total,
            cash=cash,
            market_value=mv,
            frozen=frozen,
            pnl_today=pnl,
        )

    def positions(self) -> List[Position]:
        self._require_connected()
        try:
            raw = self._client.position
        except Exception as e:
            raise RuntimeError(f"EasyTrader 持仓查询失败: {e}") from e

        result = []
        for row in (raw or []):
            sym   = str(row.get("证券代码", row.get("股票代码", "")))
            name  = str(row.get("证券名称", row.get("股票名称", "")))
            qty   = float(row.get("持仓数量", row.get("持股数量", 0)))
            avail = float(row.get("可用数量", row.get("可卖数量", 0)))
            cost  = float(row.get("成本价",   row.get("持仓成本", 0)))
            price = float(row.get("当前价",   row.get("最新价",   0)))
            mv    = float(row.get("证券市值", row.get("参考市值",  0)))
            pnl   = float(row.get("盈亏金额", row.get("参考盈亏",  0)))
            pnl_pct = float(row.get("盈亏比例(%)", 0))
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
        try:
            raw = self._client.today_entrusts
        except Exception as e:
            raise RuntimeError(f"EasyTrader 订单查询失败: {e}") from e

        result = []
        for row in (raw or [])[:limit]:
            op = str(row.get("操作", ""))
            side   = "buy" if "买" in op else "sell"
            raw_st = str(row.get("状态",  row.get("委托状态", "")))
            mapped = _easy_order_status(raw_st)
            if status != "all" and mapped != status:
                continue
            result.append(Order(
                order_id=str(row.get("委托编号", row.get("合同编号", ""))),
                symbol=str(row.get("证券代码", "")),
                name=str(row.get("证券名称", "")),
                side=side,
                order_type="limit",
                quantity=float(row.get("委托数量", 0)),
                filled_qty=float(row.get("成交数量", 0)),
                price=float(row.get("委托价格", row.get("委托价",0))),
                avg_price=float(row.get("成交均价", row.get("成交价",0))),
                status=mapped,
                created_at=str(row.get("委托时间", "")),
                currency="CNY",
            ))
        return result

    def place_order(self, symbol: str, side: str, quantity: float,
                    order_type: str = "limit", price: float = 0.0, **kwargs) -> OrderResult:
        self._require_connected()
        try:
            if side == "buy":
                result = self._client.buy(security=symbol, price=price, amount=int(quantity))
            else:
                result = self._client.sell(security=symbol, price=price, amount=int(quantity))
            oid = str(result.get("entrust_no", result.get("委托编号", "")))
            return OrderResult(success=True, order_id=oid, broker_id=self.broker_id)
        except Exception as e:
            return OrderResult(success=False, message=str(e), broker_id=self.broker_id)

    def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        try:
            self._client.cancel_entrust(order_id)
            return True
        except Exception:
            return False

    def _require_connected(self):
        if not self._connected or not self._client:
            raise RuntimeError("EasyTrader 未连接，请先调用 connect()")


def _easy_order_status(raw: str) -> str:
    if any(x in raw for x in ("已成", "全部成交")):
        return "filled"
    if any(x in raw for x in ("部分成交",)):
        return "partial"
    if any(x in raw for x in ("已撤", "撤单", "废单")):
        return "cancelled"
    return "open"
