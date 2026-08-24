"""WorkflowCommandsMixin — hooks, regen, undo, retry, note, review commands."""

from __future__ import annotations

import logging
import os
import pathlib
from datetime import datetime


logger = logging.getLogger(__name__)


import json
import asyncio
import datetime
import time
import shlex
from typing import Dict, Any, Optional

def _run_event_hook(*args, **kwargs):
    from aria_cli import _run_event_hook as fn
    return fn(*args, **kwargs)
def _load_hooks(*args, **kwargs):
    from aria_cli import _load_hooks as fn
    return fn(*args, **kwargs)
def _display_path(*args, **kwargs):
    from aria_cli import _display_path as fn
    return fn(*args, **kwargs)
def _get_MODELS():
    from aria_cli import MODELS as val
    return val
def _get__HAS_JSON_HOOKS():
    from aria_cli import _HAS_JSON_HOOKS as val
    return val
def _tool_run_command(*args, **kwargs):
    from aria_cli import _tool_run_command as fn
    return fn(*args, **kwargs)
def resolve_model_key(*args, **kwargs):
    from aria_cli import resolve_model_key as fn
    return fn(*args, **kwargs)
def _load_project_context(*args, **kwargs):
    from aria_cli import _load_project_context as fn
    return fn(*args, **kwargs)
def _fire_json_hook(*args, **kwargs):
    from aria_cli import _fire_json_hook as fn
    return fn(*args, **kwargs)
def _print_phase(*args, **kwargs):
    from aria_cli import _print_phase as fn
    return fn(*args, **kwargs)
def _get_CONFIG_DIR():
    from aria_cli import CONFIG_DIR as val
    return val

import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


import json
import asyncio
import datetime
import time
import shlex
import sys
import os
from typing import Dict, Any, Optional


