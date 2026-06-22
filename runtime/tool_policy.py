"""Persistent per-tool execution policy: allowlist, denylist, ask-always.

Stores policy in ~/.arthera/tool_policy.json.
Checked in _confirm_tool_execution_decision() before any user prompt.

Usage:
    from runtime.tool_policy import check_tool_policy, add_to_policy

    verdict = check_tool_policy("write_file")  # "allow" | "deny" | "ask" | "default"
    add_to_policy("read_file", "allow")         # permanently auto-approve
    add_to_policy("run_command", "deny")        # permanently block
    add_to_policy("edit_file", "ask")           # always prompt
    remove_from_policy("read_file")             # remove from all lists
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

PolicyVerdict = Literal["allow", "deny", "ask", "default"]

_DEFAULT_POLICY: dict = {
    "allowed": [],     # auto-approve without prompt
    "denied": [],      # always block, never execute
    "ask_always": [],  # always prompt even for non-CONFIRM_TOOLS
}


def _policy_file() -> Path:
    return Path.home() / ".arthera" / "tool_policy.json"


def load_tool_policy() -> dict:
    """Load policy from disk; returns defaults if missing or corrupt."""
    f = _policy_file()
    if f.exists():
        try:
            raw = json.loads(f.read_text(encoding="utf-8"))
            return {
                "allowed":    list(raw.get("allowed", [])),
                "denied":     list(raw.get("denied", [])),
                "ask_always": list(raw.get("ask_always", [])),
            }
        except Exception:
            pass
    return {k: list(v) for k, v in _DEFAULT_POLICY.items()}


def save_tool_policy(policy: dict) -> None:
    f = _policy_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(policy, indent=2, ensure_ascii=False), encoding="utf-8")


def check_tool_policy(tool_name: str) -> PolicyVerdict:
    """Return the persistent verdict for *tool_name*.

    "allow"   → auto-approve, skip confirmation prompt
    "deny"    → block, never execute
    "ask"     → always prompt (overrides session auto-allow)
    "default" → no override, fall through to normal flow
    """
    policy = load_tool_policy()
    if tool_name in policy.get("denied", []):
        return "deny"
    if tool_name in policy.get("allowed", []):
        return "allow"
    if tool_name in policy.get("ask_always", []):
        return "ask"
    return "default"


def add_to_policy(tool_name: str, verdict: Literal["allow", "deny", "ask"]) -> None:
    """Add *tool_name* to the given policy list, removing it from any other list first."""
    policy = load_tool_policy()
    for key in ("allowed", "denied", "ask_always"):
        policy.setdefault(key, [])
        if tool_name in policy[key]:
            policy[key].remove(tool_name)
    if verdict == "allow":
        policy["allowed"].append(tool_name)
    elif verdict == "deny":
        policy["denied"].append(tool_name)
    elif verdict == "ask":
        policy["ask_always"].append(tool_name)
    save_tool_policy(policy)


def remove_from_policy(tool_name: str) -> bool:
    """Remove *tool_name* from all lists. Returns True if it was present."""
    policy = load_tool_policy()
    changed = False
    for key in ("allowed", "denied", "ask_always"):
        if tool_name in policy.get(key, []):
            policy[key].remove(tool_name)
            changed = True
    if changed:
        save_tool_policy(policy)
    return changed


def policy_summary() -> dict:
    """Return current policy as a human-readable dict."""
    return load_tool_policy()
