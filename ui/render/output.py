"""Generic tool-result and error rendering for Aria Code.

All functions accept console / has_rich as parameters so they stay
import-free from aria_cli.py and testable in isolation.

Public surface
--------------
    FINANCE_TOOL_NAMES          frozenset of tool names with dedicated renderers
    clean_tool_error_message(e) short user-facing string from any exception
    error_hint(msg, context)    actionable recovery suggestion
    print_error(msg, context, *, console, has_rich, rich_box)
    print_tool_result(...)
"""

from __future__ import annotations

import difflib
import json
import re
import time


# ── Finance tool name registry ─────────────────────────────────────────────────

FINANCE_TOOL_NAMES: frozenset = frozenset({
    "get_market_data", "get_crypto_data", "get_forex_data",
    "get_commodities_data", "get_futures_data", "calculate_factors",
    "backtest_strategy", "cloud_backtest", "get_risk_metrics",
    "optimize_positions", "get_sector_performance", "get_northbound_flow",
    "screen_ashare", "get_limit_up_pool", "get_market_indices",
    "analyze_news", "get_bonds_data", "get_ai_signal",
    "get_market_insights", "get_predictions",
    "broker_query", "broker_order",
})


# ── Error helpers ──────────────────────────────────────────────────────────────

def clean_tool_error_message(error: object) -> str:
    raw = str(error or "failed").strip()
    low = raw.lower()
    if not raw:
        return "操作失败"
    if "curl: (28)" in low or "timed out" in low or "timeout" in low:
        return "请求超时，数据源暂时不可用。请稍后重试或运行 /health 检查服务。"
    if "connection refused" in low:
        return "连接被拒绝，服务暂时不可用。请检查本地服务或网络。"
    if "connection aborted" in low or "remotedisconnected" in low:
        return "网络连接中断，数据源未完成响应。请稍后重试。"
    if "rate" in low or "429" in low or "too many requests" in low:
        return "数据源请求频率受限，请稍后重试。"
    # Collapse verbose HTTP error strings: "web_fetch failed: 401 Client Error: Unauthorized for url: https://..."
    import re as _re
    _http = _re.match(r"web_fetch failed:\s*(\d{3})\s+\w[\w\s]+?:\s*([\w\s]+?)(?:\s+for url:.*)?$", raw, _re.I)
    if _http:
        code, phrase = _http.group(1), _http.group(2).strip()
        return f"HTTP {code} {phrase}"
    if "traceback" in low:
        return raw.splitlines()[-1][:160] if raw.splitlines() else "运行失败"
    return raw[:200]


def error_hint(error: str, context: str = "") -> str:
    err_lower = error.lower() if error else ""
    if "connection" in err_lower or "refused" in err_lower or "unreachable" in err_lower:
        return "Hint: Backend unreachable. Try /health or check your network."
    if "timeout" in err_lower or "timed out" in err_lower:
        return "Hint: Request timed out. Try again or check /health."
    if "401" in err_lower or "unauthorized" in err_lower:
        # Distinguish market data API keys from Aria auth
        if any(h in err_lower for h in ("finnhub", "alphavantage", "polygon", "api/v1", "api/v2/finance")):
            return "Hint: API key required — /apikey set finnhub <KEY>  (free at finnhub.io)"
        return "Hint: Authentication required. Run /login to sign in."
    if "403" in err_lower or "forbidden" in err_lower:
        return "Hint: Access denied. Check your API key or subscription."
    if "429" in err_lower or "rate" in err_lower:
        return "Hint: Rate limited. Wait a moment and try again."
    if ("ollama" in err_lower or "ollama http" in err_lower) and (
        "not found" in err_lower or "404" in err_lower
    ):
        m = re.search(r"model ['\"]?([^'\"]+)['\"]? not found", err_lower)
        model_hint = m.group(1) if m else "the requested model"
        try:
            from local_llm_provider import list_ollama_models
            available = list_ollama_models("http://localhost:11434")
            if available:
                suggestion = available[0]
                return (
                    f"Hint: Ollama model '{model_hint}' not found.\n"
                    f"  Available: {', '.join(available[:4])}\n"
                    f"  Run: /config model {suggestion}"
                )
        except Exception:
            pass
        return (
            f"Hint: Ollama model not found. Run `ollama list` to see available models.\n"
            f"  Or pull one: ollama pull qwen2.5-coder:7b"
        )
    # "File not found" is a path error, not a missing tool — keep hint specific
    if "file not found" in err_lower or "no such file" in err_lower:
        return "Hint: File does not exist. Check the path and try again."
    if "404" in err_lower and context == "tool":
        return "Hint: Tool not available. Check /tools for available tools."
    if "not found" in err_lower and context == "session":
        return "Hint: Session not found. Run /sessions to list available."
    if "404" in err_lower or ("not found" in err_lower and context not in ("tool", "")):
        return "Hint: Resource not found. Check the symbol or path."
    if "no data" in err_lower or "no result" in err_lower:
        return "Hint: No data returned. Verify the symbol spelling."
    if "500" in err_lower or "internal" in err_lower:
        return "Hint: Server error. Try again in a moment or /health to check."
    if context == "login":
        return "Hint: Check email/password. Usage: /login email password"
    return ""


