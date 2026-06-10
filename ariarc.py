"""
ariarc.py — Project-level configuration loader for Aria Code.

Searches for ``.ariarc`` or ``.ariarc.json`` in the current directory and all
parent directories (walks up to filesystem root, stops at $HOME).

.ariarc format (JSON or JSONC)::

    {
      // Project identity
      "project":      "Arthera Quant Engine",
      "description":  "Quantitative trading system for A-share and US markets",

      // Extra system prompt injected before every conversation
      "system_prompt": "You are helping with an A-share quant strategy codebase...",

      // Files whose contents are prepended as context
      "context_files": ["README.md", "docs/architecture.md"],

      // Tool allow/deny lists (applied on top of global policy)
      "tools_whitelist": ["read_file", "search_code", "calculate_factors"],
      "tools_blacklist": ["run_command"],

      // Default symbols for watchlist / quick commands
      "default_symbols": ["sh600519", "sh601318", "sz000858"],
      "market":          "cn",        // cn | us | global

      // A-share specific settings
      "ashare": {
        "broker":       "东方财富",
        "account_type": "普通账户",
        "risk_level":   "moderate"    // conservative | moderate | aggressive
      },

      // Slash commands defined in-project
      "commands": {
        "/morning-cn": "生成A股早盘简报，重点关注 {default_symbols}",
        "/factor-check": "计算 {symbol} 的技术因子并分析当前趋势"
      },

      // Files to auto-read at session start (feeds AI context)
      "auto_context": [
        "packages/quant_engine/strategies/quant_strategy_base.py",
        "packages/quant_engine/analysis/signal_pipeline.py"
      ],

      // Disable AI from proposing certain file patterns (safety)
      "write_deny_patterns": ["*.env", "config/secrets.*", "**/credentials*"]
    }
"""

from __future__ import annotations

import json
import os
import pathlib
import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# JSONC parser (JSON with // and /* */ comments)
# ---------------------------------------------------------------------------

def _strip_comments(text: str) -> str:
    """Remove // line comments and /* */ block comments from JSON text."""
    # Block comments
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # Line comments (not inside strings — good-enough heuristic)
    text = re.sub(r'(?<!:)(?<!https)//[^\n]*', "", text)
    return text


def _parse_jsonc(text: str) -> Any:
    return json.loads(_strip_comments(text))


# ---------------------------------------------------------------------------
# Default / empty ariarc
# ---------------------------------------------------------------------------

ARIARC_DEFAULTS: Dict[str, Any] = {
    "project":           None,
    "description":       None,
    "system_prompt":     "",
    "context_files":     [],
    "tools_whitelist":   [],
    "tools_blacklist":   [],
    "default_symbols":   [],
    "market":            "global",
    "ashare":            {},
    "commands":          {},
    "auto_context":      [],
    "write_deny_patterns": ["*.env", "**/.env*", "**/secrets.*", "**/credentials*"],
}


# ---------------------------------------------------------------------------
# Finder
# ---------------------------------------------------------------------------

