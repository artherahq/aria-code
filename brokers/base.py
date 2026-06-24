"""
brokers/base.py — 券商接入统一接口
====================================
所有券商适配器继承 BrokerBase，对外暴露统一 schema，
上层代码不关心底层是 XTQuant / IBKR / Alpaca / 富途 / 老虎。

数据类：
    AccountInfo     账户资金汇总
    Position        持仓明细
    Order           订单记录
    OrderResult     下单结果
    PortfolioSummary 持仓组合摘要

抽象接口：
    connect()           建立连接
    disconnect()        断开连接
    account_info()      账户资金
    positions()         当前持仓
    orders(status)      历史/活跃订单
    place_order(...)    下单（需用户二次确认，不在此层执行）
    cancel_order(id)    撤单
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── 统一数据 Schema ────────────────────────────────────────────────────────────

@dataclass
class AccountInfo:
    broker_id:       str            # 配置中的 id 字段
    broker_type:     str            # xtquant / ibkr / alpaca …
    label:           str            # 用户自定义别名
    account_id:      str            # 账户号（脱敏后展示末4位）
    currency:        str   = "CNY"
    total_assets:    float = 0.0    # 总资产
    cash:            float = 0.0    # 可用现金
    market_value:    float = 0.0    # 持仓市值
    frozen:          float = 0.0    # 冻结资金
    pnl_today:       float = 0.0    # 当日盈亏
    pnl_total:       float = 0.0    # 累计盈亏
    pnl_pct:         float = 0.0    # 持仓收益率(%)
    risk_level:      str   = ""     # low / medium / high
    extra:           Dict[str, Any] = field(default_factory=dict)

    @property
    def masked_account(self) -> str:
        return f"****{self.account_id[-4:]}" if len(self.account_id) > 4 else "****"


@dataclass
class Position:
    symbol:          str
    name:            str   = ""
    quantity:        float = 0.0    # 持仓数量（股/手）
    available_qty:   float = 0.0    # 可卖数量
    cost_price:      float = 0.0    # 持仓均价
    current_price:   float = 0.0    # 最新价
    market_value:    float = 0.0    # 市值
    pnl:             float = 0.0    # 盈亏金额
    pnl_pct:         float = 0.0    # 盈亏比例(%)
    currency:        str   = "CNY"
    market:          str   = ""     # a_share / us / hk / crypto
    extra:           Dict[str, Any] = field(default_factory=dict)


@dataclass
class Order:
    order_id:        str
    symbol:          str
    name:            str   = ""
    side:            str   = ""     # buy / sell
    order_type:      str   = ""     # limit / market / stop
    quantity:        float = 0.0
    filled_qty:      float = 0.0
    price:           float = 0.0    # 委托价
    avg_price:       float = 0.0    # 成交均价
    status:          str   = ""     # open / filled / cancelled / partial
    created_at:      str   = ""
    updated_at:      str   = ""
    currency:        str   = "CNY"
    extra:           Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    success:         bool
    order_id:        str   = ""
    message:         str   = ""
    broker_id:       str   = ""


@dataclass
class PortfolioSummary:
    broker_id:       str
    positions:       List[Position]     = field(default_factory=list)
    total_assets:    float              = 0.0
    total_market_value: float           = 0.0
    cash:            float              = 0.0
    total_pnl:       float              = 0.0
    total_pnl_pct:   float              = 0.0
    top_holding:     Optional[Position] = None
    currency:        str                = "CNY"


# ── 抽象基类 ───────────────────────────────────────────────────────────────────

class BrokerBase(ABC):
    """所有券商适配器的抽象基类。"""

    broker_type: str = "base"       # 子类覆盖: xtquant / ibkr / alpaca …
    broker_name: str = "券商"        # 展示名称
    market:      str = "CN"         # CN / US / HK / GLOBAL
    read_only:   bool = False       # True = 只支持查询，不支持下单

    def __init__(self, broker_id: str, config: Dict[str, Any]):
        self.broker_id = broker_id
        self.config    = config
        self.label     = config.get("label", broker_id)
        self._connected = False

    # ── 连接管理 ──────────────────────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> bool:
        """建立连接。返回 True 表示成功。"""

    def disconnect(self) -> None:
        """断开连接（默认空实现，子类可覆盖）。"""
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── 健康检查 / 自动重连 ────────────────────────────────────────────────────

    def ping(self) -> bool:
        """轻量存活探测。默认信任连接标志；拥有实时 socket/API 的子类**应覆盖**
        此方法做真实探测（如 ib.isConnected()、富途 context keepalive），
        以便检测到静默断开的连接并触发重连。"""
        return self._connected

    def ensure_connected(self, retries: int = 1) -> bool:
        """确保连接可用，断开则自动重连。返回 True 表示可用。

        长驻场景（daemon）应在每次券商操作前调用：FutuOpenD / IB Gateway /
        websocket 掉线时透明重连，而不是静默失败到下一次调用才报错。"""
        if self._connected and self.ping():
            return True
        self._connected = False
        for _ in range(max(1, retries + 1)):
            try:
                if self.connect():
                    return True
            except Exception:
                pass
        return False

    # ── 账户查询 ──────────────────────────────────────────────────────────────

    @abstractmethod
    def account_info(self) -> AccountInfo:
        """返回账户资金汇总。"""

    @abstractmethod
    def positions(self) -> List[Position]:
        """返回当前持仓列表。"""

    @abstractmethod
    def orders(self, status: str = "all", limit: int = 50) -> List[Order]:
        """
        返回订单列表。
        status: "open" | "filled" | "cancelled" | "all"
        """

    # ── 交易操作（需上层二次确认后才调用）──────────────────────────────────────

    def place_order(
        self,
        symbol:     str,
        side:       str,        # "buy" | "sell"
        quantity:   float,
        order_type: str = "limit",
        price:      float = 0.0,
        **kwargs: Any,
    ) -> OrderResult:
        """
        下单接口。默认返回不支持，子类覆盖。
        ⚠️  此方法只应在用户明确确认后被调用。
        """
        if self.read_only:
            return OrderResult(success=False, message=f"{self.broker_name} 为只读模式，不支持下单")
        return OrderResult(success=False, message="此券商暂不支持程序化下单")

    def cancel_order(self, order_id: str) -> bool:
        """撤单。返回 True 表示成功。"""
        return False

    # ── 工具方法 ─────────────────────────────────────────────────────────────

    def portfolio_summary(self) -> PortfolioSummary:
        """构建持仓摘要（默认实现，子类可覆盖）。"""
        try:
            acct = self.account_info()
            pos  = self.positions()
            top  = max(pos, key=lambda p: abs(p.market_value), default=None) if pos else None
            total_pnl = sum(p.pnl for p in pos)
            total_mv  = sum(p.market_value for p in pos)
            pnl_pct   = (total_pnl / (total_mv - total_pnl) * 100) if (total_mv - total_pnl) > 0 else 0.0
            return PortfolioSummary(
                broker_id=self.broker_id,
                positions=pos,
                total_assets=acct.total_assets,
                total_market_value=acct.market_value,
                cash=acct.cash,
                total_pnl=total_pnl,
                total_pnl_pct=pnl_pct,
                top_holding=top,
                currency=acct.currency,
            )
        except Exception as e:
            return PortfolioSummary(broker_id=self.broker_id)

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        return f"<{self.__class__.__name__} id={self.broker_id!r} {status}>"
