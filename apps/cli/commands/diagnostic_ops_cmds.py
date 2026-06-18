"""DiagnosticOpsCommandsMixin — bug, accuracy, cost, todo, doctor, datasource commands."""

from __future__ import annotations

import asyncio
import json
import pathlib


class DiagnosticOpsCommandsMixin:
    """Mixin: diagnostics, feedback, usage, and source inspection commands."""

    def cmd_bug(self, args: str):
        desc = args.strip()
        if not desc:
            console.print("[dim]用法: /bug <描述你遇到的问题>[/dim]" if HAS_RICH
                          else "Usage: /bug <description>")
            return
        ctx_parts = []
        for m in self.terminal.conversation[-6:]:
            _c = (m.get("content", "") or "")[:300]
            ctx_parts.append(f"{m.get('role','')}: {_c}")
        ctx = "\n".join(ctx_parts)
        import platform as _pf
        env = (f"v{__version__} · {_pf.system()} · py{_pf.python_version()} · "
               f"model={self.terminal.config.get('model','')}")
        self.terminal._record_feedback("bug", ctx, comment=f"{desc}\n\n[env] {env}")
        gh = "https://github.com/Cinsoul/Aria-Code/issues"
        if HAS_RICH:
            console.print("  [#C08050]✓ 已记录问题（本地）[/#C08050]")
            console.print(f"  [dim]上传需 /privacy opt-in · 或直接提 issue: {gh}[/dim]")
        else:
            print(f"  ✓ Bug recorded locally. Upload via /privacy opt-in, or file: {gh}")

    def cmd_accuracy(self, args: str):
        res = self.terminal._verify_predictions(min_age_hours=24.0)
        try:
            from apps.cli.prediction_feedback import PredictionTracker
            acc = PredictionTracker(CONFIG_DIR).accuracy()
        except Exception:
            acc = {}
        if HAS_RICH:
            console.print()
            console.print("  [bold]预测战绩[/bold]  [dim]LLM 方向判断 vs 实际行情[/dim]")
            if res.get("settled"):
                console.print(f"  [dim]本次结算 {res['settled']} 笔："
                              f"命中 [green]{res['correct']}[/green] / "
                              f"落空 [red]{res['wrong']}[/red][/dim]")
            _acc = acc.get("accuracy")
            _acc_str = f"{_acc:.0%}" if _acc is not None else "—"
            console.print(
                f"  累计：已结算 [bold]{acc.get('settled',0)}[/bold] · "
                f"命中率 [#C08050]{_acc_str}[/#C08050] · "
                f"待结算 [dim]{acc.get('pending',0)}[/dim]"
            )
            if not acc.get("total"):
                console.print("  [dim]暂无记录 — 用 /team 或 /analyze 让 AI 给出方向判断后会自动追踪[/dim]")
        else:
            print(f"  预测战绩: 结算{res.get('settled',0)} 命中率"
                  f"{acc.get('accuracy')} 待结算{acc.get('pending',0)}")

    def cmd_cost(self, args: str):
        import time as _t
        elapsed = _t.time() - self.terminal._session_start
        inp = self.terminal._session_input_tokens
        out = self.terminal._session_output_tokens
        think = self.terminal._session_thinking_tokens
        turns = self.terminal._session_turns
        total = inp + out + think

        is_local = self.terminal._last_provider in ("ollama", "ollama_cache", "local")
        cost_usd = 0.0
        if not is_local:
            cost_usd = (inp * 0.14 + out * 0.28 + think * 1.10) / 1_000_000

        hh = int(elapsed // 3600)
        mm = int((elapsed % 3600) // 60)
        ss = int(elapsed % 60)
        duration = f"{hh}h {mm:02d}m {ss:02d}s" if hh else f"{mm}m {ss:02d}s"

        if HAS_RICH:
            console.print()
            console.print("[bold]Session Usage[/bold]")
            console.print()
            console.print(f"  [dim]{'Duration':<22}[/dim]{duration}")
            console.print(f"  [dim]{'Turns':<22}[/dim]{turns}")
            console.print(f"  [dim]{'Input tokens':<22}[/dim]{inp:,}")
            console.print(f"  [dim]{'Output tokens':<22}[/dim]{out:,}")
            if think:
                console.print(f"  [dim]{'Thinking tokens':<22}[/dim]{think:,}")
            console.print(f"  [dim]{'Total tokens':<22}[/dim][bold]{total:,}[/bold]")
            if is_local:
                console.print(f"  [dim]{'Est. cost':<22}[/dim][green]$0.00 (local)[/green]")
            elif total > 0:
                console.print(f"  [dim]{'Est. cost':<22}[/dim]${cost_usd:.4f} USD")
            console.print(f"  [dim]{'Provider':<22}[/dim]{self.terminal._last_provider}")
            console.print()
        else:
            print(f"  Session: {duration}  Turns: {turns}")
            print(f"  Tokens: {inp:,} in / {out:,} out / {total:,} total")
            if not is_local and total > 0:
                print(f"  Est. cost: ${cost_usd:.4f}")

    def cmd_todo(self, args: str):
        import json as _json
        todo_file = CONFIG_DIR / "todos.json"

        def _load():
            try:
                if todo_file.exists():
                    return _json.loads(todo_file.read_text(encoding="utf-8"))
            except Exception:
                pass
            return []

        def _save(tasks):
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            todo_file.write_text(_json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""
        tasks = _load()

        if sub in ("", "list", "ls"):
            if not tasks:
                console.print("[dim]No tasks. Add with: /todo add <task>[/dim]" if HAS_RICH else "No tasks")
                return
            if HAS_RICH:
                console.print()
                for i, t in enumerate(tasks):
                    status_icon = "[green]✓[/green]" if t.get("done") else "[yellow]○[/yellow]"
                    style = "dim" if t.get("done") else ""
                    text = t.get("text", "")
                    console.print(f"  {status_icon} [dim]{i}[/dim]  [{style}]{text}[/{style}]" if style
                                  else f"  {status_icon} [dim]{i}[/dim]  {text}")
                pending = sum(1 for t in tasks if not t.get("done"))
                console.print(f"\n  [dim]{pending}/{len(tasks)} pending[/dim]")
                console.print()
            else:
                for i, t in enumerate(tasks):
                    mark = "✓" if t.get("done") else "○"
                    print(f"  {mark} {i}  {t.get('text', '')}")
        elif sub == "add":
            if not rest:
                console.print("[dim]Usage: /todo add <task text>[/dim]" if HAS_RICH else "Usage: /todo add <task>")
                return
            task = {"text": rest, "done": False, "id": len(tasks)}
            tasks.append(task)
            _save(tasks)
            console.print(f"  [dim]✓ Added: {rest}[/dim]" if HAS_RICH else f"Added: {rest}")
        elif sub in ("done", "check", "complete"):
            try:
                idx = int(rest)
                tasks[idx]["done"] = True
                _save(tasks)
                console.print(f"  [dim]✓ Done: {tasks[idx]['text']}[/dim]" if HAS_RICH
                              else f"Done: {tasks[idx]['text']}")
            except (ValueError, IndexError):
                console.print("[dim]Usage: /todo done <id>[/dim]" if HAS_RICH else "Usage: /todo done <id>")
        elif sub in ("remove", "rm", "delete", "del"):
            try:
                idx = int(rest)
                removed = tasks.pop(idx)
                _save(tasks)
                console.print(f"  [dim]Removed: {removed['text']}[/dim]" if HAS_RICH
                              else f"Removed: {removed['text']}")
            except (ValueError, IndexError):
                console.print("[dim]Usage: /todo remove <id>[/dim]" if HAS_RICH else "bad index")
        elif sub == "clear":
            _save([])
            console.print("[dim]All tasks cleared[/dim]" if HAS_RICH else "Cleared")
        else:
            full_text = (sub + " " + rest).strip()
            task = {"text": full_text, "done": False, "id": len(tasks)}
            tasks.append(task)
            _save(tasks)
            console.print(f"  [dim]✓ Added: {full_text}[/dim]" if HAS_RICH else f"Added: {full_text}")

    def cmd_doctor(self, args: str):
        try:
            from doctor import run_doctor

            report = run_doctor(
                self.terminal.config,
                check_network="--network" in (args or "").split(),
            )
            if HAS_RICH:
                from rich.table import Table as _DoctorTable
                table = _DoctorTable(title="Aria Code doctor", box=rich_box.ROUNDED)
                table.add_column("Status", width=8)
                table.add_column("Check", style="bold")
                table.add_column("Detail", style="dim")
                table.add_column("Suggestion", style="dim")
                icons = {"ok": "[green]OK[/green]", "warn": "[yellow]WARN[/yellow]", "err": "[red]ERR[/red]"}
                for check in report.checks:
                    table.add_row(
                        icons.get(check.status, check.status.upper()),
                        check.name,
                        check.detail,
                        check.suggestion,
                    )
                console.print()
                console.print(table)
                color = "green" if report.errors == 0 and report.warnings == 0 else ("yellow" if report.errors == 0 else "red")
                console.print(f"[{color}]{report.passed} passed · {report.warnings} warnings · {report.errors} errors[/{color}]")
                console.print()
            else:
                from doctor import format_doctor_plain
                print(format_doctor_plain(report))
            return
        except Exception as exc:
            console.print(f"[yellow]doctor module unavailable, using legacy checks: {exc}[/yellow]" if HAS_RICH else f"doctor module unavailable: {exc}")

        import importlib as _il, subprocess as _sp, shutil as _sh
        cfg = self.terminal.config
        ollama_url = cfg.get("ollama_url", "http://localhost:11434")
        api_url = cfg.get("api_url", "http://localhost:8000")

        checks: list[tuple] = []

        def _ok(label, detail=""): checks.append(("ok", label, detail))
        def _warn(label, detail=""): checks.append(("warn", label, detail))
        def _err(label, detail=""): checks.append(("err", label, detail))

        import sys as _sys
        pyver = f"{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}"
        if _sys.version_info >= (3, 9):
            _ok("Python", pyver)
        else:
            _warn("Python", f"{pyver} (3.9+ recommended)")

        try:
            import urllib.request as _ur
            _opener = _ur.build_opener(_ur.ProxyHandler({}))
            _r = _opener.open(f"{ollama_url}/api/tags", timeout=3)
            _data = json.loads(_r.read())
            models = [m["name"] for m in _data.get("models", [])]
            if models:
                _ok("Ollama", f"{len(models)} models: {', '.join(models[:4])}")
            else:
                _warn("Ollama", "running but no models installed (ollama pull qwen2.5-coder:1.5b)")
        except Exception as e:
            _err("Ollama", f"not reachable at {ollama_url} ({e})")

        try:
            import urllib.request as _ur
            _opener = _ur.build_opener(_ur.ProxyHandler({}))
            _r = _opener.open(f"{api_url}/health", timeout=3)
            _ok("Backend", f"running at {api_url}")
        except Exception:
            _warn("Backend", f"offline at {api_url} — local Ollama mode will be used")

        key_checks = [
            ("finnhub", "股票行情"),
            ("alphavantage", "历史数据"),
            ("newsapi", "新闻"),
            ("brave", "网络搜索"),
            ("coingecko", "加密货币"),
        ]
        for svc, desc in key_checks:
            k = _get_provider_key(svc)
            if k:
                _ok(f"API key: {svc}", f"{desc} ({'*'*6}{k[-4:]})")
            else:
                _warn(f"API key: {svc}", f"{desc} 未配置 (/apikey set {svc} <key>)")

        llm_keys = [("deepseek", "DeepSeek"), ("openai", "OpenAI"),
                    ("siliconflow", "SiliconFlow"), ("moonshot", "Moonshot")]
        _has_any_llm = False
        for svc, name in llm_keys:
            k = _get_provider_key(svc)
            if k:
                _ok(f"LLM key: {svc}", f"{name} configured")
                _has_any_llm = True
        if not _has_any_llm:
            _warn("LLM keys", "No cloud LLM keys — Ollama must be running for AI responses")

        _pkgs = [
            ("aiohttp", "async HTTP"),
            ("rich", "terminal UI"),
            ("prompt_toolkit", "autocomplete"),
            ("yfinance", "market data"),
            ("pandas", "data processing"),
            ("requests", "HTTP client"),
        ]
        for pkg, desc in _pkgs:
            try:
                m = _il.import_module(pkg)
                ver = getattr(m, "__version__", "?")
                _ok(f"pkg: {pkg}", f"{desc} v{ver}")
            except ImportError:
                _warn(f"pkg: {pkg}", f"{desc} not installed (pip install {pkg})")

        aria_md = pathlib.Path.cwd() / "ARIA.md"
        if aria_md.exists():
            lines = len(aria_md.read_text(encoding="utf-8").splitlines())
            _ok("ARIA.md", f"{lines} lines of project context")
        else:
            _warn("ARIA.md", f"not found in {pathlib.Path.cwd()} (use /init to create)")

        if _HAS_MCP:
            try:
                reg = self.terminal._mcp_registry
                if reg and hasattr(reg, "list_tools"):
                    tools = reg.list_tools()
                    _ok("MCP", f"{len(tools)} tools from MCP servers")
                else:
                    _warn("MCP", "registry not started yet")
            except Exception:
                _warn("MCP", "loaded but no active servers")
        else:
            _warn("MCP", "mcp_client not found — MCP support disabled")

        tool_count = len(ARIA_TOOLS) + len(LOCAL_TOOLS)
        _ok("Aria tools", f"{tool_count} tools loaded")

        console.print() if HAS_RICH else None
        if HAS_RICH:
            console.print("[bold]Aria Code — Diagnostics[/bold]")
            console.print()
            icons = {"ok": "[green]✓[/green]", "warn": "[yellow]⚠[/yellow]", "err": "[red]✗[/red]"}
            for status, label, detail in checks:
                icon = icons[status]
                detail_str = f"  [dim]{detail}[/dim]" if detail else ""
                console.print(f"  {icon}  {label:<28}{detail_str}")
            console.print()
            n_ok = sum(1 for s, *_ in checks if s == "ok")
            n_w = sum(1 for s, *_ in checks if s == "warn")
            n_e = sum(1 for s, *_ in checks if s == "err")
            summary_color = "green" if n_e == 0 and n_w == 0 else ("yellow" if n_e == 0 else "red")
            console.print(f"  [{summary_color}]{n_ok} passed · {n_w} warnings · {n_e} errors[/{summary_color}]")
            console.print()

            _fh_ok = bool(_get_provider_key("finnhub"))
            _av_ok = bool(_get_provider_key("alphavantage"))
            _na_ok = bool(_get_provider_key("newsapi"))
            _ak_ok = True
            _llm_ok = any(_get_provider_key(p) for p in ("deepseek", "openai", "anthropic", "groq"))

            _guide_needed = not (_fh_ok and _av_ok and _na_ok and _llm_ok)
            if _guide_needed:
                console.print("[bold]数据源配置指南[/bold]  [dim](完整功能需要以下 key)[/dim]")
                console.print()
            console.print("  [dim]Use /doctor --network for network checks[/dim]")
        else:
            print("Diagnostics complete")

    async def cmd_datasource(self, args: str):
        sub = args.strip().lower()
        if sub.startswith("test "):
            src_name = sub[5:].strip()
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: _test_datasource(src_name)
            )
            return

        if sub == "config":
            paths = [
                "~/.aria/datasources.yaml",
                "~/.aria/.env",
                str(CONFIG_DIR / "providers.json"),
            ]
            if HAS_RICH:
                console.print("  [bold]数据源配置文件:[/bold]")
                for p in paths:
                    import pathlib
                    full = pathlib.Path(p).expanduser()
                    exists = "[green]✓[/green]" if full.exists() else "[dim]✗ (未创建)[/dim]"
                    console.print(f"  {exists}  [dim]{p}[/dim]")
                console.print("\n  [dim]环境变量: TUSHARE_TOKEN FRED_API_KEY ALPHA_VANTAGE_KEY[/dim]")
            return

        try:
            from datasources.router import _SOURCE_REGISTRY, DataRouter
            router = DataRouter()
        except ImportError:
            _print_error("datasources 模块未找到")
            return

        if HAS_RICH:
            from rich.table import Table
            from rich import box as rich_box
            table = Table(title="数据源状态", box=rich_box.SIMPLE, header_style="bold dim")
            table.add_column("名称", width=16)
            table.add_column("市场", width=20)
            table.add_column("需要Key", width=8)
            table.add_column("状态", width=8)
            table.add_column("说明")
            _DESC = {
                "yfinance": "Yahoo Finance (免费)",
                "akshare": "AkShare A股 (免费)",
                "tushare": "Tushare Pro (需Token)",
                "fred": "美联储经济数据 (免费)",
                "edgar": "SEC EDGAR 财报 (免费)",
                "alpha_vantage": "Alpha Vantage (免费Key)",
                "world_bank": "世界银行 (免费)",
            }
            for name, cls in _SOURCE_REGISTRY.items():
                try:
                    src = cls()
                    configured = src.is_configured()
                    status = "[green]✓ 就绪[/green]" if configured else "[dim]✗ 未配置[/dim]"
                    needs_key = "是" if cls.requires_key else "否"
                    markets = ", ".join(getattr(cls, "markets", []))
                except Exception:
                    status, needs_key, markets = "[red]错误[/red]", "?", "?"
                table.add_row(name, markets, needs_key, status, _DESC.get(name, ""))
            console.print(table)
            console.print("  [dim]/datasource config — 配置文件路径[/dim]")
        else:
            for name, cls in _SOURCE_REGISTRY.items():
                src = cls()
                print(f"  {name}: {'ready' if src.is_configured() else 'not configured'}")
