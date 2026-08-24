"""券商账户 / 持仓 / 订单的终端渲染 —— 从 aria_cli.py 抽出的第二块。

原本在 aria_cli.py 的 4399-4578 行。跟 football_reports 一样，这些函数依赖
aria_cli 的模块级状态（``console`` / ``HAS_RICH`` / ``Panel`` / ``rich_box``），
所以必须由 aria_cli 用 ``_rebind_module_function_globals`` 绑回它自己的
globals，不能只是普通 import。

调用方有两种形态，重绑后都成立：
  - ``apps/cli/commands/broker_cmds.py`` 用显式 ``from aria_cli import
    _print_broker_account`` —— 读的是 aria_cli 的命名空间；
  - ``tests/test_responsive_rendering.py`` 用 ``aria_cli._print_broker_positions``
    —— 同上。

（这一组不像 football 那样被 mixin 裸名调用，但依赖 aria_cli 模块级 rich
状态，所以约束是一样的。）
"""

from __future__ import annotations

__all__ = [
    "_print_broker_account",
    "_print_broker_positions",
    "_print_broker_orders",
]


def _print_broker_account(acct: "AccountInfo"):
    """Render AccountInfo in a Rich Panel."""
    if not HAS_RICH:
        print(f"{acct.label}  总资产:{acct.total_assets:,.2f}  可用:{acct.cash:,.2f}  市值:{acct.market_value:,.2f}")
        return
    pnl_color = "green" if acct.pnl_today >= 0 else "red"
    pnl_sign  = "+" if acct.pnl_today >= 0 else ""
    body = (
        f"[dim]账户:[/dim]  [bold]{acct.masked_account}[/bold]  [dim]({acct.broker_type})[/dim]\n\n"
        f"  总资产       [bold]{acct.currency} {acct.total_assets:>14,.2f}[/bold]\n"
        f"  持仓市值     [bold]{acct.market_value:>14,.2f}[/bold]\n"
        f"  可用现金     [bold]{acct.cash:>14,.2f}[/bold]\n"
        f"  冻结资金     [dim]{acct.frozen:>14,.2f}[/dim]\n"
        f"  当日盈亏     [{pnl_color}]{pnl_sign}{acct.pnl_today:>14,.2f}[/{pnl_color}]\n"
    )
    if acct.pnl_total:
        tp_color = "green" if acct.pnl_total >= 0 else "red"
        body += f"  累计盈亏     [{tp_color}]{pnl_sign}{acct.pnl_total:>14,.2f}[/{tp_color}]\n"
    console.print(Panel(body, title=f"[bold]{acct.label}[/bold]",
                        border_style="dim", box=rich_box.ROUNDED, padding=(0, 1)))


def _print_broker_positions(positions: list, broker_label: str, currency: str = "CNY"):
    """Render Position list as a Rich Table."""
    if not HAS_RICH:
        for p in positions:
            print(f"  {p.symbol:<8} {p.name:<12} 持仓:{p.quantity}  市值:{p.market_value:,.2f}  盈亏:{p.pnl:+,.2f} ({p.pnl_pct:+.2f}%)")
        return
    if not positions:
        console.print(f"[dim]{broker_label} — 当前无持仓[/dim]")
        return
    from rich.markup import escape as _esc
    from aria_code.ui.render.responsive import StackedRecord, render_stacked_records, structured_layout

    ordered = sorted(positions, key=lambda x: -abs(x.market_value))
    total_mv = sum(p.market_value for p in positions)
    total_pnl = sum(p.pnl for p in positions)
    total_color = "green" if total_pnl >= 0 else "red"
    layout = structured_layout(console)
    if layout == "stacked":
        records = []
        for p in ordered:
            pnl_color = "green" if p.pnl >= 0 else "red"
            records.append(StackedRecord(
                headline=f"{_esc(str(p.symbol))}  {_esc(str(p.name or '—'))}",
                lines=(
                    f"[dim]持仓[/dim] {p.quantity:,.0f}  ·  [dim]可卖[/dim] {p.available_qty:,.0f}",
                    f"[dim]成本[/dim] {p.cost_price:.3f}  ·  [dim]现价[/dim] {p.current_price:.3f}",
                    f"[dim]市值[/dim] {p.market_value:,.2f}  ·  "
                    f"[dim]盈亏[/dim] [{pnl_color}]{p.pnl:+,.2f} ({p.pnl_pct:+.2f}%)[/{pnl_color}]",
                ),
            ))
        render_stacked_records(
            console,
            title=f"{_esc(str(broker_label))} 持仓",
            records=records,
            footer=(
                f"共 {len(positions)} 只 · 总市值 {currency} {total_mv:,.2f} · "
                f"总盈亏 [{total_color}]{total_pnl:+,.2f}[/{total_color}]"
            ),
        )
        return
    from rich.table import Table
    tbl = Table(title=f"[bold]{broker_label}[/bold] 持仓", show_header=True, header_style="bold")
    tbl.add_column("代码",   style="bold", no_wrap=True)
    tbl.add_column("名称",   max_width=12)
    tbl.add_column("持仓",   justify="right")
    if layout == "full":
        tbl.add_column("可卖",   justify="right", style="dim")
        tbl.add_column("成本",   justify="right", style="dim")
        tbl.add_column("现价",   justify="right")
    tbl.add_column("市值",   justify="right")
    tbl.add_column("盈亏",   justify="right")
    tbl.add_column("盈亏%",  justify="right")
    for p in ordered:
        pnl_color = "green" if p.pnl >= 0 else "red"
        pnl_sign  = "+" if p.pnl >= 0 else ""
        row = [p.symbol, p.name[:12] or "—", f"{p.quantity:,.0f}"]
        if layout == "full":
            row.extend([
                f"{p.available_qty:,.0f}",
                f"{p.cost_price:.3f}",
                f"{p.current_price:.3f}",
            ])
        row.extend([
            f"{p.market_value:,.2f}",
            f"[{pnl_color}]{pnl_sign}{p.pnl:,.2f}[/{pnl_color}]",
            f"[{pnl_color}]{pnl_sign}{p.pnl_pct:.2f}%[/{pnl_color}]",
        ])
        tbl.add_row(*row)
    console.print(tbl)
    console.print(
        f"  [dim]共 {len(positions)} 只  总市值 {total_mv:,.2f}  "
        f"总盈亏 [{total_color}]{'+' if total_pnl>=0 else ''}{total_pnl:,.2f}[/{total_color}][/dim]"
    )


