"""OpsCommandsMixin — watch, services, plan, git, and GitHub helper commands."""

from __future__ import annotations


class OpsCommandsMixin:
    """Mixin: operational and workflow helper commands."""

    def cmd_watch(self, args: str):
        parts = args.split() if args else ["list"]
        action = parts[0].lower() if parts else "list"
        watchlist = self.terminal.config.get("watchlist", [])

        if action == "add" and len(parts) > 1:
            symbol = parts[1].upper()
            if symbol not in watchlist:
                watchlist.append(symbol)
                self.terminal.config["watchlist"] = watchlist
                save_config(self.terminal.config)
                console.print(f"[green]Added {symbol} to watchlist[/green]" if HAS_RICH
                              else f"Added {symbol}")
            else:
                console.print(f"[dim]{symbol} already in watchlist[/dim]" if HAS_RICH
                              else f"{symbol} already in watchlist")

        elif action == "remove" and len(parts) > 1:
            symbol = parts[1].upper()
            if symbol in watchlist:
                watchlist.remove(symbol)
                self.terminal.config["watchlist"] = watchlist
                save_config(self.terminal.config)
                console.print(f"[dim]Removed {symbol} from watchlist[/dim]" if HAS_RICH
                              else f"Removed {symbol}")
            else:
                console.print(f"[red]{symbol} not in watchlist[/red]" if HAS_RICH
                              else f"{symbol} not in watchlist")

        else:
            if HAS_RICH:
                if watchlist:
                    console.print(f"  [dim]Watchlist:[/dim] {', '.join(watchlist)}")
                else:
                    console.print("  [dim]Watchlist: Empty[/dim]")
            else:
                print(f"Watchlist: {', '.join(watchlist)}")

    def cmd_services(self, args: str):
        """Show CLI service tiers and core workflows."""
        provider_summary = None
        try:
            from packages.aria_services.provider_health import GLOBAL_PROVIDER_HEALTH
            provider_summary = GLOBAL_PROVIDER_HEALTH.summary()
        except Exception:
            provider_summary = None

        cfg = getattr(self.terminal, "config", {}) or {}
        auto_compact = bool(cfg.get("auto_compact_context", True))
        try:
            auto_compact_threshold = int(float(cfg.get("auto_compact_threshold", 0.78)) * 100)
        except Exception:
            auto_compact_threshold = 78

        def _key_status(provider: str) -> str:
            try:
                return "configured" if _get_provider_key(provider) else "not set"
            except Exception:
                return "unknown"

        runtime_services = [
            ("LLM runtime", cfg.get("model", "unknown"), "local/Ollama with cloud fallback"),
            ("Context manager", f"auto compact {'on' if auto_compact else 'off'} @ {auto_compact_threshold}%", "preflight + post-turn guards"),
            ("Market data", "DataService", "quote/history/fundamentals/TA + provider health"),
            ("Charts/reports", "artifact services", "HTML/PNG charts, dashboards, Markdown/HTML reports"),
            ("News/Web", f"finnhub:{_key_status('finnhub')} · newsapi:{_key_status('newsapi')} · brave:{_key_status('brave')}", "news command + web search fallback"),
            ("Cloud AI/data", f"dashscope:{_key_status('dashscope')} · openai:{_key_status('openai')} · anthropic:{_key_status('anthropic')}", "optional external providers"),
            ("MCP/tools", f"{len(LOCAL_TOOLS)} local tools", "tool loop with repeat-call guard"),
        ]

        service_groups = [
            (
                "CORE (Standard)",
                [
                    "Code agent with local tools (read/write/edit/search/run)",
                    "Slash command workflows for quote/analyze/backtest/risk/screen",
                    "Session save/load/export and interactive history management",
                    "Model switching + thinking mode controls for response depth",
                ],
            ),
            (
                "QUANTUM Automation",
                [
                    "Agentic multi-step loop (auto read -> analyze -> edit -> execute)",
                    "Auto-recovery guidance for failed commands and code fixes",
                    "Strategy generation, backtest reporting, and risk analysis skills",
                    "Cross-workspace research sync hooks (session + export pipeline)",
                ],
            ),
            (
                "ENTERPRISE Controls (included in Quantum)",
                [
                    "Service health diagnostics (/health) for backend + local model stack",
                    "Governed command execution with dangerous-command blocking",
                    "Audit-friendly session logs and reproducible command trails",
                    "MCP-ready service integration path via external tool endpoints",
                ],
            ),
        ]

        quick_flow = [
            "/model",
            "/gen-strategy momentum AAPL",
            "/backtest momentum AAPL 2024-01-01 2025-01-01",
            "/risk AAPL",
            "/export md strategy_report.md",
        ]

        if HAS_RICH:
            console.print()
            console.print("[bold]CLI Services[/bold] [dim](runtime boundaries + workflow)[/dim]")
            console.print()
            console.print("  [bold]Runtime Service Map[/bold]")
            for name, status, detail in runtime_services:
                console.print(f"    [dim]{name:<16}[/dim] [bold]{status}[/bold]  [dim]{detail}[/dim]")
            if provider_summary is not None:
                color = "green" if provider_summary.status == "ok" else ("red" if provider_summary.status == "err" else "yellow")
                console.print(f"    [dim]{'Provider health':<16}[/dim] [{color}]{provider_summary.status}[/{color}]  [dim]{provider_summary.detail}[/dim]")
                console.print(f"    [dim]{'Suggestion':<16}[/dim] [dim]{provider_summary.suggestion}[/dim]")
            console.print()
            for group_name, items in service_groups:
                console.print(f"  [bold #C08050]{group_name}[/bold #C08050]")
                for item in items:
                    console.print(f"    [dim]> {item}[/dim]")
                console.print()

            console.print("  [bold]Quick Start Flow[/bold]")
            for cmd in quick_flow:
                console.print(f"    [bold]{cmd}[/bold]")
            console.print()
        else:
            print("\nCLI Services (runtime boundaries + workflow)\n")
            print("  Runtime Service Map")
            for name, status, detail in runtime_services:
                print(f"    {name:<16} {status}  {detail}")
            if provider_summary is not None:
                print(f"    {'Provider health':<16} {provider_summary.status}  {provider_summary.detail}")
                print(f"    {'Suggestion':<16} {provider_summary.suggestion}")
            print()
            for group_name, items in service_groups:
                print(f"  {group_name}")
                for item in items:
                    print(f"    > {item}")
                print()

            print("  Quick Start Flow")
            for cmd in quick_flow:
                print(f"    {cmd}")
            print()

    def cmd_plan(self, args: str):
        """Create an executable plan and store it for /apply-plan."""
        raw = args.strip()
        if not raw:
            if HAS_RICH:
                console.print("[dim]Usage: /plan <steps>  — see examples below[/dim]")
                console.print("[dim]  /plan fetch AAPL quote -> generate chart -> write report[/dim]")
                console.print("[dim]  /plan 1. Analyze sentiment  2. Build model  3. Backtest[/dim]")
            else:
                print("Usage: /plan <steps>")
                print("  /plan fetch AAPL quote -> generate chart -> write report")
                print("  /plan 1. Analyze sentiment  2. Build model  3. Backtest")
            return

        from plan_utils import parse_plan
        plan_steps = parse_plan(raw)
        if not plan_steps:
            console.print("[dim]No valid steps found[/dim]" if HAS_RICH else "No valid steps found")
            return

        self.terminal.pending_plan = [s.description for s in plan_steps]

        if HAS_RICH:
            console.print()
            console.print(f"[bold]Execution Plan[/bold]  [dim]({len(plan_steps)} steps)[/dim]")
            console.print()
            for s in plan_steps:
                dep_str = f"  [dim](after {', '.join(str(d) for d in s.deps)})[/dim]" if s.deps else ""
                label = f" [dim][{s.name}][/dim]" if s.name else ""
                console.print(f"  [dim]{s.index}.[/dim]{label} [bold]{s.description}[/bold]{dep_str}")
            console.print()
            console.print("[dim]Run /apply-plan to execute these steps.[/dim]")
            console.print()
        else:
            print(f"\nExecution Plan ({len(plan_steps)} steps)")
            for s in plan_steps:
                dep_str = f"  (after {', '.join(str(d) for d in s.deps)})" if s.deps else ""
                label = f" [{s.name}]" if s.name else ""
                print(f"  {s.index}.{label} {s.description}{dep_str}")
            print("Run /apply-plan to execute these steps.\n")

    def cmd_plan_report(self, args: str):
        """Show or export last plan execution report."""
        rows = list(getattr(self.terminal, "last_plan_results", []) or [])
        if not rows:
            msg = "No plan report available. Run /apply-plan first."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return

        parts = args.split()
        open_after = "--open" in parts
        parts = [p for p in parts if p != "--open"]
        fmt = parts[0].lower() if parts else "show"
        out_file = parts[1] if len(parts) > 1 else None

        if fmt == "show":
            if HAS_RICH:
                console.print()
                console.print("[bold]Last Plan Report[/bold]")
                for idx, row in enumerate(rows, 1):
                    status_color = "green" if row["status"] == "completed" else ("yellow" if row["status"] == "blocked" else "red")
                    console.print(
                        f"  [dim]{idx}.[/dim] [{status_color}]{row['status']}[/{status_color}] "
                        f"[bold]{row['step']}[/bold] [dim]({row['duration']}s, exit={row.get('exit_code')})[/dim]"
                    )
                    if row.get("error"):
                        console.print(f"     [red]{row['error']}[/red]")
                console.print()
            else:
                print("\nLast Plan Report")
                for idx, row in enumerate(rows, 1):
                    print(f"  {idx}. {row['status']}  {row['step']} ({row['duration']}s, exit={row.get('exit_code')})")
                    if row.get("error"):
                        print(f"     ERROR: {row['error']}")
            return

        if fmt not in {"md", "json"}:
            msg = "Usage: /plan-report [md|json] [file] [--open]"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return

        if not out_file:
            out_file = f"plan_report.{fmt}"

        try:
            if fmt == "json":
                content = json.dumps(rows, ensure_ascii=False, indent=2)
            else:
                md_lines = ["# Plan Execution Report", ""]
                for idx, row in enumerate(rows, 1):
                    md_lines.append(
                        f"{idx}. **{row['status']}** `{row['step']}` "
                        f"({row['duration']}s, exit={row.get('exit_code')})"
                    )
                    if row.get("error"):
                        md_lines.append(f"   - Error: {row['error']}")
                md_lines.append("")
                content = "\n".join(md_lines)

            result = _tool_write_file({"path": out_file, "content": content})
            if result.get("success"):
                saved_path = result["data"]["path"]
                msg = f"Plan report saved to {_display_path(saved_path)}"
                console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)
                if open_after:
                    self._open_file(saved_path)
            else:
                err = result.get("error", "Failed to save report")
                console.print(f"[red]{err}[/red]" if HAS_RICH else err)
        except Exception as e:
            console.print(f"[red]{e}[/red]" if HAS_RICH else str(e))

    def cmd_git(self, args: str):
        """Git helper shortcuts."""
        policy = self.terminal.config.get("command_policy", "safe")
        raw = args.strip()
        if not raw:
            sub = "status"
            sub_args = ""
        else:
            parts = raw.split(maxsplit=1)
            sub = parts[0].lower()
            sub_args = parts[1].strip() if len(parts) > 1 else ""

        mapping = {
            "status": "git status --short --branch",
            "diff": "git diff --stat",
            "summary": "git status --short --branch && git diff --stat",
            "branch": "git branch -v",
            "stash": "git stash list",
            "remote": "git remote -v",
        }
        if sub == "patch":
            cmd = "git diff" if not sub_args else f"git diff -- {sub_args}"
        elif sub == "log":
            limit = sub_args if sub_args and sub_args.isdigit() else "15"
            cmd = f"git log --oneline --graph --decorate -{limit}"
        elif sub == "commit":
            status_probe = _tool_run_command({"command": "git status --porcelain", "policy": policy})
            if not status_probe.get("success"):
                err = status_probe.get("error", "Failed to inspect git status")
                console.print(f"[red]{err}[/red]" if HAS_RICH else err)
                return

            status_out = status_probe.get("data", {}).get("stdout", "").strip()
            if not status_out:
                msg = "No changes to commit."
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return

            changed_files = []
            for line in status_out.splitlines():
                if len(line) >= 4:
                    changed_files.append(line[3:].strip())
            unique_files = [f for f in changed_files if f]
            total_files = len(unique_files)
            file_preview = ", ".join(unique_files[:5]) if unique_files else "workspace"
            body_summary = f"Files changed: {total_files}"
            body_preview = f"Top files: {file_preview}"

            if not sub_args:
                files = []
                for line in status_out.splitlines()[:3]:
                    if len(line) >= 4:
                        files.append(line[3:].strip())
                sample = ", ".join(files) if files else "workspace"
                total = len(status_out.splitlines())
                sub_args = f"chore: update {total} file(s) ({sample})"
                if HAS_RICH:
                    console.print(f"[dim]Auto commit message:[/dim] {sub_args}")
                else:
                    print(f"Auto commit message: {sub_args}")

            cmd = (
                f"git add -A && git commit "
                f"-m {shlex.quote(sub_args)} "
                f"-m {shlex.quote(body_summary)} "
                f"-m {shlex.quote(body_preview)}"
            )
        elif sub in mapping:
            cmd = mapping[sub]
        else:
            msg = "Usage: /git [status|diff|summary|patch|log [N]|branch|stash|remote|commit <msg>]"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        result = _tool_run_command({"command": cmd, "policy": policy})
        if not result.get("success"):
            console.print(f"[red]{result.get('error', 'Command failed')}[/red]" if HAS_RICH
                          else result.get("error", "Command failed"))
            return
        data = result.get("data", {})
        out = (data.get("stdout", "") + ("\n" + data.get("stderr", "") if data.get("stderr") else "")).strip()
        if out:
            if HAS_RICH:
                console.print(Syntax(out, "text", theme=_SYNTAX_THEME))
            else:
                print(out)

    def cmd_gh(self, args: str):
        """GitHub CLI helper — prs | issues | pr N | issue N | search | create-pr | diff N | checks N"""
        raw = args.strip()
        if not raw or raw in ("help", "--help"):
            lines = [
                "Usage: /gh <command>",
                "  prs            List open pull requests",
                "  issues         List open issues",
                "  pr <N>         View pull request #N",
                "  issue <N>      View issue #N",
                "  diff <N>       Show PR #N diff",
                "  checks <N>     Show PR #N CI checks",
                "  search <q>     Search code in this repo",
                "  create-pr      Create a PR (follow prompts)",
                "  commits [N]    Show last N commits (default 10)",
            ]
            for ln in lines:
                console.print(f"  [dim]{ln}[/dim]" if HAS_RICH else ln)
            return

        parts = raw.split(maxsplit=1)
        sub = parts[0].lower()
        subarg = parts[1].strip() if len(parts) > 1 else ""

        def _run(action: str, extra: dict = None):
            p = {"action": action}
            if extra:
                p.update(extra)
            r = _tool_github(p)
            if not r.get("success"):
                msg = r.get("error", "GitHub command failed")
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return
            data = r.get("data", {})
            out = data.get("stdout", "") if isinstance(data, dict) else str(data)
            if out.strip():
                if HAS_RICH:
                    try:
                        import json as _jj
                        parsed = _jj.loads(out)
                        from rich.pretty import pprint as _pp
                        _pp(parsed, expand_all=False)
                    except Exception:
                        console.print(Syntax(out, "text", theme=_SYNTAX_THEME))
                else:
                    print(out)

        if sub in ("prs", "pr_list"):
            _run("list_prs")
        elif sub in ("issues", "issue_list"):
            _run("list_issues")
        elif sub == "pr" and subarg.isdigit():
            _run("view_pr", {"number": int(subarg)})
        elif sub == "issue" and subarg.isdigit():
            _run("view_issue", {"number": int(subarg)})
        elif sub == "diff" and subarg.isdigit():
            _run("pr_diff", {"number": int(subarg)})
        elif sub == "checks" and subarg.isdigit():
            _run("pr_checks", {"number": int(subarg)})
        elif sub in ("commits", "log"):
            n = int(subarg) if subarg.isdigit() else 10
            _run("list_commits", {"limit": n})
        elif sub == "search":
            if not subarg:
                console.print("[dim]Usage: /gh search <query>[/dim]" if HAS_RICH else "Usage: /gh search <query>")
                return
            _run("search", {"q": subarg, "kind": "code"})
        elif sub in ("create-pr", "createpr", "create_pr"):
            try:
                title = (console.input("  PR title: ") if HAS_RICH else input("  PR title: ")).strip()
                body = (console.input("  PR body (optional): ") if HAS_RICH else input("  PR body (optional): ")).strip()
                base = (console.input("  Base branch [main]: ") if HAS_RICH else input("  Base branch [main]: ")).strip() or "main"
                _run("create_pr", {"title": title, "body": body, "base": base})
            except (EOFError, KeyboardInterrupt):
                console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
        else:
            console.print(f"[dim]Unknown /gh sub-command: {sub}. Try /gh help[/dim]" if HAS_RICH
                          else f"Unknown /gh sub-command: {sub}. Try /gh help")
