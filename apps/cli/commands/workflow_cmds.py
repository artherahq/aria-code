"""WorkflowCommandsMixin — hooks, regen, undo, retry, note, review commands."""

from __future__ import annotations

import os
import pathlib
from datetime import datetime


class WorkflowCommandsMixin:
    """Mixin: interactive workflow and edit-review commands."""

    def cmd_hooks(self, args: str):
        global _JSON_HOOKS
        hooks_dirs = [
            CONFIG_DIR / "hooks",
            pathlib.Path.cwd() / ".aria" / "hooks",
        ]
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "reload":
            if _HAS_JSON_HOOKS:
                try:
                    _JSON_HOOKS = _load_hooks()
                    n = sum(len(v) for v in _JSON_HOOKS.values())
                    if HAS_RICH:
                        console.print(f"  [green]✓[/green] [dim]hooks.json reloaded ({n} entries)[/dim]")
                    else:
                        print(f"  hooks.json reloaded ({n} entries)")
                except Exception as exc:
                    if HAS_RICH:
                        console.print(f"  [red]✗ reload failed: {exc}[/red]")
                    else:
                        print(f"  reload failed: {exc}")
            return

        if sub == "list":
            if _HAS_JSON_HOOKS:
                try:
                    from apps.cli.hooks import list_hooks as _list_json_hooks
                    _json_rows = _list_json_hooks()
                    if _json_rows:
                        if HAS_RICH:
                            console.print()
                            console.print("  [bold]JSON Hooks[/bold]  [dim](~/.arthera/hooks.json)[/dim]")
                            for r in _json_rows:
                                _block = " [red][blocking][/red]" if r["blocking"] else ""
                                _tool = f"[{r['tool']}]" if r["tool"] != "*" else ""
                                console.print(
                                    f"  [cyan]{r['event']:<16}[/cyan]{_tool:<14}  "
                                    f"[dim]{r['command']}[/dim]{_block}"
                                )
                        else:
                            for r in _json_rows:
                                print(f"  {r['event']:<16} {r['tool']:<12} {r['command']}")
                except Exception:
                    pass

            found: list[tuple] = []
            for hdir in hooks_dirs:
                if hdir.exists():
                    for f in sorted(hdir.iterdir()):
                        if f.is_file() and not f.name.startswith("."):
                            found.append((str(hdir), f.name, str(f)))
            if not found:
                if HAS_RICH:
                    console.print(f"  [dim]No hooks found.[/dim]")
                    console.print(f"  [dim]Hook dirs:[/dim]")
                    for d in hooks_dirs:
                        console.print(f"    [dim]{_display_path(d, fallback='hook dir')}[/dim]")
                    console.print(f"  [dim]Events: prompt_submit  response_done  tool_use  compact[/dim]")
                else:
                    print("No hooks. Dirs:", [str(d) for d in hooks_dirs])
                return
            if HAS_RICH:
                console.print()
                for hdir, name, path in found:
                    console.print(f"  [dim]{name:<28}[/dim]  {_display_path(path, fallback='hook')}")
                console.print()
            else:
                for hdir, name, path in found:
                    print(f"  {name}  {_display_path(path, fallback='hook')}")

        elif sub == "edit":
            if not rest:
                if _HAS_JSON_HOOKS:
                    from apps.cli.hooks import hooks_file_path, create_example_hooks
                    _hpath = hooks_file_path("global")
                    create_example_hooks(_hpath)
                    editor = os.getenv("EDITOR", "nano")
                    try:
                        import subprocess as _sp
                        _sp.run([editor, str(_hpath)])
                        _JSON_HOOKS = _load_hooks()
                    except Exception as exc:
                        if HAS_RICH:
                            console.print(f"[red]Could not open editor: {exc}[/red]")
                        else:
                            print(f"Could not open editor: {exc}")
                return
            event = rest
            hdir = CONFIG_DIR / "hooks"
            hdir.mkdir(parents=True, exist_ok=True)
            script = hdir / f"{event}.sh"
            if not script.exists():
                script.write_text(
                    f"#!/bin/bash\n# Aria hook: {event}\n# "
                    f"Env vars: ARIA_EVENT ARIA_TOOL ARIA_TOOL_PARAMS ARIA_RESPONSE ARIA_SESSION\n\n"
                    f'echo "Hook {event} fired"\n',
                    encoding="utf-8"
                )
                script.chmod(0o755)
            editor = os.getenv("EDITOR", "nano")
            try:
                import subprocess as _sp
                _sp.run([editor, str(script)])
            except Exception as exc:
                console.print(f"[red]Could not open editor: {exc}[/red]" if HAS_RICH else str(exc))

        elif sub == "run":
            event = rest or "ResponseDone"
            if _HAS_JSON_HOOKS:
                _fire_json_hook(event, session_id=getattr(self.terminal, "session_id", ""), hooks=_JSON_HOOKS)
            _run_event_hook(event, {"ARIA_EVENT": event, "ARIA_SESSION": getattr(self.terminal, "session_id", "")})
            if HAS_RICH:
                console.print(f"  [dim]Hook '{event}' triggered[/dim]")
            else:
                print(f"Hook '{event}' triggered")

        else:
            if HAS_RICH:
                console.print("[dim]Usage: /hooks list|edit [event]|reload|run [event][/dim]")
            else:
                print("Usage: /hooks list|edit [event]|reload|run [event]")

    async def cmd_regen(self, args: str):
        last_user_msg = None
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "assistant":
                self.terminal.conversation.pop(i)
                break
        for msg in reversed(self.terminal.conversation):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break
        if last_user_msg:
            for i in range(len(self.terminal.conversation) - 1, -1, -1):
                if self.terminal.conversation[i]["role"] == "user" and self.terminal.conversation[i]["content"] == last_user_msg:
                    self.terminal.conversation.pop(i)
                    break
            console.print("[dim]Regenerating...[/dim]" if HAS_RICH else "Regenerating...")
            await self.terminal.send_message(last_user_msg)
        else:
            console.print("[dim]No message to regenerate[/dim]" if HAS_RICH else "Nothing to regenerate")

    def cmd_undo(self, args: str):
        if len(self.terminal.conversation) < 2:
            console.print("[dim]Nothing to undo[/dim]" if HAS_RICH else "Nothing to undo")
            return
        removed = 0
        for role in ("assistant", "user"):
            for i in range(len(self.terminal.conversation) - 1, -1, -1):
                if self.terminal.conversation[i]["role"] == role:
                    self.terminal.conversation.pop(i)
                    removed += 1
                    break
        if HAS_RICH:
            console.print(f"[dim]Undone ({removed} messages removed, {len(self.terminal.conversation)} remaining)[/dim]")
        else:
            print(f"Undone ({removed} removed)")

    async def cmd_retry(self, args: str):
        last_user_msg = None
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "assistant":
                self.terminal.conversation.pop(i)
                break
        for msg in reversed(self.terminal.conversation):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break
        if not last_user_msg:
            console.print("[dim]No message to retry[/dim]" if HAS_RICH else "Nothing to retry")
            return
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "user" and self.terminal.conversation[i]["content"] == last_user_msg:
                self.terminal.conversation.pop(i)
                break
        orig_model_key = resolve_model_key(self.terminal.config.get("model", "qwen2.5:7b"))
        _fallback_model = MODELS.get("qwen-fast") or MODELS.get("qwen7b") or next(iter(MODELS.values()))
        orig_temp = MODELS.get(orig_model_key, _fallback_model).get("temperature", 0.3)
        MODELS[orig_model_key]["temperature"] = min(0.9, orig_temp + 0.3)
        if HAS_RICH:
            console.print(f"[dim]Retrying with temperature {MODELS[orig_model_key]['temperature']:.1f}...[/dim]")
        else:
            print(f"Retrying (temp +0.3)...")
        try:
            await self.terminal.send_message(last_user_msg)
        finally:
            MODELS[orig_model_key]["temperature"] = orig_temp

    def cmd_note(self, args: str):
        text = args.strip()
        if not text:
            console.print("[dim]Usage: /note <text>[/dim]" if HAS_RICH else "Usage: /note <text>")
            return
        aria_md = pathlib.Path.cwd() / "ARIA.md"
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        entry = f"\n- [{now_str}] {text}"
        if aria_md.exists():
            content = aria_md.read_text(encoding="utf-8")
            if "## Notes" not in content:
                content += "\n\n## Notes\n"
            content += entry
        else:
            content = f"# Aria Project Notes\n\n## Notes\n{entry}\n"
        aria_md.write_text(content, encoding="utf-8")
        global _PROJECT_CONTEXT
        _PROJECT_CONTEXT = _load_project_context()
        if HAS_RICH:
            console.print(f"[dim]Note saved to {aria_md.name}[/dim]")
        else:
            print(f"Saved to {aria_md.name}")

    async def cmd_review(self, args: str):
        raw = args.strip()
        policy = self.terminal.config.get("command_policy", "safe")

        if raw and not raw.startswith("--"):
            p = pathlib.Path(raw).expanduser()
            if not p.exists():
                msg = f"File not found: {raw}"
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            _print_phase("Reading file")
            try:
                content = p.read_text(errors="replace")[:12000]
            except Exception as e:
                console.print(f"[red]Cannot read file: {e}[/red]") if HAS_RICH else print(f"Cannot read: {e}")
                return
            line_count = content.count("\n")
            if HAS_RICH:
                console.print(f"  [dim]↳ {p.name}  ·  {line_count} lines[/dim]")
            _print_phase("AI Review")
            prompt = (
                f"请对以下 `{p.name}` 的代码进行专业审查，查找 Bug、安全问题和改进点。\n"
                f"每条发现用严重程度标签开头：**BUG**、**IMPROVEMENT**、**NIT**。\n"
                f"按文件组织输出，直接给结论，不要重复贴出全部代码。\n\n"
                f"```\n{content}\n```"
            )
        else:
            diff_cmd = "git diff --staged" if raw.startswith("--staged") else "git diff HEAD"
            _print_phase("Reading diff")
            tr = _tool_run_command({"command": diff_cmd})
            if not tr.get("success"):
                msg = tr.get("error", "git diff failed")
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            diff_text = (tr.get("data") or {}).get("stdout", "").strip()
            if not diff_text:
                console.print("[dim]No changes to review.[/dim]") if HAS_RICH else print("No changes to review.")
                return
            _adds = diff_text.count("\n+") - diff_text.count("\n+++")
            _dels = diff_text.count("\n-") - diff_text.count("\n---")
            _files = diff_text.count("\ndiff --git")
            if HAS_RICH:
                console.print(f"  [dim]↳ {_files} files  ·  +{_adds} −{_dels} lines[/dim]")
            diff_text = diff_text[:12000]
            _print_phase("AI Review")
            prompt = (
                "请审查以下 git diff，找出 Bug、潜在回归、安全问题和代码质量问题。\n"
                "每条发现用严重程度标签开头：**BUG**、**IMPROVEMENT**、**NIT**。\n"
                "按文件分组，直接给出结论。\n\n"
                f"```diff\n{diff_text}\n```"
            )

        await self.terminal.send_message(prompt)
