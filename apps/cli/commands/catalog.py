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
    DirectCommandSpec("tv", "cmd_tv", async_method=True),
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
    # Session
    "/help", "/clear", "/compact", "/cost", "/status", "/health",
    "/regen", "/undo", "/copy", "/recap", "/btw",
    # Sessions
    "/save", "/load", "/sessions", "/export",
    # Config
    "/model", "/thinking", "/config", "/privacy",
    # Setup & discovery
    "/setup", "/apikey", "/doctor", "/mcp", "/skills", "/tools", "/packages",
    # Auth
    "/login", "/logout", "/whoami",
    # Persistent data (direct writes)
    "/alert", "/journal", "/watch", "/note", "/todo", "/memory",
    # Broker
    "/broker", "/account", "/positions", "/orders",
    # Code & project
    "/project", "/init", "/review", "/code", "/plan", "/run",
    # Quant
    "/backtest", "/wf", "/tv",
    # UI generation
    "/ui",
    # Other
    "/artifacts", "/vision", "/upload-image", "/file", "/strategy", "/accuracy",
})