# ── Error panel ────────────────────────────────────────────────────────────────

def print_error(
    msg: str,
    context: str = "",
    *,
    console,
    has_rich: bool,
    rich_box,
) -> None:
    if has_rich:
        from rich.panel import Panel
        hint = error_hint(msg, context)
        body = f"[red]{msg}[/red]"
        if hint:
            body += f"\n[dim]{hint}[/dim]"
        console.print(Panel(body, border_style="red", box=rich_box.ROUNDED, padding=(0, 1)))
    else:
        print(msg)


# ── Tool result ────────────────────────────────────────────────────────────────

def print_tool_result(
    tool_name: str,
    result: dict,
    elapsed: float = 0,
    params: dict = None,
    *,
    console,
    has_rich: bool,
    rich_box,
    print_finance_fn,   # callable(tool_name, result) for finance tools
    bot_mode: bool = False,
) -> None:
    """Render a tool result summary — Codex-style ⎿ tree connector."""
    if bot_mode:
        return

    ts       = f"  [dim]{elapsed:.1f}s[/dim]" if elapsed >= 0.1 else ""
    ts_plain = f"  {elapsed:.1f}s" if elapsed >= 0.1 else ""
    params   = params or {}

    if tool_name in FINANCE_TOOL_NAMES:
        print_finance_fn(tool_name, result)
        if ts and has_rich:
            console.print(f"  [dim]⎿[/dim]{ts}")
        return

    if result.get("success"):
        data = result.get("data", {})

        if tool_name == "write_file":
            path      = params.get("path", data.get("path", ""))
            lines     = data.get("lines") or (params.get("content", "").count("\n") + 1 if params.get("content") else 0)
            size      = data.get("size_bytes") or len((params.get("content", "") or "").encode())
            size_str  = f"{size}B" if size < 1024 else f"{size // 1024}KB"
            if has_rich:
                console.print(f"  [dim]⎿[/dim]  [green]✓[/green]  [dim]{path}  {lines} lines  {size_str}[/dim]{ts}")
            else:
                print(f"  ⎿  ✓ {path}  {lines} lines  {size_str}{ts_plain}")

        elif tool_name == "edit_file":
            old = params.get("old_string", "")
            new = params.get("new_string", "")
            path = params.get("path", "")
            if old and new and has_rich:
                import re as _re_diff
                diff = list(difflib.unified_diff(
                    old.splitlines(),
                    new.splitlines(),
                    lineterm="",
                ))
                if diff:
                    _hdr = f"  [#C08050]{path}[/#C08050]" if path else "  [dim]⎿[/dim]"
                    console.print(f"{_hdr}{ts}")
                    o_ln = n_ln = 0
                    for line in diff[2:]:
                        # Hunk header: @@ -old_start,n +new_start,n @@
                        m = _re_diff.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                        if m:
                            o_ln, n_ln = int(m.group(1)), int(m.group(2))
                            console.print(f"    [dim]…[/dim]")
                            continue
                        body = line[1:].rstrip()
                        if line.startswith("+"):
                            console.print(f"    [dim]{n_ln:>4}[/dim] [green]+ {body}[/green]")
                            n_ln += 1
                        elif line.startswith("-"):
                            console.print(f"    [dim]{o_ln:>4}[/dim] [red]- {body}[/red]")
                            o_ln += 1
                        else:
                            console.print(f"    [dim]{n_ln:>4}[/dim] [dim]  {body}[/dim]")
                            o_ln += 1
                            n_ln += 1
                else:
                    console.print(f"  [dim]⎿  no change[/dim]{ts}")
            elif has_rich:
                console.print(f"  [dim]⎿  edited[/dim]{ts}")
            else:
                print(f"  ⎿  edited{ts_plain}")

        elif tool_name == "run_command":
            stdout     = data.get("stdout", "").strip()
            returncode = data.get("returncode", 0)
            if has_rich:
                from rich.panel import Panel
                rc_color = "green" if returncode == 0 else "red"
                rc_icon  = "✓" if returncode == 0 else "✗"
                console.print(f"  [dim]⎿[/dim]  [{rc_color}]{rc_icon} exit {returncode}[/{rc_color}]{ts}")
                if stdout:
                    out_lines = stdout.splitlines()
                    if len(out_lines) > 3:
                        truncated = "\n".join(out_lines[:40])
                        if len(out_lines) > 40:
                            truncated += f"\n[dim]… +{len(out_lines) - 40} lines[/dim]"
                        console.print(Panel(
                            f"[dim]{truncated}[/dim]",
                            border_style="dim",
                            box=rich_box.SIMPLE,
                            padding=(0, 1),
                        ))
                    else:
                        for ol in out_lines:
                            console.print(f"    [dim]{ol[:120]}[/dim]")
            else:
                print(f"  ⎿  exit {returncode}{ts_plain}")
                for ol in stdout.splitlines()[:4]:
                    print(f"    {ol[:100]}")

        elif tool_name == "read_file":
            lines = data.get("lines", 0)
            path  = params.get("path", "")
            if has_rich:
                console.print(f"  [dim]⎿  {path}  {lines} lines[/dim]{ts}")
            else:
                print(f"  ⎿  {path}  {lines} lines{ts_plain}")

        elif tool_name == "list_files":
            count = data.get("count", 0)
            if has_rich:
                color = "yellow" if count == 0 else "dim"
                msg   = "0 items — no matches" if count == 0 else f"{count} items"
                console.print(f"  [{color}]⎿  {msg}[/{color}]{ts}")
            else:
                print(f"  ⎿  {count} items{ts_plain}")

        elif tool_name == "search_code":
            matches = len(data.get("matches", []))
            if has_rich:
                console.print(f"  [dim]⎿  {matches} matches[/dim]{ts}")
            else:
                print(f"  ⎿  {matches} matches{ts_plain}")

        elif tool_name == "web_fetch":
            url    = data.get("url", params.get("url", ""))
            length = data.get("length", 0)
            trunc  = data.get("truncated", False)
            short_url = url.replace("https://", "").replace("http://", "")[:70]
            len_str = f"  {length:,} chars" if length else ""
            trunc_str = "  [dim]truncated[/dim]" if trunc else ""
            if has_rich:
                console.print(f"  [dim]⎿  {short_url}{len_str}[/dim]{trunc_str}{ts}")
            else:
                print(f"  ⎿  {short_url}{ts_plain}")

        elif tool_name in ("web_search", "search_web"):
            results = data.get("results", [])
            count   = len(results)
            if has_rich:
                console.print(f"  [dim]⎿  {count} results[/dim]{ts}")
            else:
                print(f"  ⎿  {count} results{ts_plain}")

        else:
            summary = json.dumps(data, ensure_ascii=False)
            short   = (summary[:100] + "…") if len(summary) > 100 else summary
            if has_rich:
                console.print(f"  [dim]⎿  {short}[/dim]{ts}")
            else:
                print(f"  ⎿  done{ts_plain}")

    else:
        error = clean_tool_error_message(result.get("error", "failed"))
        hint  = error_hint(str(error), context="tool")
        if has_rich:
            console.print(f"  [dim]⎿[/dim]  [red]✗ {error[:120]}[/red]")
            if hint:
                console.print(f"    [dim]{hint}[/dim]")
        else:
            print(f"  ⎿  ✗ {error[:80]}")


