"""SessionUxCommandsMixin — clear, recap, history, compact, fork, copy commands."""

from __future__ import annotations

import os


class SessionUxCommandsMixin:
    """Mixin: session UX and conversation management commands."""

    def cmd_clear(self, args: str):
        self.terminal.conversation = []
        os.system("clear" if os.name == "posix" else "cls")
        console.print("[dim]Conversation cleared[/dim]" if HAS_RICH else "Cleared")

    def cmd_btw(self, args: str):
        q = args.strip()
        if not q:
            console.print("[dim]/btw <question>  — quick question without polluting history[/dim]" if HAS_RICH else "/btw <question>")
            return
        conv = self.terminal.conversation
        if not conv:
            console.print("[dim](no conversation context yet)[/dim]" if HAS_RICH else "(no context)")
            return
        _ctx_slice = conv[-6:] if len(conv) >= 6 else conv
        _ctx = "\n".join(
            f"{m['role'].upper()}: {str(m.get('content', ''))[:300]}"
            for m in _ctx_slice
        )
        _btw_prompt = (
            f"[Side question — answer briefly, do not reference this note]\n"
            f"Context from conversation:\n{_ctx}\n\nQuestion: {q}"
        )
        if HAS_RICH:
            from rich.panel import Panel as _Panel
            from rich import box as _rbox
            console.print(_Panel(f"[dim]{q}[/dim]", title="[dim]/btw[/dim]", box=_rbox.ROUNDED, border_style="dim"))
        import asyncio as _aio

        async def _ask_btw():
            _answer_parts: list[str] = []
            try:
                async for chunk in stream_chat(
                    self.terminal.config.get("ollama_url", "http://localhost:11434"),
                    _btw_prompt,
                    [],
                    model=self.terminal.config.get("model", ""),
                    config=self.terminal.config,
                    tools=[],
                ):
                    if chunk.get("type") == "content":
                        _answer_parts.append(chunk.get("content", ""))
            except Exception as _e:
                _answer_parts = [f"(error: {_e})"]
            return "".join(_answer_parts)

        try:
            loop = _aio.get_event_loop()
            answer = loop.run_until_complete(_ask_btw()) if not loop.is_running() else "(run /btw from interactive prompt)"
        except Exception:
            answer = "(could not get answer)"
        if HAS_RICH:
            from rich.panel import Panel as _Panel
            from rich import box as _rbox
            console.print(_Panel(answer.strip(), title="[dim]↩ btw[/dim]", box=_rbox.ROUNDED, border_style="dim #C08050"))
        else:
            print(f"\n  [btw] {answer.strip()}\n")

    def cmd_recap(self, args: str):
        conv = self.terminal.conversation
        if not conv:
            console.print("[dim]No conversation yet[/dim]" if HAS_RICH else "No conversation")
            return
        turns = len([m for m in conv if m.get("role") == "user"])
        topics: list[str] = []
        for m in conv:
            if m.get("role") == "user":
                snippet = str(m.get("content", ""))[:60].strip()
                if snippet:
                    topics.append(snippet)
        if HAS_RICH:
            from rich.panel import Panel as _Panel
            from rich import box as _rbox
            body = f"[dim]{turns} 轮对话[/dim]\n"
            for i, t in enumerate(topics[-6:], 1):
                body += f"  [dim]{i}.[/dim] {t}…\n"
            console.print(_Panel(body.rstrip(), title="[bold]会话摘要[/bold]", box=_rbox.ROUNDED, border_style="dim"))
        else:
            print(f"Session: {turns} turns")
            for i, t in enumerate(topics[-6:], 1):
                print(f"  {i}. {t}…")

    def cmd_history(self, args: str):
        if not self.terminal.conversation:
            console.print("[dim]No conversation history[/dim]" if HAS_RICH else "No history")
            return
        for msg in self.terminal.conversation[-10:]:
            role = msg["role"]
            content = msg["content"][:120]
            if HAS_RICH:
                prefix = "You" if role == "user" else "Aria"
                style = "bold" if role == "user" else "bold"
                console.print(f"[{style}]{prefix}:[/{style}] [dim]{content}[/dim]")
            else:
                print(f"{'You' if role == 'user' else 'Aria'}: {content}")

    def cmd_compact(self, args: str):
        if "--hard" in args:
            if len(self.terminal.conversation) > 10:
                kept = self.terminal.conversation[-6:]
                self.terminal.conversation = kept
                console.print(f"[dim]Hard-compacted to last {len(kept)} messages[/dim]" if HAS_RICH
                              else f"Hard-compacted to {len(kept)} messages")
            else:
                console.print("[dim]Context small enough, no compaction needed[/dim]" if HAS_RICH
                              else "No compaction needed")
            return
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            loop.run_until_complete(self._smart_compact_async(silent=False))
        except RuntimeError:
            if len(self.terminal.conversation) > 6:
                from apps.cli.message_processing import compact_messages
                compacted = compact_messages(
                    self.terminal.conversation,
                    model_key=self.terminal.config.get("model", "qwen2.5:7b"),
                )
                self.terminal.conversation = (
                    compacted
                    if len(compacted) < len(self.terminal.conversation)
                    else self.terminal.conversation[-8:]
                )
                console.print("[dim]Compacted (fallback)[/dim]")

    async def _smart_compact_async(self, silent: bool = False):
        conv = self.terminal.conversation
        if len(conv) <= 4:
            if not silent:
                console.print("[dim]Context small enough — no compaction needed[/dim]" if HAS_RICH
                              else "Context small enough")
            return

        if not silent and HAS_RICH:
            console.print("[dim]Summarising conversation...[/dim]")

        try:
            max_ctx = int(get_model_cfg(self.terminal.config.get("model", "qwen2.5:7b")).get("num_ctx", 16384) or 16384)
        except Exception:
            max_ctx = 16384
        from packages.aria_services.context import build_context_service
        context_service = build_context_service(max_tokens=max_ctx)
        summary_prompt = context_service.build_summary_prompt(conv)

        summary = ""
        try:
            ollama_url = self.terminal.config.get("ollama_url", "http://localhost:11434")
            result = await stream_ollama(
                ollama_url,
                summary_prompt,
                history=[],
                model=self.terminal.config.get("model", "qwen2.5:7b"),
                enable_tools=False,
            )
            if result.get("success") and result.get("response"):
                summary = result["response"].strip()
        except Exception:
            pass

        if not summary:
            try:
                compacted = context_service.compact_messages(conv)
            except Exception:
                compacted = []
            self.terminal.conversation = compacted if compacted and len(compacted) < len(conv) else conv[-8:]
            if not silent:
                console.print("[dim]Compacted (summary failed, used local fallback)[/dim]" if HAS_RICH
                              else "Compacted (summary fallback)")
            return

        envelope = context_service.build_summary_envelope(conv, summary)
        self.terminal.conversation = envelope.messages
        new_count = len(self.terminal.conversation)
        old_count = len(conv)
        if not silent:
            if HAS_RICH:
                console.print(
                    f"  [dim]✓ Compacted {old_count} → {new_count} messages "
                    f"(summary preserved context)[/dim]"
                )
            else:
                print(f"Compacted {old_count} → {new_count} messages")

    def cmd_fork(self, args: str):
        import time as _t
        name = args.strip() or f"fork-{_t.strftime('%H%M%S')}"
        snapshot = {
            "name": name,
            "ts": _t.strftime("%Y-%m-%d %H:%M:%S"),
            "conv": [dict(m) for m in self.terminal.conversation],
            "config": dict(self.terminal.config),
        }
        self.terminal._forks.append(snapshot)
        idx = len(self.terminal._forks) - 1
        if HAS_RICH:
            console.print(
                f"  [dim]↳ Forked as [bold]{name}[/bold] "
                f"(fork #{idx}, {len(snapshot['conv'])} messages). "
                f"Restore with /load-fork {idx}[/dim]"
            )
        else:
            print(f"Forked as '{name}' (#{idx}). Restore with /load-fork {idx}")

    def cmd_load_fork(self, args: str):
        forks = self.terminal._forks
        if not forks:
            console.print("[dim]No forks yet — use /fork to create one[/dim]" if HAS_RICH else "No forks")
            return
        try:
            idx = int(args.strip())
        except (ValueError, IndexError):
            if HAS_RICH:
                for i, f in enumerate(forks):
                    console.print(f"  [dim]#{i}[/dim]  {f['name']}  [dim]{f['ts']}  {len(f['conv'])} msgs[/dim]")
            else:
                for i, f in enumerate(forks):
                    print(f"  #{i}  {f['name']}  {f['ts']}")
            return
        if idx < 0 or idx >= len(forks):
            console.print(f"[dim]Fork #{idx} not found[/dim]" if HAS_RICH else "Invalid index")
            return
        snap = forks[idx]
        self.terminal.conversation = [dict(m) for m in snap["conv"]]
        console.print(
            f"  [dim]✓ Restored fork [bold]{snap['name']}[/bold] "
            f"({len(snap['conv'])} messages)[/dim]"
            if HAS_RICH else f"Restored fork '{snap['name']}'"
        )

    def cmd_copy(self, args: str):
        text = self.terminal._last_response
        if not text:
            console.print("[dim]No response to copy yet[/dim]" if HAS_RICH else "Nothing to copy")
            return
        copied = False
        try:
            import subprocess as _sp
            _sp.run(["pbcopy"], input=text.encode(), check=True, timeout=3)
            copied = True
        except Exception:
            pass
        if not copied:
            try:
                import subprocess as _sp
                _sp.run(["xclip", "-selection", "clipboard"], input=text.encode(), check=True, timeout=3)
                copied = True
            except Exception:
                pass
        if not copied:
            try:
                import subprocess as _sp
                _sp.run(["xdotool", "type", "--clearmodifiers", text], check=True, timeout=3)
                copied = True
            except Exception:
                pass
        if copied:
            self.terminal._record_feedback("copy", text)
            preview = text[:60].replace("\n", " ")
            console.print(
                f"  [dim]✓ Copied to clipboard: \"{preview}{'…' if len(text) > 60 else ''}\"[/dim]"
                if HAS_RICH else f"Copied: \"{preview}\""
            )
        else:
            console.print(
                "[yellow]Could not reach clipboard (pbcopy/xclip not found). "
                "Here is the response:[/yellow]\n" + text
                if HAS_RICH else "Clipboard unavailable. Response:\n" + text
            )
