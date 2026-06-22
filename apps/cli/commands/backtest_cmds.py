"""BacktestCommandsMixin — backtest/strategy/scaffold commands.

Extracted from aria_cli.py. Module globals imported lazily inside method bodies.
"""
from __future__ import annotations


def format_backtest_data_error(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
    local_error: str = "",
    bars: int = 0,
) -> str:
    """Return a user-facing backtest failure message."""
    if bars and bars < 5:
        return (
            f"{symbol} 历史数据仅 {bars} 个交易日，不足以回测。"
            "请换历史更长的标的或缩短策略周期。"
        )
    if local_error:
        low = local_error.lower()
        if "histor" in low or "data" in low or "empty" in low:
            return (
                f"{symbol} 回测失败：{local_error}。"
                "请检查数据源是否可用、ticker 是否正确，或先运行 /doctor /health。"
            )
    return (
        f"{symbol} 在 {start_date} → {end_date} 范围内没有可用历史数据。"
        "请检查代码是否正确、标的是否已上市/未停牌，或缩短回测区间。"
    )


def _bt_num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _bt_pct(value, digits: int = 1, signed: bool = False) -> str:
    number = _bt_num(value)
    sign = "+" if signed and number >= 0 else ""
    return f"{sign}{number * 100:.{digits}f}%"


def _bt_money(value, currency: str = "USD") -> str:
    number = _bt_num(value)
    return f"{currency} {number:,.0f}" if abs(number) >= 1000 else f"{currency} {number:,.2f}"


def _bt_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _bt_trade_count(data: dict) -> int:
    for key in ("total_trades", "num_trades", "n_trades", "trades"):
        if key in data and data.get(key) is not None:
            return _bt_int(data.get(key))
    return 0


def _bt_value(data: dict, *keys, default=None):
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)
    return default


def _bt_volume_summary(data: dict) -> dict:
    summary = data.get("volume_summary")
    return summary if isinstance(summary, dict) else {}


def _bt_result_summary(data: dict) -> str:
    total = _bt_num(data.get("total_return"))
    benchmark = _bt_num(_bt_value(data, "buy_hold_return", "benchmark_return", default=0))
    sharpe = _bt_num(data.get("sharpe_ratio"))
    max_dd = _bt_num(data.get("max_drawdown"))
    relation = "高于" if total > benchmark else "低于" if total < benchmark else "持平"
    return (
        f"结论：策略收益 {_bt_pct(total)}，{relation}买入持有 {_bt_pct(benchmark)}；"
        f"Sharpe {sharpe:.2f}，最大回撤 {_bt_pct(max_dd)}。"
    )