# ── Activity group (OpenClaw-style batch summary) ──────────────────────────────

def _one_line_tool_summary(
    tool_name: str,
    result: dict,
    elapsed: float,
    params: dict,
) -> tuple[str, str]:
    """Return (status_markup, detail_markup) for one tool in an activity table."""
    params = params or {}
    ts = f"[dim]  {elapsed:.1f}s[/dim]" if elapsed >= 0.1 else ""

    if not result.get("success"):
        error = clean_tool_error_message(result.get("error", "failed"))
        return "[red]✗[/red]", f"[red]{error[:80]}[/red]{ts}"

    data = result.get("data", {})

    if tool_name == "write_file":
        path  = params.get("path", data.get("path", ""))
        lines = data.get("lines") or (params.get("content", "").count("\n") + 1 if params.get("content") else 0)
        size  = data.get("size_bytes") or len((params.get("content", "") or "").encode())
        size_str = f"{size}B" if size < 1024 else f"{size // 1024}KB"
        return "[green]✓[/green]", f"[dim]{path}  {lines} lines  {size_str}[/dim]{ts}"

    elif tool_name == "edit_file":
        path = params.get("path", data.get("path", ""))
        return "[green]✓[/green]", f"[dim]edited  {path}[/dim]{ts}"

    elif tool_name == "run_command":
        rc = data.get("returncode", 0)
        icon  = "[green]✓[/green]" if rc == 0 else "[red]✗[/red]"
        color = "green" if rc == 0 else "red"
        return icon, f"[{color}]exit {rc}[/{color}]{ts}"

    elif tool_name == "read_file":
        lines = data.get("lines", 0)
        path  = params.get("path", "")
        return "[green]✓[/green]", f"[dim]{path}  {lines} lines[/dim]{ts}"

    elif tool_name == "list_files":
        count = data.get("count", 0)
        color = "yellow" if count == 0 else "dim"
        msg   = "no matches" if count == 0 else f"{count} items"
        return "[green]✓[/green]", f"[{color}]{msg}[/{color}]{ts}"

    elif tool_name == "search_code":
        matches = len(data.get("matches", []))
        return "[green]✓[/green]", f"[dim]{matches} matches[/dim]{ts}"

    elif tool_name == "web_fetch":
        url    = data.get("url", params.get("url", ""))
        length = data.get("length", 0)
        short  = url.replace("https://", "").replace("http://", "")[:55]
        len_s  = f"  {length:,}c" if length else ""
        return "[green]✓[/green]", f"[dim]{short}{len_s}[/dim]{ts}"

    elif tool_name in ("web_search", "search_web"):
        count = len(data.get("results", []))
        return "[green]✓[/green]", f"[dim]{count} results[/dim]{ts}"

    else:
        return "[green]✓[/green]", f"[dim]done[/dim]{ts}"


