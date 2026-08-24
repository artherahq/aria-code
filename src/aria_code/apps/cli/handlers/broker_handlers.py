"""Deterministic broker data handler extracted from aria_cli.py."""
from __future__ import annotations

from typing import Callable


def handle_broker_query(
    message: str,
    *,
    has_brokers: bool,
    is_broker_intent: Callable[[str], bool],
    get_broker_registry: Callable,
) -> dict:
    """Deterministic broker data path for models without reliable tool_calls.

    When the user asks about their portfolio/account and no tool-call model is
    available, fetch the data directly and return a pre-formatted markdown
    response — same data the LLM tool would have provided.
    """
    if not has_brokers:
        return {"success": False, "error": "brokers_not_available"}
    if not is_broker_intent(message):
        return {"success": False, "error": "not_broker_query"}

    try:
        _reg = get_broker_registry()
        broker = _reg.active()
        if not broker or not broker.is_connected:
            return {
                "success": True,
                "response": (
                    "## 账户未连接\n\n"
                    "当前没有已连接的券商账户。\n\n"
                    "请运行 `/broker connect <id>` 或 `/broker list` 查看可用账户。"
                ),
                "tools_used": ["broker_query"],
            }

        _msg_lower = message.lower()
        lines = [f"## {broker.label} 账户信息\n"]

        _want_positions = any(k in _msg_lower for k in (
            "持仓", "position", "portfolio", "仓位", "我的股票", "持有",
        ))
        _want_orders = any(k in _msg_lower for k in (
            "订单", "order", "委托", "成交", "交易记录",
        ))
        _want_account = any(k in _msg_lower for k in (
            "余额", "balance", "账户", "account", "资金", "cash", "净值",
        ))
        if not (_want_positions or _want_orders or _want_account):
            _want_account = True
            _want_positions = True

        if _want_account:
            try:
                acct = broker.account_info()
                lines.append("### 资金概况")
                lines.append(f"- **总资产**: {acct.currency} {acct.total_assets:,.2f}")
                lines.append(f"- **可用资金**: {acct.currency} {acct.available_cash:,.2f}")
                if acct.market_value is not None:
                    lines.append(f"- **持仓市值**: {acct.currency} {acct.market_value:,.2f}")
                if acct.unrealized_pnl is not None:
                    _pnl_sign = "+" if acct.unrealized_pnl >= 0 else ""
                    lines.append(f"- **浮动盈亏**: {_pnl_sign}{acct.unrealized_pnl:,.2f}")
                lines.append("")
            except Exception as _ae:
                lines.append(f"*获取账户信息失败: {_ae}*\n")

        if _want_positions:
            try:
                positions = broker.positions()
                if positions:
                    lines.append("### 当前持仓")
                    lines.append("| 代码 | 名称 | 数量 | 成本 | 现价 | 盈亏% |")
                    lines.append("|------|------|------|------|------|-------|")
                    for p in positions[:15]:
                        _pct = (
                            f"{p.unrealized_pnl_pct:+.2f}%"
                            if p.unrealized_pnl_pct is not None else "N/A"
                        )
                        _cost = f"{p.avg_cost:.2f}" if p.avg_cost is not None else "N/A"
                        _price = f"{p.current_price:.2f}" if p.current_price is not None else "N/A"
                        lines.append(
                            f"| {p.symbol} | {p.name or '-'} | {p.qty:,} "
                            f"| {_cost} | {_price} | {_pct} |"
                        )
                    if len(positions) > 15:
                        lines.append(f"\n*共 {len(positions)} 只持仓，仅显示前 15 只*")
                else:
                    lines.append("*当前无持仓*")
                lines.append("")
            except Exception as _pe:
                lines.append(f"*获取持仓失败: {_pe}*\n")

        if _want_orders:
            try:
                orders = broker.orders()
                if orders:
                    lines.append("### 最近委托")
                    lines.append("| 代码 | 方向 | 数量 | 价格 | 状态 |")
                    lines.append("|------|------|------|------|------|")
                    for o in orders[:10]:
                        _price_str = f"{o.price:.2f}" if o.price else "市价"
                        lines.append(
                            f"| {o.symbol} | {'买入' if o.side=='buy' else '卖出'} "
                            f"| {o.qty:,} | {_price_str} | {o.status} |"
                        )
                else:
                    lines.append("*暂无委托记录*")
                lines.append("")
            except Exception as _oe:
                lines.append(f"*获取委托记录失败: {_oe}*\n")

        lines.append("*以上数据来自券商 API，不构成投资建议*")
        return {
            "success": True,
            "response": "\n".join(lines),
            "tools_used": ["broker_query"],
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}