def _print_broker_orders(orders: list, broker_label: str, status_filter: str = "all"):
    """Render Order list as a Rich Table."""
    if not HAS_RICH:
        for o in orders:
            print(f"  {o.order_id[:8]} {o.symbol:<8} {o.side:<4} {o.quantity:>8.0f} @ {o.price:.3f}  {o.status}")
        return
    if not orders:
        console.print(f"[dim]{broker_label} — 无 {status_filter} 订单[/dim]")
        return
    from rich.markup import escape as _esc
    from aria_code.ui.render.responsive import StackedRecord, render_stacked_records, structured_layout

    layout = structured_layout(console)
    _STATUS_STYLE = {"filled":"[green]成交[/green]","partial":"[yellow]部成[/yellow]",
                     "open":"[cyan]委托中[/cyan]","cancelled":"[dim]已撤[/dim]"}
    _SIDE_STYLE   = {"buy":"[green]买入[/green]","sell":"[red]卖出[/red]"}
    if layout == "stacked":
        records = []
        for o in orders:
            avg_price = f"{o.avg_price:.3f}" if o.avg_price else "—"
            records.append(StackedRecord(
                headline=(
                    f"{_esc(str(o.symbol))}  {_esc(str(o.name or '—'))}  "
                    f"[dim]#{_esc(str(o.order_id[-8:]))}[/dim]"
                ),
                lines=(
                    f"[dim]方向[/dim] {_SIDE_STYLE.get(o.side, _esc(str(o.side)))}  ·  "
                    f"[dim]状态[/dim] {_STATUS_STYLE.get(o.status, _esc(str(o.status)))}",
                    f"[dim]委托[/dim] {o.quantity:,.0f} @ {o.price:.3f}",
                    f"[dim]成交[/dim] {o.filled_qty:,.0f} @ {avg_price}",
                    f"[dim]时间[/dim] {_esc(str(o.created_at[:16] if o.created_at else '—'))}",
                ),
            ))
        render_stacked_records(
            console,
            title=f"{_esc(str(broker_label))} 订单 · {_esc(str(status_filter))}",
            records=records,
            footer=f"共 {len(orders)} 笔",
        )
        return
    from rich.table import Table
    tbl = Table(title=f"[bold]{broker_label}[/bold] 订单 [dim]({status_filter})[/dim]",
                show_header=True, header_style="bold")
    if layout == "full":
        tbl.add_column("订单号",  style="dim",   max_width=12)
        tbl.add_column("代码",    style="bold",  no_wrap=True)
        tbl.add_column("名称",    max_width=10)
        tbl.add_column("方向",    justify="center")
        tbl.add_column("类型",    style="dim")
        tbl.add_column("委托量",  justify="right")
        tbl.add_column("成交量",  justify="right")
        tbl.add_column("委托价",  justify="right", style="dim")
        tbl.add_column("均价",    justify="right")
        tbl.add_column("状态")
        tbl.add_column("时间",    style="dim", max_width=16)
    else:
        tbl.add_column("代码",    style="bold",  no_wrap=True)
        tbl.add_column("方向",    justify="center")
        tbl.add_column("委托量",  justify="right")
        tbl.add_column("委托价",  justify="right", style="dim")
        tbl.add_column("状态")
        tbl.add_column("时间",    style="dim", max_width=16)
    for o in orders:
        if layout == "full":
            row = [
                o.order_id[-8:], o.symbol, o.name[:10] or "—",
                _SIDE_STYLE.get(o.side, o.side), o.order_type,
                f"{o.quantity:,.0f}", f"{o.filled_qty:,.0f}",
                f"{o.price:.3f}", f"{o.avg_price:.3f}" if o.avg_price else "—",
                _STATUS_STYLE.get(o.status, o.status),
                o.created_at[:16] if o.created_at else "—",
            ]
        else:
            row = [
                o.symbol, _SIDE_STYLE.get(o.side, o.side),
                f"{o.quantity:,.0f}", f"{o.price:.3f}",
                _STATUS_STYLE.get(o.status, o.status),
                o.created_at[:16] if o.created_at else "—",
            ]
        tbl.add_row(*row)
    console.print(tbl)


