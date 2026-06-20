"""Message processing utilities extracted from aria_cli.py.

Functions here handle: parsing tool calls from text, stripping tool tags
from display output, and compacting conversation history.

Dependencies on aria_cli globals (LOCAL_TOOLS, get_model_cfg) are resolved
via lazy runtime imports to avoid circular import at load time.
"""
from __future__ import annotations

import json
import re
from typing import Optional


def _local_tools() -> dict:
    import aria_cli
    return aria_cli.LOCAL_TOOLS


def _fix_json(raw: str) -> str:
    import aria_cli
    return aria_cli._fix_json_string(raw)


def _get_model_cfg(key: str) -> dict:
    import aria_cli
    return aria_cli.get_model_cfg(key)


# ── Tool call parsing ─────────────────────────────────────────────────────────

def parse_text_tool_calls(text: str) -> list:
    """Parse tool calls from AI response text.

    Supports formats:
    1. <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    2. ```json\\n{"name": "...", "arguments": {...}}\\n```
    3. Bare JSON: {"name": "...", "arguments": {...}}
    """
    local_tools = _local_tools()
    calls: list = []

    def _try_parse(raw: str) -> Optional[dict]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_fix_json(raw))
        except json.JSONDecodeError:
            pass
        return None

    # Format 1: <tool_call>...</tool_call> tags
    tag_pattern = re.compile(r'<tool_call>\s*([\s\S]*?)\s*</tool_call>', re.DOTALL)
    for m in tag_pattern.finditer(text):
        obj = _try_parse(m.group(1))
        if obj:
            name = obj.get("name", "")
            args = obj.get("arguments", {})
            if name and name in local_tools:
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = json.loads(_fix_json(args))
                calls.append({"tool": name, "params": args})

    if calls:
        return calls

    # Format 2: code-fenced JSON
    fence_pattern = re.compile(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```', re.DOTALL)
    for m in fence_pattern.finditer(text):
        obj = _try_parse(m.group(1))
        if obj:
            name = obj.get("name", "")
            args = obj.get("arguments", {})
            if name and name in local_tools:
                if isinstance(args, str):
                    args = _try_parse(args) or {}
                calls.append({"tool": name, "params": args})

    if calls:
        return calls

    # Format 3: bare JSON with balanced brace scan
    brace_depth = 0
    json_start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if brace_depth == 0:
                json_start = i
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 0 and json_start >= 0:
                candidate = text[json_start:i + 1]
                obj = _try_parse(candidate)
                if obj:
                    name = obj.get("name", "")
                    args = obj.get("arguments", {})
                    if name and name in local_tools:
                        if isinstance(args, str):
                            args = _try_parse(args) or {}
                        calls.append({"tool": name, "params": args})
                json_start = -1

    return calls


def strip_tool_call_tags(text: str) -> str:
    """Remove tool calls from display text (tags, fences, bare JSON, headers)."""
    local_tools = _local_tools()

    text = re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', text, flags=re.DOTALL)

    def _remove_fence(m: re.Match) -> str:
        try:
            obj = json.loads(m.group(1))
            if obj.get("name") in local_tools and "arguments" in obj:
                return ''
        except (json.JSONDecodeError, TypeError):
            pass
        return m.group(0)

    text = re.sub(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```', _remove_fence, text, flags=re.DOTALL)

    def _remove_bare(m: re.Match) -> str:
        try:
            obj = json.loads(m.group(0))
            if obj.get("name") in local_tools and "arguments" in obj:
                return ''
        except (json.JSONDecodeError, TypeError):
            pass
        return m.group(0)

    text = re.sub(
        r'\{[^{}]*"name"\s*:\s*"[^"]*"[^{}]*"arguments"\s*:\s*\{[\s\S]*?\}\s*\}',
        _remove_bare, text,
    )
    text = re.sub(r'###\s+Step\s+\d+.*\n?', '', text)
    text = re.sub(r'###\s+.*工具调用.*\n?', '', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ── History compaction ────────────────────────────────────────────────────────

def estimate_message_tokens(messages: list, extra_content: str = "") -> int:
    """Return a rough token estimate matching the terminal context meter."""
    from packages.aria_services.context import ContextService

    return ContextService.estimate_message_tokens(messages, extra_content=extra_content)


def context_compaction_decision(
    messages: list,
    *,
    model_key: str = "qwen7b",
    extra_content: str = "",
    threshold: float = 0.78,
    min_messages: int = 8,
) -> dict:
    """Decide whether a conversation should be compacted before the next turn."""
    try:
        threshold = float(threshold)
    except Exception:
        threshold = 0.78
    max_ctx = int(_get_model_cfg(model_key).get("num_ctx", 16384) or 16384)
    from packages.aria_services.context import build_context_service

    service = build_context_service(
        max_tokens=max_ctx,
        threshold=threshold,
        min_messages=min_messages,
    )
    return service.compaction_decision(messages, extra_content=extra_content).to_dict()


def compact_messages(
    messages: list,
    max_chars: int = 0,
    model_key: str = "qwen7b",
) -> list:
    """Smart synchronous compaction for the agentic tool loop.

    Strategy (in order of priority):
    1. Always keep: system prompt + last 8 messages (recent context).
    2. For middle tool results: extract status line + error details (if any),
       drop verbose success payloads.
    3. For middle assistant turns: keep first paragraph + last sentence.
    4. Error markers are never discarded.
    """
    ctx = int(_get_model_cfg(model_key).get("num_ctx", 16384) or 16384)
    from packages.aria_services.context import build_context_service

    service = build_context_service(max_tokens=ctx)
    return service.compact_messages(messages, max_chars=max_chars)


# ── Broker context injection ──────────────────────────────────────────────────

def build_broker_context_block() -> str:
    """Return a compact broker context block for injection into the system prompt.

    Returns "" if no broker is connected or data fetch fails.
    """
    import aria_cli as _ac
    if not getattr(_ac, "_HAS_BROKERS", False):
        return ""
    try:
        reg = _ac._get_broker_registry()
        if not reg:
            return ""
        broker = reg.active()
        if not broker or not broker.is_connected:
            return ""

        parts = [f"## 券商账户实时快照 [{broker.label}]"]

        try:
            acct = broker.account_info()
            parts.append(
                f"- 账户: {acct.masked_account}  货币: {acct.currency}\n"
                f"- 总资产: {acct.total_assets:,.2f}  可用现金: {acct.cash:,.2f}"
                f"  持仓市值: {acct.market_value:,.2f}"
            )
            if acct.pnl_today:
                parts.append(f"- 当日盈亏: {acct.pnl_today:+,.2f}")
        except Exception:
            pass

        try:
            positions = broker.positions()
            if positions:
                positions_sorted = sorted(positions, key=lambda p: -abs(p.market_value))[:10]
                parts.append("\n持仓明细（市值降序，最多10条）：")
                for p in positions_sorted:
                    pnl_str = f"  盈亏 {p.pnl:+,.2f} ({p.pnl_pct:+.2f}%)" if p.pnl else ""
                    parts.append(
                        f"  {p.symbol} {p.name[:8] if p.name else ''}  "
                        f"持仓 {p.quantity:.0f}  成本 {p.cost_price:.3f}  "
                        f"现价 {p.current_price:.3f}  市值 {p.market_value:,.2f}{pnl_str}"
                    )
        except Exception:
            pass

        if len(parts) <= 1:
            return ""

        parts.append("\n(以上为实时账户数据，无需再调用 broker_query 获取基本账户/持仓信息，可直接引用。)")
        return "\n".join(parts)

    except Exception:
        return ""
