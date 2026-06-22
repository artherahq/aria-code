"""
PortfolioCommandsMixin — Portfolio commands: journal, report, portfolio, apply_plan, team.

Extracted from aria_cli.py. Methods' __globals__ are rebound to aria_cli's namespace
by _rebind_mixin_globals() called at module load time.
"""
from __future__ import annotations


def _detect_lang_for_team(text: str) -> str:
    if not text:
        return "zh"
    zh_chars = sum(1 for c in text if '一' <= c <= '鿿')
    return "zh" if zh_chars / max(len(text), 1) > 0.15 else "en"


class PortfolioCommandsMixin:
    """Mixin: Portfolio commands: journal, report, portfolio, apply_plan, team."""

    async def cmd_journal(self, args: str):
        """
        本地持仓账本（SQLite，~/.arthera/portfolio.db）
        Usage:
          /journal                              → 当前持仓
          /journal add buy  AAPL 100 185.50 [理由]
          /journal add sell AAPL 50  200.00 [理由]
          /journal trades [SYMBOL]              → 交易记录
          /journal pnl                          → 含实时报价的未实现盈亏
          /journal realized                     → 已实现盈亏（FIFO）
          /journal export                       → 导出 CSV 到桌面
          /journal delete <id>                  → 删除指定记录
        """
        try:
            from portfolio_ledger import PortfolioLedger as _PL
        except ImportError:
            msg = "portfolio_ledger 模块未找到"
            console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
            return

        ledger = _PL()
        parts  = args.strip().split() if args.strip() else []
        sub    = parts[0].lower() if parts else "positions"

        # ── add buy/sell ─────────────────────────────────────────────────────
        if sub == "add":
            # /journal add buy AAPL 100 185.50 [reason...]
            if len(parts) < 5:
                usage = "用法: /journal add <buy|sell> <symbol> <qty> <price> [理由]"
                console.print(f"[yellow]{usage}[/yellow]") if HAS_RICH else print(usage)
                return
            try:
                side   = parts[1].upper()
                symbol = parts[2].upper()
                qty    = float(parts[3])
                price  = float(parts[4])
                reason = " ".join(parts[5:]) if len(parts) > 5 else ""
                tid    = ledger.add_trade(symbol, side, qty, price, reason=reason)
                amount = round(qty * price, 2)
                msg    = (f"✓ 已记录: #{tid} {side} {symbol} × {qty} @ {price}"
                          f"  总额 {amount:,.2f}  {reason}")
                console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
            except Exception as e:
                console.print(f"[red]记录失败: {e}[/red]") if HAS_RICH else print(f"记录失败: {e}")
            return

        # ── delete ───────────────────────────────────────────────────────────
        if sub == "delete" and len(parts) >= 2:
            try:
                tid = int(parts[1])
                ok  = ledger.delete_trade(tid)
                msg = f"✓ 已删除记录 #{tid}" if ok else f"未找到记录 #{tid}"
                console.print(f"[{'green' if ok else 'yellow'}]{msg}[/{'green' if ok else 'yellow'}]") if HAS_RICH else print(msg)
            except Exception as e:
                console.print(f"[red]删除失败: {e}[/red]") if HAS_RICH else print(f"删除失败: {e}")
            return

        # ── trades history ───────────────────────────────────────────────────
        if sub == "trades":
            sym    = parts[1].upper() if len(parts) > 1 else None
            trades = ledger.get_trades(symbol=sym, limit=30)
            title  = f"交易记录{f' — {sym}' if sym else ''} (最近 {len(trades)} 条)"
            if HAS_RICH:
                from rich.table import Table
                tbl = Table(title=title, box=None, show_header=True, header_style="bold")
                tbl.add_column("#", style="dim", width=4)
                tbl.add_column("日期", width=10)
                tbl.add_column("方向", width=5)
                tbl.add_column("标的", width=8)
                tbl.add_column("数量", justify="right", width=10)
                tbl.add_column("价格", justify="right", width=10)
                tbl.add_column("总额", justify="right", width=12)
                tbl.add_column("理由", width=20)
                for t in trades:
                    side_color = "green" if t["side"] == "BUY" else "red"
                    tbl.add_row(
                        str(t["id"]),
                        t["date"],
                        f"[{side_color}]{t['side']}[/{side_color}]",
                        t["symbol"],
                        f"{t['qty']:,.4g}",
                        f"{t['price']:,.4f}",
                        f"{t['amount']:,.2f}",
                        (t["reason"] or "")[:18],
                    )
                console.print(tbl)
                if not trades:
                    console.print("[dim]无交易记录[/dim]")
            else:
                print(title)
                for t in trades:
                    print(f"  #{t['id']} {t['date']} {t['side']} {t['symbol']} "
                          f"× {t['qty']} @ {t['price']}  {t['reason']}")
            return

        # ── export ───────────────────────────────────────────────────────────
        if sub == "export":
            try:
                out = ledger.export_csv()
                msg = f"✓ 已导出 {ledger.trade_count()} 条记录 → {out}"
                console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
            except Exception as e:
                console.print(f"[red]导出失败: {e}[/red]") if HAS_RICH else print(f"导出失败: {e}")
            return

        # ── realized P&L ─────────────────────────────────────────────────────
        if sub == "realized":
            rows = ledger.get_realized_pnl()
            if HAS_RICH:
                from rich.table import Table
                tbl = Table(title="已实现盈亏（FIFO）", box=None, header_style="bold")
                tbl.add_column("标的", width=8)
                tbl.add_column("已实现盈亏", justify="right", width=14)
                tbl.add_column("剩余持仓", justify="right", width=10)
                for r in rows:
                    pnl   = r["realized_pnl"]
                    color = "green" if pnl >= 0 else "red"
                    tbl.add_row(
                        r["symbol"],
                        f"[{color}]{pnl:+,.2f}[/{color}]",
                        f"{r['open_lots']:,.4g}" if r["has_open"] else "已平仓",
                    )
                console.print(tbl)
                total = sum(r["realized_pnl"] for r in rows)
                tc    = "green" if total >= 0 else "red"
                console.print(f"  [bold]合计已实现盈亏: [{tc}]{total:+,.2f}[/{tc}][/bold]")
            else:
                for r in rows:
                    print(f"  {r['symbol']}: {r['realized_pnl']:+,.2f}")
            return

        # ── pnl with live prices ──────────────────────────────────────────────
        if sub == "pnl":
            positions = ledger.get_positions()
            if not positions:
                console.print("[dim]暂无持仓记录。用 /journal add buy … 添加。[/dim]") if HAS_RICH else print("暂无持仓")
                return
            # fetch live prices via yfinance
            live_prices: dict = {}
            syms = [p["symbol"] for p in positions]
            if HAS_RICH:
                console.print(f"  [dim]获取 {len(syms)} 只股票实时报价…[/dim]")
            try:
                import yfinance as yf
                for sym in syms:
                    try:
                        h = yf.Ticker(sym).history(period="1d")
                        if not h.empty:
                            live_prices[sym] = float(h["Close"].iloc[-1])
                    except Exception:
                        pass
            except ImportError:
                pass
            rows = ledger.get_pnl_with_prices(live_prices)
            if HAS_RICH:
                from rich.table import Table
                tbl = Table(title="持仓盈亏", box=None, header_style="bold")
                tbl.add_column("标的", width=8)
                tbl.add_column("数量", justify="right", width=10)
                tbl.add_column("均价", justify="right", width=10)
                tbl.add_column("现价", justify="right", width=10)
                tbl.add_column("市值", justify="right", width=12)
                tbl.add_column("未实现盈亏", justify="right", width=14)
                tbl.add_column("涨跌%", justify="right", width=8)
                for r in rows:
                    has_price = "current_price" in r
                    pnl   = r.get("unrealized_pnl", "")
                    pct   = r.get("unrealized_pct", "")
                    color = ("green" if isinstance(pnl, (int, float)) and pnl >= 0 else "red") if has_price else "dim"
                    tbl.add_row(
                        r["symbol"],
                        f"{r['net_qty']:,.4g}",
                        f"{r['avg_cost']:,.4f}",
                        f"{r.get('current_price', 'N/A'):,.4f}" if has_price else "N/A",
                        f"{r.get('market_value', ''):,.2f}" if has_price else "N/A",
                        f"[{color}]{pnl:+,.2f}[/{color}]" if has_price else "—",
                        f"[{color}]{pct:+.2f}%[/{color}]" if has_price else "—",
                    )
                console.print(tbl)
                total_pnl  = sum(r.get("unrealized_pnl", 0) for r in rows if "unrealized_pnl" in r)
                total_mv   = sum(r.get("market_value", 0) for r in rows if "market_value" in r)
                total_cost = sum(r["cost_basis"] for r in rows)
                tc = "green" if total_pnl >= 0 else "red"
                console.print(
                    f"  [bold]总持仓成本 {total_cost:,.2f}  "
                    f"总市值 {total_mv:,.2f}  "
                    f"未实现盈亏 [{tc}]{total_pnl:+,.2f}[/{tc}][/bold]"
                )
                # Portfolio status banner
                _pnl_pct = (total_pnl / total_cost * 100) if total_cost else 0
                _pnl_verdict = "HEALTHY" if _pnl_pct >= 0 else ("NEEDS_ATTENTION" if _pnl_pct >= -10 else "HIGH_RISK")
                _pnl_sub = f"总盈亏 {total_pnl:+,.2f}  ({_pnl_pct:+.1f}%)"
                _print_verdict_banner(_pnl_verdict, subtitle=_pnl_sub)
            else:
                for r in rows:
                    pnl = r.get("unrealized_pnl", "N/A")
                    print(f"  {r['symbol']}: {r['net_qty']} × avg {r['avg_cost']}  pnl {pnl}")
            return

        # ── default: positions ────────────────────────────────────────────────
        positions = ledger.get_positions()
        if not positions:
            hint = "暂无持仓记录。\n  添加示例: /journal add buy AAPL 100 185.50 首次建仓"
            console.print(f"[dim]{hint}[/dim]") if HAS_RICH else print(hint)
            return
        if HAS_RICH:
            from rich.table import Table
            tbl = Table(
                title=f"当前持仓（{len(positions)} 只，共 {ledger.trade_count()} 条交易）",
                box=None, header_style="bold",
            )
            tbl.add_column("标的", width=8)
            tbl.add_column("持仓量", justify="right", width=12)
            tbl.add_column("均价成本", justify="right", width=12)
            tbl.add_column("持仓成本", justify="right", width=14)
            tbl.add_column("首次建仓", width=12)
            for pos in positions:
                tbl.add_row(
                    pos["symbol"],
                    f"{pos['net_qty']:,.4g}",
                    f"{pos['avg_cost']:,.4f}",
                    f"{pos['cost_basis']:,.2f}",
                    pos.get("first_trade", ""),
                )
            console.print(tbl)
            console.print(
                f"  [dim]更多命令: /journal pnl | /journal trades | "
                f"/journal realized | /journal export[/dim]"
            )
        else:
            print(f"当前持仓 ({len(positions)} 只):")
            for pos in positions:
                print(f"  {pos['symbol']}: {pos['net_qty']} 股  均价 {pos['avg_cost']}")

    async def cmd_report(self, args: str):
        """生成综合投资报告（图表 + 多 Agent 分析 → HTML / Markdown 文件）。

        Usage:
            /report AAPL
            /report 000333
            /report AAPL --format md      # Markdown 投研报告（离线可用）
            /report AAPL --type deep      # 深度研报（8页）
            /report AAPL --type brief     # 简评（1页）
            /report AAPL --pdf            # 同时导出 PDF（需 weasyprint 或 wkhtmltopdf）
        """
        from datetime import datetime as _dt

        report_args = parse_report_args(args)
        symbol = report_args.symbol
        fmt = report_args.fmt
        report_type = report_args.report_type
        export_pdf_flag = report_args.export_pdf
        out_dir = report_args.output_dir
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M")

        # ── Markdown report mode (works fully offline) ────────────────────────
        if fmt in ("md", "markdown"):
            console.print(f"\n  📄 生成 [bold]{symbol}[/bold] Markdown 投研报告 ({report_type})...") if HAS_RICH else print(f"\n  Generating {symbol} Markdown report...")

            # Fetch real data through the service boundary so provenance and
            # quality metadata travel with the report prompt and artifact.
            mdc_data = {}
            data_bundle = None
            data_quality = {}
            try:
                from packages.aria_services.data import DataService as _ReportDataService
                data_bundle = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: _ReportDataService().bundle(symbol, history_days=370, technical_days=120),
                )
                quote = data_bundle.quote or {}
                technical = data_bundle.technical or {}
                mdc_data = {**quote, **technical}
                data_quality = data_bundle.quality or {}
            except Exception as _ds_exc:
                logger.debug("report markdown data service failed: %s", _ds_exc)
                if _HAS_MDC:
                    try:
                        mdc = _get_mdc()
                        q = mdc.quote(symbol)
                        ti = mdc.technical_indicators(symbol, days=120)
                        mdc_data = {**q, **ti}
                        data_quality = {
                            "status": "partial",
                            "stale": False,
                            "providers": mdc_data.get("provider_chain") or list(dict.fromkeys(
                                str(v) for v in [mdc_data.get("provider"), mdc_data.get("source")] if v
                            )),
                            "warnings": [f"data service unavailable: {_ds_exc}"],
                        }
                    except Exception:
                        data_quality = {"status": "data_unavailable", "warnings": [str(_ds_exc)]}

            ai_prompt = build_markdown_report_prompt(
                symbol=symbol,
                report_type=report_type,
                market_data=mdc_data,
                data_quality=data_quality,
                data_bundle=data_bundle,
                now=_dt.now(),
            )

            await self.terminal.send_message(ai_prompt)

            # Extract last AI response and save as markdown
            last_ai = next(
                (m["content"] for m in reversed(self.terminal.conversation)
                 if m.get("role") == "assistant"), ""
            )
            if last_ai:
                saved = save_markdown_report(
                    symbol=symbol,
                    report_type=report_type,
                    markdown_text=last_ai,
                    timestamp=ts,
                    output_dir=out_dir,
                    market_data=mdc_data,
                    data_quality=data_quality,
                    data_bundle=data_bundle,
                    created_at=_dt.now(),
                )
                out_f = saved.path
                if HAS_RICH:
                    console.print(f"\n  [green]✅ 报告已保存: {out_f}[/green]")
                    console.print(f"  [dim]预览: open {out_f}[/dim]\n")
                else:
                    print(f"\n  Saved: {out_f}")
            return

        # ── HTML 研报（Bloomberg 暗色主题）────────────────────────────────
        if HAS_RICH:
            console.print(f"\n  [dim]正在生成 [bold]{symbol}[/bold] 专业研报（数据清洗 + 图表 + Agent 分析）…[/dim]")
        else:
            print(f"\n  正在生成 {symbol} 研报…")

        _agent_names_for_report = report_agent_names(report_type)
        try:
            if HAS_RICH:
                with console.status(
                    f"[dim]{len(_agent_names_for_report)} agents 并行分析…[/dim]",
                    spinner="dots",
                ):
                    _html_report = await generate_html_report(
                        symbol=symbol,
                        report_type=report_type,
                        output_dir=out_dir,
                        config=self.terminal.config,
                    )
            else:
                _html_report = await generate_html_report(
                    symbol=symbol,
                    report_type=report_type,
                    output_dir=out_dir,
                    config=self.terminal.config,
                )
            out_f = _html_report.path
            _team_result = _html_report.team_result
        except Exception as e:
            if HAS_RICH:
                console.print(f"  [red]研报生成失败: {e}[/red]")
            else:
                print(f"  研报生成失败: {e}")
            return

        if not out_f:
            console.print("  [red]研报生成失败（无输出文件）[/red]") if HAS_RICH else print("  研报生成失败")
            return

        path = str(out_f)
        from ui.render.output import display_path as _display_path
        path_label = _display_path(out_f, fallback="report")
        _file_kb = report_file_size_kb(out_f)
        # Check if all agents failed — show warning instead of false success
        _all_agents_failed = all_agents_failed(_team_result)
        if HAS_RICH:
            if _all_agents_failed:
                console.print(
                    f"\n  [yellow]⚠ 研报已保存（所有 Agent 分析失败，内容仅含基础数据）[/yellow]"
                    f"  [dim]{out_f.name}  ({_file_kb}KB)[/dim]"
                )
            else:
                console.print(
                    f"\n  [green]✅ 研报已保存[/green]"
                    f"  [link={path}]{path_label}[/link]"
                    f"  [dim]({_file_kb}KB)[/dim]"
                )
            console.print(f"  [dim]文件: {path_label}[/dim]")
            if _team_result:
                _print_verdict_banner(
                    _team_result.final_signal,
                    subtitle=f"耗时 {_team_result.elapsed_sec:.1f}s · {len(_agent_names_for_report)} agents",
                    confidence=_team_result.confidence,
                )
        else:
            _pfx = "⚠ 研报已保存（Agent 全部失败）" if _all_agents_failed else "✅ 研报已保存"
            print(f"\n  {_pfx}: {path_label}  ({_file_kb}KB)")

        # ── PDF 导出 ──────────────────────────────────────────────────────────
        if export_pdf_flag:
            try:
                if HAS_RICH:
                    with console.status("[dim]导出 PDF…[/dim]", spinner="dots"):
                        _pdf_path = await export_report_pdf(out_f)
                else:
                    _pdf_path = await export_report_pdf(out_f)
                if _pdf_path:
                    _pdf_kb = report_file_size_kb(_pdf_path)
                    if HAS_RICH:
                        console.print(
                            f"  [green]PDF 导出成功[/green]"
                            f"  [link={_pdf_path}]{_pdf_path.name}[/link]"
                            f"  [dim]({_pdf_kb}KB)[/dim]"
                        )
                    else:
                        print(f"  PDF: {_pdf_path}  ({_pdf_kb}KB)")
                    import subprocess as _subp2
                    try:
                        _subp2.Popen(["open", str(_pdf_path)])
                    except Exception:
                        pass
                else:
                    _hint = "pip install weasyprint  或  brew install wkhtmltopdf"
                    if HAS_RICH:
                        console.print(
                            f"  [yellow]PDF 导出失败[/yellow]  "
                            f"[dim]请安装: {_hint}  或在浏览器按 Cmd+P → 存储为 PDF[/dim]"
                        )
                    else:
                        print(f"  PDF 导出失败，请安装: {_hint}")
            except Exception as _e:
                logger.debug("[report] pdf export error: %s", _e)

        # ── 更新研报索引 ──────────────────────────────────────────────────────
        try:
            _idx = await update_report_index(out_f.parent)
            if _idx and HAS_RICH:
                console.print(
                    f"  [dim]索引已更新: [link={_idx}]{_idx.name}[/link][/dim]"
                )
        except Exception as _e:
            logger.debug("[report] index update error: %s", _e)

        import subprocess as _subp
        try:
            _subp.Popen(["open", path])
        except Exception:
            pass

    async def cmd_portfolio(self, args: str):
        """
        组合级跨标的分析（相关性/分散度/风险）
        Usage:
          /portfolio                    → 分析 watchlist（最多 10 只）
          /portfolio analyze            → 同上
          /portfolio analyze AAPL TSLA MSFT
          /portfolio rebalance          → 生成再平衡建议（同 analyze，着重操作）
        """
        import sys as _sys
        parts      = args.strip().split()
        sub        = parts[0].lower() if parts else "analyze"
        sym_parts  = parts[1:] if parts else []
        rebalance  = (sub == "rebalance")

        # 解析标的：命令行 > watchlist
        if sym_parts:
            symbols = [s.strip(",").upper() for s in sym_parts if s.strip(",")]
        else:
            symbols = self.terminal.config.get("watchlist", ["AAPL", "MSFT", "GOOGL", "NVDA", "TSLA"])[:10]

        if not symbols:
            msg = "请先设置 watchlist 或指定标的：/portfolio analyze AAPL TSLA MSFT"
            console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
            return

        # 尝试使用新 PortfolioAgent
        _use_new = False
        try:
            from agents.portfolio_agent import PortfolioAgent as _PA
            from providers.llm.registry import get_provider as _get_prov, list_available_providers as _laps
            _use_new = True
        except ImportError:
            pass

        if _use_new:
            hdr = "分析 watchlist 组合" if not sym_parts else f"分析组合：{' '.join(symbols)}"
            if rebalance:
                hdr = "再平衡方案：" + hdr
            if HAS_RICH:
                console.print()
                console.print(f"  [bold cyan]━━━ /portfolio {hdr} ━━━[/bold cyan]")
                console.print(f"  [dim]标的 ({len(symbols)}): {', '.join(symbols)}[/dim]")
                console.print()
            else:
                print(f"\n  ━━━ /portfolio ━━━\n  标的: {', '.join(symbols)}\n")

            _llm = None
            try:
                all_avail = [p for p in _laps() if p["available"]]
                chosen    = [p for p in all_avail if p.get("local")] or all_avail
                if chosen:
                    _llm = _get_prov(chosen[0]["name"])
            except Exception as _e:
                logger.debug("portfolio LLM provider init failed: %s", _e)

            tokens: list = []
            def _on_tok(t):
                tokens.append(t)
                _sys.stdout.write(t); _sys.stdout.flush()

            try:
                agent  = _PA(llm_provider=_llm, on_token=_on_tok)
                result = await agent.run_portfolio(symbols)
                print()  # 换行（流式输出后）

                if not result:
                    if HAS_RICH:
                        console.print("[yellow]  ⚠ 组合分析返回空结果[/yellow]")
                    return

                if HAS_RICH:
                    console.print()
                    for pt in (result.key_points or []):
                        console.print(f"  [dim]• {pt}[/dim]")
                    console.print()
                    # Derive portfolio verdict from signal for the banner
                    _port_verdict = {
                        "BUY":        "HEALTHY",
                        "HOLD":       "NEEDS_ATTENTION",
                        "SELL":       "HIGH_RISK",
                        "STRONG_BUY": "HEALTHY",
                        "STRONG_SELL":"HIGH_RISK",
                    }.get(result.signal.upper() if result.signal else "HOLD", "NEEDS_ATTENTION")
                    _subtitle = " · ".join(result.key_points[:2]) if result.key_points else ""
                    _print_verdict_banner(_port_verdict, subtitle=_subtitle,
                                          confidence=result.confidence)
                else:
                    for pt in result.key_points:
                        print(f"  • {pt}")
                    print(f"\n  置信度: {result.confidence:.0%}  信号: {result.signal}")

                if rebalance and HAS_RICH:
                    console.print("\n  [dim]提示: 再平衡建议已包含在上方分析中。"
                                  "如需详细方案，可追问 Aria 具体操作步骤。[/dim]")

            except Exception as e:
                msg = f"组合分析失败: {e}"
                console.print(f"  [red]{msg}[/red]") if HAS_RICH else print(f"  {msg}")
            return

        # 旧路径回退（无新 agents 包时）
        if HAS_RICH:
            console.print("[dim]Assessing portfolio risk...[/dim]")
        else:
            print("Assessing portfolio risk...")
        result = await execute_aria_tool(self.terminal.api_url, "assess_portfolio_risk", {
            "symbols": symbols[:10],
        })
        if result.get("success") and result.get("data"):
            if HAS_RICH:
                console.print(f"\n  [bold]Portfolio Risk[/bold]\n")
                console.print(f"[dim]{json.dumps(result['data'], indent=2, ensure_ascii=False)[:1000]}[/dim]")
            else:
                print(json.dumps(result.get("data", {}), indent=2, ensure_ascii=False))
        else:
            console.print(f"[dim]No data: {result.get('error', '')}[/dim]" if HAS_RICH
                          else f"No data: {result.get('error', '')}")

    def cmd_apply_plan(self, args: str):
        """Execute the pending command plan sequentially."""
        plan = list(getattr(self.terminal, "pending_plan", []) or [])
        arg_tokens = args.split()
        start_idx = 0
        if "--from" in arg_tokens:
            idx = arg_tokens.index("--from")
            if idx + 1 >= len(arg_tokens):
                msg = "Usage: /apply-plan --from <step_number>"
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return
            try:
                start_idx = max(0, int(arg_tokens[idx + 1]) - 1)
            except ValueError:
                msg = "Invalid step number for --from"
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return

        if not plan:
            console.print("[dim]No pending plan. Use /plan first.[/dim]" if HAS_RICH
                          else "No pending plan. Use /plan first.")
            return
        if start_idx > 0:
            if start_idx >= len(plan):
                msg = f"--from {start_idx + 1} exceeds available steps ({len(plan)})"
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return
            plan = plan[start_idx:]
        if "--resume" in arg_tokens and HAS_RICH:
            console.print(f"[dim]Resuming execution from step 1 of remaining {len(plan)} step(s).[/dim]")

        policy = self.terminal.config.get("command_policy", "safe")
        results = []
        failed = None
        for i, step in enumerate(plan, 1):
            started_at = time.time()
            if HAS_RICH:
                console.print(f"[dim]Step {i}/{len(plan)}:[/dim] [bold]{step}[/bold]")
            else:
                print(f"Step {i}/{len(plan)}: {step}")

            step_decision = evaluate_command_policy(step, policy)
            if step_decision.risk == "high":
                if not self._confirm_high_risk_command(step_decision.normalized_command, step_decision.risk, policy):
                    failed = (i, step, "Cancelled by user at high-risk step confirmation")
                    results.append({
                        "step": step,
                        "status": "blocked",
                        "duration": round(time.time() - started_at, 3),
                        "exit_code": None,
                        "error": failed[2],
                    })
                    break

            res = _tool_run_command({"command": step, "policy": policy})
            duration = time.time() - started_at
            exit_code = res.get("data", {}).get("exit_code", None) if res.get("success") else None
            status = "completed" if res.get("success") and exit_code == 0 else "failed"
            results.append({
                "step": step,
                "status": status,
                "duration": round(duration, 3),
                "exit_code": exit_code,
                "error": None if status == "completed" else (res.get("error") or f"Command exited {exit_code}"),
            })
            if not res.get("success"):
                failed = (i, step, res.get("error", "Unknown error"))
                break
            exit_code = res.get("data", {}).get("exit_code", 0)
            if exit_code != 0:
                failed = (i, step, f"Command exited {exit_code}")
                break

        self.terminal.last_plan_results = results

        if failed:
            idx, step, err = failed
            self.terminal.pending_plan = plan[idx - 1:]
            if HAS_RICH:
                console.print(f"[red]Plan failed at step {idx}[/red]: [bold]{step}[/bold]")
                console.print(f"[red]{err}[/red]")
                console.print("[dim]Recovery hints:[/dim]")
                if "blocked by policy" in (err or "").lower():
                    console.print("  [dim]> /run --dry-run <command> to inspect risk[/dim]")
                    console.print("  [dim]> /config set command_policy=balanced (or full) if needed[/dim]")
                else:
                    console.print("  [dim]> Fix code/config, then rerun /apply-plan[/dim]")
                    console.print("  [dim]> Use /git diff to inspect changes[/dim]")
            else:
                print(f"Plan failed at step {idx}: {step}\n{err}")
                if "blocked by policy" in (err or "").lower():
                    print("Recovery: /run --dry-run <command> and /config set command_policy=balanced")
                else:
                    print("Recovery: fix issue, then rerun /apply-plan")
        else:
            if HAS_RICH:
                console.print(f"[green]Plan completed ({len(plan)} steps)[/green]")
                for i, row in enumerate(results, 1):
                    console.print(f"  [dim]{i}. {row['step']} ({row['duration']}s)[/dim]")
            else:
                print(f"Plan completed ({len(plan)} steps)")
            self.terminal.pending_plan = []

    async def cmd_deep(self, args: str):
        """
        深度多层研究（Claude-Code 架构 P0–P3）：
        团队并行 → 主题分组 → 工具深挖 → 量化融合+置信度校准 → Critic 自检 → 分级报告
        Usage: /deep NVDA            ← 标准档
               /deep AAPL --deep     ← 深度档（含量化地面真值/自检/数据血缘）
               /deep 000333 --brief  ← 简报档
               /deep TSLA --agents technical,risk,macro
               /deep calibrate       ← 用真实价回评历史预测，更新置信度校准
        """
        def _latest_close(symbol: str):
            try:
                import data_cleaner
                df, _ = data_cleaner.get_clean_prices(symbol, period="5d")
                if df is not None and len(df):
                    for col in ("close", "Close", "adj_close", "收盘"):
                        if col in df.columns:
                            return float(df[col].iloc[-1])
            except Exception:
                pass
            return None

        # /deep calibrate — score logged predictions against realised price (P2 loop)
        if args.strip().lower().startswith(("calibrate", "校准")):
            from agents.deep.calibration_loop import (
                PredictionLog, evaluate_due, evaluate_from_ledger)
            from agents.deep.quant_fusion import CalibrationStore
            store, log = CalibrationStore(), PredictionLog()
            led_res = {"evaluated": 0, "hits": 0}
            try:  # actual realised P&L first — the strongest ground truth
                from portfolio_ledger import PortfolioLedger
                led_res = evaluate_from_ledger(store, log, PortfolioLedger())
            except Exception:
                pass
            px_res = evaluate_due(store, log, _latest_close)   # market price for the rest
            total = led_res["evaluated"] + px_res["evaluated"]
            hits = led_res["hits"] + px_res["hits"]
            if total:
                msg = (f"校准完成：评估 {total} 条（实盘 {led_res['evaluated']} + "
                       f"市价 {px_res['evaluated']}），命中 {hits}，"
                       f"命中率 {hits / total:.0%}（置信度校准已更新）")
            else:
                msg = "暂无到期预测可校准（先用 /deep 跑几次分析积累预测）。"
            console.print(f"[green]✓[/green] {msg}") if HAS_RICH else print(msg)
            return

        team_args = parse_team_args(args)
        symbols = resolve_team_symbols(team_args, self.terminal.config)
        agent_names = team_agent_names(team_args)
        _low = args.lower()
        tier = ("deep" if ("--deep" in _low or "--full" in _low)
                else "brief" if "--brief" in _low else "standard")
        _zh = sum(1 for c in args if '一' <= c <= '鿿')
        _lang = "zh" if _zh / max(len(args), 1) > 0.15 else "en"

        from agents.deep.tiers import render_tier
        from ui.render.team import render_agent_tree_root, render_agent_node

        for sym in symbols:
            def _on_agent_done(name, result):
                _kps = getattr(result, "key_points", None)
                _kp = (_kps[0] if isinstance(_kps, (list, tuple)) and _kps else "")
                if HAS_RICH:
                    render_agent_node(
                        console, name, getattr(result, "signal", None), _kp,
                        success=bool(getattr(result, "success", True)),
                        error=getattr(result, "error", None),
                    )
                else:
                    print(f"  ⎿ {name}  {getattr(result, 'signal', '')}  {_kp[:50]}")

            if HAS_RICH:
                render_agent_tree_root(console, sym, len(agent_names), lang=_lang)
            else:
                print(f"\n  ⏺ 深度分析 {sym}  {len(agent_names)} 个分析师")

            try:
                result = await run_deep_cli(
                    symbol=sym, args=team_args, config=self.terminal.config,
                    lang=_lang, on_agent_done=_on_agent_done,
                )
            except Exception as e:
                _print_error(str(e), "deep")
                continue

            md = render_tier(result, tier)
            if HAS_RICH:
                from rich import box as _box
                from rich.markdown import Markdown
                from rich.panel import Panel
                console.print(Panel(
                    Markdown(md), border_style="dim", box=_box.ROUNDED,
                    title=f"[bold]深度研究 · {sym}[/bold] [dim]({tier})[/dim]",
                    title_align="left", padding=(1, 2),
                ))
            else:
                print("\n" + md)

            # P2 closed loop: log the verdict so /deep calibrate can score it later
            try:
                from agents.deep.calibration_loop import PredictionLog
                _p = _latest_close(sym)
                if _p and result.final_signal:
                    PredictionLog().log(sym, result.final_signal,
                                        result.calibrated_confidence, _p)
            except Exception:
                pass

    async def cmd_team(self, args: str):
        """
        多 Agent 金融研究团队：宏观 + 基本面 + 技术 + 风控 → 综合报告
        Usage: /team NVDA
               /team 000333 --agents technical,risk
               /team watchlist
               /team AAPL --full          ← 7-agent 完整模式（+新闻/催化剂/行业）
        """
        import sys as _sys
        team_args = parse_team_args(args)
        symbols = resolve_team_symbols(team_args, self.terminal.config)
        agent_names = team_agent_names(team_args)
        _zh = sum(1 for c in args if '一' <= c <= '鿿')
        _lang = "zh" if _zh / max(len(args), 1) > 0.15 else "en"

        for sym in symbols:
            _agent_count = len(agent_names)

            # ── Streaming nested agent tree (Claude Code-style) ──────────────
            from ui.render.team import (
                render_agent_tree_root, render_agent_node,
                render_agent_synthesis_leaf,
            )

            def _on_agent_done(name, result):
                # Fires as each analyst finishes — render its leaf live.
                _kp = ""
                _kps = getattr(result, "key_points", None)
                if _kps:
                    _kp = _kps[0] if isinstance(_kps, (list, tuple)) else str(_kps)
                if HAS_RICH:
                    render_agent_node(
                        console, name,
                        getattr(result, "signal", None), _kp,
                        success=bool(getattr(result, "success", True)),
                        error=getattr(result, "error", None),
                    )
                else:
                    print(f"  ⎿ {name}  {getattr(result, 'signal', '')}  {_kp[:50]}")

            if HAS_RICH:
                render_agent_tree_root(console, sym, _agent_count, lang=_lang)
            else:
                print(f"\n  ⏺ 多代理分析 {sym}  {_agent_count} 个分析师并行")

            try:
                # ── 新 Agent 系统（无 Ollama 依赖）────────────────────────
                _analysis = await run_team_analysis(
                    symbol=sym,
                    args=team_args,
                    config=self.terminal.config,
                    sanitize_result=_sanitize_team_result_with_market_data,
                    lang=_lang,
                    on_agent_done=_on_agent_done,
                )

                team_result = _analysis.team_result
                _data_bundle = _analysis.data_bundle
                _quality_notes = _analysis.quality_notes or []

                if HAS_RICH:
                    # Synthesis leaf closes the tree, then the detailed Panel
                    render_agent_synthesis_leaf(
                        console,
                        team_result.final_signal,
                        team_result.confidence,
                        team_result.elapsed_sec,
                        lang=_lang,
                    )
                    if _quality_notes:
                        console.print(
                            "  [yellow]数据质量警告:[/yellow] "
                            + "; ".join(_quality_notes[:3])
                        )

                    # Signal divergence notice — only when DebateAgent ran
                    _has_debate = any(
                        getattr(r, "agent", "") == "debate"
                        for r in (team_result.results or [])
                    )
                    if _has_debate:
                        console.print(
                            "  [#C08050]🔥 信号分歧已触发 DebateAgent 调解[/#C08050]"
                        )

                    # Synthesis in a Panel for visual separation
                    from rich import box as _rbox_team
                    from ui.render.team import SIGNAL_COLORS as _SC, VERDICT_STYLE as _VS
                    _syn      = team_result.synthesis or "*(无综合结论)*"
                    _elapsed  = f"  [dim]耗时 {team_result.elapsed_sec:.1f}s[/dim]"
                    _sig_str  = team_result.final_signal or ""
                    _conf_str = (f"  [dim]置信度 {team_result.confidence:.0%}[/dim]"
                                 if team_result.confidence else "")
                    _sig_color = _SC.get(_sig_str.upper(), "dim")
                    _sig_icon  = _VS.get(_sig_str.upper(), ("dim", "●"))[1]
                    _footer    = (f"[{_sig_color}]{_sig_icon} {_sig_str}[/{_sig_color}]"
                                  f"{_conf_str}{_elapsed}")
                    console.print(Panel(
                        f"{_syn}\n\n{_footer}",
                        title="[bold]综合结论[/bold]",
                        box=_rbox_team.ROUNDED,
                        border_style="#C08050",
                        padding=(0, 1),
                    ))
                else:
                    # agents already streamed via _on_agent_done (plain print)
                    if _quality_notes:
                        print("  数据质量警告: " + "; ".join(_quality_notes[:3]))
                    print("\n  ── 综合结论 ──")
                    print(team_result.synthesis or "*(无综合结论)*")
                    print(f"\n  耗时 {team_result.elapsed_sec:.1f}s  "
                          f"Signal: {team_result.final_signal}  "
                          f"置信度: {team_result.confidence:.0%}")

                # 保存报告
                await self._save_team_report(sym, team_result, _data_bundle, _quality_notes)

                # Record the directional call for outcome verification (DPO loop).
                # synthesis + final_signal → detect_direction; entry price fetched
                # by _record_prediction. Best-effort, never blocks.
                try:
                    _call_text = f"{team_result.synthesis or ''} {team_result.final_signal or ''}"
                    self.terminal._record_prediction(sym, _call_text)
                except Exception:
                    pass

            except ImportError as _imp_err:
                # agents 包不可用 — 不再回退到已废弃的 financial_agents
                _m = (f"多代理分析模块加载失败：{_imp_err}。"
                      "请确认 agents 包完整（/install 或 pip install -e .）。")
                console.print(f"\n  [red]{_m}[/red]") if HAS_RICH else print(f"\n  {_m}")
                continue
            except Exception as e:
                msg = f"团队分析失败: {e}"
                console.print(f"\n  [red]{msg}[/red]") if HAS_RICH else print(f"\n  {msg}")
                continue

    async def _save_team_report(self, symbol: str, team_result, data_bundle=None, quality_notes: Optional[list] = None) -> None:
        """将 /team 分析结果保存为 Markdown 报告"""
        saved = save_team_report(
            symbol=symbol,
            team_result=team_result,
            data_bundle=data_bundle,
            quality_notes=quality_notes,
        )
        msg = f"  📄 报告已保存: {saved.path}"
        console.print(f"  [dim]{msg}[/dim]") if HAS_RICH else print(msg)
