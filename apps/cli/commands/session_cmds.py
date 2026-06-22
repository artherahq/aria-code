"""SessionCommandsMixin — session list/load/save/export commands."""

from __future__ import annotations

import json

from apps.cli.session_export import build_session_export_payload


class SessionCommandsMixin:
    """Mixin: session list/load/save/export commands."""

    def cmd_sessions(self, args: str):
        keyword = args.strip().lower()
        sessions = self.terminal.session_mgr.list_sessions()
        if keyword:
            sessions = [s for s in sessions if keyword in s["title"].lower()]
        if not sessions:
            msg = f"No sessions matching '{keyword}'" if keyword else "No saved sessions"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if HAS_RICH:
            console.print()
            header = f"  [bold]Sessions[/bold]  [dim]({len(sessions)} found)[/dim]" if keyword else "  [bold]Sessions[/bold]"
            console.print(header)
            for i, s in enumerate(sessions, 1):
                updated = s["updated"][:16] if s["updated"] else "-"
                console.print(f"    [dim]{i}.[/dim] [bold]{s['title']}[/bold]  "
                              f"[dim]{s['id'][:8]}  {s['messages']} msgs  {updated}[/dim]")
            console.print()
            console.print("  [dim]Use /load <number> to resume · /sessions <keyword> to search[/dim]")
        else:
            for i, s in enumerate(sessions, 1):
                print(f"  {i}. [{s['id'][:8]}] {s['title']} ({s['messages']} msgs)")

    def cmd_save(self, args: str):
        if not self.terminal.conversation:
            console.print("[dim]Nothing to save[/dim]" if HAS_RICH else "Nothing to save")
            return
        sid = self.terminal.session_id
        title = args.strip().strip('"').strip("'") if args.strip() else None
        meta = {}
        if title:
            meta["title"] = title
        self.terminal.session_mgr.save_session(sid, self.terminal.conversation, metadata=meta)
        self.terminal.config["last_session_id"] = sid
        save_config(self.terminal.config)
        display = f"{title} ({sid[:8]})" if title else f"{sid[:8]}..."
        console.print(f"[green]Session saved: {display}[/green]" if HAS_RICH
                      else f"Saved: {display}")

    def cmd_rename(self, args: str):
        """Rename current session."""
        title = args.strip().strip('"').strip("'")
        if not title:
            console.print("[dim]Usage: /rename <title>[/dim]" if HAS_RICH else "Usage: /rename <title>")
            return
        sid = self.terminal.session_id
        data = self.terminal.session_mgr.load_session(sid)
        if data:
            meta = data.get("metadata", {})
            meta["title"] = title
            self.terminal.session_mgr.save_session(sid, self.terminal.conversation, metadata=meta)
        else:
            self.terminal.session_mgr.save_session(sid, self.terminal.conversation, metadata={"title": title})
        console.print(f"[green]Renamed: {title}[/green]" if HAS_RICH else f"Renamed: {title}")

    def cmd_load(self, args: str):
        session_id = args.strip()
        if not session_id:
            sessions = self.terminal.session_mgr.list_sessions()
            if not sessions:
                console.print("[dim]No sessions. Usage: /load <session_id>[/dim]" if HAS_RICH
                              else "No sessions")
                return
            options = []
            for s in sessions[:20]:
                title = s.get("metadata", {}).get("title", s["id"][:8])
                ts = s.get("updated", "")[:10]
                options.append((title, ts))
            choice = _arrow_select(options, selected=0, title="Load Session")
            if 0 <= choice < len(sessions):
                session_id = sessions[choice]["id"]
            else:
                if HAS_RICH:
                    console.print("[dim]Cancelled[/dim]")
                else:
                    print("Cancelled")
                return

        data = self.terminal.session_mgr.load_session(session_id)
        if data:
            self.terminal.conversation = data.get("messages", [])
            self.terminal.session_id = data["id"]
            title = data.get("metadata", {}).get("title", "Untitled")
            n = len(self.terminal.conversation)
            console.print(f"[green]Loaded: {title} ({n} messages)[/green]" if HAS_RICH
                          else f"Loaded: {title} ({n} msgs)")
        else:
            _print_error(f"Session not found: {session_id}", "session")

    def cmd_recall(self, args: str):
        """Full-text search across all saved sessions: /recall <query>"""
        query = args.strip()
        if not query:
            console.print("[dim]Usage: /recall <query>[/dim]" if HAS_RICH else "Usage: /recall <query>")
            return
        results = self.terminal.session_mgr.search_sessions(query)
        if not results:
            msg = f"No sessions found matching '{query}'"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        if HAS_RICH:
            console.print()
            console.print(f"  [bold]Recall[/bold]  [dim]{len(results)} session(s) match '{query}'[/dim]")
            console.print()
            for r in results[:10]:
                updated = r["updated"][:16] if r["updated"] else ""
                console.print(
                    f"  [bold]{r['title']}[/bold]  "
                    f"[dim]{r['id'][:8]}  {r['match_count']} hit(s)  {updated}[/dim]"
                )
                preview = r["preview"].replace("\n", " ")[:100]
                console.print(f"    [dim]…{preview}…[/dim]")
                console.print()
            console.print("  [dim]Use /load <id> to resume a session[/dim]")
        else:
            print(f"\n{len(results)} session(s) found:")
            for r in results[:10]:
                print(f"  [{r['id'][:8]}] {r['title']} ({r['match_count']} hits)")
                print(f"    ...{r['preview'][:80]}...")

    async def cmd_export(self, args: str):
        parts = args.split()
        fmt = parts[0].lower() if parts else "json"
        filename = parts[1] if len(parts) > 1 else None

        if not self.terminal.conversation:
            console.print("[dim]Nothing to export[/dim]" if HAS_RICH else "Nothing to export")
            return

        try:
            provider_health = []
            if fmt == "bundle":
                try:
                    from packages.aria_services.provider_health import GLOBAL_PROVIDER_HEALTH
                    provider_health = GLOBAL_PROVIDER_HEALTH.snapshot()
                except Exception:
                    provider_health = []
            content, ext, prefix = build_session_export_payload(
                fmt,
                self.terminal.conversation,
                session_id=self.terminal.session_id,
                config=self.terminal.config,
                trace=getattr(self.terminal, "runtime_trace", None),
                provider_health=provider_health,
            )
        except ValueError as exc:
            if fmt == "sft" and "No user→assistant pairs" in str(exc):
                console.print("[dim]No user→assistant pairs to export[/dim]" if HAS_RICH else "No pairs to export")
                return
            console.print("[dim]Format: json, csv, md, sft, or bundle[/dim]" if HAS_RICH
                          else "Format: json, csv, md, sft, bundle")
            return

        if fmt == "sft":
            pairs = json.loads(content)
            if HAS_RICH:
                console.print(f"[dim]{len(pairs)} training pairs extracted[/dim]")
            else:
                print(f"{len(pairs)} training pairs")

        if not filename:
            from datetime import datetime
            filename = f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        console.print(f"[green]Exported to {filename}[/green]" if HAS_RICH
                      else f"Exported: {filename}")
