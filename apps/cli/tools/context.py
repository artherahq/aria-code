"""ToolContext — dependency bundle for tools that need write/display state."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console


@dataclass
class ToolContext:
    """Passed to write-capable tools so they don't need aria_cli.py globals.

    Instantiate once in ``ArtheraTerminal.__init__`` and share via reference:

        self._tool_ctx = ToolContext(
            console=console,
            has_rich=HAS_RICH,
            write_policy=_ACTIVE_WRITE_POLICY,
            change_store=GLOBAL_CHANGE_STORE,
            config_dir=CONFIG_DIR,
            sessions_dir=SESSIONS_DIR,
        )
    """
    console: "Console | None" = None
    has_rich: bool = True
    write_policy: list[str] = field(default_factory=lambda: ["desktop_only"])
    change_store: Any = None   # GLOBAL_CHANGE_STORE
    config_dir: Path = field(default_factory=lambda: Path.home() / ".arthera")
    sessions_dir: Path = field(default_factory=lambda: Path.home() / ".arthera" / "sessions")

    # ── helpers ──────────────────────────────────────────────────────
    def print(self, *args, **kwargs) -> None:
        if self.has_rich and self.console is not None:
            self.console.print(*args, **kwargs)
        else:
            import builtins
            builtins.print(*args)

    @property
    def policy(self) -> str:
        return self.write_policy[0] if self.write_policy else "desktop_only"