class BacktestCommandsMixin:
    """Mixin providing backtest, strategy, factor-lab, and scaffold commands."""

    async def cmd_backtest(self, args: str):
        """Direct REST backtest → /api/v1/backtest (falls back to Aria tool).

        Usage:
          /backtest [strategy] [symbol] [start_date] [end_date]
          /backtest momentum AAPL 2023-01-01 2024-12-31
          /backtest momentum AAPL --period 1y
          /backtest momentum AAPL --period 6m
        """
        import re as _re_bt
        today = __import__("datetime").date.today()

        raw_parts = args.split() if args else ["momentum", "SPY"]

        # Handle flags (e.g. --period 1y, --fast 20, --slow 60, --symbol AAPL)
        _period_match = None
        _symbol_flag = None
        _fast_period = 20
        _slow_period = 60
        _momentum_period = 20
        _initial_capital = 100000
        _output_dir = None
        _cleaned = []
        i = 0
        while i < len(raw_parts):
            if raw_parts[i] == "--period" and i + 1 < len(raw_parts):
                _period_match = raw_parts[i + 1]
                i += 2
            elif raw_parts[i].startswith("--period="):
                _period_match = raw_parts[i].split("=", 1)[1]
                i += 1
            elif raw_parts[i] == "--symbol" and i + 1 < len(raw_parts):
                _symbol_flag = raw_parts[i + 1].upper()
                i += 2
            elif raw_parts[i].startswith("--symbol="):
                _symbol_flag = raw_parts[i].split("=", 1)[1].upper()
                i += 1
            elif raw_parts[i] == "--fast" and i + 1 < len(raw_parts):
                try:
                    _fast_period = int(raw_parts[i + 1])
                except Exception:
                    pass
                i += 2
            elif raw_parts[i].startswith("--fast="):
                try:
                    _fast_period = int(raw_parts[i].split("=", 1)[1])
                except Exception:
                    pass
                i += 1
            elif raw_parts[i] == "--slow" and i + 1 < len(raw_parts):
                try:
                    _slow_period = int(raw_parts[i + 1])
                except Exception:
                    pass
                i += 2
            elif raw_parts[i].startswith("--slow="):
                try:
                    _slow_period = int(raw_parts[i].split("=", 1)[1])
                except Exception:
                    pass
                i += 1
            elif raw_parts[i] == "--momentum" and i + 1 < len(raw_parts):
                try:
                    _momentum_period = int(raw_parts[i + 1])
                except Exception:
                    pass
                i += 2
            elif raw_parts[i].startswith("--momentum="):
                try:
                    _momentum_period = int(raw_parts[i].split("=", 1)[1])
                except Exception:
                    pass
                i += 1
            elif raw_parts[i] == "--capital" and i + 1 < len(raw_parts):
                try:
                    _initial_capital = float(raw_parts[i + 1])
                except Exception:
                    pass
                i += 2
            elif raw_parts[i].startswith("--capital="):
                try:
                    _initial_capital = float(raw_parts[i].split("=", 1)[1])
                except Exception:
                    pass
                i += 1
            elif raw_parts[i] == "--output" and i + 1 < len(raw_parts):
                _output_dir = raw_parts[i + 1]
                i += 2
            elif raw_parts[i].startswith("--output="):
                _output_dir = raw_parts[i].split("=", 1)[1]
                i += 1
            else:
                _cleaned.append(raw_parts[i])
                i += 1
        parts = _cleaned

        # Resolve --period to a start date
        if _period_match:
            _pm = _period_match.lower()
            _months = {"1m": 1, "3m": 3, "6m": 6, "1y": 12, "2y": 24, "3y": 36, "5y": 60}
            if _pm in _months:
                from datetime import timedelta
                _delta_days = _months[_pm] * 30
                _start_dt = today - timedelta(days=_delta_days)
                _resolved_start = _start_dt.isoformat()
            else:
                _resolved_start = None
        else:
            _resolved_start = None

        _known_strategies = {"momentum", "mom", "sma_cross", "ma_cross", "moving_average",
                              "buy_hold", "buyhold", "hold", "ml", "ml_signal"}
        if len(parts) == 1 and parts[0].lower() not in _known_strategies:
            strategy = "momentum"
            symbol = parts[0].upper()
        else:
            strategy = parts[0] if len(parts) > 0 else "momentum"
            symbol = parts[1].upper() if len(parts) > 1 else "SPY"
        if _symbol_flag:
            symbol = _symbol_flag

        # ── ML 信号组合回测 ──────────────────────────────────────────────────
        if strategy.lower() in ("ml", "ml_signal"):
            await self._cmd_ml_signal_backtest(parts[1:], start_date=start_date,
                                                end_date=end_date,
                                                capital=_initial_capital)
            return

        # Positional start/end dates only accepted if they look like YYYY-MM-DD
        _date_re = _re_bt.compile(r'^\d{4}-\d{2}-\d{2}$')
        _raw_start = parts[2] if len(parts) > 2 else None
        _raw_end   = parts[3] if len(parts) > 3 else None
        start_date = (_raw_start if _raw_start and _date_re.match(_raw_start) else None) \
                     or _resolved_start or "2023-01-01"
        end_date   = (_raw_end if _raw_end and _date_re.match(_raw_end) else None) \
                     or today.isoformat()

        label = f"Backtesting {strategy} on {symbol} ({start_date}→{end_date})"
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")

        async def _do_backtest():
            from backtest_report import BacktestConfig, generate_backtest_report
            local_config = BacktestConfig(
                symbol=symbol,
                strategy=strategy,
                start_date=start_date,
                end_date=end_date,
                initial_capital=float(_initial_capital),
                fast_period=int(_fast_period),
                slow_period=int(_slow_period),
                momentum_period=int(_momentum_period),
            )
            local_error = ""
            try:
                _out_path = None
                if _output_dir:
                    _out_path = pathlib.Path(_output_dir).expanduser()
                    if not _out_path.is_absolute():
                        from artifacts import user_generated_dir
                        _out_path = user_generated_dir() / _out_path
                local_result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: generate_backtest_report(local_config, output_dir=_out_path)
                )
                if local_result and local_result.get("success"):
                    return {"success": True, "data": local_result, "_source": "local-real-data"}
                local_error = local_result.get("error") if local_result else ""
                logger.debug("local backtest failed, falling back to yfinance direct: %s", local_error or "None")
            except Exception as _e:
                local_error = str(_e)
                logger.debug("local backtest failed, falling back to yfinance direct: %s", _e)

            # ── Direct yfinance backtest — works offline, no backend needed ──
            try:
                import yfinance as _yf
                import numpy as _np
                import statistics as _stats

                _yf_bars = [0]   # records bars found, for a precise error message

                def _run_yf_backtest():
                    _ticker = _yf.Ticker(symbol)
                    _df = _ticker.history(start=start_date, end=end_date, auto_adjust=True)
                    if _df is None or _df.empty:
                        return {"success": False, "error": format_backtest_data_error(
                            symbol,
                            start_date=start_date,
                            end_date=end_date,
                            bars=0,
                        ), "bars": 0}
                    _close = _df["Close"].dropna()
                    _yf_bars[0] = len(_close)
                    if len(_close) < 5:
                        return {"success": False, "error": format_backtest_data_error(
                            symbol,
                            start_date=start_date,
                            end_date=end_date,
                            bars=_yf_bars[0],
                        ), "bars": _yf_bars[0]}
                    _prices = list(_close)
                    n = len(_prices)
                    # Momentum strategy: buy when N-day momentum > 0
                    _mp = int(_momentum_period)
                    _signals = [0] * n
                    for i in range(_mp, n):
                        _signals[i] = 1 if _prices[i] > _prices[i - _mp] else -1
                    # Simulate portfolio
                    _cap = float(_initial_capital)
                    _position = 0.0  # shares
                    _cash = _cap
                    _trades = 0
                    _portfolio = []
                    for i in range(1, n):
                        _p = _prices[i]
                        _sig = _signals[i - 1]
                        if _sig == 1 and _position == 0 and _cash > 0:
                            _shares = _cash / _p
                            _position = _shares
                            _cash = 0
                            _trades += 1
                        elif _sig == -1 and _position > 0:
                            _cash = _position * _p
                            _position = 0
                            _trades += 1
                        _portfolio.append(_cash + _position * _p)
                    if not _portfolio:
                        return None
                    _final = _portfolio[-1]
                    _total_return = (_final - _cap) / _cap
                    _bh_return = (_prices[-1] - _prices[0]) / _prices[0]
                    # Daily returns for Sharpe
                    _rets = [(_portfolio[i] - _portfolio[i-1]) / _portfolio[i-1] for i in range(1, len(_portfolio)) if _portfolio[i-1] > 0]
                    _ann_return = sum(_rets) / len(_rets) * 252 if _rets else 0
                    _ann_vol = _stats.stdev(_rets) * (252 ** 0.5) if len(_rets) > 1 else 0
                    _sharpe = _ann_return / _ann_vol if _ann_vol > 0 else 0
                    # Max drawdown
                    _peak = _portfolio[0]
                    _max_dd = 0.0
                    for v in _portfolio:
                        if v > _peak:
                            _peak = v
                        _dd = (_peak - v) / _peak if _peak > 0 else 0
                        if _dd > _max_dd:
                            _max_dd = _dd
                    # Equity curve (sampled monthly)
                    _step = max(1, n // 24)
                    _equity_curve = [
                        {"date": str(_close.index[min(i + 1, n - 1)].date()), "strategy": round(_portfolio[min(i, len(_portfolio)-1)], 2)}
                        for i in range(0, len(_portfolio), _step)
                    ]
                    _win_trades = sum(1 for i in range(1, len(_portfolio)) if _portfolio[i] > _portfolio[i-1])
                    _vol = _df["Volume"].dropna() if "Volume" in _df else []
                    _vol_count = len(_vol) if hasattr(_vol, "__len__") else 0
                    return {
                        "success": True,
                        "symbol": symbol,
                        "strategy": strategy,
                        "total_return": round(_total_return, 4),
                        "buy_hold_return": round(_bh_return, 4),
                        "annualized_return": round(_ann_return, 4),
                        "sharpe_ratio": round(_sharpe, 3),
                        "max_drawdown": round(-_max_dd, 4),
                        "win_rate": round(_win_trades / max(len(_portfolio) - 1, 1), 3),
                        "num_trades": _trades,
                        "equity_curve": _equity_curve,
                        "data_provider": "yfinance",
                        "provider_chain": ["yfinance"],
                        "start_date": start_date,
                        "end_date": end_date,
                        "initial_capital": float(_initial_capital),
                        "bars": n,
                        "volume_summary": {
                            "last": round(float(_vol.iloc[-1]), 2) if _vol_count else None,
                            "average": round(float(_vol.mean()), 2) if _vol_count else None,
                            "min": round(float(_vol.min()), 2) if _vol_count else None,
                            "max": round(float(_vol.max()), 2) if _vol_count else None,
                            "coverage": round(_vol_count / max(len(_df), 1), 4),
                        },
                    }

                yf_result = await asyncio.get_event_loop().run_in_executor(None, _run_yf_backtest)
                if yf_result and yf_result.get("success"):
                    return {"success": True, "data": yf_result, "_source": "yfinance-local"}
                if yf_result and not yf_result.get("success"):
                    return yf_result
            except Exception as _e:
                logger.debug("yfinance direct backtest failed: %s", _e)

            import aiohttp
            payload = {
                "symbols": [symbol],
                "strategy_type": strategy,
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": float(_initial_capital),
                "commission_rate": 0.0003,
                "include_monte_carlo": False,
            }
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.post(f"{api_url}/api/v1/backtest", json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status == 200:
                            body = await resp.json()
                            _rest_data = body.get("data", body)
                            if _rest_data and isinstance(_rest_data, dict):
                                return {"success": True, "data": _rest_data, "_source": "rest"}
            except Exception as _e:
                logger.debug("backtest REST call failed: %s", _e)
            # Honest, actionable error: the dominant cause is too little history
            # (new IPO / halted / wrong ticker), not "all data sources down".
            if 0 < _yf_bars[0] < 5:
                return {"success": False, "error": format_backtest_data_error(
                    symbol,
                    start_date=start_date,
                    end_date=end_date,
                    bars=_yf_bars[0],
                )}
            return {"success": False, "error": format_backtest_data_error(
                symbol,
                start_date=start_date,
                end_date=end_date,
                local_error=local_error,
                bars=_yf_bars[0],
            )}

        if HAS_RICH:
            with console.status(f"[dim]{label}...[/dim]", spinner="dots"):
                result = await _do_backtest()
        else:
            print(label)
            result = await _do_backtest()

        # Guard: execute_aria_tool / REST fallback can return None
        if not result:
            _print_error("回测服务不可用 (API未运行)  — 先启动后端: cd apps/api && python -m uvicorn src.main:app", "tool")
            return

        if result.get("success"):
            d = result.get("data", result)
            if not isinstance(d, dict):
                _print_error(f"回测结果格式异常: {type(d)}", "tool")
                return
            src = result.get("_source", "aria")
            if HAS_RICH:
                from rich.table import Table
                tbl = Table(title=f"[bold]{symbol} · {strategy.upper()}[/bold]", show_header=True, header_style="bold")
                tbl.add_column("Metric", style="#57606a")
                tbl.add_column("Value", justify="right")
                tbl.add_column("vs B&H", justify="right", style="#57606a")
                bh = _bt_num(_bt_value(d, "buy_hold_return", "benchmark_return", default=0))
                trades = _bt_trade_count(d)
                rows = [
                    ("Total Return", _bt_pct(d.get("total_return")), _bt_pct(bh)),
                    ("Ann. Return",  _bt_pct(_bt_value(d, "annualized_return", "annual_return", default=0)), ""),
                    ("Sharpe Ratio", f"{_bt_num(d.get('sharpe_ratio')):.2f}", ""),
                    ("Max Drawdown", _bt_pct(d.get("max_drawdown")), ""),
                    ("Win Rate",     _bt_pct(d.get("win_rate")), ""),
                    ("# Trades",     str(trades), ""),
                ]
                if d.get("calmar_ratio"):
                    rows.append(("Calmar Ratio", f"{d['calmar_ratio']:.2f}", ""))
                if d.get("sortino_ratio"):
                    rows.append(("Sortino Ratio", f"{d['sortino_ratio']:.2f}", ""))
                for r in rows:
                    tbl.add_row(*r)
                console.print(tbl)
                console.print(f"  [bold]{_bt_result_summary(d)}[/bold]")

                actual_start = _bt_value(d, "start", "start_date", default=start_date)
                actual_end = _bt_value(d, "end", "end_date", default=end_date)
                bars = _bt_int(d.get("bars"))
                initial = _bt_money(d.get("initial_capital", _initial_capital))
                console.print(
                    f"  [#57606a]source:[/#57606a] {src}"
                    f"  [#57606a]period:[/#57606a] {actual_start} → {actual_end}"
                    f"  [#57606a]bars:[/#57606a] {bars}"
                    f"  [#57606a]capital:[/#57606a] {initial}"
                )
                console.print(
                    f"  [#57606a]params:[/#57606a] "
                    f"momentum={_momentum_period} fast={_fast_period} slow={_slow_period}"
                )
                if d.get("provider_chain"):
                    chain = " → ".join(str(x) for x in d.get("provider_chain") or [])
                    status = d.get("data_status") or "complete"
                    missing = ", ".join(str(x) for x in (d.get("missing_fields") or [])) or "none"
                    console.print(
                        f"  [#57606a]data:[/#57606a] {chain}"
                        f"  [#57606a]status:[/#57606a] {status}"
                        f"  [#57606a]missing:[/#57606a] {missing}"
                    )
                vol = _bt_volume_summary(d)
                if vol:
                    avg = vol.get("average")
                    last = vol.get("last")
                    coverage = _bt_num(vol.get("coverage"))
                    console.print(
                        f"  [#57606a]volume:[/#57606a] "
                        f"avg {avg:,.0f} · last {last:,.0f} · coverage {coverage:.0%}"
                        if avg is not None and last is not None
                        else f"  [#57606a]volume:[/#57606a] unavailable"
                    )
                if d.get("report_path"):
                    console.print(f"  [#57606a]report:[/#57606a] {d['report_path']}")
                if trades == 0:
                    console.print(
                        "  [yellow]注意:[/yellow] # Trades 为 0，表示本次规则没有触发入场；"
                        "收益可能来自全程空仓/持仓逻辑或上游交易统计口径。"
                    )
            else:
                print(f"Total Return: {d.get('total_return',0)*100:.1f}%  Sharpe: {d.get('sharpe_ratio',0):.2f}  MaxDD: {d.get('max_drawdown',0)*100:.1f}%")
                if d.get("report_path"):
                    print(f"HTML Report: {d['report_path']}")

            eq = d.get("equity_curve", [])
            if eq:
                strat_vals = [p.get("strategy", p.get("portfolio_value", 0)) for p in eq if isinstance(p, dict)]
                if strat_vals:
                    spark = format_sparkline(strat_vals)
                    if spark:
                        console.print(f"  [#57606a]Equity:[/#57606a] [green]{spark}[/green]" if HAS_RICH else f"  Equity: {spark}")
            await self._print_backtest_broker_plan(d)
        else:
            _print_error(f"Backtest failed: {result.get('error', 'Unknown')}", "tool")

    async def _print_backtest_broker_plan(self, backtest_result: dict):
        """Print an account-aware order plan for a successful backtest, if a broker is connected."""
        if not _HAS_BROKERS or not isinstance(backtest_result, dict):
            return
        try:
            reg = _get_broker_registry()
            broker = reg.active() if reg else None
            if not broker:
                return
            from brokers import plans_from_strategy_results, snapshot_from_broker
            import asyncio as _aio

            def _build_plan():
                snapshot = snapshot_from_broker(broker)
                plans = plans_from_strategy_results(snapshot, [backtest_result])
                return snapshot, plans[0] if plans else None

            snapshot, plan = await _aio.get_event_loop().run_in_executor(None, _build_plan)
            if not plan:
                return
            data = plan.to_dict()
            order = data.get("estimated_order") or {}
            risk = data.get("risk") or {}
            if HAS_RICH:
                from rich.table import Table
                t = Table(title=f"Broker Plan — {snapshot.broker_label}", show_header=False, box=None)
                t.add_column("Field", style="dim")
                t.add_column("Value")
                t.add_row("Current Weight", f"{data.get('current_weight', 0) * 100:.2f}%")
                t.add_row("Target Weight", f"{data.get('target_weight', 0) * 100:.2f}%")
                if order:
                    side = "买入" if order.get("side") == "buy" else "卖出"
                    t.add_row("Suggested Order", f"{side} {order.get('quantity', 0):,.0f} {data.get('symbol')} @ {order.get('price', 0):,.2f}")
                    t.add_row("Estimated Value", f"{snapshot.currency} {order.get('estimated_value', 0):,.2f}")
                    t.add_row("Cash After", f"{snapshot.currency} {data.get('cash_after', 0):,.2f}")
                else:
                    t.add_row("Suggested Order", "No trade")
                status = "passed" if risk.get("passed") else "blocked"
                t.add_row("Risk Gate", status)
                console.print(t)
                for msg in risk.get("violations", []):
                    console.print(f"  [red]- {msg}[/red]")
                for msg in risk.get("warnings", []):
                    console.print(f"  [yellow]- {msg}[/yellow]")
                if order and risk.get("passed"):
                    console.print("  [dim]这是订单计划，不会自动下单。执行前仍需用户明确确认。[/dim]")
            else:
                print(f"Broker Plan: {snapshot.broker_label}")
                if order:
                    print(f"  {order.get('side')} {order.get('quantity')} {data.get('symbol')} @ {order.get('price')}")
                print(f"  Risk: {'passed' if risk.get('passed') else 'blocked'}")
        except Exception as exc:
            logger.debug("backtest broker plan skipped: %s", exc)

    async def cmd_walk_forward(self, args: str):
        """Walk-Forward 滚动回测 → /api/v1/backtest/walk-forward"""
        parts = args.split() if args else ["SPY"]
        symbol = parts[0].upper() if parts else "SPY"
        strategy = parts[1] if len(parts) > 1 else "momentum"
        method = parts[2] if len(parts) > 2 else "rolling"
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")

        label = f"Walk-Forward ({method}) · {strategy} · {symbol}"
        import aiohttp

        async def _do_wf():
            payload = {
                "symbol": symbol, "strategy_type": strategy, "method": method,
                "start_date": "2020-01-01",
                "end_date": __import__("datetime").date.today().isoformat(),
                "train_period_days": 252, "test_period_days": 63, "step_days": 21,
            }
            async with aiohttp.ClientSession() as sess:
                async with sess.post(f"{api_url}/api/v1/backtest/walk-forward", json=payload, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    body = await resp.json()
                    return body.get("data", body)

        if HAS_RICH:
            with console.status(f"[dim]{label}...[/dim]", spinner="dots"):
                try:
                    data = await _do_wf()
                except Exception as e:
                    _print_error(str(e), "tool"); return
        else:
            print(label)
            try:
                data = await _do_wf()
            except Exception as e:
                _print_error(str(e), "tool"); return

        summary = data.get("summary", data)
        folds = data.get("folds", [])
        verdict = summary.get("verdict", "?")
        verdict_color = "green" if verdict == "PASS" else "red"

        if HAS_RICH:
            from rich.table import Table
            # Summary
            console.print(f"\n[bold]{symbol} · {strategy} · {method}[/bold]  Verdict: [bold {verdict_color}]{verdict}[/bold {verdict_color}]")
            console.print(f"  Folds: {summary.get('n_folds')}  "
                          f"Avg OOS Sharpe: [bold]{summary.get('avg_oos_sharpe', 0):.3f}[/bold]  "
                          f"Consistency: {summary.get('consistency_ratio_pct', 0):.0f}%  "
                          f"Robustness: {summary.get('robustness_score', 0):.3f}  "
                          f"p-value: {summary.get('p_value', 1):.4f}")
            # Fold table
            if folds:
                tbl = Table(title="Fold Results", show_header=True, header_style="bold dim")
                for col in ["Fold", "Test Period", "OOS Return", "OOS Sharpe", "OOS MaxDD", "Win%"]:
                    tbl.add_column(col, justify="right")
                for f in folds[:12]:
                    ret = f.get("test_return_pct", 0)
                    tbl.add_row(
                        str(f.get("fold_id", "")),
                        f.get("test_period", ""),
                        f"{'+'if ret>=0 else ''}{ret:.1f}%",
                        f"{f.get('test_sharpe', 0):.3f}",
                        f"{f.get('test_max_drawdown_pct', 0):.1f}%",
                        f"{f.get('test_win_rate_pct', 0):.0f}%",
                    )
                console.print(tbl)
        else:
            print(f"Verdict: {verdict}  Folds: {summary.get('n_folds')}  Avg OOS Sharpe: {summary.get('avg_oos_sharpe',0):.3f}")

    async def cmd_auto_strategy(self, args: str):
        """AI strategy auto-optimization loop (unique to Aria).

        Generates a strategy, runs backtest, reads results, iterates until
        the target metric is reached or max rounds exhausted.

        Usage:
            /auto-strategy momentum SPY
            /auto-strategy momentum SPY --target sharpe=1.5
            /auto-strategy meanrev AAPL --target sharpe=1.2 --rounds 3
        """
        import re as _re, time as _time

        parts = args.split()
        strategy_type = parts[0].lower() if parts else "momentum"
        symbol = parts[1].upper() if len(parts) > 1 else "SPY"
        target_sharpe = 1.0
        max_rounds = 3
        for p in parts[2:]:
            m = _re.match(r"--target\s*sharpe=([0-9.]+)", p)
            if m:
                target_sharpe = float(m.group(1))
            m = _re.match(r"--rounds=?([0-9]+)", p)
            if m:
                max_rounds = int(m.group(1))

        if HAS_RICH:
            console.print()
            console.print(f"  [bold cyan]🔄 策略自动优化[/bold cyan]  [dim]{strategy_type} / {symbol}  目标 Sharpe≥{target_sharpe}  最多{max_rounds}轮[/dim]")
            console.print()

        best_sharpe = 0.0
        best_version = None

        for round_num in range(1, max_rounds + 1):
            console.print(f"  [bold]第 {round_num}/{max_rounds} 轮[/bold]") if HAS_RICH else print(f"  Round {round_num}/{max_rounds}")

            # ── Step 1: Generate strategy code ──────────────────────────────
            feedback_ctx = ""
            if round_num > 1 and best_version:
                feedback_ctx = (
                    f"\n\nPrevious backtest Sharpe={best_sharpe:.2f} (target={target_sharpe})."
                    " Modify the strategy to improve Sharpe: adjust lookback period, "
                    "add momentum filter, tighten stop-loss, or change position sizing."
                )

            gen_prompt = (
                f"Generate a complete, self-contained Python backtest strategy script.\n"
                f"Strategy type: {strategy_type}\n"
                f"Symbol: {symbol}\n"
                f"Requirements:\n"
                f"1. Use yfinance to download 2 years of daily OHLCV data\n"
                f"2. Implement the {strategy_type} strategy with clear entry/exit signals\n"
                f"3. Simulate trades: track portfolio value, returns, Sharpe ratio\n"
                f"4. Print EXACTLY this at the end (machine-parseable):\n"
                f"   BACKTEST_RESULT: sharpe=X.XX annual_return=X.XX% max_drawdown=X.XX% trades=N\n"
                f"5. All code in one file, no external dependencies except yfinance/pandas/numpy\n"
                f"{feedback_ctx}\n"
                f"Output ONLY the Python code in ```python``` fences."
            )

            _fname = f"auto_strat_{strategy_type}_{symbol}_r{round_num}_{int(_time.time())}.py"
            from artifacts import user_generated_dir as _user_generated_dir
            _fpath = _user_generated_dir() / _fname

            console.print(f"  [dim]生成策略代码...[/dim]") if HAS_RICH else print("  Generating strategy...")
            await self.terminal.send_message(gen_prompt)

            # Extract code from last response
            last_ai = next(
                (m["content"] for m in reversed(self.terminal.conversation)
                 if m.get("role") == "assistant"), ""
            )
            import re as _re2
            py_blocks = _re2.findall(r"```python\n(.*?)```", last_ai, _re2.DOTALL)
            if not py_blocks:
                # fallback: grab after fence
                m = _re2.search(r"```python\n(.*)", last_ai, _re2.DOTALL)
                if m:
                    py_blocks = [m.group(1)]

            if not py_blocks:
                console.print("  [yellow]⚠ 未生成代码，跳过本轮[/yellow]") if HAS_RICH else print("  No code generated, skipping")
                continue

            code = py_blocks[-1].strip()
            _tool_write_file({"path": str(_fpath), "content": code, "_skip_confirm": True})
            console.print(f"  [dim]策略已保存: {_fpath.name}[/dim]") if HAS_RICH else print(f"  Saved: {_fpath.name}")

            # ── Step 2: Run backtest ─────────────────────────────────────────
            console.print(f"  [dim]运行回测...[/dim]") if HAS_RICH else print("  Running backtest...")
            bt_result = _tool_run_command({
                "command": f"python3 {_fpath}",
                "timeout": 120,
            })
            stdout = bt_result.get("data", {}).get("stdout", "") or ""
            stderr = bt_result.get("data", {}).get("stderr", "") or ""

            # ── Step 3: Parse backtest metrics ──────────────────────────────
            sharpe = 0.0
            ann_return = 0.0
            max_dd = 0.0
            n_trades = 0
            m = _re2.search(r"BACKTEST_RESULT:.*?sharpe=([0-9.-]+)", stdout)
            if m:
                sharpe = float(m.group(1))
            m = _re2.search(r"annual_return=([0-9.-]+)%", stdout)
            if m:
                ann_return = float(m.group(1))
            m = _re2.search(r"max_drawdown=([0-9.-]+)%", stdout)
            if m:
                max_dd = float(m.group(1))
            m = _re2.search(r"trades=([0-9]+)", stdout)
            if m:
                n_trades = int(m.group(1))

            # Update best
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_version = _fpath

            # Display round result
            sharpe_color = "green" if sharpe >= target_sharpe else ("yellow" if sharpe > 0 else "red")
            if HAS_RICH:
                console.print(
                    f"  [dim]回测结果:[/dim]  "
                    f"Sharpe=[{sharpe_color}]{sharpe:.2f}[/{sharpe_color}]  "
                    f"年化={ann_return:.1f}%  "
                    f"最大回撤={max_dd:.1f}%  "
                    f"交易次数={n_trades}"
                )
            else:
                print(f"  Backtest: Sharpe={sharpe:.2f}  Return={ann_return:.1f}%  MaxDD={max_dd:.1f}%  Trades={n_trades}")

            if stderr and "Error" in stderr:
                console.print(f"  [red]执行错误: {stderr[:200]}[/red]") if HAS_RICH else print(f"  Error: {stderr[:200]}")

            # ── Step 4: Check convergence ────────────────────────────────────
            if sharpe >= target_sharpe:
                console.print(f"\n  [green]✅ 目标达成！Sharpe={sharpe:.2f} ≥ {target_sharpe}[/green]") if HAS_RICH else print(f"\n  ✓ Target reached: Sharpe={sharpe:.2f}")
                break
            elif round_num < max_rounds:
                console.print(f"  [dim]Sharpe={sharpe:.2f} < 目标{target_sharpe}，继续优化...[/dim]\n") if HAS_RICH else print(f"  Sharpe={sharpe:.2f} < {target_sharpe}, optimizing...\n")

        # ── Summary ──────────────────────────────────────────────────────────
        if HAS_RICH:
            console.print()
            console.print(f"  [bold]优化完成[/bold]  最佳 Sharpe=[{'green' if best_sharpe >= target_sharpe else 'yellow'}]{best_sharpe:.2f}[/{'green' if best_sharpe >= target_sharpe else 'yellow'}]")
            if best_version:
                console.print(f"  最优策略文件: [dim]{best_version}[/dim]")
                console.print(f"  [dim]运行: python3 {best_version}[/dim]")
            console.print()
        else:
            print(f"\n  Best Sharpe={best_sharpe:.2f}  File: {best_version}")

    async def cmd_factor_lab(self, args: str):
        """Factor analysis workstation — compute IC, ICIR, factor returns (Aria exclusive).

        Usage:
            /factor-lab AAPL
            /factor-lab QQQ --days 252
            /factor-lab SPY --factors momentum,value,quality
        """
        import re as _re

        parts = args.split()
        symbol = parts[0].upper() if parts else "SPY"
        days = 252
        for p in parts[1:]:
            m = _re.match(r"--days=?(\d+)", p)
            if m:
                days = int(m.group(1))

        if HAS_RICH:
            console.print()
            console.print(f"  [bold cyan]🔬 因子分析工作台[/bold cyan]  [dim]{symbol}  {days}天数据[/dim]")
            console.print()

        if not _HAS_MDC:
            console.print("[red]需要 market_data_client 模块[/red]") if HAS_RICH else print("market_data_client not available")
            return

        try:
            import numpy as np
            import pandas as pd

            mdc = _get_mdc()

            # ── Fetch data ────────────────────────────────────────────────────
            console.print("  [dim]拉取行情数据...[/dim]") if HAS_RICH else print("  Fetching data...")
            hist = mdc.history(symbol, days=days)
            if not hist.get("success") or not hist.get("data"):
                console.print(f"[red]无法获取 {symbol} 历史数据[/red]") if HAS_RICH else print(f"No data for {symbol}")
                return

            df = pd.DataFrame(hist["data"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["volume"] = pd.to_numeric(df.get("volume", pd.Series()), errors="coerce")
            df = df.dropna(subset=["close"])
            close = df["close"]
            returns = close.pct_change().dropna()

            # ── Compute factors ───────────────────────────────────────────────
            factors: dict = {}

            # 1. Momentum (1M, 3M, 6M, 12M)
            for months, label in [(21, "Mom1M"), (63, "Mom3M"), (126, "Mom6M"), (252, "Mom12M")]:
                if len(close) > months:
                    factors[label] = close.pct_change(months)

            # 2. Mean Reversion (short-term)
            if len(close) > 5:
                factors["MeanRev5D"] = -close.pct_change(5)

            # 3. Volatility (annualized)
            if len(returns) > 20:
                factors["Vol20D"] = returns.rolling(20).std() * np.sqrt(252)

            # 4. Volume trend
            if "volume" in df.columns and df["volume"].notna().sum() > 20:
                vol_series = df["volume"].astype(float)
                factors["VolTrend"] = vol_series.pct_change(20)

            # 5. RSI factor
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            factors["RSI14"] = 100 - 100 / (1 + rs)

            # ── Compute IC (Information Coefficient) for each factor ──────────
            # IC = correlation between factor value at t and next-period return
            fwd_returns = returns.shift(-1)  # 1-day forward return

            ic_results = {}
            for fname, fseries in factors.items():
                try:
                    aligned = pd.concat([fseries, fwd_returns], axis=1).dropna()
                    aligned.columns = ["factor", "fwd"]
                    if len(aligned) < 20:
                        continue
                    ic = aligned["factor"].corr(aligned["fwd"])
                    if np.isnan(ic):
                        continue
                    # Rolling IC (window=20) — compute manually to avoid rolling.apply issues
                    roll_ics = []
                    for start in range(0, len(aligned) - 20, 5):
                        chunk = aligned.iloc[start:start + 20]
                        chunk_ic = chunk["factor"].corr(chunk["fwd"])
                        if not np.isnan(chunk_ic):
                            roll_ics.append(chunk_ic)
                    icir = ic / (np.std(roll_ics) + 1e-9) if len(roll_ics) >= 3 else 0.0
                    ic_results[fname] = {"ic": ic, "icir": float(icir), "abs_ic": abs(ic)}
                except Exception:
                    continue

            # ── Current factor values (latest bar) ───────────────────────────
            latest = {fname: float(fseries.dropna().iloc[-1]) if not fseries.dropna().empty else None
                      for fname, fseries in factors.items()}

            # ── Display results ───────────────────────────────────────────────
            if HAS_RICH:
                console.print(f"  [bold]{symbol}[/bold]  [dim]当前价: {close.iloc[-1]:.2f}  数据: {len(df)}天[/dim]")
                console.print()
                console.print("  [bold]因子分析[/bold]")
                console.print()
                console.print(f"  [dim]{'因子':<14s}{'IC':>8s}{'|IC|':>8s}{'ICIR':>8s}{'当前值':>12s}  信号[/dim]")
                console.print("  " + "─" * 60)
                for fname, metrics in sorted(ic_results.items(), key=lambda x: -abs(x[1]["ic"])):
                    ic   = metrics["ic"]
                    icir = metrics["icir"]
                    curr = latest.get(fname)
                    curr_str = f"{curr:.3f}" if curr is not None else "N/A"
                    signal = ""
                    if abs(ic) > 0.03:
                        signal = "↑ 看多" if ic > 0 else "↓ 看空"
                    ic_color = "green" if ic > 0.03 else ("red" if ic < -0.03 else "dim")
                    console.print(
                        f"  [{ic_color}]{fname:<14s}[/{ic_color}]"
                        f"[{ic_color}]{ic:>8.3f}[/{ic_color}]"
                        f"{abs(ic):>8.3f}"
                        f"{icir:>8.2f}"
                        f"{curr_str:>12s}"
                        f"  [dim]{signal}[/dim]"
                    )
                console.print()
                # AI interpretation
                top_factors = sorted(ic_results.items(), key=lambda x: -abs(x[1]["ic"]))[:3]
                if top_factors:
                    console.print("  [bold]AI 解读[/bold]")
                    fac_summary = ", ".join(f"{f}(IC={m['ic']:.3f})" for f, m in top_factors)
                    console.print(f"  [dim]最有效因子: {fac_summary}[/dim]")
                    console.print(f"  [dim]使用 /deep-analysis {symbol} 获取完整 AI 投研分析[/dim]")
                    console.print()
            else:
                print(f"  {symbol} Factor Analysis ({len(df)} days)")
                print(f"  {'Factor':<14} {'IC':>8} {'|IC|':>8} {'ICIR':>8} {'Current':>12}")
                for fname, metrics in sorted(ic_results.items(), key=lambda x: -abs(x[1]["ic"])):
                    curr = latest.get(fname)
                    curr_str = f"{curr:.3f}" if curr is not None else "N/A"
                    print(f"  {fname:<14} {metrics['ic']:>8.3f} {abs(metrics['ic']):>8.3f} {metrics['icir']:>8.2f} {curr_str:>12}")

        except ImportError as e:
            console.print(f"[red]需要 numpy/pandas: {e}[/red]") if HAS_RICH else print(f"Missing: {e}")
        except Exception as e:
            console.print(f"[red]因子分析失败: {e}[/red]") if HAS_RICH else print(f"Error: {e}")

    def _scaffold_with_llm(self, project_name: str, description: str, base_dir) -> None:
        """Call the configured LLM to generate a custom project structure and write files."""
        import json, urllib.request, textwrap, pathlib

        ollama_url = self.terminal.config.get("ollama_url", "http://localhost:11434")
        model      = self.terminal.config.get("model", "qwen2.5:7b")

        _SCAFFOLD_SYS = (
            "You are a project scaffolding assistant. Output ONLY valid JSON — no markdown, no explanation.\n"
            "Schema:\n"
            '{"description": "one-line summary", "entry": "main.py", '
            '"files": {"relative/path.py": "file content", ...}}\n'
            "CRITICAL JSON rules:\n"
            r'- Inside string values use \n for newlines (backslash-n), NEVER literal newlines.'
            "\n"
            r'- Inside string values use \" for double quotes, \\ for backslashes.'
            "\n"
            "- 3–8 files total. Content must be complete and runnable.\n"
            "- Always include: main entry point, requirements.txt, README.md\n"
            "- requirements.txt: one package per line. README.md: install + usage.\n"
            "- No markdown code fences. Raw JSON only."
        )
        _SCAFFOLD_USER = (
            f"Project name: {project_name}\n"
            f"Description:  {description}\n"
            "Generate the complete file structure."
        )

        if HAS_RICH:
            console.print(f"\n  [#C08050]⏺[/#C08050]  [bold]LLM 生成项目结构[/bold]  [dim]{description}[/dim]")
        else:
            print(f"\n⏺ 生成项目结构: {description}")

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SCAFFOLD_SYS},
                {"role": "user",   "content": _SCAFFOLD_USER},
            ],
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 4096},
        }
        try:
            req = urllib.request.Request(
                ollama_url.rstrip("/") + "/api/chat",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
            raw = data.get("message", {}).get("content", "").strip()
        except Exception as e:
            msg = f"LLM 调用失败: {e}"
            console.print(f"  [red]{msg}[/red]") if HAS_RICH else print(f"  {msg}")
            return

        # Strip accidental markdown fences
        import re as _re
        raw = _re.sub(r'^```[a-z]*\n?', '', raw).rstrip('`').strip()

        def _parse_scaffold_json(text: str):
            """Try several strategies to extract valid JSON from LLM output."""
            # Strategy 1: strict parse
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
            # Strategy 2: replace literal newlines inside string values
            try:
                # Escape literal newlines that appear inside JSON string values
                fixed = _re.sub(
                    r'("(?:[^"\\]|\\.)*")',
                    lambda m: m.group().replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t'),
                    text,
                )
                return json.loads(fixed)
            except Exception:
                pass
            # Strategy 3: find outermost {...} block
            m = _re.search(r'\{.*\}', text, _re.DOTALL)
            if m:
                try:
                    return json.loads(m.group())
                except Exception:
                    # Strategy 4: same but with newline escaping
                    try:
                        blob = m.group()
                        fixed = _re.sub(
                            r'("(?:[^"\\]|\\.)*")',
                            lambda mx: mx.group().replace('\n', '\\n').replace('\r', '\\r'),
                            blob,
                        )
                        return json.loads(fixed)
                    except Exception:
                        pass
            return None

        structure = _parse_scaffold_json(raw)
        if not structure or "files" not in structure:
            msg = "LLM 未返回有效 JSON 结构，请重试或使用 --template"
            console.print(f"  [red]{msg}[/red]") if HAS_RICH else print(f"  {msg}")
            return

        files: dict = structure["files"]
        proj_desc   = structure.get("description", description)
        entry       = structure.get("entry", "main.py")

        # ── Preview ───────────────────────────────────────────────────────────
        if HAS_RICH:
            console.print(f"  [green]✓[/green]  [dim]{proj_desc}[/dim]")
            console.print(f"\n  [dim]{base_dir.name}/[/dim]")
            for fname, fcontent in files.items():
                lines = fcontent.count("\n") + 1 if fcontent else 0
                console.print(f"  [dim]  ├── {fname:<26s}[/dim] {lines} lines")
            console.print()
            choice = console.input(
                "  [bold]Create these files?[/bold] [dim]\\[y=all / n=cancel / r=review each][/dim] "
            ).strip().lower()
        else:
            print(f"\n  {base_dir.name}/")
            for fname, fcontent in files.items():
                lines = fcontent.count("\n") + 1 if fcontent else 0
                print(f"    ├── {fname:<26s}  {lines} lines")
            choice = input("  Create these files? [y/all / n=cancel / r=review each] ").strip().lower()

        if choice in ("n", "no"):
            console.print("[dim]取消。[/dim]") if HAS_RICH else print("Cancelled.")
            return

        approve_each = choice in ("r", "review")
        created, skipped = [], []

        for fname, fcontent in files.items():
            target = pathlib.Path(base_dir) / fname
            target.parent.mkdir(parents=True, exist_ok=True)

            if approve_each:
                if HAS_RICH:
                    console.print(f"\n  [dim]{fname}[/dim]  ({fcontent.count(chr(10))+1} lines)")
                    sub = console.input("  [dim]写入? [y/n] [/dim]").strip().lower()
                else:
                    print(f"\n  {fname}  ({fcontent.count(chr(10))+1} lines)")
                    sub = input("  写入? [y/n] ").strip().lower()
                if sub not in ("y", "yes", ""):
                    skipped.append(fname)
                    continue

            result = _tool_write_file({"path": str(target), "content": fcontent, "_skip_confirm": True})
            if result["success"]:
                created.append(fname)
            else:
                err = result.get("error", "?")
                console.print(f"  [red]Failed {fname}: {err}[/red]") if HAS_RICH else print(f"  Failed {fname}: {err}")

        if HAS_RICH:
            console.print()
            if created:
                console.print(f"  [green]✓[/green] 创建 {len(created)} 个文件 → [bold]{base_dir}[/bold]")
                for f in created:
                    console.print(f"    [dim]{f}[/dim]")
            if skipped:
                console.print(f"  [dim]跳过: {', '.join(skipped)}[/dim]")
            console.print(f"\n  [dim]启动: cd \"{base_dir}\" && python3 {entry}[/dim]\n")
        else:
            print(f"\n创建 {len(created)} 个文件 → {base_dir}")
            if skipped:
                print(f"跳过: {', '.join(skipped)}")
            print(f"启动: cd \"{base_dir}\" && python3 {entry}")

    def cmd_scaffold(self, args: str):
        """Generate a project folder structure with files, with user approval.

        Usage:
          /scaffold <project_name>                         → blank template
          /scaffold <project_name> <description...>        → LLM generates custom structure
          /scaffold <project_name> --template analysis     → fixed finance template
          /scaffold <project_name> --template strategy
          /scaffold <project_name> --template pipeline

        Examples:
          /scaffold my-api FastAPI REST API with JWT auth and PostgreSQL
          /scaffold price-alert CLI tool that monitors stock prices and sends alerts
          /scaffold aapl-analysis --template analysis
        """
        import textwrap

        parts = args.strip().split()
        if not parts:
            if HAS_RICH:
                console.print("[dim]Usage: /scaffold <name> [description] | [--template analysis|strategy|pipeline|blank][/dim]")
                console.print("[dim]Examples:[/dim]")
                console.print("[dim]  /scaffold my-api  FastAPI REST API with JWT auth[/dim]")
                console.print("[dim]  /scaffold price-bot  CLI tool that monitors stock prices[/dim]")
                console.print("[dim]  /scaffold aapl-analysis --template analysis[/dim]")
            else:
                print("Usage: /scaffold <name> [description] | [--template analysis|strategy|pipeline|blank]")
            return

        # Parse project name, template flag, and optional description
        project_name = parts[0]
        template = None
        description = ""
        if "--template" in parts:
            idx = parts.index("--template")
            if idx + 1 < len(parts):
                template = parts[idx + 1]
            # remaining words before --template are ignored
        elif len(parts) > 1:
            description = " ".join(parts[1:])  # everything after name = LLM description

        # Resolve base directory under the user's local Aria Code workspace.
        # Generated strategy/code projects must not silently land in the source repo.
        from artifacts import user_projects_dir as _user_projects_dir
        base_dir = _user_projects_dir() / project_name

        # ── LLM-generated scaffold (when user gives a description) ────────────
        if description and not template:
            self._scaffold_with_llm(project_name, description, base_dir)
            return

        # Fallback to blank when no template and no description
        if template is None:
            template = "blank"

        # Built-in templates
        TEMPLATES = {
            "analysis": {
                "description": "Stock/asset analysis project",
                "files": {
                    "main.py": textwrap.dedent("""\
                        #!/usr/bin/env python3
                        \"\"\"
                        {project} — market analysis entry point.
                        Usage: python3 main.py AAPL
                        \"\"\"
                        import sys
                        import os
                        import numpy as np
                        import pandas as pd
                        import yfinance as yf
                        import matplotlib; matplotlib.use('Agg')
                        import matplotlib.pyplot as plt
                        from analysis import run_analysis
                        from report import generate_report

                        if __name__ == "__main__":
                            symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
                            data = run_analysis(symbol)
                            generate_report(symbol, data)
                        """),
                    "analysis.py": textwrap.dedent("""\
                        \"\"\"Core analysis logic for {project}.\"\"\"
                        import numpy as np
                        import pandas as pd
                        import yfinance as yf


                        def run_analysis(symbol: str, period: str = "1y") -> dict:
                            ticker = yf.Ticker(symbol)
                            hist = ticker.history(period=period, auto_adjust=True, progress=False)
                            if hist.empty:
                                raise ValueError(f"No data for {{symbol}}")
                            hist.columns = hist.columns.droplevel(1) if hasattr(hist.columns, 'droplevel') and hist.columns.nlevels > 1 else hist.columns
                            close = hist["Close"]
                            returns = close.pct_change().dropna()
                            sma20 = close.rolling(20).mean()
                            sma50 = close.rolling(50).mean()
                            rsi = _calc_rsi(close)
                            return {{
                                "symbol": symbol,
                                "current_price": round(float(close.iloc[-1]), 2),
                                "sma20": round(float(sma20.iloc[-1]), 2),
                                "sma50": round(float(sma50.iloc[-1]), 2),
                                "rsi": round(float(rsi.iloc[-1]), 1),
                                "annual_return": round(float(returns.mean() * 252), 4),
                                "volatility": round(float(returns.std() * (252 ** 0.5)), 4),
                                "hist": hist,
                                "returns": returns,
                            }}


                        def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
                            delta = close.diff()
                            gain = delta.clip(lower=0).rolling(period).mean()
                            loss = (-delta.clip(upper=0)).rolling(period).mean()
                            rs = gain / loss.replace(0, float("nan"))
                            return 100 - 100 / (1 + rs)
                        """),
                    "report.py": textwrap.dedent("""\
                        \"\"\"Report generation for {project}.\"\"\"
                        import os
                        import matplotlib; matplotlib.use('Agg')
                        import matplotlib.pyplot as plt


                        def generate_report(symbol: str, data: dict):
                            fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
                            hist = data["hist"]
                            close = hist["Close"]
                            # Price + SMAs
                            axes[0].plot(close.index, close, label="Close", color="#C08050", linewidth=1.5)
                            axes[0].plot(close.index, hist["Close"].rolling(20).mean(), label="SMA20", color="#2AE8A5", linewidth=1)
                            axes[0].plot(close.index, hist["Close"].rolling(50).mean(), label="SMA50", color="#EF4444", linewidth=1)
                            axes[0].set_title(f"{{symbol}} — Price & Moving Averages", fontsize=14)
                            axes[0].legend(); axes[0].grid(alpha=0.3)
                            # Volume
                            axes[1].bar(hist.index, hist["Volume"], color="#C08050", alpha=0.5, label="Volume")
                            axes[1].set_title("Volume"); axes[1].grid(alpha=0.3)
                            plt.tight_layout()
                            os.makedirs("outputs", exist_ok=True)
                            out = os.path.abspath(os.path.join("outputs", f"{symbol}_analysis.png"))
                            plt.savefig(out, dpi=150, bbox_inches="tight")
                            plt.close()
                            print(f"Chart saved: {{out}}")
                            print(f"Price: ${{data['current_price']}}  RSI: {{data['rsi']}}  "
                                  f"Annual Return: {{data['annual_return']*100:.1f}}%  Vol: {{data['volatility']*100:.1f}}%")
                        """),
                    "requirements.txt": "numpy\npandas\nyfinance\nmatplotlib\n",
                    "README.md": textwrap.dedent("""\
                        # {project}
                        Stock analysis project generated by Aria CLI.

                        ## Usage
                        ```bash
                        pip3 install -r requirements.txt
                        python3 main.py AAPL
                        ```
                        """),
                },
            },
            "strategy": {
                "description": "Quant trading strategy with backtesting",
                "files": {
                    "main.py": textwrap.dedent("""\
                        #!/usr/bin/env python3
                        \"\"\"
                        {project} — backtest entry point.
                        Usage: python3 main.py AAPL 2022-01-01 2024-01-01
                        \"\"\"
                        import sys
                        from strategy import MomentumStrategy
                        from backtest import run_backtest

                        if __name__ == "__main__":
                            symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
                            start  = sys.argv[2] if len(sys.argv) > 2 else "2022-01-01"
                            end    = sys.argv[3] if len(sys.argv) > 3 else "2024-01-01"
                            strat  = MomentumStrategy(lookback=20)
                            result = run_backtest(strat, symbol, start, end)
                            print(result)
                        """),
                    "strategy.py": textwrap.dedent("""\
                        \"\"\"Strategy definitions for {project}.\"\"\"
                        import pandas as pd


                        class MomentumStrategy:
                            def __init__(self, lookback: int = 20):
                                self.lookback = lookback
                                self.name = f"Momentum({{lookback}})"

                            def generate_signals(self, prices: pd.Series) -> pd.Series:
                                \"\"\"Return +1 (long), -1 (short), 0 (flat) signals.\"\"\"
                                momentum = prices.pct_change(self.lookback)
                                signals = pd.Series(0, index=prices.index)
                                signals[momentum > 0] = 1
                                signals[momentum < 0] = -1
                                return signals.shift(1).fillna(0)  # avoid lookahead
                        """),
                    "backtest.py": textwrap.dedent("""\
                        \"\"\"Backtest engine for {project}.\"\"\"
                        import os
                        import numpy as np
                        import pandas as pd
                        import yfinance as yf
                        import matplotlib; matplotlib.use('Agg')
                        import matplotlib.pyplot as plt


                        def run_backtest(strategy, symbol: str, start: str, end: str) -> dict:
                            ticker = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
                            if ticker.empty:
                                raise ValueError(f"No data for {{symbol}}")
                            prices = ticker["Close"].squeeze()
                            signals = strategy.generate_signals(prices)
                            returns = prices.pct_change().fillna(0)
                            strat_returns = signals * returns
                            equity = (1 + strat_returns).cumprod()
                            bh_equity = (1 + returns).cumprod()
                            # Metrics
                            ann_return = strat_returns.mean() * 252
                            ann_vol    = strat_returns.std() * (252 ** 0.5)
                            sharpe     = ann_return / ann_vol if ann_vol > 0 else 0
                            max_dd     = (equity / equity.cummax() - 1).min()
                            # Plot
                            fig, ax = plt.subplots(figsize=(14, 6))
                            ax.plot(equity.index, equity, label=strategy.name, color="#C08050", linewidth=2)
                            ax.plot(bh_equity.index, bh_equity, label="Buy & Hold", color="#2AE8A5", linewidth=1.5, linestyle="--")
                            ax.set_title(f"{{symbol}} — {{strategy.name}} Backtest"); ax.legend(); ax.grid(alpha=0.3)
                            os.makedirs("outputs", exist_ok=True)
                            out = os.path.abspath(os.path.join("outputs", f"{{symbol}}_backtest.png"))
                            plt.savefig(out, dpi=150, bbox_inches="tight"); plt.close()
                            result = {{
                                "symbol": symbol, "strategy": strategy.name,
                                "ann_return": round(ann_return * 100, 2),
                                "ann_vol": round(ann_vol * 100, 2),
                                "sharpe": round(sharpe, 3),
                                "max_drawdown": round(max_dd * 100, 2),
                                "chart": out,
                            }}
                            print(f"Sharpe: {{result['sharpe']}}  Return: {{result['ann_return']}}%  "
                                  f"MaxDD: {{result['max_drawdown']}}%  Chart: {{out}}")
                            return result
                        """),
                    "requirements.txt": "numpy\npandas\nyfinance\nmatplotlib\n",
                    "README.md": textwrap.dedent("""\
                        # {project}
                        Quant strategy backtest project generated by Aria CLI.

                        ## Usage
                        ```bash
                        pip3 install -r requirements.txt
                        python3 main.py SPY 2022-01-01 2024-01-01
                        ```
                        """),
                },
            },
            "pipeline": {
                "description": "Market data pipeline (fetch → process → store)",
                "files": {
                    "main.py": textwrap.dedent("""\
                        #!/usr/bin/env python3
                        \"\"\"
                        {project} — data pipeline entry point.
                        Usage: python3 main.py AAPL MSFT TSLA
                        \"\"\"
                        import sys
                        from pipeline import DataPipeline

                        if __name__ == "__main__":
                            symbols = sys.argv[1:] or ["AAPL", "MSFT", "TSLA"]
                            pipe = DataPipeline(symbols)
                            pipe.run()
                        """),
                    "pipeline.py": textwrap.dedent("""\
                        \"\"\"Data pipeline for {project}.\"\"\"
                        import os
                        import pandas as pd
                        import yfinance as yf


                        class DataPipeline:
                            def __init__(self, symbols: list, period: str = "1y", output_dir: str = "data"):
                                self.symbols = symbols
                                self.period = period
                                self.output_dir = os.path.expanduser(output_dir)
                                os.makedirs(self.output_dir, exist_ok=True)

                            def fetch(self, symbol: str) -> pd.DataFrame:
                                df = yf.download(symbol, period=self.period, auto_adjust=True, progress=False)
                                df.columns = df.columns.droplevel(1) if df.columns.nlevels > 1 else df.columns
                                return df

                            def process(self, df: pd.DataFrame) -> pd.DataFrame:
                                df = df.copy()
                                df["Returns"] = df["Close"].pct_change()
                                df["SMA20"]   = df["Close"].rolling(20).mean()
                                df["SMA50"]   = df["Close"].rolling(50).mean()
                                df["Volatility"] = df["Returns"].rolling(20).std() * (252 ** 0.5)
                                return df.dropna()

                            def store(self, symbol: str, df: pd.DataFrame):
                                path = os.path.join(self.output_dir, f"{{symbol}}.csv")
                                df.to_csv(path)
                                print(f"  Saved {{len(df)}} rows → {{path}}")

                            def run(self):
                                print(f"Running pipeline for: {{self.symbols}}")
                                for symbol in self.symbols:
                                    try:
                                        raw = self.fetch(symbol)
                                        processed = self.process(raw)
                                        self.store(symbol, processed)
                                    except Exception as e:
                                        print(f"  Error {{symbol}}: {{e}}")
                                print("Pipeline complete.")
                        """),
                    "requirements.txt": "pandas\nyfinance\n",
                    "README.md": textwrap.dedent("""\
                        # {project}
                        Market data pipeline generated by Aria CLI.

                        ## Usage
                        ```bash
                        pip3 install -r requirements.txt
                        python3 main.py AAPL MSFT TSLA
                        # Output CSVs saved to ./data/
                        ```
                        """),
                },
            },
            "blank": {
                "description": "Blank project scaffold",
                "files": {
                    "main.py": textwrap.dedent("""\
                        #!/usr/bin/env python3
                        \"\"\"
                        {project} — main entry point.
                        \"\"\"
                        import os
                        import sys
                        import numpy as np
                        import pandas as pd


                        def main():
                            print("Hello from {project}!")


                        if __name__ == "__main__":
                            main()
                        """),
                    "requirements.txt": "numpy\npandas\n",
                    "README.md": "# {project}\n\nProject generated by Aria CLI.\n",
                },
            },
        }

        if template not in TEMPLATES:
            msg = f"Unknown template '{template}'. Available: {', '.join(TEMPLATES)}"
            console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
            return

        tmpl = TEMPLATES[template]
        files = {
            k: v.format(project=project_name) if isinstance(v, str) else v
            for k, v in tmpl["files"].items()
        }

        # ── Preview: show tree + file summaries ──────────────────────────────
        if HAS_RICH:
            console.print()
            console.print(f"  [bold]Scaffold:[/bold] [cyan]{project_name}[/cyan]  "
                          f"[dim]({tmpl['description']}, {template} template)[/dim]")
            console.print(f"  [dim]Location:[/dim] {base_dir}")
            console.print()
            console.print(f"  [dim]{base_dir.name}/[/dim]")
            for fname, fcontent in files.items():
                lines = fcontent.count("\n") + 1 if fcontent else 0
                exists_tag = " [yellow](exists)[/yellow]" if (base_dir / fname).exists() else ""
                console.print(f"  [dim]  ├── {fname:<24s}[/dim] {lines} lines{exists_tag}")
            console.print()
        else:
            print(f"\nScaffold: {project_name}  ({template} template)")
            print(f"Location: {base_dir}")
            print(f"\n  {base_dir.name}/")
            for fname, fcontent in files.items():
                lines = fcontent.count("\n") + 1 if fcontent else 0
                exists_tag = " (exists)" if (base_dir / fname).exists() else ""
                print(f"    ├── {fname:<24s}  {lines} lines{exists_tag}")
            print()

        # ── Ask: approve all / approve each / cancel ─────────────────────────
        # In non-interactive mode (-p flag / piped stdin) auto-approve all files.
        if not sys.stdin.isatty():
            choice = "y"
            console.print("  [dim](非交互模式：自动确认创建所有文件)[/dim]") if HAS_RICH else print("  (Auto-approved: non-interactive mode)")
        elif HAS_RICH:
            choice = console.input(
                "  [bold]Create these files?[/bold] "
                "[dim]\\[y=all / n=cancel / r=review each][/dim] "
            ).strip().lower()
        else:
            choice = input("  Create these files? [y=all / n=cancel / r=review each] ").strip().lower()

        if choice in ("n", "no"):
            console.print("[dim]Scaffold cancelled.[/dim]" if HAS_RICH else "Cancelled.")
            return

        approve_each = choice in ("r", "review")
        created, skipped = [], []

        for fname, fcontent in files.items():
            target = base_dir / fname
            if approve_each:
                if HAS_RICH:
                    console.print(f"\n  [dim]{fname}[/dim]  ({fcontent.count(chr(10))+1} lines)")
                    sub = console.input(
                        "  [dim]Write this file? [y/n] [/dim]"
                    ).strip().lower()
                else:
                    print(f"\n  {fname}  ({fcontent.count(chr(10))+1} lines)")
                    sub = input("  Write? [y/n] ").strip().lower()
                if sub not in ("y", "yes", ""):
                    skipped.append(fname)
                    continue

            result = _tool_write_file({"path": str(target), "content": fcontent, "_skip_confirm": True})
            if result["success"]:
                created.append(fname)
            else:
                err = result.get("error", "?")
                if HAS_RICH:
                    console.print(f"  [red]Failed {fname}: {err}[/red]")
                else:
                    print(f"  Failed {fname}: {err}")

        # ── Summary ───────────────────────────────────────────────────────────
        if HAS_RICH:
            console.print()
            if created:
                console.print(f"  [green]✓[/green] Created {len(created)} file(s) in [bold]{base_dir}[/bold]")
                for f in created:
                    console.print(f"    [dim]{f}[/dim]")
            if skipped:
                console.print(f"  [dim]Skipped: {', '.join(skipped)}[/dim]")
            console.print()
            console.print(f"  [dim]Run:  cd \"{base_dir}\" && python3 main.py[/dim]")
            console.print()
        else:
            print(f"\nCreated {len(created)} files in {base_dir}")
            if skipped:
                print(f"Skipped: {', '.join(skipped)}")
            print(f"Run: cd \"{base_dir}\" && python3 main.py")

    async def cmd_strategy(self, args: str):
        """
        策略版本管理系统 (Strategy Vault)

        /strategy save [name] [message]   — 保存当前对话中最后一段代码
        /strategy list [name]             — 列出所有版本
        /strategy diff [name] [v1] [v2]   — 查看版本差异
        /strategy load [name] [tag/id]    — 加载版本到上下文
        /strategy review                  — AI审查+静态检测
        """
        if not _HAS_VAULT:
            console.print("  [yellow]strategy_vault.py 未找到[/yellow]" if HAS_RICH
                          else "  strategy_vault.py not found")
            return

        parts = args.strip().split(None, 3)
        sub   = parts[0].lower() if parts else "list"

        vault = _get_vault()

        # ── save ──────────────────────────────────────────────────────────
        if sub == "save":
            # 从对话历史中提取最后一段 Python 代码
            code = self._extract_last_code()
            if not code:
                if HAS_RICH:
                    console.print("  [yellow]未在对话中找到代码块。先让 Aria 生成策略代码。[/yellow]")
                else:
                    print("  No code found in conversation. Generate strategy code first.")
                return
            name    = parts[1] if len(parts) > 1 and not parts[1].startswith('"') else "strategy"
            message = " ".join(parts[2:]).strip('"') if len(parts) > 2 else ""
            sv = vault.save(code, name=name, message=message)
            if HAS_RICH:
                console.print(
                    f"\n  [green]✓[/green] 策略已保存  "
                    f"[bold]{sv.name}[/bold] [dim]{sv.version_tag}[/dim]  "
                    f"hash={sv.code_hash}  {sv.created_at[:16]}"
                )
            else:
                print(f"  Saved: {sv.name} {sv.version_tag} ({sv.created_at[:16]})")

        # ── list ──────────────────────────────────────────────────────────
        elif sub == "list":
            name = parts[1] if len(parts) > 1 else None
            if name:
                versions = vault.list(name)
                title = f"  策略: {name}"
            else:
                # Show all strategies
                all_names = vault.list_all_names()
                if not all_names:
                    console.print("  [dim]策略金库为空。使用 /strategy save 保存策略。[/dim]" if HAS_RICH
                                  else "  Vault is empty.")
                    return
                if HAS_RICH:
                    console.print("\n  [bold]策略金库[/bold]\n")
                    for n in all_names:
                        vs = vault.list(n, limit=3)
                        latest = vs[0] if vs else None
                        if latest:
                            bt = ""
                            if latest.backtest_result:
                                br = latest.backtest_result
                                bt = f"  sharpe={br.get('sharpe_ratio','?')} ret={br.get('total_return_pct','?')}%"
                            console.print(
                                f"  [bold]{n}[/bold]  [dim]{len(vs)}个版本  "
                                f"最新:{latest.version_tag}  {latest.created_at[:10]}{bt}[/dim]"
                            )
                    console.print()
                else:
                    for n in all_names:
                        print(f"  {n}")
                return
            if not versions:
                console.print(f"  [dim]没有找到策略 '{name}'[/dim]" if HAS_RICH else f"  Not found: {name}")
                return
            if HAS_RICH:
                console.print(f"\n  [bold]{title}[/bold]\n")
                for v in versions:
                    bt = ""
                    if v.backtest_result:
                        br = v.backtest_result
                        sharpe = br.get("sharpe_ratio")
                        ret    = br.get("total_return_pct")
                        bt = f"  [green]sharpe={sharpe:.2f}  ret={ret:.1f}%[/green]" if sharpe else ""
                    reviewed = "  [dim]✓reviewed[/dim]" if v.review_result else ""
                    msg = f"  [dim]{v.message[:50]}[/dim]" if v.message else ""
                    console.print(
                        f"  [dim]{v.id:4d}[/dim]  [bold]{v.version_tag}[/bold]  "
                        f"[dim]{v.created_at[:16]}[/dim]{msg}{bt}{reviewed}"
                    )
                console.print()
            else:
                for v in versions:
                    print(v.summary_line())

        # ── diff ──────────────────────────────────────────────────────────
        elif sub == "diff":
            name  = parts[1] if len(parts) > 1 else "strategy"
            tag_a = parts[2] if len(parts) > 2 else None
            tag_b = parts[3] if len(parts) > 3 else None
            diff_text = vault.diff(name, tag_a, tag_b)
            if HAS_RICH:
                console.print()
                # Simple color: + lines green, - lines red
                for line in diff_text.splitlines():
                    if line.startswith("+++") or line.startswith("---"):
                        console.print(f"  [bold]{line}[/bold]")
                    elif line.startswith("+"):
                        console.print(f"  [green]{line}[/green]")
                    elif line.startswith("-"):
                        console.print(f"  [red]{line}[/red]")
                    elif line.startswith("@@"):
                        console.print(f"  [cyan]{line}[/cyan]")
                    else:
                        console.print(f"  {line}")
                console.print()
            else:
                print(diff_text)

        # ── load ──────────────────────────────────────────────────────────
        elif sub == "load":
            name    = parts[1] if len(parts) > 1 else "strategy"
            tag     = parts[2] if len(parts) > 2 else None
            version = vault.load(name, version_tag=tag)
            if not version:
                console.print(f"  [red]未找到: {name} {tag or '(latest)'}[/red]" if HAS_RICH
                              else f"  Not found: {name} {tag}")
                return
            # Inject code into conversation context as a user message
            code_msg = f"以下是策略 {version.name} {version.version_tag} 的代码：\n\n```python\n{version.code}\n```"
            self.terminal.conversation.append({"role": "assistant", "content": code_msg})
            if HAS_RICH:
                console.print(
                    f"\n  [green]✓[/green] 已加载 [bold]{version.name} {version.version_tag}[/bold]  "
                    f"[dim]{len(version.code)} chars  {version.created_at[:16]}[/dim]"
                )
                console.print(f"  [dim]{version.message}[/dim]" if version.message else "")
                lines = version.code.count("\n")
                console.print(f"  [dim]代码 {lines} 行已注入上下文，可继续对话修改。[/dim]")
            else:
                print(f"  Loaded: {version.name} {version.version_tag}")

        # ── review ────────────────────────────────────────────────────────
        elif sub == "review":
            name    = parts[1] if len(parts) > 1 else "strategy"
            tag     = parts[2] if len(parts) > 2 else None
            version = vault.load(name, version_tag=tag)
            if not version:
                code = self._extract_last_code()
                if not code:
                    console.print("  [yellow]未找到策略，请先 /strategy save 或生成代码[/yellow]" if HAS_RICH
                                  else "  No strategy found.")
                    return
                ver_id = None
            else:
                code   = version.code
                ver_id = version.id

            if HAS_RICH:
                console.print()
                console.print("  [bold]🔬 策略审查中...[/bold]")
                console.print()

            ollama_url = self.terminal.config.get("ollama_url", "http://localhost:11434")
            model      = self.terminal.config.get("model", "qwen2.5:7b")
            bt_result  = version.backtest_result if version else None

            import sys
            def on_token(tok):
                sys.stdout.write(tok)
                sys.stdout.flush()

            review = await _ai_review(code, bt_result, ollama_url, model, on_token=on_token)

            # Print static results
            static = review.get("static", {})
            if HAS_RICH:
                console.print()
                console.print(f"\n  [bold]静态检测[/bold]  评级:{static.get('grade','?')}  "
                              f"{static.get('summary','')}")
                for e in static.get("errors", []):
                    console.print(f"  [red]❌ {e['detail']}[/red]")
                for w in static.get("warnings", []):
                    console.print(f"  [yellow]⚠️  {w['detail']}[/yellow]")
                for q in static.get("quality_checks", []):
                    console.print(f"  [dim]💡 {q}[/dim]")
                console.print()
            else:
                print(f"\n  Static: {static.get('summary','')}")

            if ver_id:
                vault.save_review(ver_id, review)
                if HAS_RICH:
                    console.print("  [dim]审查结果已保存到策略金库[/dim]")

        else:
            if HAS_RICH:
                console.print(
                    "\n  [bold]Strategy Vault 命令[/bold]\n\n"
                    "  /strategy save [name] [message]   保存当前代码快照\n"
                    "  /strategy list [name]              列出版本历史\n"
                    "  /strategy diff [name] [v1] [v2]   查看版本差异\n"
                    "  /strategy load [name] [tag]        加载版本到上下文\n"
                    "  /strategy review [name] [tag]      AI + 静态代码审查\n"
                )
            else:
                print("  Usage: /strategy save|list|diff|load|review [name] [tag]")

    # ── ML 信号组合回测 ──────────────────────────────────────────────────────────

    async def _cmd_ml_signal_backtest(
        self, symbol_args: list, start_date: str = "2023-01-01",
        end_date: str = "", capital: float = 1_000_000,
    ):
        """
        /backtest ml [sym1 sym2 ...] [--start YYYY-MM-DD] [--capital N]

        三策略对比: ML-Weighted / Equal-Weight / Buy-and-Hold
        支持 A股(T+1)、港股、美股混合组合。
        """
        # ML signal backtest is part of the private Arthera engine (alpha IP).
        # If a local Arthera checkout is present (dev), make it importable;
        # otherwise the import below fails and we show a Pro-feature notice.
        import sys, os
        _arthera_pkgs = os.environ.get("ARTHERA_ROOT") or os.path.expanduser("~/Desktop/Arthera")
        _arthera_pkgs = os.path.join(_arthera_pkgs, "packages")
        if os.path.isdir(_arthera_pkgs) and _arthera_pkgs not in sys.path:
            sys.path.insert(0, _arthera_pkgs)

        if HAS_RICH:
            console.print("\n  [bold cyan]ML 信号组合回测[/bold cyan]  三策略对比\n")
        else:
            print("\n  ML 信号组合回测  三策略对比\n")

        # 解析标的列表（去掉标志位）
        symbols = [s.upper() for s in symbol_args if not s.startswith("--")]
        if not symbols:
            symbols = ["600519", "300750", "NVDA", "AAPL"]
            if HAS_RICH:
                console.print(f"  [dim]未指定标的，使用默认组合: {symbols}[/dim]")

        if HAS_RICH:
            console.print(f"  标的: [yellow]{' | '.join(symbols)}[/yellow]")
            console.print(f"  区间: {start_date} → {end_date or '今日'}")
            console.print(f"  初始资金: {capital:,.0f}\n")
            console.print("  [dim]正在拉取行情并训练模型，请稍候…[/dim]")

        try:
            from quant_engine.backtest.ml_signal_backtest import MLSignalBacktest

            bt = MLSignalBacktest(
                symbols=symbols,
                initial_cash=capital,
                rebalance_freq="W",
            )
            report = bt.run(start=start_date, end=end_date or "")
            report.print_report()

            if HAS_RICH:
                # 额外渲染净值图（纯 ASCII sparkline）
                ml_nav  = report.ml_strategy.nav_series
                ew_nav  = report.ew_strategy.nav_series
                if not ml_nav.empty and not ew_nav.empty:
                    console.print("\n  [bold]净值走势（最近 40 个交易日）[/bold]")
                    _print_sparkline("ML 权重", ml_nav,  "cyan")
                    _print_sparkline("等权基准", ew_nav, "yellow")

        except ImportError:
            # Moat feature — the ML/alpha engine ships only with the full
            # Arthera platform, not the open CLI. Degrade with a clear notice.
            _msg = ("ML 信号回测属于 Arthera 高级引擎（含 ML 选股/alpha 因子），"
                    "开源 CLI 未内置。\n  基础回测可用：/backtest momentum <symbol>")
            if HAS_RICH:
                console.print(f"  [#C08050]◆ Pro 功能[/#C08050]  [dim]{_msg}[/dim]")
            else:
                print(f"  ◆ Pro 功能  {_msg}")
        except Exception as e:
            _print_error(f"ML 回测失败: {e}")
            import traceback
            console.print(f"  [dim]{traceback.format_exc()}[/dim]") if HAS_RICH else print(traceback.format_exc())


def _print_sparkline(label: str, nav: "pd.Series", color: str = "white", width: int = 40):
    """打印 ASCII sparkline。"""
    try:
        import sys
        HAS_RICH = "rich" in sys.modules
        vals = nav.iloc[-width:].values if len(nav) > width else nav.values
        if len(vals) < 2:
            return
        lo, hi = vals.min(), vals.max()
        chars = "▁▂▃▄▅▆▇█"
        spark  = "".join(chars[min(7, int((v - lo) / (hi - lo + 1e-9) * 8))] for v in vals)
        change = (vals[-1] / vals[0] - 1) * 100
        sign   = "+" if change >= 0 else ""
        if HAS_RICH:
            from rich.console import Console as _C
            _C().print(f"  [{color}]{label:<8}[/{color}] {spark}  [{color}]{sign}{change:.2f}%[/{color}]")
        else:
            print(f"  {label:<8} {spark}  {sign}{change:.2f}%")
    except Exception:
        pass