def print_tool_activity_group(
    results: list,       # list of (tool_name, result, elapsed, params)
    *,
    console,
    has_rich: bool,
    rich_box,
    print_finance_fn,
    bot_mode: bool = False,
) -> None:
    """Render multiple tool results as a compact Activity block (OpenClaw style).

    For N >= 2 tools: prints a titled table.
    For N == 1: delegates to print_tool_result (single-line).
    """
    if bot_mode or not results:
        return

    if len(results) == 1:
        tool_name, result, elapsed, params = results[0]
        print_tool_result(tool_name, result, elapsed, params,
                          console=console, has_rich=has_rich, rich_box=rich_box,
                          print_finance_fn=print_finance_fn, bot_mode=bot_mode)
        return

    total_elapsed = sum(e for _, _, e, _ in results)
    n = len(results)

    # Finance tools: print with dedicated renderer, then add to activity table
    finance_rows = []
    for tool_name, result, elapsed, params in results:
        if tool_name in FINANCE_TOOL_NAMES:
            print_finance_fn(tool_name, result)
            finance_rows.append(tool_name)

    if has_rich:
        from rich.table import Table
        ts_total = f"  [dim]{total_elapsed:.1f}s[/dim]" if total_elapsed >= 0.1 else ""
        header = f"[dim]Activity · {n} tools[/dim]{ts_total}"
        console.print(f"\n  {header}")
        tbl = Table.grid(padding=(0, 2))
        tbl.add_column(no_wrap=True, min_width=14, style="dim")   # tool name
        tbl.add_column(no_wrap=True, min_width=2)                  # status icon
        tbl.add_column()                                            # detail

        for tool_name, result, elapsed, params in results:
            if tool_name in finance_rows:
                icon = "[green]✓[/green]" if result.get("success") else "[red]✗[/red]"
                tbl.add_row(tool_name, icon, "")
            else:
                icon, detail = _one_line_tool_summary(tool_name, result, elapsed, params)
                tbl.add_row(f"[dim]{tool_name}[/dim]", icon, detail)

        from rich.padding import Padding
        console.print(Padding(tbl, (0, 0, 0, 4)))

        # For run_command with stdout, still print the output panel
        for tool_name, result, elapsed, params in results:
            if tool_name == "run_command" and result.get("success"):
                stdout = result.get("data", {}).get("stdout", "").strip()
                if stdout:
                    from rich.panel import Panel
                    out_lines = stdout.splitlines()
                    if len(out_lines) > 3:
                        truncated = "\n".join(out_lines[:40])
                        if len(out_lines) > 40:
                            truncated += f"\n[dim]… +{len(out_lines) - 40} lines[/dim]"
                        console.print(Panel(f"[dim]{truncated}[/dim]",
                                            border_style="dim", box=rich_box.SIMPLE,
                                            padding=(0, 1)))
                    else:
                        for ol in out_lines:
                            console.print(f"      [dim]{ol[:120]}[/dim]")

            # For edit_file, still print diff
            elif tool_name == "edit_file" and result.get("success"):
                old = (params or {}).get("old_string", "")
                new = (params or {}).get("new_string", "")
                if old and new:
                    diff = list(difflib.unified_diff(
                        old.splitlines(keepends=True),
                        new.splitlines(keepends=True),
                        lineterm="",
                    ))
                    for line in diff[2:]:
                        if line.startswith("+"):
                            console.print(f"      [green]{line.rstrip()}[/green]")
                        elif line.startswith("-"):
                            console.print(f"      [red]{line.rstrip()}[/red]")
    else:
        ts_total = f"  {total_elapsed:.1f}s" if total_elapsed >= 0.1 else ""
        print(f"\n  Activity · {n} tools{ts_total}")
        for tool_name, result, elapsed, params in results:
            icon, detail = _one_line_tool_summary(tool_name, result, elapsed, params)
            detail_plain = re.sub(r"\[/?[^\]]+\]", "", detail)
            icon_plain   = "✓" if result.get("success") else "✗"
            print(f"    {tool_name:<18}{icon_plain}  {detail_plain}")