class WorkflowCommandsMixin:
    """Mixin: interactive workflow and edit-review commands."""

    def cmd_hooks(self, args: str):
        global _JSON_HOOKS
        hooks_dirs = [
            _get_CONFIG_DIR() / "hooks",
            pathlib.Path.cwd() / ".aria" / "hooks",
        ]
        parts = args.strip().split(maxsplit=1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "reload":
            if _get__HAS_JSON_HOOKS():
                try:
                    _JSON_HOOKS = _load_hooks()
                    n = sum(len(v) for v in _JSON_HOOKS.values())
                    if self.context.has_rich:
                        self.context.console.print(f"  [green]✓[/green] [dim]hooks.json reloaded ({n} entries)[/dim]")
                    else:
                        print(f"  hooks.json reloaded ({n} entries)")
                except Exception as exc:
                    if self.context.has_rich:
                        self.context.console.print(f"  [red]✗ reload failed: {exc}[/red]")
                    else:
                        print(f"  reload failed: {exc}")
            return

        if sub == "list":
            if _get__HAS_JSON_HOOKS():
                try:
                    from apps.cli.hooks import list_hooks as _list_json_hooks
                    _json_rows = _list_json_hooks()
                    if _json_rows:
                        if self.context.has_rich:
                            self.context.console.print()
                            self.context.console.print("  [bold]JSON Hooks[/bold]  [dim](~/.arthera/hooks.json)[/dim]")
                            for r in _json_rows:
                                _block = " [red][blocking][/red]" if r["blocking"] else ""
                                _tool = f"[{r['tool']}]" if r["tool"] != "*" else ""
                                self.context.console.print(
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
                if self.context.has_rich:
                    self.context.console.print("  [dim]No hooks found.[/dim]")
                    self.context.console.print("  [dim]Hook dirs:[/dim]")
                    for d in hooks_dirs:
                        self.context.console.print(f"    [dim]{_display_path(d, fallback='hook dir')}[/dim]")
                    self.context.console.print("  [dim]Events: prompt_submit  response_done  tool_use  compact[/dim]")
                else:
                    print("No hooks. Dirs:", [str(d) for d in hooks_dirs])
                return
            if self.context.has_rich:
                self.context.console.print()
                for hdir, name, path in found:
                    self.context.console.print(f"  [dim]{name:<28}[/dim]  {_display_path(path, fallback='hook')}")
                self.context.console.print()
            else:
                for hdir, name, path in found:
                    print(f"  {name}  {_display_path(path, fallback='hook')}")

        elif sub == "edit":
            if not rest:
                if _get__HAS_JSON_HOOKS():
                    from apps.cli.hooks import hooks_file_path, create_example_hooks
                    _hpath = hooks_file_path("global")
                    create_example_hooks(_hpath)
                    editor = os.getenv("EDITOR", "nano")
                    try:
                        import subprocess as _sp
                        _sp.run([editor, str(_hpath)])
                        _JSON_HOOKS = _load_hooks()
                    except Exception as exc:
                        if self.context.has_rich:
                            self.context.console.print(f"[red]Could not open editor: {exc}[/red]")
                        else:
                            print(f"Could not open editor: {exc}")
                return
            event = rest
            hdir = _get_CONFIG_DIR() / "hooks"
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
                self.context.console.print(f"[red]Could not open editor: {exc}[/red]" if self.context.has_rich else str(exc))

        elif sub == "run":
            event = rest or "ResponseDone"
            if _get__HAS_JSON_HOOKS():
                _fire_json_hook(event, session_id=getattr(self.terminal, "session_id", ""), hooks=_JSON_HOOKS)
            _run_event_hook(event, {"ARIA_EVENT": event, "ARIA_SESSION": getattr(self.terminal, "session_id", "")})
            if self.context.has_rich:
                self.context.console.print(f"  [dim]Hook '{event}' triggered[/dim]")
            else:
                print(f"Hook '{event}' triggered")

        else:
            if self.context.has_rich:
                self.context.console.print("[dim]Usage: /hooks list|edit [event]|reload|run [event][/dim]")
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
            self.context.console.print("[dim]Regenerating...[/dim]" if self.context.has_rich else "Regenerating...")
            await self.terminal.send_message(last_user_msg)
        else:
            self.context.console.print("[dim]No message to regenerate[/dim]" if self.context.has_rich else "Nothing to regenerate")

    def cmd_undo(self, args: str):
        if len(self.terminal.conversation) < 2:
            self.context.console.print("[dim]Nothing to undo[/dim]" if self.context.has_rich else "Nothing to undo")
            return
        removed = 0
        for role in ("assistant", "user"):
            for i in range(len(self.terminal.conversation) - 1, -1, -1):
                if self.terminal.conversation[i]["role"] == role:
                    self.terminal.conversation.pop(i)
                    removed += 1
                    break
        if self.context.has_rich:
            self.context.console.print(f"[dim]Undone ({removed} messages removed, {len(self.terminal.conversation)} remaining)[/dim]")
        else:
            print(f"Undone ({removed} removed)")

    def cmd_rewind(self, args: str):
        """Restore code checkpoints, conversation history, or both."""
        from runtime.checkpoints import (
            CheckpointConflictError,
            CheckpointNotFoundError,
            CheckpointStore,
        )

        parts = [part for part in args.strip().split() if part]
        assume_yes = "--yes" in parts or "-y" in parts
        parts = [part for part in parts if part not in {"--yes", "-y"}]
        mode = parts[0].lower() if parts else "code"
        identifier = parts[1] if len(parts) > 1 else ""
        is_zh = str(self.terminal.config.get("ui_lang", "en")).lower().startswith("zh")

        if mode in {"conversation", "chat"}:
            self.cmd_undo("")
            return
        if mode not in {"code", "both", "list"}:
            identifier = parts[0]
            mode = "code"

        try:
            store = CheckpointStore()
        except Exception as exc:
            message = f"无法打开检查点存储: {exc}" if is_zh else f"Cannot open checkpoint store: {exc}"
            self.context.console.print(f"[red]{message}[/red]" if self.context.has_rich else message)
            return

        if mode == "list":
            records = store.list(session_id=self.terminal.session_id, status="active", limit=12)
            if not records:
                records = store.list(status="active", limit=12)
            if not records:
                message = "没有可恢复的代码检查点" if is_zh else "No active code checkpoints"
                self.context.console.print(f"[dim]{message}[/dim]" if self.context.has_rich else message)
                return
            if self.context.has_rich:
                self.context.console.print()
                self.context.console.print("  [bold]Code checkpoints[/bold]")
                for record in records:
                    paths = ", ".join(pathlib.Path(item.path).name for item in record.files)
                    run_label = (record.run_id or "standalone")[:10]
                    self.context.console.print(
                        f"  [#C08050]{record.checkpoint_id[:10]}[/#C08050]  "
                        f"[dim]{run_label:<10} · {record.source:<10} · {paths}[/dim]"
                    )
                self.context.console.print()
            else:
                for record in records:
                    paths = ", ".join(pathlib.Path(item.path).name for item in record.files)
                    print(f"{record.checkpoint_id[:10]} {record.run_id or '-'} {record.source} {paths}")
            return

        if not assume_yes:
            prompt = (
                "  恢复代码到检查点状态？检查点之后的修改不会被覆盖，存在冲突时会停止。 [y/N] "
                if is_zh else
                "  Rewind code to its checkpoint? Later changes are protected by conflict checks. [y/N] "
            )
            try:
                answer = (self.context.console.input(prompt) if self.context.has_rich else input(prompt)).strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            if answer not in {"y", "yes"}:
                message = "已取消" if is_zh else "Cancelled"
                self.context.console.print(f"[dim]{message}[/dim]" if self.context.has_rich else message)
                return

        try:
            if identifier:
                checkpoint = store.get(identifier)
                if checkpoint is not None:
                    result = store.restore_checkpoint(checkpoint.checkpoint_id)
                else:
                    result = store.restore_run(identifier)
            else:
                try:
                    result = store.restore_latest(session_id=self.terminal.session_id)
                except CheckpointNotFoundError:
                    result = store.restore_latest()
        except CheckpointConflictError as exc:
            message = f"恢复已停止: {exc}" if is_zh else f"Rewind stopped: {exc}"
            self.context.console.print(f"[red]{message}[/red]" if self.context.has_rich else message)
            return
        except CheckpointNotFoundError:
            message = "未找到可恢复的检查点" if is_zh else "No restorable checkpoint found"
            self.context.console.print(f"[dim]{message}[/dim]" if self.context.has_rich else message)
            return
        except Exception as exc:
            message = f"恢复失败: {exc}" if is_zh else f"Rewind failed: {exc}"
            self.context.console.print(f"[red]{message}[/red]" if self.context.has_rich else message)
            return

        if mode == "both":
            self.cmd_undo("")
        count = len(result.restored_paths)
        message = (
            f"已恢复 {count} 个文件" if is_zh else f"Rewound {count} file{'s' if count != 1 else ''}"
        )
        if self.context.has_rich:
            self.context.console.print(f"[green]✓[/green] [dim]{message}[/dim]")
            for path in result.restored_paths:
                self.context.console.print(f"  [dim]{path}[/dim]")
        else:
            print(message)
            for path in result.restored_paths:
                print(f"  {path}")

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
            self.context.console.print("[dim]No message to retry[/dim]" if self.context.has_rich else "Nothing to retry")
            return
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "user" and self.terminal.conversation[i]["content"] == last_user_msg:
                self.terminal.conversation.pop(i)
                break
        orig_model_key = resolve_model_key(self.terminal.config.get("model", "qwen2.5:7b"))
        _fallback_model = _get_MODELS().get("qwen-fast") or _get_MODELS().get("qwen7b") or next(iter(_get_MODELS().values()))
        orig_temp = _get_MODELS().get(orig_model_key, _fallback_model).get("temperature", 0.3)
        _get_MODELS()[orig_model_key]["temperature"] = min(0.9, orig_temp + 0.3)
        if self.context.has_rich:
            self.context.console.print(f"[dim]Retrying with temperature {_get_MODELS()[orig_model_key]['temperature']:.1f}...[/dim]")
        else:
            print("Retrying (temp +0.3)...")
        try:
            await self.terminal.send_message(last_user_msg)
        finally:
            _get_MODELS()[orig_model_key]["temperature"] = orig_temp

    def cmd_note(self, args: str):
        text = args.strip()
        if not text:
            self.context.console.print("[dim]Usage: /note <text>[/dim]" if self.context.has_rich else "Usage: /note <text>")
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
        if self.context.has_rich:
            self.context.console.print(f"[dim]Note saved to {aria_md.name}[/dim]")
        else:
            print(f"Saved to {aria_md.name}")

    async def cmd_review(self, args: str):
        raw = args.strip()
        policy = self.terminal.config.get("command_policy", "safe")

        if raw and not raw.startswith("--"):
            p = pathlib.Path(raw).expanduser()
            if not p.exists():
                msg = f"File not found: {raw}"
                self.context.console.print(f"[red]{msg}[/red]") if self.context.has_rich else print(msg)
                return
            _print_phase("Reading file")
            try:
                content = p.read_text(errors="replace")[:12000]
            except Exception as e:
                self.context.console.print(f"[red]Cannot read file: {e}[/red]") if self.context.has_rich else print(f"Cannot read: {e}")
                return
            line_count = content.count("\n")
            if self.context.has_rich:
                self.context.console.print(f"  [dim]↳ {p.name}  ·  {line_count} lines[/dim]")
            _print_phase("AI Review")
            prompt = (
                f"请对以下 `{p.name}` 的代码进行专业审查，查找 Bug、安全问题和改进点。\n"
                f"每条发现用严重程度标签开头：**BUG**、**IMPROVEMENT**、**NIT**。\n"
                f"按文件组织输出，直接给结论，不要重复贴出全部代码。\n\n"
                f"```\n{content}\n```"
            )
            review_source, review_name, review_is_diff = content, p.name, False
        else:
            diff_cmd = "git diff --staged" if raw.startswith("--staged") else "git diff HEAD"
            _print_phase("Reading diff")
            tr = _tool_run_command({"command": diff_cmd})
            if not tr.get("success"):
                msg = tr.get("error", "git diff failed")
                self.context.console.print(f"[red]{msg}[/red]") if self.context.has_rich else print(msg)
                return
            diff_text = (tr.get("data") or {}).get("stdout", "").strip()
            if not diff_text:
                self.context.console.print("[dim]No changes to review.[/dim]") if self.context.has_rich else print("No changes to review.")
                return
            _adds = diff_text.count("\n+") - diff_text.count("\n+++")
            _dels = diff_text.count("\n-") - diff_text.count("\n---")
            _files = diff_text.count("\ndiff --git")
            if self.context.has_rich:
                self.context.console.print(f"  [dim]↳ {_files} files  ·  +{_adds} −{_dels} lines[/dim]")
            diff_text = diff_text[:12000]
            _print_phase("AI Review")
            prompt = (
                "请审查以下 git diff，找出 Bug、潜在回归、安全问题和代码质量问题。\n"
                "每条发现用严重程度标签开头：**BUG**、**IMPROVEMENT**、**NIT**。\n"
                "按文件分组，直接给出结论。\n\n"
                f"```diff\n{diff_text}\n```"
            )
            review_source, review_name, review_is_diff = diff_text, "staged.diff", True

        # Run a bounded, deterministic first pass. The conversational model
        # receives it as evidence and must not pretend it accessed anything
        # beyond the supplied file or diff.
        try:
            from agents.code_review import CodeReviewAgent

            findings = CodeReviewAgent.review_source(
                review_source,
                filename=review_name,
                is_diff=review_is_diff,
            )
            static_review = CodeReviewAgent.format_findings(findings)
            if self.context.has_rich:
                self.context.console.print("[bold]Deterministic Review[/bold]")
                self.context.console.print(static_review)
            else:
                print("Deterministic Review\n" + static_review)
            prompt = (
                "以下是只基于提交文本的确定性检查结果。保留有证据的发现，补充语义、"
                "回归和可测试性审查；不要声称执行过测试或读取过其他文件。\n\n"
                f"{static_review}\n\n{prompt}"
            )
        except Exception as exc:
            logger.debug("Deterministic review unavailable: %s", exc)

        await self.terminal.send_message(prompt)