def find_ariarc(start_dir: Optional[str] = None) -> Optional[pathlib.Path]:
    """
    Walk up from *start_dir* (default: cwd) looking for .ariarc or .ariarc.json.
    Stops at $HOME or filesystem root.
    """
    home   = pathlib.Path.home()
    cwd    = pathlib.Path(start_dir or os.getcwd()).resolve()
    names  = [".ariarc", ".ariarc.json", ".ariarc.jsonc"]

    current = cwd
    while True:
        for name in names:
            candidate = current / name
            if candidate.exists() and candidate.is_file():
                return candidate
        if current == home or current.parent == current:
            break
        current = current.parent
    return None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class AriaRC:
    """
    Parsed project configuration from .ariarc.

    Usage::

        rc = AriaRC.load()          # searches cwd upward
        rc = AriaRC.load("/path")   # explicit start dir

        rc.project                  # "Arthera Quant Engine"
        rc.system_prompt            # extra text injected into system prompt
        rc.get_context_text()       # concatenated content of context_files
        rc.is_tool_allowed("run_command")
    """

    def __init__(self, data: Dict[str, Any], source_path: Optional[pathlib.Path] = None):
        cfg = {**ARIARC_DEFAULTS, **data}
        self.source_path:    Optional[pathlib.Path] = source_path
        self.project:        Optional[str]  = cfg.get("project")
        self.description:    Optional[str]  = cfg.get("description")
        self.system_prompt:  str            = cfg.get("system_prompt", "")
        self.context_files:  List[str]      = list(cfg.get("context_files", []))
        self.tools_whitelist: List[str]     = list(cfg.get("tools_whitelist", []))
        self.tools_blacklist: List[str]     = list(cfg.get("tools_blacklist", []))
        self.default_symbols: List[str]     = list(cfg.get("default_symbols", []))
        self.market:         str            = cfg.get("market", "global")
        self.ashare:         Dict[str, Any] = cfg.get("ashare", {})
        self.commands:       Dict[str, str] = cfg.get("commands", {})
        self.auto_context:   List[str]      = list(cfg.get("auto_context", []))
        self.write_deny_patterns: List[str] = list(cfg.get("write_deny_patterns", []))

    # ── class methods ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, start_dir: Optional[str] = None) -> "AriaRC":
        path = find_ariarc(start_dir)
        if path is None:
            return cls({})
        try:
            text = path.read_text(encoding="utf-8")
            data = _parse_jsonc(text)
            if not isinstance(data, dict):
                data = {}
            return cls(data, source_path=path)
        except Exception:
            return cls({}, source_path=path)

    @classmethod
    def empty(cls) -> "AriaRC":
        return cls({})

    # ── helpers ────────────────────────────────────────────────────────────

    @property
    def found(self) -> bool:
        return self.source_path is not None

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Return True if tool is allowed under whitelist/blacklist rules."""
        if self.tools_blacklist and tool_name in self.tools_blacklist:
            return False
        if self.tools_whitelist:
            return tool_name in self.tools_whitelist
        return True

    def get_context_text(self, base_dir: Optional[str] = None) -> str:
        """
        Read all context_files and return their concatenated content.
        Paths are relative to the .ariarc location (or cwd if not found).
        """
        base = pathlib.Path(base_dir or (self.source_path.parent if self.source_path else os.getcwd()))
        parts: List[str] = []
        for rel_path in self.context_files:
            p = base / rel_path
            if p.exists() and p.is_file():
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    parts.append(f"## {rel_path}\n\n```\n{content[:4000]}\n```")
                except Exception:
                    pass
        return "\n\n".join(parts)

    def get_auto_context_text(self, base_dir: Optional[str] = None) -> str:
        """Same as get_context_text but for auto_context files."""
        base = pathlib.Path(base_dir or (self.source_path.parent if self.source_path else os.getcwd()))
        parts: List[str] = []
        for rel_path in self.auto_context:
            p = base / rel_path
            if p.exists() and p.is_file():
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    # Show only first 80 lines to avoid blowing up context
                    lines    = content.splitlines()[:80]
                    snippet  = "\n".join(lines)
                    parts.append(f"## {rel_path} (first {len(lines)} lines)\n\n```python\n{snippet}\n```")
                except Exception:
                    pass
        return "\n\n".join(parts)

    def build_system_prompt_block(self, base_dir: Optional[str] = None) -> str:
        """
        Build the full system-prompt injection block from this ariarc.
        Returns empty string if nothing to inject.
        """
        lines: List[str] = []

        if self.project:
            lines.append(f"**Project:** {self.project}")
        if self.description:
            lines.append(f"**Description:** {self.description}")
        if self.market != "global":
            mkt = "A股 (Chinese equities)" if self.market == "cn" else self.market.upper()
            lines.append(f"**Primary market:** {mkt}")
        if self.default_symbols:
            lines.append(f"**Default symbols:** {', '.join(self.default_symbols)}")
        if self.ashare:
            a = self.ashare
            if a.get("risk_level"):
                lines.append(f"**Risk preference:** {a['risk_level']}")

        header = "\n".join(lines)
        extra  = self.system_prompt.strip()
        ctx    = self.get_context_text(base_dir)
        auto   = self.get_auto_context_text(base_dir)

        parts: List[str] = []
        if header:
            parts.append(header)
        if extra:
            parts.append(extra)
        if ctx:
            parts.append("### Project Context Files\n\n" + ctx)
        if auto:
            parts.append("### Auto-loaded Code Context\n\n" + auto)

        if not parts:
            return ""

        return "\n\n---\n\n# Project Context (.ariarc)\n\n" + "\n\n".join(parts)

    def resolve_command(self, command: str, symbol: str = "", **kwargs) -> Optional[str]:
        """
        Resolve a custom command defined in .ariarc ``commands`` dict.

        Example:
            .ariarc: { "commands": { "/morning-cn": "生成A股早盘简报 {symbols}" } }
            rc.resolve_command("/morning-cn")
            → "生成A股早盘简报 sh600519, sh601318"
        """
        template = self.commands.get(command)
        if template is None:
            return None
        syms    = symbol or ", ".join(self.default_symbols)
        return template.format(
            symbol=symbol,
            symbols=syms,
            default_symbols=syms,
            market=self.market,
            **kwargs,
        )

    def is_write_denied(self, file_path: str) -> bool:
        """Return True if writing to file_path is blocked by write_deny_patterns."""
        import fnmatch
        p = file_path.replace("\\", "/")
        for pattern in self.write_deny_patterns:
            if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(pathlib.Path(p).name, pattern):
                return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_path":        str(self.source_path) if self.source_path else None,
            "project":            self.project,
            "description":        self.description,
            "market":             self.market,
            "default_symbols":    self.default_symbols,
            "tools_whitelist":    self.tools_whitelist,
            "tools_blacklist":    self.tools_blacklist,
            "commands":           list(self.commands.keys()),
            "context_files":      self.context_files,
            "auto_context":       self.auto_context,
            "write_deny_patterns": self.write_deny_patterns,
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_current_rc: Optional[AriaRC] = None


def get_ariarc(reload: bool = False) -> AriaRC:
    """Return the current session's AriaRC (lazy-loaded from cwd)."""
    global _current_rc
    if _current_rc is None or reload:
        _current_rc = AriaRC.load()
    return _current_rc


def reload_ariarc() -> AriaRC:
    return get_ariarc(reload=True)
