"""DataCommandsMixin — data, alert, correlation, and comparison commands."""

from __future__ import annotations


class DataCommandsMixin:
    """Mixin: data analysis and comparison commands."""

    async def cmd_data(self, args: str):
        """
        /data sql "SELECT ..."     — DuckDB SQL 查询
        /data export [filename]    — 导出上次结果到 Excel
        /data load <csv_path>      — 加载 CSV 到 DuckDB
        /data tables               — 列出已加载的表
        """
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        parts = args.strip().split(None, 1) if args.strip() else []
        sub = parts[0].lower() if parts else "help"
        rest = parts[1] if len(parts) > 1 else ""

        try:
            from data_analysis_tools import (sql_query, sql_list_tables,
                                              export_to_excel, load_csv_data)
        except ImportError as e:
            if HAS_RICH:
                console.print(f"[red]data_analysis_tools 未加载: {e}[/red]")
            return

        if sub == "sql":
            query = rest.strip().strip('"').strip("'")
            if not query:
                if HAS_RICH:
                    console.print("[dim]用法: /data sql \"SELECT ...\"|/dim]")
                return
            if HAS_RICH:
                with console.status("[dim]执行 SQL...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, sql_query, {"query": query})
            else:
                r = sql_query({"query": query})
            _render_sql_result(r)

        elif sub == "export":
            fname = rest.strip() or None
            watchlist = self.terminal.config.get("watchlist", ["AAPL", "MSFT", "SPY"])
            try:
                import yfinance as _yf
                raw = _yf.download(watchlist[:5], period="1mo", progress=False, auto_adjust=True)
                closes = raw["Close"] if hasattr(raw.columns, "levels") else raw
                export_data = {"价格历史": closes.reset_index().to_dict("records")}
            except Exception:
                export_data = {"示例数据": [{"symbol": s, "note": "需 yfinance"} for s in watchlist]}
            p = {"data": export_data, "filename": fname}
            if HAS_RICH:
                with console.status("[dim]生成 Excel...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, export_to_excel, p)
            else:
                r = export_to_excel(p)
            if r.get("success"):
                msg = f"✓ 已导出: {r['path']}  ({r['total_rows']} 行)"
                if HAS_RICH:
                    console.print(f"[green]{msg}[/green]")
                else:
                    print(msg)
            else:
                if HAS_RICH:
                    console.print(f"[red]{r.get('error')}[/red]")

        elif sub == "load":
            csv_path = rest.strip()
            if not csv_path:
                if HAS_RICH:
                    console.print("[dim]用法: /data load <csv文件路径>[/dim]")
                return
            if HAS_RICH:
                with console.status("[dim]加载 CSV...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, load_csv_data, {"path": csv_path})
            else:
                r = load_csv_data({"path": csv_path})
            if r.get("success"):
                if HAS_RICH:
                    console.print(f"[green]✓ 已加载 {r['rows']} 行 → 表 {r['table_name']}[/green]")
                    console.print(f"[dim]列: {', '.join(r['columns'][:10])}[/dim]")
                    console.print(f"[dim]现在可以: /data sql \"SELECT * FROM {r['table_name']} LIMIT 10\"[/dim]")
            else:
                if HAS_RICH:
                    console.print(f"[red]{r.get('error')}[/red]")

        elif sub == "tables":
            r = sql_list_tables()
            if r.get("success"):
                tables = r.get("tables", [])
                if HAS_RICH:
                    if tables:
                        console.print(f"[bold]已加载表:[/bold] {', '.join(tables)}")
                    else:
                        console.print("[dim]暂无已加载的表。使用 /data load <csv> 加载数据[/dim]")

        else:
            if HAS_RICH:
                console.print("[dim]用法: /data [sql|export|load|tables][/dim]")
                console.print("[dim]  /data sql \"SELECT * FROM my_table LIMIT 10\"[/dim]")
                console.print("[dim]  /data load ~/Desktop/data.csv[/dim]")
                console.print("[dim]  /data export my_report.xlsx[/dim]")
                console.print("[dim]  /data tables[/dim]")

    async def cmd_alert(self, args: str):
        """
        /alert add AAPL gt 200     — 设置预警（gt/lt/cross_up/cross_down）
        /alert list                 — 列出所有预警
        /alert delete <id>          — 删除预警
        /alert check                — 检查所有预警状态
        """
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        parts = args.strip().split() if args.strip() else []
        sub = parts[0].lower() if parts else "list"

        try:
            from data_analysis_tools import (add_price_alert, list_price_alerts,
                                              delete_price_alert, check_alerts)
        except ImportError as e:
            if HAS_RICH:
                console.print(f"[red]data_analysis_tools 未加载: {e}[/red]")
            return

        if sub == "add":
            if len(parts) < 4:
                if HAS_RICH:
                    console.print("[dim]用法: /alert add <symbol> <gt|lt|cross_up|cross_down> <price> [备注][/dim]")
                return
            sym = parts[1].upper()
            cond = parts[2].lower()
            try:
                price = float(parts[3])
            except ValueError:
                if HAS_RICH:
                    console.print("[red]价格必须是数字[/red]")
                return
            note = " ".join(parts[4:]) if len(parts) > 4 else ""
            r = add_price_alert({"symbol": sym, "condition": cond, "price": price, "note": note})
            if r.get("success"):
                msg = r.get("message", "预警已设置")
                if HAS_RICH:
                    console.print(f"[green]✓ {msg}[/green]")
                else:
                    print(f"✓ {msg}")
            else:
                if HAS_RICH:
                    console.print(f"[red]{r.get('error')}[/red]")

        elif sub == "list":
            r = list_price_alerts()
            _render_alerts(r)

        elif sub in ("delete", "del", "remove"):
            alert_id = parts[1] if len(parts) > 1 else ""
            if not alert_id:
                if HAS_RICH:
                    console.print("[dim]用法: /alert delete <预警ID>[/dim]")
                return
            r = delete_price_alert({"alert_id": alert_id})
            if r.get("success"):
                if HAS_RICH:
                    console.print(f"[green]✓ 已删除预警 {r['deleted_id']}[/green]")
            else:
                if HAS_RICH:
                    console.print(f"[red]{r.get('error')}[/red]")

        elif sub == "check":
            if HAS_RICH:
                with console.status("[dim]检查价格预警...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, check_alerts)
            else:
                r = check_alerts()
            triggered = r.get("triggered", [])
            if triggered:
                if HAS_RICH:
                    console.print(f"[bold yellow]🔔 {len(triggered)} 个预警已触发![/bold yellow]")
                    for a in triggered:
                        console.print(f"  [yellow]{a['symbol']}[/yellow] {a.get('condition','')} "
                                      f"{a['price']} → 当前 [bold]{a.get('triggered_price','')}[/bold]")
            else:
                msg = r.get("message", "暂无触发的预警")
                if HAS_RICH:
                    console.print(f"[dim]{msg}[/dim]")

        else:
            if HAS_RICH:
                console.print("[dim]用法: /alert [add|list|delete|check][/dim]")

    async def cmd_corr(self, args: str):
        """/corr AAPL MSFT TSLA SPY [1y|2y|6mo]  — 计算相关性矩阵"""
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        parts = args.strip().upper().split() if args.strip() else []

        period = "1y"
        if parts and parts[-1].lower() in ("1y", "2y", "3y", "6mo", "ytd", "5y"):
            period = parts[-1].lower()
            parts = parts[:-1]

        symbols = parts if parts else ["AAPL", "MSFT", "TSLA", "SPY", "QQQ"]

        try:
            from data_analysis_tools import calc_correlation_matrix
        except ImportError as e:
            if HAS_RICH:
                console.print(f"[red]data_analysis_tools 未加载: {e}[/red]")
            return

        if HAS_RICH:
            with console.status(f"[dim]计算 {', '.join(symbols)} 相关性矩阵...[/dim]", spinner="dots"):
                r = await loop.run_in_executor(None, calc_correlation_matrix,
                                               {"symbols": symbols, "period": period})
        else:
            r = calc_correlation_matrix({"symbols": symbols, "period": period})
        _render_corr_matrix(r)

    async def cmd_portfolio_bt(self, args: str):
        """/ptbt AAPL MSFT GOOG [0.4 0.3 0.3] [2y] [monthly]  — 多资产组合回测"""
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        parts = args.strip().split() if args.strip() else []

        try:
            from data_analysis_tools import portfolio_backtest
        except ImportError as e:
            if HAS_RICH:
                console.print(f"[red]data_analysis_tools 未加载: {e}[/red]")
            return

        symbols, weights, period, rebalance = [], [], "2y", "monthly"
        _PERIODS = {"1y", "2y", "3y", "5y", "6mo", "ytd", "max"}
        _REBALANCE = {"monthly", "quarterly", "none"}
        for p in parts:
            pl = p.lower()
            if pl in _PERIODS:
                period = pl
                continue
            if pl in _REBALANCE:
                rebalance = pl
                continue
            try:
                f = float(p)
                if f < 2:
                    weights.append(f)
                else:
                    symbols.append(p.upper())
            except ValueError:
                symbols.append(p.upper())

        if not symbols:
            symbols = ["AAPL", "MSFT", "GOOGL", "SPY"]
            if HAS_RICH:
                console.print(f"[dim]未指定标的，使用默认: {symbols}[/dim]")

        p_params = {"symbols": symbols, "period": period, "rebalance": rebalance}
        if weights:
            p_params["weights"] = weights

        if HAS_RICH:
            with console.status(f"[dim]回测 {', '.join(symbols)} ({period})...[/dim]", spinner="dots"):
                r = await loop.run_in_executor(None, portfolio_backtest, p_params)
        else:
            r = portfolio_backtest(p_params)
        _render_portfolio_bt(r)

    async def cmd_peer(self, args: str):
        """/peer <symbol> [peer1 peer2 ...]  — 同行估值对比"""
        parts = args.strip().upper().split() if args.strip() else []
        symbol = parts[0] if parts else "AAPL"
        peers = parts[1:] if len(parts) > 1 else []

        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH:
                console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]获取 {symbol} 同行数据...[/dim]", spinner="dots"):
                from local_finance_tools import _peer_comparison
                r = await loop.run_in_executor(None, _peer_comparison,
                                               {"symbol": symbol, "peers": peers})
        else:
            from local_finance_tools import _peer_comparison
            r = _peer_comparison({"symbol": symbol, "peers": peers})

        _render_peer_comparison(r)

    async def cmd_compare(self, args: str):
        """多策略横向对比 → /api/v1/backtest/compare-strategies"""
        parts = args.split() if args else ["SPY"]
        symbol = parts[0].upper() if parts else "SPY"
        start = parts[1] if len(parts) > 1 else "2020-01-01"
        end = parts[2] if len(parts) > 2 else __import__("datetime").date.today().isoformat()
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        import aiohttp

        async def _do():
            payload = {"symbol": symbol, "strategies": ["momentum", "mean_reversion", "breakout", "turtle", "ma_crossover"],
                       "start_date": start, "end_date": end, "initial_capital": 100000, "commission_rate": 0.0003}
            async with aiohttp.ClientSession() as sess:
                async with sess.post(f"{api_url}/api/v1/backtest/compare-strategies", json=payload, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    body = await resp.json()
                    return body.get("data", body)

        if HAS_RICH:
            with console.status(f"[dim]Comparing strategies on {symbol}...[/dim]", spinner="dots"):
                try:
                    data = await _do()
                except Exception as e:
                    _print_error(str(e), "tool")
                    return
        else:
            print(f"Comparing strategies on {symbol}...")
            try:
                data = await _do()
            except Exception as e:
                _print_error(str(e), "tool")
                return

        strategies = data.get("strategies", [])
        bh = data.get("benchmark", {})
        if HAS_RICH:
            from rich.table import Table
            tbl = Table(title=f"[bold]{symbol} Strategy Comparison[/bold]  {start} → {end}", show_header=True, header_style="bold")
            for col in ["Rank", "Strategy", "Ann.Ret%", "Sharpe", "MaxDD%", "Calmar", "Sortino", "Win%", "Trades"]:
                tbl.add_column(col, justify="right")
            for s in strategies:
                tbl.add_row(
                    str(s.get("rank_by_sharpe", "")),
                    s["name"],
                    f"{s.get('annualized_return_pct',0):+.1f}%",
                    f"{s.get('sharpe_ratio',0):.3f}",
                    f"{s.get('max_drawdown_pct',0):.1f}%",
                    f"{s.get('calmar_ratio',0):.2f}",
                    f"{s.get('sortino_ratio',0):.2f}",
                    f"{s.get('win_rate_pct',0):.0f}%",
                    str(s.get("n_trades",0)),
                )
            tbl.add_row("—", "[dim]Buy & Hold[/dim]",
                        f"{bh.get('annualized_return_pct',0):+.1f}%",
                        f"{bh.get('sharpe_ratio',0):.3f}",
                        f"{bh.get('max_drawdown_pct',0):.1f}%", "—", "—", "—", "2")
            console.print(tbl)
        else:
            for s in strategies:
                print(f"{s['name']}: Ann={s.get('annualized_return_pct',0):+.1f}% Sharpe={s.get('sharpe_ratio',0):.2f} DD={s.get('max_drawdown_pct',0):.1f}%")
