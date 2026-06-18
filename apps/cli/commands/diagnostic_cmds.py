"""DiagnosticCommandsMixin — runtime/status/trace/health commands."""

from __future__ import annotations

import json
import os


class DiagnosticCommandsMixin:
    """Mixin providing runtime diagnostics commands."""

    async def cmd_status(self, args: str):
        """Runtime status panel: engine · tools · model · context · risk"""
        t = self.terminal
        cfg = t.config
        model_id = cfg.get("model", "qwen2.5:7b")
        tool_count = len(ARIA_TOOLS) + len(LOCAL_TOOLS)
        skill_count = len(SKILLS)

        _lp = t._last_provider or ""
        _badge = next((v.get("badge", "") for v in MODELS.values() if v["id"] == model_id), "")
        if _lp == "ollama":
            runtime = "local (Ollama)"
        elif _lp in ("deepseek", "openai", "anthropic", "groq", "dashscope", "together"):
            runtime = f"cloud ({_lp})"
        elif _badge == "Cloud" or "cloud" in model_id.lower():
            runtime = "cloud"
        else:
            runtime = "local" if getattr(t, "_ollama_alive", False) else "unknown"

        conv = t.conversation
        est_tok = sum(len(m.get("content", "")) for m in conv) // 3
        max_ctx = get_model_cfg(model_id).get("num_ctx", 16384)
        ctx_pct = min(100, int(est_tok / max_ctx * 100))

        mk = next((k for k, v in MODELS.items() if v["id"] == model_id), None)
        model_display = MODELS[mk]["name"] if mk else model_id

        if HAS_RICH:
            console.print()
            console.print("[bold]Runtime Status[/bold]")
            console.print()
            rows = [
                ("runtime", runtime),
                ("model", model_display),
                ("engine", "quant engine v3.0"),
                ("tools", f"{tool_count} available  ·  {skill_count} skills"),
                ("risk", "enabled"),
                ("context", f"{est_tok:,} / {max_ctx:,} tokens  ({ctx_pct}%)"),
            ]
            if getattr(t, "_project_session", None):
                rows.append(("project", f"{t._project_session.name}  ({t._project_session.stats.get('total_files',0)} files)"))
            if getattr(t, "_file_session", None) and t._file_session.get_active():
                fc = t._file_session.get_active()
                rows.append(("file", f"{fc.filename}  ({fc.size_kb:.0f} KB)"))
            rows.append(("banner", cfg.get("banner", "full")))
            rows.append(("workspace", os.getcwd().replace(os.path.expanduser("~"), "~")))
            for k, v in rows:
                console.print(f"  [dim]{k:<12}[/dim][cyan]{v}[/cyan]")
            console.print()
        else:
            print("\nRuntime Status")
            print(f"  runtime  {runtime}")
            print(f"  model    {model_display}")
            print(f"  tools    {tool_count}")
            print(f"  context  {est_tok}/{max_ctx}")
            print()

    def cmd_trace(self, args: str):
        """Show runtime trace for recent tool calls."""
        trace = getattr(self.terminal, "runtime_trace", None)
        if trace is None:
            msg = "Runtime trace is unavailable."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if "--json" in args.split():
            payload = json.dumps(trace.to_dict(), ensure_ascii=False, indent=2)
            if HAS_RICH:
                console.print(Syntax(payload, "json", theme=_SYNTAX_THEME))
            else:
                print(payload)
            return
        turns = trace.turn_results[-5:]
        calls = trace.tool_calls[-20:]
        if not calls and not turns:
            msg = "No tool calls recorded yet."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if HAS_RICH:
            console.print()
            console.print("[bold]Runtime Trace[/bold]")
            console.print()
            if turns:
                console.print("  [dim]Recent turns[/dim]")
                for turn in turns:
                    ok = bool(turn.success)
                    style = "green" if ok else "red"
                    status = turn.status or ("ok" if ok else "err")
                    summary = turn.summary or turn.final_text[:120]
                    if len(summary) > 120:
                        summary = summary[:117] + "..."
                    console.print(
                        f"    [{style}]{status:<8}[/{style}] "
                        f"[bold]{turn.provider or '?'}[/bold] "
                        f"[dim]{summary}[/dim]"
                    )
                console.print()
            for call in calls:
                ok = bool(call.result.get("success"))
                style = "green" if ok else "red"
                console.print(
                    f"  [{style}]{'ok' if ok else 'err':<3}[/{style}] "
                    f"[bold]{call.tool}[/bold] "
                    f"[dim]{call.elapsed_ms:.0f} ms[/dim]"
                )
                if not ok and call.result.get("error"):
                    console.print(f"      [red]{str(call.result.get('error'))[:180]}[/red]")
            console.print()
        else:
            print("\nRuntime Trace")
            for turn in turns:
                ok = "ok" if turn.success else "err"
                summary = turn.summary or turn.final_text[:120]
                if len(summary) > 120:
                    summary = summary[:117] + "..."
                print(f"  {ok:<3} {turn.provider or '?'} {summary}")
            for call in calls:
                ok = "ok" if call.result.get("success") else "err"
                print(f"  {ok:<3} {call.tool} {call.elapsed_ms:.0f} ms")
            print()

    async def cmd_health(self, args: str):
        import aiohttp
        if HAS_RICH:
            console.print()
        urls = [
            ("AWS Backend", self.terminal.api_url, "/health"),
            ("Local Server", self.terminal.config.get("local_url", "http://localhost:8001"), "/health"),
            ("Ollama", self.terminal.config.get("ollama_url", "http://localhost:11434"), "/api/tags"),
        ]
        for label, url, path in urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{url}{path}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        data = await resp.json()
                        if label == "Ollama":
                            models = [m.get("name", "?") for m in data.get("models", [])[:3]]
                            detail = ", ".join(models)
                        else:
                            detail = f"v{data.get('version', '?')}"
                        if HAS_RICH:
                            console.print(f"  [green]●[/green] [dim]{label}[/dim]  {detail}")
                        else:
                            print(f"  + {label}  {detail}")
            except Exception:
                if HAS_RICH:
                    console.print(f"  [red]●[/red] [dim]{label}[/dim]  offline")
                else:
                    print(f"  - {label}  offline")
        if HAS_RICH:
            console.print()
