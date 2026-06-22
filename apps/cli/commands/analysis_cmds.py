"""AnalysisCommandsMixin — market analysis and indicator commands."""

from __future__ import annotations

from apps.cli.commands.market import parse_analysis_args


class AnalysisCommandsMixin:
    """Mixin: market analysis and technical indicator commands."""

    async def cmd_analyze(self, args: str):
        """Deep analysis: fetch real quote + TA + fundamentals, then ask LLM."""
        parsed = parse_analysis_args(args)
        symbol = parsed.symbol
        is_cn = _is_ashare_symbol(symbol)
        response_lang = parsed.lang or ("zh" if is_cn else "en")

        if HAS_RICH:
            with console.status(f"[dim]正在获取 {symbol} 数据...[/dim]", spinner="dots"):
                ctx = await self._build_analyze_context(symbol, is_cn)
        else:
            print(f"Fetching data for {symbol}...")
            ctx = await self._build_analyze_context(symbol, is_cn)

        if parsed.focus == "volume":
            if response_lang == "en":
                ctx += (
                    "\n\n### User Focus\n"
                    "- Focus on volume, price-volume confirmation, and volume versus recent average. "
                    "If volume is unavailable, state that the source did not return it and do not invent values."
                )
            else:
                ctx += (
                    "\n\n### 用户关注\n"
                    "- 重点分析成交量、量价关系、成交量相对近期均量的变化；"
                    "若成交量数据不可用，请直接说明来源未返回成交量，不要编造数值。"
                )
        elif parsed.focus:
            heading = "User Focus" if response_lang == "en" else "用户关注"
            label = "Focus on" if response_lang == "en" else "重点分析"
            ctx += f"\n\n### {heading}\n- {label}: {parsed.focus}"

        await self.terminal.send_message(build_analyze_prompt(symbol, ctx, is_cn, response_lang=response_lang))
        try:
            _resp = next((m["content"] for m in reversed(self.terminal.conversation)
                          if m.get("role") == "assistant" and m.get("content")), "")
            if _resp:
                self.terminal._record_prediction(symbol, _resp)
        except Exception:
            pass

    async def _build_analyze_context(self, symbol: str, is_cn: bool) -> str:
        """Fetch real market data and return a structured context string for the LLM."""
        return await build_analyze_context(
            symbol,
            is_cn,
            has_mdc=_HAS_MDC,
            get_mdc=_get_mdc if _HAS_MDC else None,
            ashare_name_lookup=_ashare_code_to_name,
            has_brokers=_HAS_BROKERS,
            get_broker_registry=_get_broker_registry if _HAS_BROKERS else None,
            logger=logger,
        )

    async def cmd_macro(self, args: str):
        """/macro [us|cn|rates|calendar] [indicator]  — 宏观经济数据仪表板"""
        import asyncio as _asyncio
        parts = args.strip().lower().split() if args.strip() else []
        region = parts[0] if parts else "all"
        indicator = parts[1] if len(parts) > 1 else "all"

        try:
            from macro_tools import get_us_macro, get_cn_macro, get_central_bank_rates, get_economic_calendar
        except ImportError:
            if HAS_RICH:
                console.print("[red]macro_tools 模块未找到[/red]")
            return

        loop = _asyncio.get_event_loop()

        if region in ("us", "all"):
            if HAS_RICH:
                with console.status("[dim]获取美国宏观数据 (FRED)...[/dim]", spinner="dots"):
                    r = await loop.run_in_executor(None, lambda: get_us_macro(indicator if region == "us" else "all"))
            else:
                r = get_us_macro(indicator if region == "us" else "all")
            _render_macro_result(r, "🇺🇸 美国宏观")

        if region in ("cn", "all"):
            if HAS_RICH:
                with console.status("[dim]获取中国宏观数据 (akshare)...[/dim]", spinner="dots"):
                    r_cn = await loop.run_in_executor(None, lambda: get_cn_macro(indicator if region == "cn" else "all"))
            else:
                r_cn = get_cn_macro(indicator if region == "cn" else "all")
            _render_macro_result(r_cn, "🇨🇳 中国宏观")

        if region in ("rates", "all"):
            if HAS_RICH:
                with console.status("[dim]获取央行利率...[/dim]", spinner="dots"):
                    r_rates = await loop.run_in_executor(None, get_central_bank_rates)
            else:
                r_rates = get_central_bank_rates()
            _render_cb_rates(r_rates)

        if region == "calendar":
            if HAS_RICH:
                with console.status("[dim]获取经济日历...[/dim]", spinner="dots"):
                    r_cal = await loop.run_in_executor(None, lambda: get_economic_calendar(7))
            else:
                r_cal = get_economic_calendar(7)
            _render_econ_calendar(r_cal)

    async def cmd_options(self, args: str):
        """/options <symbol> [calls|puts] [expiry]  — 期权链查询"""
        parts = args.strip().split() if args.strip() else []
        symbol = parts[0].upper() if parts else "AAPL"
        opt_type = "both"
        expiry = ""
        for p in parts[1:]:
            if p.lower() in ("calls", "puts"):
                opt_type = p.lower()
            elif "-" in p and len(p) == 10:
                expiry = p

        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH:
                console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]获取 {symbol} 期权链...[/dim]", spinner="dots"):
                from local_finance_tools import _get_options_chain
                r = await loop.run_in_executor(None, _get_options_chain,
                                               {"symbol": symbol, "type": opt_type, "expiry": expiry, "limit": 20})
        else:
            from local_finance_tools import _get_options_chain
            r = _get_options_chain({"symbol": symbol, "type": opt_type, "expiry": expiry, "limit": 20})

        if not r.get("success"):
            if HAS_RICH:
                console.print(f"[red]{r.get('error')}[/red]")
            return

        _render_options_chain(r)
        try:
            spot = r.get("current_price") or r.get("spot_price")
            if spot and spot > 0:
                from packages.quant_engine.stochastic.options_pricing import OptionSpec, black_scholes
                T = 30 / 365
                r_f = 0.05
                chain = r.get("calls", []) or r.get("chain", []) or []
                sigma = 0.25
                for row in chain[:5]:
                    iv = row.get("impliedVolatility") or row.get("iv")
                    if iv and 0.01 < float(iv) < 5.0:
                        sigma = float(iv)
                        break

                atm_call = black_scholes(OptionSpec(S=spot, K=round(spot, -1) or spot, T=T, r=r_f, sigma=sigma, option_type="call"))
                atm_put = black_scholes(OptionSpec(S=spot, K=round(spot, -1) or spot, T=T, r=r_f, sigma=sigma, option_type="put"))
                if HAS_RICH:
                    from rich.table import Table
                    from rich import box as _box
                    tbl = Table(title=f"[bold]B-S ATM 理论价格[/bold]  σ={sigma:.0%}  T=30d  r=5%",
                                box=_box.SIMPLE, show_header=True, header_style="bold dim")
                    tbl.add_column("", style="dim")
                    tbl.add_column("理论价", justify="right")
                    tbl.add_column("Delta", justify="right")
                    tbl.add_column("Gamma", justify="right")
                    tbl.add_column("Theta/日", justify="right")
                    tbl.add_column("Vega/1%", justify="right")
                    tbl.add_column("Vanna", justify="right")
                    tbl.add_row("Call", f"{atm_call.price:.2f}", f"{atm_call.delta:+.3f}",
                                f"{atm_call.gamma:.4f}", f"{atm_call.theta:+.4f}",
                                f"{atm_call.vega:.4f}", f"{atm_call.vanna:.4f}")
                    tbl.add_row("Put", f"{atm_put.price:.2f}", f"{atm_put.delta:+.3f}",
                                f"{atm_put.gamma:.4f}", f"{atm_put.theta:+.4f}",
                                f"{atm_put.vega:.4f}", f"{atm_put.vanna:.4f}")
                    console.print(tbl)
                else:
                    print(f"B-S ATM call={atm_call.price:.2f} Δ={atm_call.delta:+.3f}  put={atm_put.price:.2f} Δ={atm_put.delta:+.3f}  σ={sigma:.0%}")
        except Exception:
            pass

    async def cmd_quality(self, args: str):
        """/quality <symbol>  — Piotroski F-Score + Altman Z-Score 双维财务质量评估"""
        symbol = args.strip().upper() if args.strip() else "AAPL"
        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH:
                console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]计算 {symbol} 财务质量评分...[/dim]", spinner="dots"):
                from local_finance_tools import _piotroski_fscore, _altman_zscore
                f_r = await loop.run_in_executor(None, _piotroski_fscore, {"symbol": symbol})
                z_r = await loop.run_in_executor(None, _altman_zscore, {"symbol": symbol})
        else:
            from local_finance_tools import _piotroski_fscore, _altman_zscore
            f_r = _piotroski_fscore({"symbol": symbol})
            z_r = _altman_zscore({"symbol": symbol})

        _render_quality_scores(symbol, f_r, z_r)

    async def cmd_ichimoku(self, args: str):
        """/ichimoku <symbol>  — 一目均衡表分析"""
        symbol = args.strip().upper() if args.strip() else "AAPL"
        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH:
                console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status(f"[dim]计算 {symbol} 一目均衡表...[/dim]", spinner="dots"):
                from local_finance_tools import _calculate_ichimoku
                r = await loop.run_in_executor(None, _calculate_ichimoku, {"symbol": symbol})
        else:
            from local_finance_tools import _calculate_ichimoku
            r = _calculate_ichimoku({"symbol": symbol})

        _render_ichimoku(r)

    async def cmd_fear_greed(self, args: str):
        """/feargreed  — 加密货币恐惧贪婪指数"""
        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH:
                console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if HAS_RICH:
            with console.status("[dim]获取恐惧贪婪指数...[/dim]", spinner="dots"):
                from local_finance_tools import _get_fear_greed_index
                r = await loop.run_in_executor(None, _get_fear_greed_index, {})
        else:
            from local_finance_tools import _get_fear_greed_index
            r = _get_fear_greed_index({})

        _render_fear_greed(r)

    async def cmd_funding(self, args: str):
        """/funding [compare] [BTC ETH SOL] [exchange]  — 永续合约资金费率"""
        parts = args.strip().split() if args.strip() else []
        compare_mode = any(p.lower() == "compare" for p in parts)
        parts = [p for p in parts if p.lower() != "compare"]

        exchange = "binance"
        syms = []
        for p in parts:
            if p.lower() in ("binance", "okx", "bybit", "coinbase"):
                exchange = p.lower()
            else:
                syms.append(p.upper() + "/USDT" if "/" not in p else p.upper())
        if not syms:
            syms = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

        if not _HAS_LOCAL_FINANCE:
            if HAS_RICH:
                console.print("[red]local_finance_tools 未加载[/red]")
            return

        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        if compare_mode:
            if HAS_RICH:
                with console.status("[dim]并行查询 binance / okx / bybit...[/dim]", spinner="dots"):
                    from local_finance_tools import _get_funding_rates_compare
                    r = await loop.run_in_executor(None, _get_funding_rates_compare, {"symbols": syms})
            else:
                from local_finance_tools import _get_funding_rates_compare
                r = _get_funding_rates_compare({"symbols": syms})
            _render_funding_compare(r)
        else:
            if HAS_RICH:
                with console.status(f"[dim]获取 {exchange} 资金费率...[/dim]", spinner="dots"):
                    from local_finance_tools import _get_funding_rates
                    r = await loop.run_in_executor(None, _get_funding_rates, {"exchange": exchange, "symbols": syms})
            else:
                from local_finance_tools import _get_funding_rates
                r = _get_funding_rates({"exchange": exchange, "symbols": syms})
            _render_funding_rates(r)
