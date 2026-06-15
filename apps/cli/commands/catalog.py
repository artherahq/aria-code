"""Shared CLI command metadata.

This module is intentionally UI-free so future channel adapters such as Feishu
or a local gateway can reuse the same command categories without importing the
large terminal implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Tuple


@dataclass(frozen=True)
class DirectCommandSpec:
    name: str
    method_name: str
    async_method: bool = False
    watchable: bool = False
    aliases: Tuple[str, ...] = ()

    @property
    def names(self) -> Tuple[str, ...]:
        return (self.name, *self.aliases)


DIRECT_COMMANDS: Tuple[DirectCommandSpec, ...] = (
    DirectCommandSpec("quote", "cmd_quote", async_method=True, watchable=True),
    DirectCommandSpec("backtest", "cmd_backtest", async_method=True),
    DirectCommandSpec("health", "cmd_health", async_method=True, watchable=True),
    DirectCommandSpec("doctor", "cmd_doctor"),
    DirectCommandSpec("tools", "cmd_tools"),
    DirectCommandSpec("skills", "cmd_skills"),
    DirectCommandSpec("sessions", "cmd_sessions"),
    DirectCommandSpec("watch", "cmd_watch", aliases=("watchlist",)),
    DirectCommandSpec("export", "cmd_export", async_method=True),
)


DIRECT_COMMAND_MAP = {
    alias: spec
    for spec in DIRECT_COMMANDS
    for alias in spec.names
}


WATCHABLE_DIRECT_COMMANDS: FrozenSet[str] = frozenset(
    alias
    for spec in DIRECT_COMMANDS
    if spec.watchable
    for alias in spec.names
)


# Commands shown by default in /help. Other slash commands remain executable but
# are hidden to keep the startup surface compact.
VISIBLE_SLASH_COMMANDS: FrozenSet[str] = frozenset({
    # Conversation management
    "/clear", "/compact", "/undo", "/regen", "/fork", "/load-fork",
    "/copy", "/history", "/save", "/load", "/rename", "/sessions", "/export",
    # Config & setup
    "/model", "/thinking", "/config", "/input", "/context", "/cost", "/local",
    "/setup", "/apikey", "/providers", "/mcp", "/ariarc",
    "/packages",
    # Context loading
    "/file", "/project", "/vision", "/browser", "/screenshot",
    # Info & diagnostics
    "/help", "/status", "/tools", "/skills", "/doctor", "/health", "/artifacts",
    # Persistent-state CRUD
    "/watch", "/alert", "/strategy", "/todo", "/note", "/memory", "/init",
    # Broker / trading
    "/broker", "/account", "/positions", "/orders",
    # Planning & multi-step workflows
    "/plan", "/apply-plan", "/plan-report",
    # Code review & git
    "/git", "/gh", "/review",
    # Auth and privacy
    "/login", "/logout", "/whoami", "/feedback", "/privacy",
})