# ── Fallback / model-switch toast ──────────────────────────────────────────────

def print_fallback_toast(
    from_provider: str,
    to_provider: str,
    reason: str = "",
    *,
    console,
    has_rich: bool,
) -> None:
    """Show a transient yellow notification when the active model/provider switches."""
    if not has_rich:
        print(f"\n  ⚡ 模型切换  {from_provider} → {to_provider}{('  ' + reason) if reason else ''}")
        return
    body = f"[bold #C08050]⚡[/bold #C08050]  [#C08050]{from_provider}[/#C08050] [dim]→[/dim] [#C08050]{to_provider}[/#C08050]"
    if reason:
        body += f"\n  [dim]{reason}[/dim]"
    console.print(f"\n  {body}")


# ── Context pressure warning ───────────────────────────────────────────────────

_CTX_WARNED: dict[str, float] = {}   # session_id → last warn time

def print_context_warning(
    est_tokens: int,
    max_tokens: int,
    *,
    console,
    has_rich: bool,
    session_id: str = "",
    cooldown: float = 120.0,         # only warn once every 2 min per session
) -> None:
    """Warn when context is >85% full; rate-limited to avoid spam."""
    if max_tokens <= 0:
        return
    ratio = est_tokens / max_tokens
    if ratio < 0.85:
        return
    now = time.monotonic()
    if now - _CTX_WARNED.get(session_id, 0) < cooldown:
        return
    _CTX_WARNED[session_id] = now

    def _k(n: int) -> str:
        return f"{n // 1000}K" if n >= 1000 else str(n)

    pct = int(ratio * 100)
    if has_rich:
        color  = "red" if ratio >= 0.95 else "#C08050"
        icon   = "●" if ratio >= 0.95 else "⚠"
        msg    = f"  [{color}]{icon} 上下文 {pct}% 已满  ({_k(est_tokens)}/{_k(max_tokens)} tokens)[/{color}]"
        msg   += "  [dim]→ /compact 压缩历史  /clear 重置[/dim]"
        console.print(msg)
    else:
        print(f"  ⚠ 上下文 {pct}% ({_k(est_tokens)}/{_k(max_tokens)} tokens) — /compact 或 /clear")


# ── Blocked / cancelled tool visual ───────────────────────────────────────────

def print_tool_blocked(
    tool_name: str,
    reason: str = "用户取消",
    *,
    console,
    has_rich: bool,
) -> None:
    """Show a styled 'Blocked' line when tool execution is denied or cancelled."""
    if has_rich:
        console.print(
            f"  [dim]⎿[/dim]  [#C08050]⊘  {tool_name}[/#C08050]  [dim]{reason}[/dim]"
        )
    else:
        print(f"  ⎿  ⊘ {tool_name}  {reason}")


# ── Robot thinking / response header ──────────────────────────────────────────

def print_thinking_header(*, console, has_rich: bool) -> None:
    """Print a subtle copper 'Aria ▸' header before each AI response stream.

    Gives the response a clear starting-point rather than appearing inline.
    Called once per turn, right before the first streaming token is printed.
    """
    if not has_rich:
        return
    console.print("[bold #C08050]▣[/bold #C08050]  [dim #C08050]Aria[/dim #C08050]", end="  ")


def print_done_footer(elapsed: float, *, console, has_rich: bool) -> None:
    """Print a dim elapsed-time line after the response stream ends."""
    if not has_rich:
        return
    console.print(f"\n[dim]  ✓  {elapsed:.1f}s[/dim]")
