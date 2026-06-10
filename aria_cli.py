#!/usr/bin/env python3
# ruff: noqa: E501
"""
Aria Code v3.0 — Claude Code 风格的量化投资终端 + 编程代理

Features:
  - SSE 流式 AI 对话 + Ollama 本地回退 (支持工具调用)
  - 本地工具系统: read_file, write_file, edit_file, list_files, search_code, run_command
  - Agentic 工具循环: AI 自动读取→分析→编辑→执行 (最多 8 轮)
  - 22 个远程 Aria 工具 + 6 个本地工具 + 15 个 Skills + 30 个 Slash 命令
  - Tab 补全, Rich Syntax 高亮, ESC 取消流式, Ctrl+D 退出
  - 会话管理 (保存/加载/恢复/导出)
  - 用户认证 + 上下文注入 + 反馈机制

Usage:
    aria-code                                          # 交互式 REPL（推荐）
    aria-code --resume                                 # 恢复上次会话
    aria-code -p "分析AAPL的技术面"                      # 单次查询
    aria-code quote AAPL MSFT                          # 快速报价
    python3 apps/cli/aria_cli.py                         # 交互式 REPL
    python3 apps/cli/aria_cli.py --resume                # 恢复上次会话
    python3 apps/cli/aria_cli.py -p "分析AAPL的技术面"     # 单次查询
    python3 apps/cli/aria_cli.py -p "写一个动量策略"       # AI 自动生成代码并保存
    python3 apps/cli/aria_cli.py quote AAPL MSFT          # 快速报价
    python3 apps/cli/aria_cli.py backtest momentum SPY    # 策略回测
    python3 apps/cli/aria_cli.py -p "AAPL PE" --json     # JSON 输出
"""

__version__ = "3.0.0"

import sys
import os
import asyncio
import json
import argparse
import readline
import logging
import time
import shlex
import pathlib
import signal
import uuid
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable
from command_safety import evaluate_command_policy
from plan_utils import parse_plan_steps

# ── New modules: local LLM provider stack, finance tools, MCP, ariarc ──────
try:
    from model_capability import (
        get_model_capability, build_tool_system_prompt,
        RECOMMENDED_FINANCE_MODELS, parse_tool_calls_from_response as _parse_model_tool_calls,
    )
    _HAS_MODEL_CAP = True
except ImportError:
    _HAS_MODEL_CAP = False

try:
    from local_finance_tools import register_local_finance_tools
    _HAS_LOCAL_FINANCE = True
except ImportError:
    _HAS_LOCAL_FINANCE = False

try:
    from market_data_client import MarketDataClient as _MDC, get_mdc as _get_mdc
    _HAS_MDC = True
except ImportError:
    _HAS_MDC = False

try:
    from financial_agents import run_team_analysis as _run_team
    _HAS_AGENTS = True
except ImportError:
    _HAS_AGENTS = False

try:
    from strategy_vault import get_vault as _get_vault, ai_review_strategy as _ai_review
    _HAS_VAULT = True
except ImportError:
    _HAS_VAULT = False

try:
    from mcp_client import MCPToolRegistry, MCP_CONFIG_PATH, init_mcp as _init_mcp
    _HAS_MCP = True
    _mcp_registry: Optional["MCPToolRegistry"] = None
except ImportError:
    _HAS_MCP = False
    _mcp_registry = None

try:
    from ariarc import AriaRC, get_ariarc, reload_ariarc
    _HAS_ARIARC = True
except ImportError:
    _HAS_ARIARC = False

try:
    from plugin_loader import register_plugin_tools, find_plugin_file, PluginWatcher
    _HAS_PLUGIN = True
    _plugin_watcher: Optional["PluginWatcher"] = None
except ImportError:
    _HAS_PLUGIN = False
    _plugin_watcher = None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.WARNING)

# ============================================================================
# Rich Console (graceful fallback to ANSI if not installed)
# ============================================================================

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.live import Live
    from rich.text import Text
    from rich.status import Status
    from rich.syntax import Syntax
    from rich.panel import Panel
    from rich.rule import Rule
    from rich import box as rich_box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# prompt_toolkit for interactive input (slash command dropdown, placeholder, toolbar)
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style as PTStyle
    HAS_PT = True
except ImportError:
    HAS_PT = False

if HAS_RICH:
    console = Console(highlight=False)
    # Syntax highlight theme — override via config "syntax_theme" key
    _SYNTAX_THEME: str = "monokai"
else:
    _SYNTAX_THEME: str = "monokai"
    class _FallbackConsole:
        def print(self, *a, **kw): print(*[str(x) for x in a])
        def input(self, prompt=""): return input(prompt)
        def status(self, msg):
            class _Ctx:
                def __enter__(self): print(msg); return self
                def __exit__(self, *a): pass
                def update(self, msg): print(msg)
            return _Ctx()
    console = _FallbackConsole()

# Terminal raw-mode for ESC key detection (macOS / Linux)
try:
    import termios, tty, select as _select
    _HAS_TERMIOS = True
except ImportError:
    _HAS_TERMIOS = False


class _EscWatcher:
    """Background thread that watches for ESC key press to cancel operations."""

    def __init__(self):
        self._active = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._old_settings = None
        self._cancel_event: Optional[asyncio.Event] = None
        self._fd: Optional[int] = None

    def start(self, cancel_event: asyncio.Event):
        """Start watching for ESC. Call before streaming begins."""
        if not _HAS_TERMIOS or not sys.stdin.isatty():
            return
        self._cancel_event = cancel_event
        self._fd = sys.stdin.fileno()
        try:
            self._old_settings = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except Exception:
            self._old_settings = None
            return
        self._active = True
        self._paused = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self):
        """Pause watching and restore terminal for input prompts."""
        self._paused = True
        if self._old_settings and self._fd is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)
            except Exception:
                pass

    def resume(self):
        """Resume watching after input prompt completes."""
        if not self._active or not _HAS_TERMIOS or self._fd is None:
            return
        if self._cancel_event and self._cancel_event.is_set():
            return
        try:
            termios.tcflush(self._fd, termios.TCIFLUSH)
            self._old_settings = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        except Exception:
            return
        self._paused = False

    def stop(self):
        """Stop watching and restore terminal settings."""
        self._active = False
        self._paused = False
        if self._old_settings and self._fd is not None:
            try:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_settings)
            except Exception:
                pass
            self._old_settings = None
        if self._thread:
            self._thread.join(timeout=0.3)
            self._thread = None

    def _run(self):
        fd = self._fd
        try:
            while self._active:
                if self._paused:
                    time.sleep(0.1)
                    continue
                try:
                    ready, _, _ = _select.select([fd], [], [], 0.15)
                except (ValueError, OSError):
                    break
                if not self._active or self._paused:
                    continue
                if ready:
                    try:
                        ch = os.read(fd, 1)
                    except OSError:
                        break
                    if ch == b'\x1b':
                        # Distinguish standalone ESC from escape sequences (arrow keys etc)
                        try:
                            r2, _, _ = _select.select([fd], [], [], 0.05)
                        except (ValueError, OSError):
                            break
                        if r2:
                            try:
                                os.read(fd, 16)  # consume rest of escape sequence
                            except OSError:
                                pass
                        else:
                            # Standalone ESC key → cancel
                            if self._cancel_event:
                                self._cancel_event.set()
                            self._active = False
        except Exception:
            pass


_esc_watcher = _EscWatcher()


# ============================================================================
# Arrow-key selector — replaces numbered input prompts
# ============================================================================

def _arrow_select(options: list, selected: int = 0, title: str = "",
                  max_visible: int = 10) -> int:
    """Interactive arrow-key selector with scrolling window.

    Args:
        options:     list of (label, description) tuples or plain strings
        selected:    initially highlighted index
        title:       optional header shown above the list
        max_visible: max rows rendered at once; scrolls when list is larger
    Returns:
        index of chosen option, or -1 if cancelled / no selection
    """
    if not options:
        return -1

    # ── Non-interactive fallback (no TTY or no termios) ─────────────────────
    if not _HAS_TERMIOS or not sys.stdin.isatty():
        if title:
            print(f"\n  {title}\n")
        for i, opt in enumerate(options):
            label = opt[0] if isinstance(opt, tuple) else opt
            marker = "❯" if i == selected else " "
            print(f"  {marker} {i + 1:2d}. {label}")
        try:
            c = input("\n  Enter number (or Enter to keep current): ").strip()
            if not c:
                return selected
            idx = int(c) - 1
            return idx if 0 <= idx < len(options) else -1
        except (ValueError, EOFError, KeyboardInterrupt):
            return -1

    n = len(options)
    visible = min(max_visible, n)          # rows actually drawn
    scroll  = max(0, selected - visible + 1)  # first visible index

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    # Terminal column count — needed for physical-line calculation
    try:
        _tcols = os.get_terminal_size(fd).columns
    except Exception:
        _tcols = 80

    def _display_width(s: str) -> int:
        """Display column width of s: CJK/full-width chars count as 2."""
        import re as _re
        # Strip ANSI escape sequences before measuring
        clean = _re.sub(r'\x1b\[[0-9;]*[mJKHABCDfGrs]', '', s)
        w = 0
        for ch in clean:
            cp = ord(ch)
            if (0x1100 <= cp <= 0x115F or 0x2E80 <= cp <= 0xA4CF or
                    0xAC00 <= cp <= 0xD7AF or 0xF900 <= cp <= 0xFAFF or
                    0xFE10 <= cp <= 0xFE1F or 0xFE30 <= cp <= 0xFE4F or
                    0xFF01 <= cp <= 0xFF60 or 0xFFE0 <= cp <= 0xFFE6 or
                    0x3000 <= cp <= 0x303F):
                w += 2
            else:
                w += 1
        return w

    def _physical_lines(text: str) -> int:
        """How many terminal rows does text occupy (accounting for CJK wrap)."""
        dw = _display_width(text)
        return max(1, (dw + _tcols - 1) // _tcols)

    # Write raw bytes directly — bypasses Rich / prompt_toolkit buffering
    def _raw(s: str):
        os.write(1, s.encode())

    def _render():
        nonlocal scroll
        # Keep selected inside the visible window
        if selected < scroll:
            scroll = selected
        elif selected >= scroll + visible:
            scroll = selected - visible + 1

        buf = ""
        # On re-render: move cursor back to top of the drawn block.
        # Use the ACTUAL physical height from the last render (not just
        # the logical visible count) so CJK label wraps don't drift the cursor.
        if _render.drawn:
            buf += f"\033[{_render.last_phys_height}A"

        phys_height = 0
        for row in range(visible):
            idx = scroll + row
            opt   = options[idx]
            label = opt[0] if isinstance(opt, tuple) else opt
            desc  = opt[1] if isinstance(opt, tuple) and len(opt) > 1 else ""
            if idx == selected:
                line = f"  \033[1m\033[38;2;192;128;80m❯\033[0m \033[1m{label}\033[0m"
                if desc:
                    line += f"  \033[2m{desc}\033[0m"
            else:
                line = f"    {label}"
                if desc:
                    line += f"  \033[2m{desc}\033[0m"
            buf += f"\033[2K{line}\n"
            phys_height += _physical_lines(line)

        # Scroll indicator — always exactly 1 row, always below the list
        if n > visible:
            hint = f"  \033[2m{selected + 1}/{n}\033[0m"
            buf += f"\033[2K{hint}\n"
        else:
            buf += f"\033[2K\n"   # blank placeholder keeps cursor math stable
        phys_height += 1  # hint/blank line

        _render.last_phys_height = phys_height
        _raw(buf)
        _render.drawn = True

    _render.drawn = False
    _render.last_phys_height = visible + 1  # safe initial value

    try:
        # Pause ESC watcher so it doesn't consume \x1b[A/B arrow escape sequences
        _esc_watcher.pause()
        tty.setcbreak(fd)
        sys.stdout.flush()
        if title:
            _raw(f"\n  \033[1m{title}\033[0m  \033[2m↑↓/j·k  Enter  q=cancel\033[0m\n\n")
        else:
            _raw(f"\n  \033[2m↑↓/j·k  Enter  q=cancel\033[0m\n\n")

        _render()

        while True:
            ch = os.read(fd, 1)
            if ch == b'\x1b':
                seq = b''
                if _select.select([fd], [], [], 0.05)[0]:
                    seq = os.read(fd, 2)
                if seq == b'[A':        # ↑ Up arrow
                    selected = (selected - 1) % n
                    _render()
                elif seq == b'[B':      # ↓ Down arrow
                    selected = (selected + 1) % n
                    _render()
                elif seq == b'[5~':     # Page Up
                    selected = max(0, selected - visible)
                    _render()
                elif seq == b'[6~':     # Page Down
                    selected = min(n - 1, selected + visible)
                    _render()
                elif not seq:           # Bare ESC — cancel
                    return -1
            elif ch in (b'\r', b'\n'):  # Enter
                return selected
            elif ch == b'q':
                return -1
            elif ch == b'k':            # vim ↑
                selected = (selected - 1) % n
                _render()
            elif ch == b'j':            # vim ↓
                selected = (selected + 1) % n
                _render()
            elif ch == b'g':            # vim G top
                selected = 0
                _render()
            elif ch == b'G':            # vim G bottom
                selected = n - 1
                _render()
            elif ch == b'\x03':         # Ctrl+C
                return -1
            elif ch == b'\x04':         # Ctrl+D
                return -1
    except (EOFError, KeyboardInterrupt, OSError):
        return -1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        _esc_watcher.resume()
        _raw("\n")


# ============================================================================
# Configuration & Persistent Memory
# ============================================================================

CONFIG_DIR = pathlib.Path.home() / ".arthera"
CONFIG_FILE = CONFIG_DIR / "config.json"
HISTORY_FILE = CONFIG_DIR / "history"
SESSIONS_DIR = CONFIG_DIR / "sessions"
PROVIDERS_FILE = CONFIG_DIR / "providers.json"  # Cloud API keys (Open Interpreter style)

# ── Cloud Provider key map ───────────────────────────────────────────────────
# Maps provider short name → environment variable name for API key.
_PROVIDER_KEY_MAP: Dict[str, str] = {
    "deepseek":    "DEEPSEEK_API_KEY",
    "openai":      "OPENAI_API_KEY",
    "anthropic":   "ANTHROPIC_API_KEY",
    "claude":      "ANTHROPIC_API_KEY",
    "groq":        "GROQ_API_KEY",
    "together":    "TOGETHER_API_KEY",
    "dashscope":   "DASHSCOPE_API_KEY",
    "aliyun":      "DASHSCOPE_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
    "moonshot":    "MOONSHOT_API_KEY",
    "zhipu":       "ZHIPUAI_API_KEY",
}

# Default base URLs for cloud providers
_PROVIDER_BASE_URLS: Dict[str, str] = {
    "deepseek":    "https://api.deepseek.com",
    "openai":      "https://api.openai.com",
    "anthropic":   "https://api.anthropic.com",
    "claude":      "https://api.anthropic.com",
    "groq":        "https://api.groq.com/openai",
    "together":    "https://api.together.xyz",
    "dashscope":   "https://dashscope.aliyuncs.com/compatible-mode",
    "aliyun":      "https://dashscope.aliyuncs.com/compatible-mode",
    "siliconflow": "https://api.siliconflow.cn",
    "moonshot":    "https://api.moonshot.cn/v1",
    "zhipu":       "https://open.bigmodel.cn/api/paas/v4",
}


# ── Data / Market Service key map ────────────────────────────────────────────
# Maps service short name → environment variable name for API key.
# When the Arthera backend (Alibaba Cloud) is offline, these are used directly.
_DATA_KEY_MAP: Dict[str, str] = {
    "finnhub":      "FINNHUB_API_KEY",       # Real-time stock data + news (free tier: 60/min)
    "newsapi":      "NEWS_API_KEY",           # Financial news aggregator (free: 100/day)
    "brave":        "BRAVE_SEARCH_API_KEY",   # Web search (free: 2000/month)
    "coingecko":    "COINGECKO_API_KEY",      # Crypto data Pro (basic tier is free)
    "alphavantage": "ALPHA_VANTAGE_API_KEY",  # Stock history (free: 25/day)
    "polygon":      "POLYGON_API_KEY",        # US market data (free tier available)
    "fmp":          "FMP_API_KEY",            # Financial Modeling Prep (free tier)
    "twelvedata":   "TWELVEDATA_API_KEY",     # Global market data (free: 800/day)
}

# Registration / signup URLs for each data service
_DATA_SIGNUP_URLS: Dict[str, str] = {
    "finnhub":      "https://finnhub.io/register",
    "newsapi":      "https://newsapi.org/register",
    "brave":        "https://api.search.brave.com/app/keys",
    "coingecko":    "https://www.coingecko.com/en/api",
    "alphavantage": "https://www.alphavantage.co/support/#api-key",
    "polygon":      "https://polygon.io/signup",
    "fmp":          "https://financialmodelingprep.com/register",
    "twelvedata":   "https://twelvedata.com/register",
}


def _load_providers_json() -> Dict[str, Any]:
    """Load ~/.arthera/providers.json and return the 'llm' section.

    Returns an empty dict if the file doesn't exist or is malformed.
    """
    try:
        if PROVIDERS_FILE.exists():
            data = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
            return data.get("llm", data) if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_providers_json(llm_section: Dict[str, Any]) -> None:
    """Persist LLM provider API keys to ~/.arthera/providers.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing: Dict = {}
    if PROVIDERS_FILE.exists():
        try:
            existing = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    existing["llm"] = llm_section
    PROVIDERS_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_data_key(service: str, key: str) -> None:
    """Persist a data service API key to ~/.arthera/providers.json under 'data' section."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing: Dict = {}
    if PROVIDERS_FILE.exists():
        try:
            existing = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    data_section = existing.get("data", {})
    data_section[service] = {"api_key": key}
    existing["data"] = data_section
    PROVIDERS_FILE.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_data_keys() -> Dict[str, str]:
    """Return a dict of {service: api_key} for all configured data services.
    Merges environment variables (priority) and providers.json."""
    result: Dict[str, str] = {}
    # 1. Environment variables
    for svc, env_var in _DATA_KEY_MAP.items():
        val = os.getenv(env_var, "")
        if val:
            result[svc] = val
    # 2. providers.json "data" section
    try:
        if PROVIDERS_FILE.exists():
            raw = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
            for svc, entry in raw.get("data", {}).items():
                if svc not in result and entry.get("api_key"):
                    result[svc] = entry["api_key"]
    except Exception:
        pass
    return result


def _get_provider_key(provider: str) -> str:
    """Return the configured API key for a provider (env var takes priority)."""
    env_var = (_PROVIDER_KEY_MAP.get(provider.lower())
               or _DATA_KEY_MAP.get(provider.lower(), ""))
    if env_var:
        val = os.getenv(env_var, "")
        if val:
            return val
    # Check providers.json under both "llm" and "data" sections
    try:
        if PROVIDERS_FILE.exists():
            raw = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
            for section in ("llm", "data"):
                entry = raw.get(section, {}).get(provider.lower(), {})
                if entry.get("api_key"):
                    return entry["api_key"]
    except Exception:
        pass
    return ""

DEFAULT_CONFIG = {
    "api_url": os.getenv(
        "ARTHERA_API_URL",
        "http://localhost:8000"  # 直接运行时用 8000；Docker 模式设 ARTHERA_API_URL=http://localhost:8100
    ),
    "local_url": "http://localhost:8000",  # quant engine is the unified service
    "ollama_url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
    "model": "qwen2.5-coder:1.5b",  # smallest available local model; upgrade chain handles coding tasks
    "thinking_mode": "auto",
    "watchlist": ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"],
    "auth_token": None,
    "user_id": None,
    "last_session_id": None,
    "auto_save_sessions": True,
    "command_policy": "safe",   # safe | balanced | full
    "write_policy": "desktop_only",  # desktop_only | confirm_outside | always_confirm
    "local_mode": False,        # True = skip AWS, always use Ollama
    "conversation_history": [],
}

# Module-level write/command policies — updated whenever config is loaded/changed.
# Used by standalone tool functions without terminal access.
_ACTIVE_WRITE_POLICY = ["desktop_only"]  # list so closures can mutate it
_ACTIVE_COMMAND_POLICY = ["safe"]


def _sync_write_policy(config: dict):
    """Sync module-level write/command policies from config dict."""
    _ACTIVE_WRITE_POLICY[0] = config.get("write_policy", "desktop_only")
    _ACTIVE_COMMAND_POLICY[0] = config.get("command_policy", "safe")


def _run_event_hook(event: str, env_extra: dict = None):
    """Execute hook scripts for a given lifecycle event.

    Looks in ~/.arthera/hooks/<event>.sh and .aria/hooks/<event>.sh.
    Passes ARIA_* env vars to the script. Silently skips if not found.
    Inspired by Claude Code's hooks system (PreToolUse / PostToolUse / etc.).

    Events: prompt_submit, response_done, compact, session_start, session_end
    """
    import subprocess as _sp, os as _os
    dirs = [
        pathlib.Path.home() / ".arthera" / "hooks",
        pathlib.Path.cwd() / ".aria" / "hooks",
    ]
    env = dict(_os.environ)
    env["ARIA_EVENT"] = event
    if env_extra:
        env.update(env_extra)
    for hdir in dirs:
        script = hdir / f"{event}.sh"
        if script.exists() and script.stat().st_size > 0:
            try:
                _sp.run(
                    [str(script)], env=env, timeout=10,
                    capture_output=True, text=True, check=False
                )
            except Exception:
                pass


def load_config() -> dict:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            merged = {**DEFAULT_CONFIG, **saved}
            # Auto-fix: if saved model is not in MODELS and not a valid Ollama ID pattern,
            # fall back to the default model to prevent HTTP 404 on startup
            saved_model = merged.get("model", "")
            valid_ids = {m["id"] for m in MODELS.values()}
            if saved_model and saved_model not in valid_ids:
                merged["model"] = DEFAULT_CONFIG["model"]
            # Warn once if saved model looks like a non-existent aria-* model name
            # (these were old hardcoded names; they no longer exist in Ollama)
            _stale_prefixes = ("aria-opus", "aria-prelude", "aria-sonata:3", "aria-sonata:4")
            if any(saved_model.startswith(p) for p in _stale_prefixes):
                merged["model"] = DEFAULT_CONFIG["model"]
            _sync_write_policy(merged)
            return merged
        except Exception:
            pass
    cfg = dict(DEFAULT_CONFIG)
    _sync_write_policy(cfg)
    return cfg


def save_config(cfg: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    exclude = {"conversation_history"}
    to_save = {k: v for k, v in cfg.items() if k not in exclude}
    with open(CONFIG_FILE, "w") as f:
        json.dump(to_save, f, indent=2, ensure_ascii=False)


# ============================================================================
# Aria Tool Executor — calls /api/aria/execute-tool
# ============================================================================

ARIA_TOOLS = [
    ("get_market_data",         "Stock quotes, prices, chart data"),
    ("get_crypto_data",         "Cryptocurrency market data"),
    ("get_forex_data",          "Foreign exchange rates"),
    ("get_commodities_data",    "Commodities prices (gold, oil, etc.)"),
    ("get_futures_data",        "Futures contract data"),
    ("get_bonds_data",          "Bond yields and data"),
    ("backtest_strategy",       "Run strategy backtesting"),
    ("calculate_factors",       "Calculate quantitative factors"),
    ("get_alpha158_factors",    "Alpha158 factor set"),
    ("get_risk_metrics",        "Risk metrics and VaR"),
    ("optimize_positions",      "Portfolio optimization"),
    ("stress_test_strategy",    "Strategy stress testing"),
    ("check_strategy_compliance", "Strategy compliance check"),
    ("recommend_strategy",      "AI strategy recommendation"),
    ("analyze_news",            "News sentiment analysis"),
    ("web_search",              "Web search for research"),
    ("get_world_bank_reports",  "World Bank economic reports"),
    ("generate_chart",          "Generate chart visualization"),
    ("generate_report",         "Generate analysis report"),
    ("assess_portfolio_risk",   "Portfolio risk assessment"),
    ("get_sector_performance",  "Sector performance heatmap"),
    ("get_market_indices",      "Global market indices"),
]


# ============================================================================
# Models Registry — like Claude Code model picker
# ============================================================================

MODELS = {
    # ── 生产级本地模型（7B，无幻觉）──────────────────────────────────────
    "qwen7b": {
        "id": "qwen2.5:7b",
        "name": "Qwen 2.5",
        "version": "7B",
        "tag": "Sonata",
        "speed": "★★★★",
        "intelligence": "★★★★★",
        "description": "主力模型：Qwen2.5-7B，工具调用稳定，无幻觉，金融知识扎实",
        "capabilities": ["chat", "tool calls", "financial analysis", "coding", "Chinese"],
        "thinking": False,
        "tools": True,
        "max_tokens": 4096,
        "num_ctx": 32768,
        "temperature": 0.3,
        "badge": "Default",
    },
    "qwen-coder": {
        "id": "qwen2.5-coder:7b",
        "name": "Qwen Coder",
        "version": "7B",
        "tag": "Coder",
        "speed": "★★★★",
        "intelligence": "★★★★★",
        "description": "代码专精：量化策略生成、回测代码、Python 金融工具",
        "capabilities": ["strategy code", "backtest", "Python", "quant development"],
        "thinking": False,
        "tools": True,
        "max_tokens": 4096,
        "num_ctx": 32768,
        "temperature": 0.2,
        "badge": "Code",
    },
    "qwen-fast": {
        "id": "qwen2.5-coder:1.5b",
        "name": "Qwen Fast",
        "version": "1.5B",
        "tag": "Prelude",
        "speed": "★★★★★",
        "intelligence": "★★★",
        "description": "超快响应：简单问答、实时报价、快速指令",
        "capabilities": ["fast chat", "simple queries", "ultra-low latency"],
        "thinking": False,
        "tools": False,
        "max_tokens": 2048,
        "num_ctx": 8192,
        "temperature": 0.3,
        "badge": "Fast",
    },
    "deepseek-r1": {
        "id": "deepseek-r1:7b",
        "name": "DeepSeek R1",
        "version": "7B",
        "tag": "Reasoning",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "深度推理：复杂投资决策、多步骤分析、Chain-of-Thought",
        "capabilities": ["deep reasoning", "chain-of-thought", "complex quant", "investment thesis"],
        "thinking": True,
        "tools": False,
        "max_tokens": 4096,
        "num_ctx": 32768,
        "temperature": 0.3,
        "badge": "Think",
    },
    # ── 云端大模型（Ollama Cloud 路由）────────────────────────────────────
    "gpt-oss-120b": {
        "id": "gpt-oss:120b-cloud",
        "name": "GPT-OSS",
        "version": "120B",
        "tag": "Cloud·120B",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "云端 120B 模型：机构级分析，复杂金融报告",
        "capabilities": ["institutional analysis", "long-form reports", "complex reasoning"],
        "thinking": False,
        "tools": True,
        "max_tokens": 8192,
        "num_ctx": 131072,
        "temperature": 0.3,
        "badge": "Cloud",
    },
    "deepseek-v3": {
        "id": "deepseek-v3.1:671b-cloud",
        "name": "DeepSeek V3",
        "version": "671B",
        "tag": "Cloud·671B",
        "speed": "★★★",
        "intelligence": "★★★★★",
        "description": "云端 671B 旗舰：最强推理能力，研报级分析",
        "capabilities": ["flagship reasoning", "research report", "quant strategy"],
        "thinking": False,
        "tools": True,
        "max_tokens": 8192,
        "num_ctx": 131072,
        "temperature": 0.3,
        "badge": "Cloud",
    },
}

# Model aliases: short names → model key
MODEL_ALIASES = {
    # 新模型
    "qwen7b": "qwen7b",   "q7": "qwen7b",   "sonata": "qwen7b",   "s": "qwen7b",
    "qwen-coder": "qwen-coder", "coder": "qwen-coder", "c": "qwen-coder",
    "qwen-fast": "qwen-fast",   "fast": "qwen-fast",   "prelude": "qwen-fast", "p": "qwen-fast",
    "deepseek-r1": "deepseek-r1", "r1": "deepseek-r1",
    "gpt-oss": "gpt-oss-120b",    "120b": "gpt-oss-120b",
    "deepseek-v3": "deepseek-v3", "v3": "deepseek-v3",  "671b": "deepseek-v3",
    # 旧名向后兼容
    "sonata-thinking": "deepseek-r1", "st": "deepseek-r1",
    "sonata-verbose":  "qwen7b",      "sv": "qwen7b",
    # direct model IDs → map to new registry keys
    "qwen2.5:7b":             "qwen7b",
    "qwen2.5:3b":             "qwen-fast",
    "qwen2.5-coder:7b":       "qwen-coder",
    "qwen2.5-coder:1.5b":     "qwen-fast",
    "deepseek-r1:7b":         "deepseek-r1",
    "gpt-oss:120b-cloud":     "gpt-oss-120b",
    "deepseek-v3.1:671b-cloud": "deepseek-v3",
    # 旧 aria 模型 ID → 向后兼容
    "aria-sonata:4.5":         "qwen7b",
    "aria-sonata:4.5-thinking":"deepseek-r1",
    "aria-sonata:4.5-verbose": "qwen7b",
    "aria-sonata:4.6":         "qwen7b",
    "aria-sonata:4.6-thinking":"deepseek-r1",
    "aria-prelude:4.3":        "qwen-fast",
    "aria-prelude:1.5b":       "qwen-fast",
}

# ── 模型降级优先级（单一事实源：预检 / 运行时 fallback 共用）────────────────
# NOTE: 不用字母排序 — "deepseek-v3.1:671b-cloud" 字母排最前但需要付费
# Ollama 订阅且时常超时；"gpt-oss:120b-cloud" 中继更可靠。
_MODEL_FALLBACK_PREFIXES = [
    "gpt-oss",           # cloud relay, reliable (~1 s)
    "qwen2.5-coder:7b",  # local, coding capable
    "qwen2.5:7b",        # local, general capable
    "qwen2.5-coder:3b",  # local, small coding
    "qwen2.5:3b",        # local, small general
    "llama3.2:3b",       # local fallback
    "mistral",           # local fallback
    "deepseek-v3.1",     # last resort (requires subscription)
]


def _pick_best_installed_model(installed, preferred: str = ""):
    """从已安装模型中选出实际将使用的模型（预检与运行时共用此逻辑）。

    优先精确匹配 preferred；否则按 _MODEL_FALLBACK_PREFIXES 能力顺序；
    全部未命中才退化到字母排序第一个。installed 为空返回 None。
    """
    if not installed:
        return None
    if preferred and preferred in installed:
        return preferred
    for pref in _MODEL_FALLBACK_PREFIXES:
        cand = next((m for m in sorted(installed) if m.startswith(pref)), None)
        if cand:
            return cand
    return sorted(installed)[0]


def detect_ollama_models(ollama_url: str = "http://localhost:11434") -> list:
    """Query Ollama /api/tags and return list of available model names.

    Always bypasses HTTP_PROXY so localhost is reached directly even when a
    system proxy (VPN / clash / surge) is active.
    """
    import urllib.request
    # Force direct connection — bypass any HTTP_PROXY / HTTPS_PROXY env vars
    _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with _opener.open(f"{ollama_url}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        # Also try 127.0.0.1 if hostname is "localhost" (IPv6 resolution fallback)
        if "localhost" in ollama_url:
            try:
                fallback = ollama_url.replace("localhost", "127.0.0.1")
                with _opener.open(f"{fallback}/api/tags", timeout=5) as r:
                    data = json.loads(r.read())
                return [m["name"] for m in data.get("models", [])]
            except Exception:
                pass
        return []


def detect_ollama_models_rich(ollama_url: str = "http://localhost:11434") -> tuple:
    """Return (models_list, error_str) where each entry in models_list is a dict:
        {"name": str, "size_label": str, "family": str, "quant": str}
    error_str is None on success, or a short human-readable reason on failure.
    """
    import urllib.request
    _opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _try(url: str):
        with _opener.open(f"{url}/api/tags", timeout=5) as r:
            return json.loads(r.read())

    data = None
    last_err = None
    for u in [ollama_url] + ([ollama_url.replace("localhost", "127.0.0.1")]
                              if "localhost" in ollama_url else []):
        try:
            data = _try(u)
            break
        except OSError as e:
            last_err = str(e)
        except Exception as e:
            last_err = str(e)

    if data is None:
        return [], last_err or "connection failed"

    results = []
    for m in data.get("models", []):
        det  = m.get("details", {})
        size = det.get("parameter_size", "")
        fam  = det.get("family", "")
        qnt  = det.get("quantization_level", "")
        results.append({
            "name":       m["name"],
            "size_label": size,    # e.g. "1.5B", "7B", "671.0B"
            "family":     fam,     # e.g. "qwen2", "deepseek2"
            "quant":      qnt,     # e.g. "Q4_K_M", "MXFP4"
        })
    return results, None


# ── Response cache for stateless queries (TTL = 60s) ─────────────────────────
# Avoids sending the same market/concept query to Ollama multiple times
# in rapid succession (e.g., user retries or tab-completion tests).
import hashlib as _hashlib
_RESPONSE_CACHE: dict = {}   # key → (response_text, expire_ts)
_RESPONSE_CACHE_TTL = 60.0   # seconds

def _cache_get(key: str) -> str | None:
    """Return cached response text if still valid, else None."""
    entry = _RESPONSE_CACHE.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None

def _cache_set(key: str, value: str) -> None:
    """Store response in cache with TTL expiry."""
    _RESPONSE_CACHE[key] = (value, time.time() + _RESPONSE_CACHE_TTL)
    # Keep cache small — evict expired entries when it grows large
    if len(_RESPONSE_CACHE) > 200:
        now = time.time()
        for k in list(_RESPONSE_CACHE.keys()):
            if _RESPONSE_CACHE[k][1] < now:
                del _RESPONSE_CACHE[k]

def _cache_key(model: str, message: str) -> str:
    raw = f"{model}::{message.strip().lower()}"
    return _hashlib.md5(raw.encode()).hexdigest()

def _is_simple_greeting(message: str) -> bool:
    text = (message or "").strip().lower()
    greetings = {
        "hi", "hello", "hey", "你好", "您好", "嗨", "哈喽", "在吗",
        "早上好", "下午好", "晚上好",
    }
    return text in greetings or (len(text) <= 8 and any(g in text for g in greetings))


def _offline_greeting_response() -> dict:
    return {
        "success": True,
        "response": (
            "你好，我是 Aria Code。\n\n"
            "当前云端模型不可用，且本地 Ollama 服务没有启动；简单问候可以直接响应。"
            "如果要进行代码修改、市场分析或长文本推理，请先启动本地模型：\n\n"
            "```bash\n"
            "ollama serve\n"
            "```\n\n"
            "然后可用 `ollama list` 检查已安装模型，或运行 `/health` 查看 Aria Code 状态。"
        ),
        "provider": "builtin",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0},
    }


def _ollama_unavailable_result(ollama_url: str, err: str = "") -> dict:
    host = ollama_url or "http://localhost:11434"
    detail = f"\n\nDetail: {err}" if err else ""
    return {
        "success": False,
        "provider": "ollama",
        "error": (
            "Local Ollama is not reachable.\n\n"
            f"Host: {host}\n"
            "Start it in another terminal:\n\n"
            "  ollama serve\n\n"
            "Then verify:\n\n"
            "  curl http://127.0.0.1:11434/api/tags\n"
            "  ollama list\n\n"
            "If you do not want local fallback, use a working cloud/API provider or disable local mode."
            f"{detail}"
        ),
    }


def resolve_model_key(model_str: str) -> str:
    """Resolve any model alias/ID/key to a MODELS key.

    For community Ollama models (qwen2.5-coder, llama3.2, deepseek-r1, etc.)
    that are NOT in the MODELS registry, returns the sentinel "_community_"
    so callers know to use model_capability.get_model_capability() instead
    of falling back to hardcoded "prelude" settings.
    """
    if model_str in MODELS:
        return model_str
    if model_str in MODEL_ALIASES:
        return MODEL_ALIASES[model_str]
    # Community/custom Ollama model — not in registry
    return "_community_"


def get_model_cfg(model_str: str) -> dict:
    """Return the best available config dict for *model_str*.

    For registered models (MODELS table): returns the table entry.
    For community Ollama models: synthesizes a config from model_capability.
    Never silently falls back to 'prelude' settings for an unrelated model.
    """
    key = resolve_model_key(model_str)
    if key in MODELS:
        return MODELS[key]
    # Community model — build config from model_capability registry
    if _HAS_MODEL_CAP:
        cap = get_model_capability(model_str)
        return {
            "id":          model_str,
            "name":        model_str,
            "num_ctx":     cap.context_window,
            "temperature": cap.temperature,
            "max_tokens":  min(cap.context_window // 4, 8192),
            "thinking":    cap.thinking,
            "tools":       cap.tool_calls,
        }
    # Last resort fallback — use qwen7b (sonata) settings as a safe default
    return MODELS.get("sonata", MODELS.get("qwen7b", next(iter(MODELS.values()))))

THINKING_MODES = {
    "auto":     {"label": "Auto",     "description": "Let Aria decide when to think deeply"},
    "instant":  {"label": "Instant",  "description": "Fast responses, no extended thinking"},
    "thinking": {"label": "Thinking", "description": "Always show reasoning chain"},
}


# ============================================================================
# Skills System — Claude Code-style expandable prompt templates
# ============================================================================

SKILLS = [
    {
        "command": "/morning-brief",
        "name": "Morning Brief",
        "category": "research",
        "description": "Daily market briefing with key events and outlook",
        "args": "[focus_area]",
        "prompt": (
            "Generate a comprehensive morning market briefing:\n"
            "1. US market futures and overnight moves\n"
            "2. Key economic events and earnings today\n"
            "3. Global markets overview (Asia, Europe)\n"
            "4. Top sector movers and themes\n"
            "5. Trading outlook and key levels to watch\n"
            "{extra}"
        ),
        "tools_hint": ["get_market_indices", "get_sector_performance", "analyze_news"],
    },
    {
        "command": "/deep-analysis",
        "name": "Deep Analysis",
        "category": "analysis",
        "description": "Multi-factor stock deep dive (technical + fundamental + sentiment)",
        "args": "<symbol>",
        "prompt": (
            "Perform a comprehensive multi-factor analysis of {symbol}:\n"
            "1. Technical Analysis: trend, support/resistance, indicators (RSI, MACD, Bollinger)\n"
            "2. Fundamental Analysis: PE, PB, revenue growth, margins, debt ratios\n"
            "3. Sentiment Analysis: recent news sentiment, analyst ratings, social buzz\n"
            "4. Risk Assessment: VaR, beta, max drawdown potential\n"
            "5. Verdict: Bull/Bear/Neutral with confidence level and price targets"
        ),
        "tools_hint": ["get_market_data", "calculate_factors", "analyze_news", "get_risk_metrics"],
    },
    {
        "command": "/trade-idea",
        "name": "Trade Idea",
        "category": "strategy",
        "description": "AI-generated trade ideas with entry/exit levels",
        "args": "[market_or_sector]",
        "prompt": (
            "Generate 3 actionable trade ideas{context}:\n"
            "For each idea provide:\n"
            "1. Symbol and direction (Long/Short)\n"
            "2. Entry zone, stop loss, and 2 take-profit levels\n"
            "3. Risk-reward ratio\n"
            "4. Catalyst: what's driving the trade\n"
            "5. Timeframe (swing/position/day)\n"
            "6. Confidence level (1-10)"
        ),
        "tools_hint": ["get_market_data", "analyze_news", "recommend_strategy"],
    },
    {
        "command": "/risk-report",
        "name": "Risk Report",
        "category": "risk",
        "description": "Portfolio risk analysis with VaR, stress tests, and correlation",
        "args": "[symbols...]",
        "prompt": (
            "Generate a comprehensive risk report for portfolio: {symbols}\n"
            "1. Portfolio VaR (95%, 99%) — daily and monthly\n"
            "2. Correlation matrix between holdings\n"
            "3. Concentration risk by sector/geography\n"
            "4. Stress test scenarios (2008 crisis, COVID crash, rate hike)\n"
            "5. Tail risk analysis\n"
            "6. Recommendations: rebalancing suggestions to reduce risk"
        ),
        "tools_hint": ["assess_portfolio_risk", "get_risk_metrics", "stress_test_strategy"],
    },
    {
        "command": "/sector-rotation",
        "name": "Sector Rotation",
        "category": "strategy",
        "description": "Sector rotation analysis with economic cycle positioning",
        "args": "",
        "prompt": (
            "Analyze current sector rotation dynamics:\n"
            "1. Current economic cycle phase (early/mid/late/recession)\n"
            "2. All 11 GICS sectors: performance, momentum, relative strength\n"
            "3. Leading vs lagging sectors and why\n"
            "4. Sector rotation strategy: which sectors to overweight/underweight\n"
            "5. Top stock picks from the strongest sectors\n"
            "6. Historical analog: which past period is most similar"
        ),
        "tools_hint": ["get_sector_performance", "get_market_indices", "analyze_news"],
    },
    {
        "command": "/macro-outlook",
        "name": "Macro Outlook",
        "category": "research",
        "description": "Macroeconomic analysis: rates, inflation, growth & cycle",
        "args": "[region]",
        "prompt": (
            "Provide a macroeconomic outlook{context}:\n"
            "1. GDP growth forecast and trends\n"
            "2. Inflation trajectory (CPI, PCE) and central bank response\n"
            "3. Interest rate path: current level and expectations\n"
            "4. Employment situation: jobs, wages, participation\n"
            "5. Key risks: geopolitical, financial, systemic\n"
            "6. Asset class implications: equities, bonds, commodities, crypto"
        ),
        "tools_hint": ["get_world_bank_reports", "get_bonds_data", "analyze_news"],
    },
    {
        "command": "/factor-screen",
        "name": "Factor Screen",
        "category": "quant",
        "description": "Factor-based stock screening (value, momentum, quality, etc.)",
        "args": "<factor_type>",
        "prompt": (
            "Screen US stocks using {factor} factor strategy:\n"
            "1. Define the factor criteria and thresholds\n"
            "2. Top 10 stocks ranking highest on {factor}\n"
            "3. For each: symbol, score, key metrics, sector\n"
            "4. Historical factor performance: how has {factor} performed\n"
            "5. Current factor environment: is {factor} in favor?\n"
            "6. Combined multi-factor overlay suggestion"
        ),
        "tools_hint": ["calculate_factors", "get_alpha158_factors", "get_market_data"],
    },
    {
        "command": "/crypto-scan",
        "name": "Crypto Scanner",
        "category": "crypto",
        "description": "Cryptocurrency market scan with top movers and DeFi trends",
        "args": "[focus]",
        "prompt": (
            "Scan the cryptocurrency market:\n"
            "1. BTC and ETH: price, trend, dominance, key levels\n"
            "2. Top 5 gainers and top 5 losers (24h)\n"
            "3. Market sentiment: Fear & Greed index, funding rates\n"
            "4. DeFi and Layer-2 highlights\n"
            "5. Upcoming catalysts: halvings, upgrades, token unlocks\n"
            "6. Trading opportunities with risk levels\n"
            "{extra}"
        ),
        "tools_hint": ["get_crypto_data", "analyze_news"],
    },
    {
        "command": "/backtest-report",
        "name": "Backtest Report",
        "category": "quant",
        "description": "Run and analyze a strategy backtest with detailed metrics",
        "args": "<strategy> <symbol> [start] [end]",
        "prompt": (
            "Run a detailed backtest of '{strategy}' strategy on {symbol} from {start} to {end}:\n"
            "1. Performance summary: total return, annualized, Sharpe, Sortino\n"
            "2. Risk metrics: max drawdown, VaR, downside deviation\n"
            "3. Trade analysis: win rate, avg win/loss, profit factor\n"
            "4. Monthly returns breakdown\n"
            "5. Comparison vs buy-and-hold and benchmark (SPY)\n"
            "6. Optimization suggestions: parameter sensitivity"
        ),
        "tools_hint": ["backtest_strategy", "get_market_data", "get_risk_metrics"],
    },
    {
        "command": "/watchlist-scan",
        "name": "Watchlist Scan",
        "category": "tools",
        "description": "Scan all watchlist stocks for signals and alerts",
        "args": "",
        "prompt": (
            "Scan my watchlist ({symbols}) and for each stock provide:\n"
            "1. Current price and daily change\n"
            "2. Technical signal: Buy/Sell/Hold based on key indicators\n"
            "3. Any earnings or events upcoming\n"
            "4. News sentiment (positive/neutral/negative)\n"
            "5. Overall alert level: Green/Yellow/Red\n"
            "Sort by urgency of action needed."
        ),
        "tools_hint": ["get_market_data", "analyze_news"],
    },
    {
        "command": "/gen-strategy",
        "name": "Generate Strategy Code",
        "category": "code",
        "description": "Generate complete Python trading strategy code",
        "args": "<strategy_type> [symbol]",
        "prompt": (
            "Generate a complete, production-ready Python backtrader trading strategy.\n"
            "Strategy type: {strategy}\n"
            "Target symbol: {symbol}\n\n"
            "Requirements:\n"
            "1. Full backtrader Strategy class with __init__, next, notify_order\n"
            "2. Proper indicator initialization (use bt.indicators)\n"
            "3. Entry/exit logic with clear conditions\n"
            "4. Position sizing (percent sizer or fixed)\n"
            "5. Risk management: stop-loss and take-profit\n"
            "6. Logging via self.log()\n"
            "7. Complete cerebro setup code at the bottom\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "Include inline comments explaining the logic."
        ),
        "tools_hint": ["recommend_strategy", "backtest_strategy"],
    },
    {
        "command": "/gen-analysis",
        "name": "Generate Analysis Script",
        "category": "code",
        "description": "Generate a Python analysis/visualization script",
        "args": "<topic> [symbols...]",
        "prompt": (
            "Generate a Python script for financial analysis and visualization.\n"
            "Topic: {topic}\n"
            "Symbols: {symbols}\n\n"
            "Requirements:\n"
            "1. Use pandas, numpy, matplotlib/plotly, yfinance\n"
            "2. Fetch real market data with yfinance\n"
            "3. Compute relevant metrics/indicators\n"
            "4. Create informative charts/plots\n"
            "5. Print a summary table of key findings\n"
            "6. Include error handling for data fetching\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "Include inline comments."
        ),
        "tools_hint": ["get_market_data", "calculate_factors"],
    },
    {
        "command": "/gen-bot",
        "name": "Generate Trading Bot",
        "category": "code",
        "description": "Generate live trading bot with exchange API (ccxt)",
        "args": "<exchange> <strategy>",
        "prompt": (
            "Generate a Python trading bot for live execution.\n"
            "Exchange: {exchange}\n"
            "Strategy: {strategy}\n\n"
            "Requirements:\n"
            "1. Use ccxt library for exchange connection\n"
            "2. Market data fetching and order execution\n"
            "3. Signal generation based on the strategy logic\n"
            "4. Risk management: max position size, daily loss limit\n"
            "5. Logging with timestamps\n"
            "6. Graceful shutdown handling (SIGINT)\n"
            "7. Configuration via environment variables (API keys)\n"
            "8. Paper trading mode toggle\n\n"
            "Return ONLY the Python code wrapped in ```python``` fences. "
            "NEVER include actual API keys. Use env vars."
        ),
        "tools_hint": ["recommend_strategy"],
    },
    {
        "command": "/orcl-deep",
        "name": "Oracle Corp Deep Dive",
        "category": "analysis",
        "description": "Full multi-factor analysis of Oracle Corporation (ORCL)",
        "args": "",
        "prompt": (
            "Perform a comprehensive analysis of Oracle Corporation (ORCL):\n"
            "1. Technical: trend, RSI, MACD, Bollinger Bands, key support/resistance\n"
            "2. Fundamental: cloud ARR growth, OCI revenue, margins, PE vs SAP/NOW/MSFT\n"
            "3. AI infrastructure thesis: Oracle's GPU cluster deals (xAI, OpenAI, Meta)\n"
            "4. Competitive moat: Autonomous DB, ERP lock-in, Cerner healthcare\n"
            "5. Balance sheet: debt from cloud capex, FCF generation, buyback pace\n"
            "6. Risks & catalysts: cloud transition pace, FX, Oracle DB migration risk\n"
            "7. Price target range (bull/base/bear) and conviction score"
        ),
        "tools_hint": ["get_market_data", "calculate_factors", "analyze_news", "get_risk_metrics"],
    },
    {
        "command": "/train-status",
        "name": "Training Status",
        "category": "tools",
        "description": "Check Aria model training and data pipeline status",
        "args": "",
        "prompt": (
            "Check the current Aria model training status.\n"
            "1. Locate the project root via the ARIA_PROJECT_ROOT environment variable, or "
            "search upward from the current directory for a 'packages/ml/llm/training' folder.\n"
            "2. List checkpoint directories inside 'packages/ml/llm/training/outputs/' "
            "(any subdirectory containing 'trainer_state.json').\n"
            "3. Read the latest checkpoint's trainer_state.json: report current step, "
            "total steps, epoch, eval_loss, and best_model_checkpoint.\n"
            "4. Check for model_versions.json in the training outputs and report the "
            "currently deployed version if present.\n"
            "5. List recent training data files under 'data/training/' (newest 5 files).\n"
            "Summarize: training progress (step/total, %), eval_loss trend, "
            "deployed version, and data pipeline status."
        ),
        "tools_hint": ["read_file", "list_files"],
    },
]


# ============================================================================
# Local Tool System — Claude Code-style file operations
# ============================================================================

import subprocess
import glob as glob_module
import re
import re as re_module
import difflib


def _is_safe_path(resolved: pathlib.Path) -> bool:
    """Return True if the resolved path is inside an allowed root directory.

    Allowed roots: home directory, /tmp, /var/folders (macOS temp).
    Blocks: /etc, /sys, /proc, /dev, and any path that resolves through a
    symlink to outside those roots (symlink traversal prevention).
    """
    home = pathlib.Path.home().resolve()
    allowed = [home, pathlib.Path("/tmp").resolve()]
    # macOS temp directories (resolve both with and without /private prefix)
    for tmp_candidate in ["/var/folders", "/private/tmp", "/private/var/folders",
                          "/var/tmp", "/private/var/tmp"]:
        try:
            allowed.append(pathlib.Path(tmp_candidate).resolve())
        except Exception:
            pass
    # Also include the system temp dir at runtime
    import tempfile as _tempfile
    try:
        allowed.append(pathlib.Path(_tempfile.gettempdir()).resolve())
    except Exception:
        pass

    blocked_prefixes = [
        pathlib.Path("/etc"),
        pathlib.Path("/sys"),
        pathlib.Path("/proc"),
        pathlib.Path("/dev"),
        pathlib.Path("/boot"),
        pathlib.Path("/root"),
    ]
    for blocked in blocked_prefixes:
        try:
            resolved.relative_to(blocked.resolve())
            return False
        except ValueError:
            pass
    for root in allowed:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            pass
    # Also allow current working directory and its subtree
    try:
        resolved.relative_to(pathlib.Path.cwd().resolve())
        return True
    except ValueError:
        pass
    return False


def _tool_read_file(params: dict) -> dict:
    """Read file contents with optional line range."""
    path = params.get("path", "")
    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "error": f"File not found: {p}"}
        if not p.is_file():
            return {"success": False, "error": f"Not a file: {p}"}
        if not _is_safe_path(p):
            return {"success": False, "error": f"Access denied: path '{p}' is outside allowed directories"}
        _MAX_FILE_BYTES = 2_000_000   # 2 MB hard limit
        _LARGE_FILE_DEFAULT_LINES = 500  # auto-cap for files > 500 KB

        if p.stat().st_size > _MAX_FILE_BYTES:
            return {"success": False, "error": f"File too large: {p.stat().st_size:,} bytes (max 2 MB). Use offset/limit parameters to read sections."}
        content = p.read_text(errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)
        offset = params.get("offset", 0)
        limit = params.get("limit", 0)
        # Auto-limit large files when no range specified
        if not offset and not limit and p.stat().st_size > 500_000:
            limit = _LARGE_FILE_DEFAULT_LINES
        if offset or limit:
            end = offset + limit if limit else total_lines
            lines = lines[offset:end]
            content = "\n".join(f"{i+offset+1:4d}│ {l}" for i, l in enumerate(lines))
            if limit and end < total_lines:
                content += f"\n... [{len(lines)} of {total_lines} lines shown — use offset/limit to read more]"
        else:
            content = "\n".join(f"{i+1:4d}│ {l}" for i, l in enumerate(lines))
        return {"success": True, "data": {
            "path": str(p), "lines": len(lines),
            "content": content[:30000]
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _strip_markdown_fences(content: str) -> str:
    """Strip markdown code fences that LLMs sometimes wrap around file content."""
    stripped = content.strip()
    # Check for opening fence: ```python, ```py, ```javascript, ```json, etc.
    if stripped.startswith("```"):
        first_nl = stripped.find("\n")
        if first_nl >= 0:
            stripped = stripped[first_nl + 1:]
        else:
            return content  # Just ``` with no content
    # Check for closing fence
    if stripped.rstrip().endswith("```"):
        stripped = stripped.rstrip()[:-3].rstrip()
    # Only return stripped version if we actually removed fences
    if stripped != content.strip():
        return stripped + "\n"  # Ensure trailing newline
    return content


def _auto_fix_python(content: str, path: str) -> str:
    """Auto-fix common Python issues before writing. Harness-level intelligence."""
    if not path.endswith(".py"):
        return content
    lines = content.split("\n")
    imports_present = set()
    first_non_comment = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith('"""') and not stripped.startswith("'''"):
            first_non_comment = i
            break
    # Scan existing imports
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            # Extract module name
            if stripped.startswith("import "):
                mod = stripped.split()[1].split(".")[0].split(",")[0]
                imports_present.add(mod)
            elif stripped.startswith("from "):
                mod = stripped.split()[1].split(".")[0]
                imports_present.add(mod)
    # Detect needed imports by scanning code usage
    code_text = content
    needed = []
    if "os.path" in code_text or "os.expanduser" in code_text or "os.getcwd" in code_text or "os.makedirs" in code_text:
        if "os" not in imports_present:
            needed.append("import os")
    if "sys." in code_text or "sys.exit" in code_text:
        if "sys" not in imports_present:
            needed.append("import sys")
    if "np." in code_text and "numpy" not in imports_present and "np" not in imports_present:
        needed.append("import numpy as np")
    if "pd." in code_text and "pandas" not in imports_present and "pd" not in imports_present:
        needed.append("import pandas as pd")
    if "yf." in code_text and "yfinance" not in imports_present and "yf" not in imports_present:
        needed.append("import yfinance as yf")
    # matplotlib.use('Agg') must come before matplotlib.pyplot
    has_plt = "plt." in code_text
    has_matplotlib_use = "matplotlib.use" in code_text
    if has_plt and "matplotlib" not in imports_present:
        needed.append("import matplotlib; matplotlib.use('Agg')")
        needed.append("import matplotlib.pyplot as plt")
    elif has_plt and not has_matplotlib_use:
        # matplotlib imported but use('Agg') missing — inject before pyplot import
        for i, line in enumerate(lines):
            if "import matplotlib.pyplot" in line and "matplotlib.use" not in "\n".join(lines[:i]):
                lines.insert(i, "import matplotlib; matplotlib.use('Agg')")
                content = "\n".join(lines)
                break
    if "mpf." in code_text and "mplfinance" not in imports_present and "mpf" not in imports_present:
        needed.append("import mplfinance as mpf")
    if "re." in code_text and "re" not in imports_present:
        needed.append("import re")
    if "json." in code_text and "json" not in imports_present:
        needed.append("import json")
    if "datetime" in code_text and "datetime" not in imports_present:
        needed.append("from datetime import datetime, timedelta")
    if (re_module.search(r'\bta\.(?:sma|ema|rsi|macd|bbands|stoch|atr|adx|obv|vwap)\b', code_text) or "pandas_ta" in code_text) and "ta" not in imports_present and "pandas_ta" not in imports_present:
        needed.append("import pandas_ta as ta")
    if (re_module.search(r'\bgo\.(?:Figure|Candlestick|Scatter|Bar|Heatmap|Layout|Table)', code_text) or "px." in code_text or "plotly" in code_text) and "plotly" not in imports_present:
        if "go.Figure" in code_text or "go.Candlestick" in code_text:
            needed.append("import plotly.graph_objects as go")
        if "px." in code_text:
            needed.append("import plotly.express as px")
        if "make_subplots" in code_text:
            needed.append("from plotly.subplots import make_subplots")
    if "scipy" in code_text and "scipy" not in imports_present:
        needed.append("import scipy")
    # Auto-inject warnings suppression for finance scripts (yfinance/pandas emit many warnings)
    has_warnings_in_needed = any("warnings" in n for n in needed)
    if ("yf." in code_text or "pd." in code_text) and "warnings" not in imports_present and not has_warnings_in_needed:
        needed.insert(0, "import warnings; warnings.filterwarnings('ignore')")
    elif "warnings" in code_text and "warnings" not in imports_present and not has_warnings_in_needed:
        needed.append("import warnings")
    if needed:
        # Insert missing imports after first non-comment line (or at top)
        insert_point = first_non_comment
        for imp in reversed(needed):
            lines.insert(insert_point, imp)
        content = "\n".join(lines)
    # Syntax validation
    try:
        import ast
        ast.parse(content)
    except SyntaxError as e:
        # Try common auto-fixes
        fixed = content
        # Fix: trailing comma in last function arg
        # Fix: unclosed parenthesis — can't auto-fix, just report
        if HAS_RICH:
            console.print(f"  [dim]Warning: syntax issue at line {e.lineno}: {e.msg}[/dim]")
    return content


def _write_policy_confirm(p: pathlib.Path, content: str, existed: bool) -> tuple:
    """Prompt user to confirm a write operation. Returns (approved: bool, final_path: Path).

    Shows:
    - For overwrites: diff summary (lines added/removed)
    - For new files: path + line count
    Allows user to approve (y), deny (n), or redirect to a different path (r).
    """
    import difflib
    lines_new = content.count("\n") + 1
    desktop = pathlib.Path.home() / "Desktop"
    is_desktop = str(p).startswith(str(desktop))

    if HAS_RICH:
        console.print()
        if existed:
            old_content = p.read_text(errors="replace")
            diff = list(difflib.unified_diff(
                old_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"current/{p.name}",
                tofile=f"new/{p.name}",
                n=2,
            ))
            added   = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
            console.print(f"  [yellow]⚠ Overwrite[/yellow]  [bold]{p}[/bold]")
            console.print(f"  [dim]  +{added} lines  -{removed} lines  ({lines_new} total)[/dim]")
            # Show first 8 diff lines as preview
            for line in diff[:8]:
                if line.startswith("+") and not line.startswith("+++"):
                    console.print(f"  [green]{line.rstrip()}[/green]")
                elif line.startswith("-") and not line.startswith("---"):
                    console.print(f"  [red]{line.rstrip()}[/red]")
        else:
            loc = "[dim cyan](Desktop)[/dim cyan]" if is_desktop else "[yellow](outside Desktop)[/yellow]"
            console.print(f"  [cyan]New file[/cyan] {loc}  [bold]{p}[/bold]  ({lines_new} lines)")
        console.print()
        choice = console.input("  [bold]Write this file?[/bold] [dim]\\[y/n/r=redirect path][/dim] ").strip().lower()
    else:
        print()
        print(f"  {'Overwrite' if existed else 'New file'}: {p}  ({lines_new} lines)")
        choice = input("  Write this file? [y/n/r=redirect path] ").strip().lower()

    if choice == "r":
        if HAS_RICH:
            new_path_str = console.input("  [dim]Enter new path: [/dim]").strip()
        else:
            new_path_str = input("  Enter new path: ").strip()
        if new_path_str:
            new_p = pathlib.Path(new_path_str).expanduser().resolve()
            if _is_safe_path(new_p):
                return True, new_p
            if HAS_RICH:
                console.print(f"  [red]Path not allowed: {new_p}[/red]")
            else:
                print(f"  Path not allowed: {new_p}")
        return False, p

    return choice in ("y", "yes", ""), p


def _tool_write_file(params: dict) -> dict:
    """Write content to a file (create or overwrite)."""
    path = params.get("path", "")
    content = params.get("content", "")
    skip_confirm = params.get("_skip_confirm", False)  # internal flag for scaffold
    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    if not content:
        return {"success": False, "error": "Missing 'content' parameter"}
    # Auto-strip markdown code fences from content
    content = _strip_markdown_fences(content)
    # Reject placeholder / obviously invalid content
    stripped_check = content.strip()
    if len(stripped_check) < 20:
        return {"success": False,
                "error": f"Content too short ({len(stripped_check)} chars). "
                "You must write the COMPLETE script code, not a placeholder. "
                "Write the full Python code with all imports, logic, and output."}
    # Detect XML/HTML-like placeholder tags — only flag SHORT single-tag content.
    # A real HTML file starting with <!DOCTYPE html> is valid even if it has no newlines.
    if (stripped_check.startswith("<") and stripped_check.endswith(">")
            and "\n" not in stripped_check and len(stripped_check) < 200
            and not stripped_check.lower().startswith("<!doctype")
            and not stripped_check.lower().startswith("<html")):
        return {"success": False,
                "error": f"Content appears to be a placeholder tag: '{stripped_check[:120]}'. "
                "You must write the ACTUAL code, not a tag or placeholder. "
                "Write the complete script with imports, data fetching, computation, and output."}
    # Auto-fix Python: inject missing imports, validate syntax
    content = _auto_fix_python(content, path)
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not _is_safe_path(p):
            return {"success": False, "error": f"Access denied: path '{p}' is outside allowed directories"}

        existed = p.exists()
        desktop = pathlib.Path.home() / "Desktop"
        is_desktop = str(p).startswith(str(desktop))
        # Paths that never need confirmation (tmpdir, Desktop, session dirs)
        import tempfile as _tf
        _auto_trusted_prefixes = (
            str(desktop),
            str(pathlib.Path(_tf.gettempdir()).resolve()),
            "/tmp", "/private/tmp", "/private/var/folders",
            str(CONFIG_DIR), str(SESSIONS_DIR),
        )
        is_auto_trusted = any(str(p).startswith(pfx) for pfx in _auto_trusted_prefixes)
        policy = _ACTIVE_WRITE_POLICY[0]

        # Determine if confirmation is required:
        # - auto-trusted paths (Desktop, tmpdir, config dirs): never confirm
        # - always_confirm: confirm everything else
        # - confirm_outside: confirm new/overwrite outside Desktop
        # - desktop_only: confirm new files outside Desktop; also confirm overwrites outside trusted
        needs_confirm = (
            not skip_confirm
            and not is_auto_trusted
            and (
                policy == "always_confirm"
                or policy in ("desktop_only", "confirm_outside")
                or existed  # overwrite outside auto-trusted paths
            )
        )

        if needs_confirm:
            approved, p = _write_policy_confirm(p, content, existed)
            if not approved:
                return {"success": False, "error": "Write cancelled by user.",
                        "data": {"cancelled": True}}

        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        lines = content.count("\n") + 1
        action = "Updated" if existed else "Created"
        if HAS_RICH:
            console.print(f"  [dim]{action} {p} ({lines} lines)[/dim]")
        else:
            print(f"  {action} {p} ({lines} lines)")
        try:
            _size_bytes = p.stat().st_size
        except Exception:
            _size_bytes = len(content.encode("utf-8"))
        return {"success": True, "data": {
            "path": str(p), "action": action.lower(),
            "lines": lines, "size_bytes": _size_bytes,
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_edit_file(params: dict) -> dict:
    """Edit file by replacing old_string with new_string."""
    path = params.get("path", "")
    old_str = params.get("old_string", params.get("old_str", ""))
    new_str = params.get("new_string", params.get("new_str", ""))
    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    if not old_str:
        return {"success": False, "error": "Missing 'old_string' parameter"}
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "error": f"File not found: {p}"}
        if not _is_safe_path(p):
            return {"success": False, "error": f"Access denied: path '{p}' is outside allowed directories"}
        content = p.read_text(errors="replace")
        count = content.count(old_str)
        if count == 0:
            # Show first few lines of actual file content to help model fix the old_string
            preview = "\n".join(content.splitlines()[:10])
            return {"success": False,
                    "error": f"old_string not found in file. "
                    f"The file starts with:\n{preview}\n\n"
                    f"HINT: Use read_file to see the actual content, then retry edit_file with the correct old_string. "
                    f"Or use write_file to overwrite the entire file."}
        new_content = content.replace(old_str, new_str, 1)
        p.write_text(new_content)
        added = len(new_str.splitlines())
        removed = len(old_str.splitlines())
        if HAS_RICH:
            summary = []
            if added > 0:
                summary.append(f"[green]+{added}[/green]")
            if removed > 0:
                summary.append(f"[red]-{removed}[/red]")
            console.print(f"  [dim]Applied ({', '.join(summary)} lines)[/dim]")
        else:
            print(f"  Applied (+{added}, -{removed} lines)")
        return {"success": True, "data": {
            "path": str(p), "replacements": 1,
            "lines": new_content.count("\n") + 1
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_list_files(params: dict) -> dict:
    """List files in a directory, optionally matching a glob pattern."""
    path = params.get("path", ".")
    pattern = params.get("pattern", "*")
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "error": f"Path not found: {p}"}
        if p.is_file():
            return _tool_read_file({"path": str(p)})
        matches = sorted(p.glob(pattern))[:100]
        items = []
        for m in matches:
            rel = m.relative_to(p) if m.is_relative_to(p) else m
            kind = "dir" if m.is_dir() else "file"
            size = m.stat().st_size if m.is_file() else 0
            items.append({"name": str(rel), "type": kind, "size": size})
        return {"success": True, "data": {
            "path": str(p), "pattern": pattern,
            "count": len(items), "items": items
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_search_code(params: dict) -> dict:
    """Search for a pattern in files (like grep)."""
    pattern = params.get("pattern", "")
    path = params.get("path", ".")
    file_glob = params.get("glob", "**/*.py")
    if not pattern:
        return {"success": False, "error": "Missing 'pattern' parameter"}
    try:
        p = pathlib.Path(path).expanduser().resolve()
        matches = []
        regex = re_module.compile(pattern, re_module.IGNORECASE)
        for fpath in sorted(p.glob(file_glob))[:200]:
            if not fpath.is_file() or fpath.stat().st_size > 5_000_000:  # 5 MB search limit
                continue
            try:
                lines = fpath.read_text(errors="replace").splitlines()
                for i, line in enumerate(lines, 1):
                    if regex.search(line):
                        matches.append({
                            "file": str(fpath.relative_to(p) if fpath.is_relative_to(p) else fpath),
                            "line": i,
                            "content": line.strip()[:200],
                        })
                        if len(matches) >= 50:
                            break
            except Exception:
                continue
            if len(matches) >= 50:
                break
        return {"success": True, "data": {
            "pattern": pattern, "path": str(p),
            "count": len(matches), "matches": matches
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_run_command(params: dict) -> dict:
    """Run a shell command and return output."""
    command = params.get("command", "")
    if not command:
        return {"success": False, "error": "Missing 'command' parameter"}

    # If the user explicitly approved this command in the confirmation picker,
    # use "balanced" as the effective policy so medium-risk commands are not
    # double-blocked after the user already said yes.
    effective_policy = params.get("policy", "safe")
    if params.get("user_approved") and effective_policy == "safe":
        effective_policy = "balanced"

    decision = evaluate_command_policy(command, effective_policy)
    command = decision.normalized_command

    # Safety: always block truly dangerous commands regardless of approval
    dangerous = ["rm -rf /", "mkfs", "dd if=", "> /dev/", ":(){ :", "fork bomb"]
    for d in dangerous:
        if d in command:
            return {"success": False, "error": f"Blocked dangerous command: {command}"}

    if params.get("dry_run"):
        return {"success": True, "data": {"command": command, "risk": decision.risk, "policy": decision.policy, "dry_run": True}}
    if not decision.allowed:
        return {"success": False, "error": decision.reason}
    try:
        cwd = params.get("cwd", None)
        timeout = min(params.get("timeout", 120), 300)
        use_shell = True
        argv = None
        # For low-risk commands, prefer argv execution to reduce shell injection surface.
        if decision.risk == "low":
            has_shell_meta = any(ch in command for ch in ["|", "&", ";", "<", ">", "$", "`", "\n"])
            if not has_shell_meta:
                try:
                    argv = shlex.split(command)
                    if argv:
                        use_shell = False
                except ValueError:
                    use_shell = True
                    argv = None

        result = subprocess.run(
            argv if (argv and not use_shell) else command,
            shell=use_shell,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        output = result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout
        stderr = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr

        # ---- HARNESS-LEVEL AUTO-FIX (v3.8: multi-round, up to 3 retries) ----
        # If python3 script failed, try to auto-fix common errors and re-run
        MAX_AUTO_FIX_ROUNDS = 3
        if result.returncode != 0 and command.strip().startswith("python3 "):
            script_path = command.strip().split("python3 ", 1)[1].strip().split()[0]
            script_p = pathlib.Path(script_path).expanduser().resolve()

            for _fix_round in range(MAX_AUTO_FIX_ROUNDS):
                combined_err = (output + " " + stderr).strip()
                auto_fixed = False

                if not (script_p.exists() and script_p.suffix == ".py"):
                    break
                script_content = script_p.read_text(errors="replace")

                # Auto-fix: NameError — missing import
                name_match = re_module.search(r"NameError: name ['\"](\w+)['\"] is not defined", combined_err)
                if name_match and not auto_fixed:
                    missing = name_match.group(1)
                    import_map = {
                        "os": "import os", "sys": "import sys", "re": "import re",
                        "json": "import json", "math": "import math", "time": "import time",
                        "np": "import numpy as np", "pd": "import pandas as pd",
                        "yf": "import yfinance as yf", "plt": "import matplotlib.pyplot as plt",
                        "mpf": "import mplfinance as mpf", "datetime": "from datetime import datetime, timedelta",
                        "Path": "from pathlib import Path", "timedelta": "from datetime import datetime, timedelta",
                        "go": "import plotly.graph_objects as go", "px": "import plotly.express as px",
                        "ta": "import pandas_ta as ta", "warnings": "import warnings",
                        "make_subplots": "from plotly.subplots import make_subplots",
                        "bt": "import backtrader as bt", "vbt": "import vectorbt as vbt",
                        "ccxt": "import ccxt", "requests": "import requests",
                        "BeautifulSoup": "from bs4 import BeautifulSoup",
                        "tqdm": "from tqdm import tqdm",
                        "xgb": "import xgboost as xgb",
                        "Prophet": "from prophet import Prophet",
                        "arch": "from arch import arch_model",
                        "statsmodels": "import statsmodels.api as sm",
                        "sm": "import statsmodels.api as sm",
                    }
                    fix_import = import_map.get(missing)
                    if fix_import and fix_import not in script_content:
                        lines = script_content.split("\n")
                        insert_at = 0
                        for i, l in enumerate(lines):
                            if l.strip().startswith("#!") or l.strip().startswith("# -*-"):
                                insert_at = i + 1
                            else:
                                break
                        lines.insert(insert_at, fix_import)
                        if missing == "plt" and "matplotlib.use" not in script_content:
                            lines.insert(insert_at, "import matplotlib; matplotlib.use('Agg')")
                        script_p.write_text("\n".join(lines))
                        auto_fixed = True
                        if HAS_RICH:
                            console.print(f"  [#C08050]Auto-fix[{_fix_round+1}/{MAX_AUTO_FIX_ROUNDS}]:[/#C08050] [dim]added '{fix_import}'[/dim]")

                # Auto-fix: matplotlib.use() ordering
                if not auto_fixed and ("cannot be resolved at runtime" in combined_err.lower() or
                   ("matplotlib" in combined_err and "backend" in combined_err.lower())):
                    if "matplotlib.use" not in script_content and "matplotlib.pyplot" in script_content:
                        script_content = script_content.replace(
                            "import matplotlib.pyplot as plt",
                            "import matplotlib; matplotlib.use('Agg')\nimport matplotlib.pyplot as plt"
                        )
                        script_p.write_text(script_content)
                        auto_fixed = True
                        if HAS_RICH:
                            console.print(f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050] [dim]added matplotlib.use('Agg')[/dim]")

                # Auto-fix: yfinance MultiIndex KeyError
                key_match = re_module.search(r"KeyError: ['\"]?(Close|Open|High|Low|Volume|Adj Close)", combined_err)
                if key_match and not auto_fixed and "yfinance" in script_content:
                    if "columns.droplevel" not in script_content:
                        fix_line = (
                            "\n# Fix yfinance MultiIndex columns\n"
                            "if isinstance(df.columns, pd.MultiIndex):\n"
                            "    df.columns = df.columns.droplevel(1)\n"
                        )
                        dl_match = re_module.search(r'(.*=\s*yf\.download\([^)]+\))', script_content)
                        if dl_match:
                            script_content = script_content.replace(
                                dl_match.group(0), dl_match.group(0) + fix_line
                            )
                            script_p.write_text(script_content)
                            auto_fixed = True
                            if HAS_RICH:
                                console.print(f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050] [dim]MultiIndex column fix[/dim]")

                # Auto-fix: AttributeError common patterns
                attr_match = re_module.search(r"AttributeError: '(\w+)' object has no attribute '(\w+)'", combined_err)
                if attr_match and not auto_fixed:
                    obj_type, attr_name = attr_match.group(1), attr_match.group(2)
                    # DataFrame.append → pd.concat
                    if obj_type == "DataFrame" and attr_name == "append":
                        script_content = re_module.sub(
                            r'(\w+)\.append\(([^)]+)\)',
                            r'pd.concat([\1, \2], ignore_index=True)',
                            script_content
                        )
                        script_p.write_text(script_content)
                        auto_fixed = True
                        if HAS_RICH:
                            console.print(f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050] [dim]DataFrame.append→pd.concat[/dim]")

                # Auto-fix: TypeError common patterns (e.g., yfinance parameter changes)
                if not auto_fixed and "TypeError" in combined_err:
                    # yfinance: auto_adjust parameter removed in newer versions
                    if "auto_adjust" in combined_err and "auto_adjust" in script_content:
                        script_content = re_module.sub(r',\s*auto_adjust\s*=\s*(True|False)', '', script_content)
                        script_p.write_text(script_content)
                        auto_fixed = True
                        if HAS_RICH:
                            console.print(f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050] [dim]removed deprecated auto_adjust param[/dim]")

                # Auto-fix: ModuleNotFoundError — auto pip3 install
                mod_match = re_module.search(r"No module named ['\"]?(\w+)", combined_err)
                if mod_match and not auto_fixed:
                    missing_mod = mod_match.group(1)
                    pip_map = {
                        "mplfinance": "mplfinance", "plotly": "plotly",
                        "pandas_ta": "pandas_ta", "ta": "ta",
                        "sklearn": "scikit-learn", "cv2": "opencv-python",
                        "bs4": "beautifulsoup4", "PIL": "Pillow",
                        "backtrader": "backtrader", "vectorbt": "vectorbt",
                        "ccxt": "ccxt", "prophet": "prophet",
                        "arch": "arch", "xgboost": "xgboost",
                        "lightgbm": "lightgbm", "statsmodels": "statsmodels",
                        "akshare": "akshare", "tushare": "tushare",
                        "empyrical": "empyrical", "pyfolio": "pyfolio",
                        "seaborn": "seaborn", "openpyxl": "openpyxl",
                    }
                    pip_pkg = pip_map.get(missing_mod, missing_mod)
                    if HAS_RICH:
                        console.print(f"  [#C08050]Auto-fix[{_fix_round+1}]:[/#C08050] [dim]pip3 install {pip_pkg}[/dim]")
                    pip_result = subprocess.run(
                        f"pip3 install {pip_pkg}", shell=True, capture_output=True,
                        text=True, timeout=60
                    )
                    if pip_result.returncode == 0:
                        auto_fixed = True

                # If auto-fixed, re-run and check again (loop continues)
                if auto_fixed:
                    if HAS_RICH:
                        console.print(f"  [dim]Re-running after auto-fix (round {_fix_round+1}/{MAX_AUTO_FIX_ROUNDS})...[/dim]")
                    result = subprocess.run(
                        command, shell=True, capture_output=True, text=True,
                        timeout=timeout, cwd=cwd,
                    )
                    output = result.stdout[-5000:] if len(result.stdout) > 5000 else result.stdout
                    stderr = result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr
                    if result.returncode == 0:
                        break  # Success! Exit fix loop
                else:
                    break  # No fix found, stop trying
        # ---- END AUTO-FIX ----

        if HAS_RICH:
            if result.returncode == 0:
                console.print(f"  [green]Command completed[/green] [dim](exit {result.returncode})[/dim]")
            else:
                console.print(f"  [dim]Command exited {result.returncode}[/dim]")
            # Show stdout/stderr preview
            out_preview = output.strip().splitlines()[:6]
            for ol in out_preview:
                console.print(f"    [dim]{ol[:120]}[/dim]")
            if len(output.strip().splitlines()) > 6:
                console.print(f"    [dim]...truncated[/dim]")
            if stderr.strip() and result.returncode != 0:
                for el in stderr.strip().splitlines()[:3]:
                    console.print(f"    [red]{el[:120]}[/red]")
        else:
            print(f"  Command exit: {result.returncode}")
        return {"success": True, "data": {
            "command": command, "exit_code": result.returncode,
            "stdout": output, "stderr": stderr,
        }}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Command timed out ({timeout}s)"}
    except KeyboardInterrupt:
        if HAS_RICH:
            console.print(f"  [dim]Command interrupted[/dim]")
        return {"success": False, "error": "Command interrupted by user (Ctrl+C)"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ─── Extended tools: web_fetch, github, glob, notebook, multi_edit ────────────

def _tool_web_fetch(params: dict) -> dict:
    """Fetch the text content of any URL (web page, GitHub raw, docs, APIs).

    Returns cleaned plain-text suitable for LLM context.
    """
    url = params.get("url", "").strip()
    if not url:
        return {"success": False, "error": "Missing 'url' parameter"}
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    max_chars = min(int(params.get("max_chars", 12000)), 40000)
    timeout   = min(int(params.get("timeout", 15)), 30)
    try:
        import urllib.request as _ur, ssl as _ssl, re as _re
        _prx = _ur.getproxies()
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
        }
        # GitHub: convert HTML page URLs to raw.githubusercontent.com for cleaner text
        _gh = _re.match(
            r"https://github\.com/([^/]+/[^/]+)/blob/([^?#]+)", url
        )
        if _gh:
            url = f"https://raw.githubusercontent.com/{_gh.group(1)}/{_gh.group(2)}"

        import requests as _req
        s = _req.Session()
        s.proxies = _prx
        s.verify = False
        r = s.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        raw = r.text

        # Detect if JSON
        ct = r.headers.get("content-type", "")
        if "json" in ct or raw.lstrip().startswith(("{", "[")):
            return {"success": True, "data": {
                "url": url, "content_type": ct,
                "text": raw[:max_chars], "length": len(raw),
            }}

        # Strip HTML → plain text
        text = _re.sub(r"<script[^>]*>.*?</script>", " ", raw, flags=_re.DOTALL | _re.I)
        text = _re.sub(r"<style[^>]*>.*?</style>",   " ", text, flags=_re.DOTALL | _re.I)
        text = _re.sub(r"<[^>]+>",     " ", text)
        text = _re.sub(r"&nbsp;",      " ", text)
        text = _re.sub(r"&amp;",       "&", text)
        text = _re.sub(r"&lt;",        "<", text)
        text = _re.sub(r"&gt;",        ">", text)
        text = _re.sub(r"&quot;",      '"', text)
        text = _re.sub(r"\s{3,}",      "\n", text)
        text = text.strip()

        return {"success": True, "data": {
            "url": url, "content_type": ct,
            "text": text[:max_chars], "length": len(text),
            "truncated": len(text) > max_chars,
        }}
    except Exception as e:
        return {"success": False, "error": f"web_fetch failed: {e}"}


def _tool_github(params: dict) -> dict:
    """GitHub API / gh CLI integration.

    actions:
      list_prs          - list open pull requests
      list_issues       - list open issues
      view_pr N         - view PR #N (title, body, diff summary)
      view_issue N      - view issue #N
      create_pr         - create a new PR (requires title, body, branch, base)
      list_commits [N]  - recent N commits (default 10)
      read_file path    - read a file from GitHub (owner/repo@ref:path)
      search q          - search code/issues on GitHub
    All read-only operations use gh CLI; create_pr also uses gh.
    """
    action = params.get("action", "list_prs").lower().replace("-", "_")
    cwd    = params.get("cwd") or None
    policy = "safe"

    # ── Helper: run gh CLI ─────────────────────────────────────────────
    def _gh(cmd: str, timeout: int = 20) -> dict:
        import shutil
        if not shutil.which("gh"):
            return {"success": False,
                    "error": "gh CLI not found. Install: brew install gh && gh auth login"}
        return _tool_run_command({"command": cmd, "cwd": cwd, "timeout": timeout,
                                   "policy": policy})

    # ── list_prs ───────────────────────────────────────────────────────
    if action in ("list_prs", "prs", "pull_requests"):
        state = params.get("state", "open")
        limit = int(params.get("limit", 20))
        return _gh(f"gh pr list --state {state} --limit {limit} --json number,title,author,state,headRefName,url")

    # ── list_issues ────────────────────────────────────────────────────
    if action in ("list_issues", "issues"):
        state = params.get("state", "open")
        limit = int(params.get("limit", 20))
        label = f' --label "{params["label"]}"' if params.get("label") else ""
        return _gh(f"gh issue list --state {state} --limit {limit}{label} --json number,title,author,state,labels,url")

    # ── view_pr ────────────────────────────────────────────────────────
    if action in ("view_pr", "pr"):
        number = params.get("number") or params.get("pr")
        if not number:
            return {"success": False, "error": "Missing 'number' parameter"}
        r = _gh(f"gh pr view {number} --json number,title,body,state,headRefName,baseRefName,additions,deletions,files,url")
        return r

    # ── view_issue ─────────────────────────────────────────────────────
    if action in ("view_issue", "issue"):
        number = params.get("number") or params.get("issue")
        if not number:
            return {"success": False, "error": "Missing 'number' parameter"}
        return _gh(f"gh issue view {number} --json number,title,body,state,labels,comments,url")

    # ── create_pr ─────────────────────────────────────────────────────
    if action == "create_pr":
        title  = params.get("title", "")
        body   = params.get("body", "")
        branch = params.get("branch", "")
        base   = params.get("base", "main")
        if not title:
            return {"success": False, "error": "Missing 'title' for create_pr"}
        b_flag = f"--head {shlex.quote(branch)}" if branch else ""
        cmd = (
            f"gh pr create --title {shlex.quote(title)} "
            f"--body {shlex.quote(body)} "
            f"--base {shlex.quote(base)} {b_flag}"
        )
        return _gh(cmd, timeout=30)

    # ── list_commits ───────────────────────────────────────────────────
    if action in ("list_commits", "commits", "log"):
        limit = int(params.get("limit", 10))
        return _gh(f"gh api repos/{{owner}}/{{repo}}/commits?per_page={limit} "
                   f"--jq '[.[] | {{sha: .sha[:7], message: .commit.message | split(\"\\n\")[0], author: .commit.author.name, date: .commit.author.date}}]'")

    # ── search ─────────────────────────────────────────────────────────
    if action == "search":
        q = params.get("q") or params.get("query", "")
        kind = params.get("kind", "code")  # code / issues / repos
        if not q:
            return {"success": False, "error": "Missing 'q' parameter"}
        return _gh(f"gh search {kind} {shlex.quote(q)} --limit 10 --json url,path,textMatches", timeout=15)

    # ── read_file (via raw.githubusercontent.com) ──────────────────────
    if action in ("read_file", "file"):
        ref = params.get("ref", "")       # e.g. "owner/repo@main:path/to/file"
        file_path = params.get("path", "")
        if ref:
            # Parse "owner/repo@ref:path" format
            import re as _re2
            m = _re2.match(r"([^@:]+)@([^:]+):(.+)", ref)
            if m:
                repo, branch, fp = m.groups()
                url = f"https://raw.githubusercontent.com/{repo}/{branch}/{fp}"
                return _tool_web_fetch({"url": url, "max_chars": 20000})
        if file_path:
            return _gh(f"gh api repos/{{owner}}/{{repo}}/contents/{file_path} --jq '.content' | base64 -d")
        return {"success": False, "error": "Provide 'ref' (owner/repo@branch:path) or 'path'"}

    # ── pr_diff ────────────────────────────────────────────────────────
    if action in ("pr_diff", "diff"):
        number = params.get("number") or params.get("pr")
        if not number:
            return {"success": False, "error": "Missing 'number' parameter"}
        return _gh(f"gh pr diff {number}", timeout=30)

    # ── pr_checks ─────────────────────────────────────────────────────
    if action in ("pr_checks", "checks", "ci"):
        number = params.get("number") or params.get("pr")
        return _gh(f"gh pr checks {number or ''}")

    return {"success": False, "error": f"Unknown GitHub action: '{action}'. "
            "Use: list_prs, list_issues, view_pr, view_issue, create_pr, list_commits, search, read_file, pr_diff, pr_checks"}


def _tool_glob(params: dict) -> dict:
    """Fast file-pattern search across the project.

    Returns a flat list of matching file paths, sorted.
    Supports ** recursive globs. Faster and more focused than list_files.
    """
    pattern = params.get("pattern", "**/*")
    root    = params.get("path", ".").strip() or "."
    limit   = min(int(params.get("limit", 200)), 1000)
    try:
        p = pathlib.Path(root).expanduser().resolve()
        if not p.is_dir():
            return {"success": False, "error": f"Directory not found: {p}"}
        results = sorted(
            str(fp.relative_to(p) if fp.is_relative_to(p) else fp)
            for fp in p.glob(pattern)
            if fp.is_file()
        )[:limit]
        return {"success": True, "data": {
            "pattern": pattern, "root": str(p),
            "count": len(results), "files": results,
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_notebook_read(params: dict) -> dict:
    """Read a Jupyter notebook (.ipynb) and return cells as text.

    Returns each cell's source + outputs formatted for LLM context.
    """
    path = params.get("path", "")
    if not path:
        return {"success": False, "error": "Missing 'path' parameter"}
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not p.exists():
            return {"success": False, "error": f"File not found: {p}"}
        if p.suffix != ".ipynb":
            return {"success": False, "error": f"Not a notebook: {p}"}
        if not _is_safe_path(p):
            return {"success": False, "error": f"Access denied: {p}"}
        nb = json.loads(p.read_text(errors="replace"))
        cells = nb.get("cells", [])
        lines = []
        for i, cell in enumerate(cells):
            ct   = cell.get("cell_type", "code")
            src  = "".join(cell.get("source", []))
            prefix = f"[Cell {i+1} | {ct}]"
            lines.append(f"{prefix}\n{src}")
            if ct == "code":
                for out in cell.get("outputs", []):
                    text = out.get("text") or out.get("data", {}).get("text/plain", [])
                    if isinstance(text, list):
                        text = "".join(text)
                    if text:
                        lines.append(f"  # Output: {text[:300].strip()}")
        content = "\n\n".join(lines)
        return {"success": True, "data": {
            "path": str(p), "cell_count": len(cells),
            "content": content[:30000],
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_notebook_edit(params: dict) -> dict:
    """Edit a cell in a Jupyter notebook by cell index.

    params: path, cell_index (0-based), new_source
    """
    path       = params.get("path", "")
    cell_index = int(params.get("cell_index", 0))
    new_source = params.get("new_source") or params.get("source", "")
    if not path:
        return {"success": False, "error": "Missing 'path'"}
    if not new_source:
        return {"success": False, "error": "Missing 'new_source'"}
    try:
        p = pathlib.Path(path).expanduser().resolve()
        if not _is_safe_path(p):
            return {"success": False, "error": f"Access denied: {p}"}
        nb = json.loads(p.read_text(errors="replace"))
        cells = nb.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return {"success": False, "error": f"Cell index {cell_index} out of range (0–{len(cells)-1})"}
        cells[cell_index]["source"] = [new_source]
        # Clear outputs for the edited cell
        if cells[cell_index].get("cell_type") == "code":
            cells[cell_index]["outputs"] = []
            cells[cell_index]["execution_count"] = None
        p.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n")
        return {"success": True, "data": {
            "path": str(p), "cell_index": cell_index,
            "message": f"Cell {cell_index} updated successfully",
        }}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _tool_get_market_data(params: dict) -> dict:
    """
    Fetch real-time market data (quote + technical indicators) for a stock/ETF/crypto.
    Supports A-shares (6-digit code), HK (.HK), US (AAPL), crypto (BTC).
    Primary: yfinance / MarketDataClient. Fallback: Finnhub.
    Returns structured data for LLM consumption.
    """
    symbol = str(params.get("symbol", "")).strip().upper()
    if not symbol:
        return {"success": False, "error": "symbol is required"}

    # ── 1. Quote ──────────────────────────────────────────────────────────────
    quote: dict = {"success": False, "error": "market data client unavailable"}
    if _HAS_MDC:
        import time as _t
        mdc = _get_mdc()
        for _att in range(3):
            try:
                quote = mdc.quote(symbol)
                if quote.get("success"):
                    break
                _e = str(quote.get("error", "")).lower()
                if ("rate" in _e or "429" in _e) and _att < 2:
                    _t.sleep(2 ** _att)
                    continue
                break
            except Exception as exc:
                _es = str(exc).lower()
                if ("rate" in _es or "429" in _es) and _att < 2:
                    _t.sleep(2 ** _att)
                    continue
                # Clean up raw Python exception strings
                _raw = str(exc)
                if "Connection aborted" in _raw or "RemoteDisconnected" in _raw:
                    quote = {"success": False, "error": "网络连接中断，请稍后重试"}
                elif "Connection refused" in _raw:
                    quote = {"success": False, "error": "连接被拒绝，数据服务暂时不可用"}
                elif "timeout" in _raw.lower():
                    quote = {"success": False, "error": "连接超时，请稍后重试"}
                else:
                    quote = {"success": False, "error": _raw}
                break

    # Finnhub fallback for US/global symbols
    if not quote.get("success"):
        _fh_key = _get_provider_key("finnhub")
        if _fh_key:
            try:
                import requests as _rq
                _r = _rq.get(
                    f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={_fh_key}",
                    timeout=6,
                )
                if _r.status_code == 200:
                    _fh = _r.json()
                    if _fh.get("c"):
                        _pc = _fh.get("pc") or _fh["c"]
                        quote = {
                            "success": True, "symbol": symbol,
                            "price": round(_fh["c"], 4),
                            "change_pct": round((_fh["c"] - _pc) / _pc * 100, 2) if _pc else 0,
                            "high": round(_fh.get("h", 0), 4),
                            "low": round(_fh.get("l", 0), 4),
                            "currency": "USD", "provider": "finnhub",
                        }
            except Exception:
                pass

    if not quote.get("success"):
        return quote  # propagate error with clean message

    result = {
        "success":    True,
        "symbol":     symbol,
        "name":       quote.get("name") or symbol,
        "price":      quote.get("price"),
        "change_pct": quote.get("change_pct"),
        "high":       quote.get("high"),
        "low":        quote.get("low"),
        "volume":     quote.get("volume"),
        "market_cap": quote.get("market_cap"),
        "currency":   quote.get("currency") or "USD",
        "provider":   quote.get("provider") or "market_data_client",
    }

    # ── 2. Technical indicators ───────────────────────────────────────────────
    ti: dict = {}
    if _HAS_MDC:
        try:
            ti = mdc.technical_indicators(symbol, days=120) or {}
        except Exception:
            ti = {}

    if not ti.get("success") or ti.get("rsi") is None:
        try:
            import yfinance as _yf, numpy as _np
            _yf_sym = symbol
            if symbol.isdigit() and len(symbol) == 6:
                _yf_sym = symbol + (".SS" if symbol.startswith("6") else ".SZ")
            _hist = _yf.Ticker(_yf_sym).history(period="6mo")
            if len(_hist) >= 20:
                _c = _hist["Close"]
                _v = _hist["Volume"]
                _d = _c.diff()
                _g = _d.clip(lower=0).rolling(14).mean()
                _l = (-_d.clip(upper=0)).rolling(14).mean()
                _rsi = float((100 - 100 / (1 + _g / _l.replace(0, _np.nan))).iloc[-1])
                _ema12 = _c.ewm(span=12).mean()
                _ema26 = _c.ewm(span=26).mean()
                _macd  = _ema12 - _ema26
                _mhist = float((_macd - _macd.ewm(span=9).mean()).iloc[-1])
                _ma20  = _c.rolling(20).mean()
                _std20 = _c.rolling(20).std()
                _ma60  = _c.rolling(60).mean() if len(_c) >= 60 else _ma20
                ti = {
                    "success":   True,
                    "rsi":       round(_rsi, 2) if not _np.isnan(_rsi) else None,
                    "macd_hist": round(_mhist, 4),
                    "ma20":      round(float(_ma20.iloc[-1]), 2),
                    "ma60":      round(float(_ma60.iloc[-1]), 2),
                    "bb_upper":  round(float((_ma20 + 2*_std20).iloc[-1]), 2),
                    "bb_lower":  round(float((_ma20 - 2*_std20).iloc[-1]), 2),
                }
                if result.get("volume") is None:
                    _rv = _v.iloc[-1]
                    if not _np.isnan(_rv):
                        result["volume"] = int(_rv)
        except Exception:
            pass

    if ti.get("success"):
        for _k in ("rsi", "macd_hist", "ma20", "ma60", "bb_upper", "bb_lower"):
            if ti.get(_k) is not None:
                result[_k] = ti[_k]

    return result


# Local tool registry: name → (handler, description, for display)
LOCAL_TOOLS = {
    # ── Core file tools ──────────────────────────────────────────────────────
    "read_file":      (_tool_read_file,      "Read a file's contents"),
    "write_file":     (_tool_write_file,     "Create or overwrite a file"),
    "edit_file":      (_tool_edit_file,      "Edit a file (find & replace)"),
    "list_files":     (_tool_list_files,     "List files in a directory"),
    "search_code":    (_tool_search_code,    "Search for patterns in code (grep)"),
    "run_command":    (_tool_run_command,    "Execute a shell command"),
    # ── Extended tools (Claude Code parity) ─────────────────────────────────
    "web_fetch":      (_tool_web_fetch,      "Fetch a URL and return page text"),
    "github":         (_tool_github,         "GitHub API/CLI: PRs, issues, diffs, search"),
    "glob":           (_tool_glob,           "Fast glob file-pattern search"),
    "notebook_read":  (_tool_notebook_read,  "Read a Jupyter notebook (.ipynb)"),
    "notebook_edit":  (_tool_notebook_edit,  "Edit a cell in a Jupyter notebook"),
    # ── Market data ─────────────────────────────────────────────────────────
    "get_market_data": (_tool_get_market_data, "Fetch real-time quote + technical indicators for any stock/ETF/crypto"),
}

# ── Register local finance fallback tools (yfinance / akshare / ccxt) ──────
# These fill in for remote Aria tools when local_mode=True or backend offline.
if _HAS_LOCAL_FINANCE:
    try:
        _n_finance = register_local_finance_tools(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)
        if _n_finance:
            logger.info("Registered %d local finance tools", _n_finance)
    except Exception as _exc:
        logger.debug("Local finance tools init error: %s", _exc)

# ── Register project plugin tools (aria_tools.py auto-discovery) ─────────
if _HAS_PLUGIN:
    try:
        _n_plugin, _plugin_path = register_plugin_tools(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)
        if _n_plugin and _plugin_path:
            logger.info("Registered %d plugin tools from %s", _n_plugin, _plugin_path.name)
    except Exception as _exc:
        logger.debug("Plugin tool registration error: %s", _exc)

# Ollama tool schemas (for function calling)
LOCAL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Use this to understand existing code before modifying it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute or relative file path"},
                    "offset": {"type": "integer", "description": "Start line (0-based), optional"},
                    "limit": {"type": "integer", "description": "Number of lines to read, optional"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write to"},
                    "content": {"type": "string", "description": "Complete file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Edit a file by replacing old_string with new_string. Read the file first to get the exact text to replace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to edit"},
                    "old_string": {"type": "string", "description": "Exact text to find and replace (must match exactly)"},
                    "new_string": {"type": "string", "description": "Replacement text"},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files in a directory. Use glob patterns like '**/*.py' to filter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: current dir)"},
                    "pattern": {"type": "string", "description": "Glob pattern (default: *)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a regex pattern in source files. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Directory to search in (default: current dir)"},
                    "glob": {"type": "string", "description": "File glob filter (default: **/*.py)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command. Use for: git, pip, python, pytest, ls, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "cwd": {"type": "string", "description": "Working directory (optional)"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (max 300, default 120). Use 180+ for data-heavy scripts."},
                },
                "required": ["command"],
            },
        },
    },
    # ── Extended tools ─────────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch the text/content of any URL: web pages, GitHub files, "
                "documentation, API responses, PyPI pages. "
                "GitHub blob URLs are auto-converted to raw content. "
                "Use this to read docs, README files, or look up library APIs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url":       {"type": "string",  "description": "Full URL to fetch (https://)"},
                    "max_chars": {"type": "integer", "description": "Max characters to return (default 12000, max 40000)"},
                    "timeout":   {"type": "integer", "description": "Request timeout seconds (default 15, max 30)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "github",
            "description": (
                "GitHub integration via gh CLI. Actions: "
                "list_prs, list_issues, view_pr, view_issue, create_pr, "
                "list_commits, search, read_file, pr_diff, pr_checks. "
                "Use cwd to specify repo directory. "
                "Requires: gh CLI installed and authenticated (gh auth login)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action":  {"type": "string", "description": "Action to perform (list_prs|list_issues|view_pr|view_issue|create_pr|list_commits|search|read_file|pr_diff|pr_checks)"},
                    "number":  {"type": "integer","description": "PR or issue number (for view_pr, view_issue, pr_diff)"},
                    "title":   {"type": "string", "description": "PR title (for create_pr)"},
                    "body":    {"type": "string", "description": "PR body (for create_pr)"},
                    "branch":  {"type": "string", "description": "Head branch (for create_pr)"},
                    "base":    {"type": "string", "description": "Base branch (for create_pr, default main)"},
                    "state":   {"type": "string", "description": "Filter state: open|closed|all (default open)"},
                    "limit":   {"type": "integer","description": "Max results (default 20)"},
                    "q":       {"type": "string", "description": "Search query (for search action)"},
                    "kind":    {"type": "string", "description": "Search kind: code|issues|repos (default code)"},
                    "ref":     {"type": "string", "description": "File ref in owner/repo@branch:path format (for read_file)"},
                    "cwd":     {"type": "string", "description": "Working directory (git repo root)"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "glob",
            "description": (
                "Fast file-pattern search. Returns a sorted list of matching file paths. "
                "Supports ** recursive globs: e.g. '**/*.py', 'src/**/*.ts', '*.json'. "
                "Use this to discover files before reading them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. **/*.py, src/**/*.ts)"},
                    "path":    {"type": "string", "description": "Root directory to search (default: current dir)"},
                    "limit":   {"type": "integer","description": "Max files to return (default 200)"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notebook_read",
            "description": "Read a Jupyter notebook (.ipynb) — returns all cells with source and outputs as formatted text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the .ipynb file"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notebook_edit",
            "description": "Edit a specific cell in a Jupyter notebook by its index (0-based). Clears cell outputs after edit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":        {"type": "string",  "description": "Path to the .ipynb file"},
                    "cell_index":  {"type": "integer", "description": "0-based cell index to replace"},
                    "new_source":  {"type": "string",  "description": "New cell source code/text"},
                },
                "required": ["path", "cell_index", "new_source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_data",
            "description": (
                "Fetch real-time market data for any stock, ETF, index, or cryptocurrency. "
                "Returns price, change, high/low, volume, RSI(14), MACD histogram, MA20/60, "
                "Bollinger Bands. Supports: US tickers (AAPL, NVDA), A-shares (6-digit code like 600519), "
                "HK stocks (0700.HK), crypto (BTC, ETH), indices (SPY, QQQ). "
                "You must look up the correct ticker symbol yourself — e.g. LVMH → MC.PA, "
                "路易威登/路易斯威登 → MC.PA or LVMUY, 宝马 → BMW.DE, 大众 → VWAGY."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": (
                            "Ticker symbol. Examples: AAPL, NVDA, 600519, 0700.HK, BTC, MC.PA. "
                            "For A-shares use the 6-digit code without exchange suffix. "
                            "Do NOT guess — if unsure about a ticker, say so and ask the user."
                        ),
                    },
                },
                "required": ["symbol"],
            },
        },
    },
]


# Tools that require user confirmation before execution
_CONFIRM_TOOLS = {"write_file", "edit_file", "run_command"}
_auto_approve_session = False  # Set True when user chooses "Yes, allow all"


def _show_edit_preview(params: dict):
    """Show a diff preview for edit_file (Claude Code style, Panel-boxed)."""
    path = params.get("path", "")
    old_str = params.get("old_string", params.get("old_str", ""))
    new_str = params.get("new_string", params.get("new_str", ""))
    if not path or not old_str:
        return

    p = pathlib.Path(path).expanduser().resolve()
    try:
        tw = os.get_terminal_size().columns
    except Exception:
        tw = 80
    short = str(p)
    if len(short) > tw - 10:
        short = "…" + short[-(tw - 11):]

    if not HAS_RICH:
        print(f"\n  Edit file  {short}")
        return

    body_parts: list = []
    try:
        content = p.read_text(errors="replace")
        pos = content.find(old_str)
        if pos >= 0:
            line_num = content[:pos].count("\n") + 1
            all_lines = content.splitlines()
            old_lines = old_str.splitlines()
            new_lines = new_str.splitlines()

            # Context before (up to 2 lines)
            ctx_start = max(0, line_num - 3)
            for i in range(ctx_start, line_num - 1):
                if i < len(all_lines):
                    body_parts.append(f"[dim]{i+1:4}  {all_lines[i][:100]}[/dim]")

            # Removed lines
            for i, ol in enumerate(old_lines):
                ln = line_num + i
                body_parts.append(f"[red]{ln:4} -  {ol[:100]}[/red]")

            # Added lines
            for i, nl in enumerate(new_lines):
                ln = line_num + i
                body_parts.append(f"[green]{ln:4} +  {nl[:100]}[/green]")

            # Context after (up to 2 lines)
            after_start = line_num - 1 + len(old_lines)
            for i in range(after_start, min(after_start + 2, len(all_lines))):
                body_parts.append(f"[dim]{i+1:4}  {all_lines[i][:100]}[/dim]")
        else:
            # String not found — fallback to plain diff lines
            for ol in old_str.splitlines()[:6]:
                body_parts.append(f"[red]-  {ol[:100]}[/red]")
            for nl in new_str.splitlines()[:6]:
                body_parts.append(f"[green]+  {nl[:100]}[/green]")
    except Exception:
        for ol in old_str.splitlines()[:6]:
            body_parts.append(f"[red]-  {ol[:100]}[/red]")
        for nl in new_str.splitlines()[:6]:
            body_parts.append(f"[green]+  {nl[:100]}[/green]")

    console.print()
    console.print(Panel(
        "\n".join(body_parts) if body_parts else "[dim](no preview)[/dim]",
        title=f"[yellow]Edit file[/yellow] [dim]{short}[/dim]",
        title_align="left",
        border_style="yellow",
        box=rich_box.ROUNDED,
        padding=(0, 1),
    ))


def _show_write_preview(params: dict):
    """Show a content preview for write_file (Claude Code style, Panel-boxed)."""
    path = params.get("path", "")
    content = params.get("content", "")
    if not path:
        return
    # Show cleaned content (without markdown fences)
    content = _strip_markdown_fences(content)

    p = pathlib.Path(path).expanduser().resolve()
    try:
        tw = os.get_terminal_size().columns
    except Exception:
        tw = 80
    short = str(p)
    if len(short) > tw - 10:
        short = "…" + short[-(tw - 11):]

    existed = p.exists()
    action = "Overwrite file" if existed else "Write new file"
    action_color = "yellow" if existed else "green"
    lines = content.count("\n") + 1

    if not HAS_RICH:
        print(f"\n  {action}  {short} ({lines} lines)")
        return

    preview_lines = content.splitlines()[:8]
    body_parts = [f"[green]+ {pl[:100]}[/green]" for pl in preview_lines]
    if lines > 8:
        n = lines - 8
        body_parts.append(f"[dim]… +{n} more line{'s' if n != 1 else ''}[/dim]")
    body = "\n".join(body_parts)

    console.print()
    console.print(Panel(
        body,
        title=f"[{action_color}]{action}[/{action_color}] [dim]{short}  ({lines} lines)[/dim]",
        title_align="left",
        border_style=action_color if existed else "dim",
        box=rich_box.ROUNDED,
        padding=(0, 1),
    ))


def _confirm_tool_execution(tool_name: str, params: dict,
                            config_policy: str = None) -> bool:
    """Ask user to confirm before executing a destructive tool.
    Returns True if approved, False if denied.

    For run_command: pre-flight policy check happens HERE, before showing the
    picker. If the command would be blocked even with user approval (high-risk),
    show error immediately. If medium-risk with 'safe' policy, offer to upgrade
    policy inline so the user can act without leaving the flow.
    """
    global _auto_approve_session
    if config_policy is None:
        config_policy = _ACTIVE_COMMAND_POLICY[0]
    if _auto_approve_session:
        # Still inject policy so run_command doesn't re-block
        if tool_name == "run_command":
            params["policy"] = config_policy
            params["user_approved"] = True
        return True
    if tool_name not in _CONFIRM_TOOLS:
        return True

    # ── Pre-flight for run_command ────────────────────────────────────────────
    if tool_name == "run_command":
        from command_safety import evaluate_command_policy, classify_command_risk
        cmd = params.get("command", "")
        risk = classify_command_risk(cmd)

        if risk == "high":
            # Always block high-risk regardless of user approval
            if HAS_RICH:
                console.print(Panel(
                    f"[red]✗ 高风险命令已拒绝[/red]\n[dim]{cmd[:120]}[/dim]\n"
                    f"[dim]高风险操作（rm -rf / docker / sudo 等）需要在终端手动执行。[/dim]",
                    border_style="red", box=rich_box.ROUNDED, padding=(0, 1),
                ))
            else:
                print(f"  ✗ 高风险命令已拒绝: {cmd[:80]}")
            return False

        if risk == "medium" and config_policy == "safe":
            # Show a richer picker that includes a "Allow & upgrade policy" option
            if HAS_RICH:
                console.print()
                console.print(Panel(
                    f"[yellow]⚠ 此命令需要 balanced 策略（当前: safe）[/yellow]\n"
                    f"[dim]{cmd[:120]}[/dim]",
                    border_style="yellow", box=rich_box.ROUNDED, padding=(0, 1),
                ))
            options = [
                ("Allow once",         "仅此次允许（不改变策略）"),
                ("Allow & set balanced","允许并升级策略（本会话有效）"),
                ("Yes, allow all",     "本会话内所有命令自动允许"),
                ("No",                 "拒绝执行"),
            ]
            choice = _arrow_select(options, selected=0, title="")
            if choice == 0:
                params["policy"] = "balanced"
                params["user_approved"] = True
                return True
            if choice == 1:
                # Persist to config if possible
                params["policy"] = "balanced"
                params["user_approved"] = True
                params["_upgrade_policy"] = True   # caller can read and save config
                return True
            if choice == 2:
                _auto_approve_session = True
                params["policy"] = "balanced"
                params["user_approved"] = True
                return True
            return False   # No

    # ── Default confirmation for write_file / edit_file / low-risk run ────────
    if tool_name == "edit_file":
        _show_edit_preview(params)
    elif tool_name == "write_file":
        _show_write_preview(params)
    elif tool_name == "run_command":
        # Header already printed by on_tool_call — just pass through policy
        params["policy"] = config_policy
        params["user_approved"] = True

    options = [
        ("Yes",          ""),
        ("Yes, allow all", "本会话内自动允许"),
        ("No",           ""),
    ]
    choice = _arrow_select(options, selected=0, title="")

    if choice == 0:
        if tool_name == "run_command":
            params["user_approved"] = True
        return True
    if choice == 1:
        _auto_approve_session = True
        if tool_name == "run_command":
            params["user_approved"] = True
        return True
    return False


def execute_local_tool(tool_name: str, params: dict) -> dict:
    """Execute a local tool by name."""
    if tool_name in LOCAL_TOOLS:
        handler, _ = LOCAL_TOOLS[tool_name]
        return handler(params)
    return {"success": False, "error": f"Unknown local tool: {tool_name}"}


def _run_hook(hook_type: str, tool_name: str, params: dict, result: dict = None) -> None:
    """Fire-and-forget hook execution from .ariarc hooks config.

    hook_type: "pre_tool" | "post_tool" | "on_error"
    Hooks are shell commands with {key} template substitution from params/result.

    Example .ariarc:
      "hooks": {
        "pre_tool":  {"write_file": "echo 'Writing: {path}'"},
        "post_tool": {"run_command": "notify-send 'Done'"},
        "on_error":  "echo 'Error: {error}'"
      }
    """
    if not _HAS_ARIARC:
        return
    try:
        _arc = get_ariarc()
        hooks = _arc.data.get("hooks", {}) if hasattr(_arc, "data") else {}
        if not hooks:
            return
        hook_spec = hooks.get(hook_type, {})
        # hook_spec can be: dict keyed by tool_name, or bare string for all tools
        if isinstance(hook_spec, dict):
            cmd = hook_spec.get(tool_name) or hook_spec.get("*")
        else:
            cmd = hook_spec  # bare string applies to all tools
        if not cmd:
            return
        # Template substitution: {path}, {command}, {error}, etc.
        fmt_ctx: Dict[str, str] = {k: str(v) for k, v in (params or {}).items()}
        if result:
            fmt_ctx["error"] = str(result.get("error", ""))
            fmt_ctx["success"] = str(result.get("success", ""))
        try:
            cmd = cmd.format_map(fmt_ctx)
        except (KeyError, ValueError):
            pass  # Ignore missing keys in template
        import subprocess as _sp
        _sp.run(cmd, shell=True, timeout=5, capture_output=True)
    except Exception:
        pass  # Hooks must never crash the main flow


# TTL cache for read-only tool responses
_TOOL_CACHE: Dict[str, tuple] = {}  # key -> (result, timestamp)
_CACHE_TTL = {
    "get_market_data": 30, "get_crypto_data": 30, "get_forex_data": 30,
    "get_commodities_data": 60, "get_bonds_data": 60, "get_futures_data": 60,
    "get_news": 300, "get_sector_performance": 60, "get_market_overview": 60,
}


async def execute_aria_tool(base_url: str, tool_name: str, params: dict,
                           timeout: int = 30, auth_token: str = None,
                           max_retries: int = 2) -> dict:
    """Execute an Aria tool via the backend API with auto-retry and TTL cache."""
    # --- Parameter validation before sending to API ---
    _symbol_tools = {
        "get_market_data", "get_risk_metrics", "calculate_factors",
        "get_alpha158_factors", "assess_portfolio_risk",
    }
    _date_tools = {"backtest_strategy", "stress_test_strategy"}

    if tool_name in _symbol_tools and "symbol" in params:
        sym = str(params["symbol"]).strip().upper()
        if not re_module.match(r'^[A-Z0-9.\-/=]{1,12}$', sym):
            return {"success": False, "error": f"Invalid symbol format: '{sym}'"}
        params = {**params, "symbol": sym}

    if tool_name in _date_tools:
        for date_key in ("start_date", "end_date", "start", "end"):
            if date_key in params:
                date_val = str(params[date_key]).strip()
                if not re_module.match(r'^\d{4}-\d{2}-\d{2}$', date_val):
                    return {"success": False, "error": f"Invalid date format for '{date_key}': '{date_val}' (expected YYYY-MM-DD)"}
        # Check chronological order
        start = params.get("start_date") or params.get("start")
        end = params.get("end_date") or params.get("end")
        if start and end and start > end:
            return {"success": False, "error": f"start_date ({start}) must be before end_date ({end})"}

    # Check cache for read-only tools
    ttl = _CACHE_TTL.get(tool_name)
    if ttl:
        cache_key = f"{tool_name}:{json.dumps(params, sort_keys=True)}"
        cached = _TOOL_CACHE.get(cache_key)
        if cached and (time.time() - cached[1]) < ttl:
            return cached[0]
    import aiohttp
    url = f"{base_url}/api/aria/execute-tool"
    payload = {"tool_name": tool_name, "params": params}
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                    result = await resp.json()
                    if result.get("success") or attempt >= max_retries:
                        # Cache successful results for read-only tools
                        if result.get("success") and ttl:
                            _TOOL_CACHE[cache_key] = (result, time.time())
                        return result
                    last_error = result.get("error", "Unknown error")
        except Exception as e:
            last_error = str(e)
        if attempt < max_retries:
            await asyncio.sleep(1 * (attempt + 1))  # 1s, 2s backoff
    return {"success": False, "error": f"Failed after {max_retries + 1} attempts: {last_error}"}


# ============================================================================
# Ollama Local Client (fallback when AWS unavailable)
# ============================================================================

CODING_SYSTEM_PROMPT = (
    "You are Aria, an elite quantitative finance AI agent with direct file system access on macOS.\n"
    "You are a full-stack quant developer: you write production-grade Python code for financial analysis, "
    "charting, backtesting, report generation, and interactive dashboards.\n\n"

    "## ABSOLUTE RULES\n"
    "EVERY response MUST contain exactly ONE <tool_call>. NEVER respond with only text. "
    "NEVER say \"I will do X\" — just DO it with a tool call. Final summary after all work = no tool call.\n\n"

    "## ABSOLUTELY FORBIDDEN\n"
    "1. NEVER pass slash-commands (/config, /model, /note, /apikey, etc.) to run_command — "
    "   they are NOT shell commands. To change policy tell the user to type the slash command directly.\n"
    "2. If run_command returns 'Command blocked by policy': STOP immediately. "
    "   Do NOT retry the same command. The user declined or the command is high-risk. "
    "   Tell the user briefly why it was blocked, then output NO more tool calls.\n"
    "3. Do NOT preemptively pip install packages. Common packages (yfinance, pandas, "
    "   numpy, matplotlib) are usually already installed. Run the script FIRST; "
    "   only pip3 install a package after ModuleNotFoundError names it.\n\n"

    "## Tool Call Format\n"
    "<tool_call>{\"name\": \"tool_name\", \"arguments\": {\"key\": \"value\"}}</tool_call>\n\n"

    "## Tools\n"
    "- write_file: {path, content} — Create/overwrite file. PURE code only, NO markdown fences.\n"
    "- read_file: {path} — Read file content to inspect/debug.\n"
    "- edit_file: {path, old_string, new_string} — Surgical edit. old_string must match EXACTLY.\n"
    "- run_command: {command, timeout} — Shell command (default timeout=120, max=300).\n"
    "- list_files: {path, pattern} — List directory or glob match.\n"
    "- search_code: {pattern, path, glob} — Grep for pattern in files.\n\n"

    "## Workflow (strict order, one tool per step)\n"
    "1. Check if file exists: read_file ~/Desktop/<name>.py — if exists, read it and improve with edit_file.\n"
    "   If not exists: write_file ~/Desktop/<descriptive_name>.py — COMPLETE self-contained script.\n"
    "2. run_command: python3 ~/Desktop/<name>.py (timeout=120).\n"
    "3. If ModuleNotFoundError: pip3 install <the missing package only>, then re-run.\n"
    "4. Verify: list_files ~/Desktop/ to confirm output files were saved.\n"
    "5. If error: read_file the script → find the EXACT bug → edit_file to fix → run_command again.\n"
    "   NEVER re-run the same command without fixing the code first!\n"
    "   NEVER give up. Keep fixing until it works. Max 10 rounds.\n\n"

    "## Python Rules\n"
    "- Always python3, pip3 (never python/pip).\n"
    "- FIRST LINE of any chart script: `import matplotlib; matplotlib.use('Agg')`\n"
    "  THEN: `import matplotlib.pyplot as plt`\n"
    "- savefig BEFORE plt.show(): `plt.savefig(os.path.expanduser('~/Desktop/name.png'), dpi=150, bbox_inches='tight')`\n"
    "- yfinance: always `progress=False`, `auto_adjust=True`.\n"
    "- All scripts must be fully self-contained (imports, data fetch, compute, output).\n"
    "- Print ALL key results to stdout AND save charts/reports to ~/Desktop/.\n"
    "- NEVER wrap file content in markdown fences (```). Write pure code.\n"
    "- Handle yfinance MultiIndex columns: use `df.columns = df.columns.droplevel(1)` if needed.\n\n"

    "## CRITICAL: Quant strategy quality bar (backtest scripts MUST satisfy ALL)\n"
    "1. TRANSACTION COSTS are mandatory — never report zero-cost returns:\n"
    "   A-share roundtrip: commission 0.025%x2 + stamp tax 0.05% (sell) + slippage 0.1%.\n"
    "   US stocks: commission ~0 + slippage 0.05%. Subtract cost on every position change:\n"
    "   `cost = df['Position'].diff().abs() * COST_RATE; df['Strategy_Return'] -= cost`\n"
    "2. Report ALL of: total/annual return, Sharpe, max drawdown, TRADE COUNT,\n"
    "   WIN RATE, profit/loss ratio, and the SAME metrics for buy-and-hold.\n"
    "3. Align comparison periods — strategy and buy-and-hold MUST start from the\n"
    "   same date (after indicator warm-up), else the comparison is meaningless.\n"
    "4. Out-of-sample check: if data > 1 year, split train/test (e.g. first 70% to\n"
    "   pick params, last 30% to validate) OR state clearly '参数未经样本外验证'.\n"
    "5. A-share specifics — state assumptions in output: T+1 (no same-day exit),\n"
    "   ±10% price limit (±20% ChiNext/STAR) means fills at limit price may fail.\n"
    "6. A-share DATA: prefer akshare (`ak.stock_zh_a_hist(symbol='600519', adjust='qfq')`)\n"
    "   — yfinance A-share coverage is unreliable. yfinance is fine for indices\n"
    "   (000001.SS) and US stocks. If user says A股 without naming a stock, ask or\n"
    "   pick a liquid leader (600519/300750), NOT an index — indices are not tradeable.\n\n"

    "## CRITICAL: Choose ONE chart library per script — NEVER mix plotly and matplotlib\n"
    "- For interactive HTML charts: use ONLY plotly (import plotly; use fig.write_html())\n"
    "- For static PNG charts: use ONLY matplotlib/mplfinance (import matplotlib; use plt.savefig())\n"
    "- NEVER import plotly.graph_objects AND matplotlib.pyplot in the same script\n"
    "- NEVER use `plt.figure()` after importing plotly — `plt` is matplotlib, not plotly\n\n"

    "## CRITICAL: Variable naming — define EVERY variable before using it\n"
    "- BAD: define `seven_days_ago` then reference `start_date` (undefined)\n"
    "- GOOD: define `start_date = date.today() - timedelta(days=7)` and use `start_date` consistently\n"
    "- Check that every variable referenced in the script is assigned exactly once above its first use\n\n"

    "## Common stock tickers (DO NOT TYPO)\n"
    "- Apple: AAPL (NOT 'APPL', NOT 'APPLE')\n"
    "- Microsoft: MSFT, Google: GOOGL, Amazon: AMZN, Tesla: TSLA, Nvidia: NVDA\n"
    "- Always double-check ticker spellings — a wrong ticker returns empty data silently\n\n"

    "## MANDATORY IMPORTS — always include these at the top of EVERY script:\n"
    "```\n"
    "import os\n"
    "import sys\n"
    "import numpy as np\n"
    "import pandas as pd\n"
    "import yfinance as yf\n"
    "import matplotlib; matplotlib.use('Agg')\n"
    "import matplotlib.pyplot as plt\n"
    "```\n"
    "ALWAYS import 'os' — it is needed for os.path.expanduser() in savefig paths.\n"
    "ALWAYS import 'numpy as np' — almost all financial calculations need it.\n\n"

    "## Code Quality Rules\n"
    "- Write COMPLETE, PRODUCTION-GRADE scripts. No shortcuts, no stubs, no TODOs.\n"
    "- There is NO length limit. Write as much code as needed (100, 200, 500+ lines is fine).\n"
    "- Include comprehensive error handling: try/except around data fetching, NaN handling, empty data checks.\n"
    "- Add print() statements throughout for progress feedback (user sees stdout in real-time).\n"
    "- Use descriptive variable names. Add brief comments for complex logic.\n"
    "- For multi-asset analysis: process ALL requested assets, don't skip any.\n"
    "- Charts: use proper labels, titles, legends, grid. Set figure size (14,8) or larger.\n"
    "- When comparing stocks: use percentage returns (not absolute prices) for fair comparison.\n"
    "- Multiple output files are fine: kline_AAPL.png, kline_BABA.png, comparison.png, backtest.png, etc.\n\n"

    "## ERROR RECOVERY (Skill 6: Code Debugging)\n"
    "When run_command fails:\n"
    "1. READ the error traceback carefully — identify the EXACT file, line number, and error type.\n"
    "2. Common fixes:\n"
    "   - NameError: 'X' not defined → you forgot to import X. edit_file to add `import X` at top.\n"
    "   - ModuleNotFoundError → run_command: pip3 install <module>\n"
    "   - FileNotFoundError → the script path is wrong, or write_file was skipped\n"
    "   - SyntaxError at line N → read_file the script, find line N, edit_file to fix\n"
    "   - KeyError/IndexError → data structure mismatch, read_file to inspect logic\n"
    "   - TypeError → wrong argument types, read_file → edit_file\n"
    "3. ALWAYS read_file the script BEFORE attempting edit_file (to see actual content).\n"
    "4. Fix the root cause, not symptoms. If the approach is fundamentally wrong, rewrite with write_file.\n"
    "5. After fixing, run_command again to verify. Repeat until success.\n"
    "6. If a library doesn't work, try an alternative (e.g., mplfinance → plotly, ta → pandas_ta).\n\n"
    "### CRITICAL ERROR RECOVERY RULES:\n"
    "- NEVER re-run the exact same command after it fails. Fix the code FIRST, then retry.\n"
    "- NEVER read_file more than once without fixing. Read → Fix → Retry.\n"
    "- If you read a file and see the problem, immediately call edit_file (not read_file again).\n"
    "- If write_file produced a placeholder or incomplete code, call write_file again with COMPLETE code.\n\n"

    # ==================== SKILL 1: Report Generation ====================
    "## SKILL 1: Financial Report Generation\n"
    "Generate comprehensive reports as HTML files with embedded CSS (no external deps).\n"
    "Save to ~/Desktop/<report_name>.html — user can open in browser.\n\n"

    "### Report structure pattern:\n"
    "```\n"
    "html = f'''\n"
    "<!DOCTYPE html>\n"
    "<html><head><meta charset=\"utf-8\">\n"
    "<title>{title}</title>\n"
    "<style>\n"
    "  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 40px; max-width: 1200px; margin: 0 auto; }}\n"
    "  .header {{ border-bottom: 2px solid #C08050; padding-bottom: 20px; margin-bottom: 30px; }}\n"
    "  .header h1 {{ color: #C08050; font-size: 28px; margin: 0; }}\n"
    "  .header .subtitle {{ color: #888; font-size: 14px; }}\n"
    "  .metric-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin: 20px 0; }}\n"
    "  .metric-card {{ background: #16213e; border-radius: 12px; padding: 20px; border: 1px solid #333; }}\n"
    "  .metric-card .label {{ color: #888; font-size: 12px; text-transform: uppercase; }}\n"
    "  .metric-card .value {{ font-size: 24px; font-weight: bold; margin-top: 4px; }}\n"
    "  .positive {{ color: #2AE8A5; }} .negative {{ color: #EF4444; }}\n"
    "  table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}\n"
    "  th {{ background: #16213e; color: #C08050; text-align: left; padding: 12px; border-bottom: 2px solid #333; }}\n"
    "  td {{ padding: 10px 12px; border-bottom: 1px solid #222; }}\n"
    "  tr:hover {{ background: #16213e; }}\n"
    "  .section {{ margin: 30px 0; }}\n"
    "  .section h2 {{ color: #C08050; font-size: 20px; border-bottom: 1px solid #333; padding-bottom: 8px; }}\n"
    "  .chart-container {{ background: #16213e; border-radius: 12px; padding: 20px; margin: 20px 0; }}\n"
    "</style></head><body>\n"
    "'''\n"
    "# Build content sections, then:\n"
    "html += '</body></html>'\n"
    "with open(os.path.expanduser('~/Desktop/report.html'), 'w') as f: f.write(html)\n"
    "```\n\n"

    "### Report types you can generate:\n"
    "- Stock analysis report: company overview, financials, ratios, price history, technical indicators\n"
    "- Portfolio report: holdings, allocation, performance, risk metrics, correlation matrix\n"
    "- Backtest report: strategy description, equity curve, trade log, drawdown analysis, monthly returns\n"
    "- Sector/market report: sector performance, heatmap, breadth indicators, macro overview\n"
    "- Earnings report: revenue/EPS trends, margin analysis, guidance vs actual, peer comparison\n\n"
    "### HTML Report Rules:\n"
    "- NEVER use <br> tags for spacing — use CSS margin/padding instead.\n"
    "- NEVER build table rows by string-concatenating cells with <br> — use proper <tr><td> structure.\n"
    "- ALWAYS use f-strings or proper string building, never raw HTML line-by-line printing.\n"
    "- Use CSS classes (.positive/.negative) for color, not inline style= attributes.\n"
    "- ALL dynamic data (prices, dates, numbers) must come from yfinance or computed variables.\n"
    "  NEVER hardcode placeholder values like 'N/A' or '0.00' for fields you should compute.\n\n"

    # ==================== SKILL 2: Interactive HTML Charts ====================
    "## SKILL 2: Interactive HTML Charts (Plotly)\n"
    "For interactive/flowing charts, use Plotly and save as self-contained HTML.\n"
    "Install: pip3 install plotly\n\n"

    "### Plotly chart patterns:\n"
    "```\n"
    "import plotly.graph_objects as go\n"
    "from plotly.subplots import make_subplots\n\n"

    "# Candlestick with volume\n"
    "fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.7, 0.3], vertical_spacing=0.02)\n"
    "fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='OHLC'), row=1, col=1)\n"
    "fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='Volume', marker_color='rgba(192,128,80,0.5)'), row=2, col=1)\n"
    "# Add moving averages\n"
    "for period, color in [(20, '#2AE8A5'), (50, '#C08050'), (200, '#EF4444')]:\n"
    "    sma = df['Close'].rolling(period).mean()\n"
    "    fig.add_trace(go.Scatter(x=df.index, y=sma, name=f'SMA{period}', line=dict(color=color, width=1)), row=1, col=1)\n"
    "fig.update_layout(template='plotly_dark', title=f'{ticker} Interactive Chart', xaxis_rangeslider_visible=False, height=700)\n"
    "fig.write_html(os.path.expanduser('~/Desktop/chart.html'), include_plotlyjs=True)\n"
    "```\n\n"

    "### More Plotly chart types:\n"
    "- go.Scatter: line/area charts, equity curves, indicator overlays\n"
    "- go.Bar: volume, monthly returns, sector comparison\n"
    "- go.Heatmap: correlation matrix, monthly returns heatmap, sector heatmap\n"
    "- go.Pie/go.Sunburst: portfolio allocation, sector breakdown\n"
    "- go.Waterfall: P&L attribution, earnings bridge\n"
    "- go.Table: data tables within the HTML\n"
    "- go.Indicator: gauge charts for sentiment, risk scores\n"
    "- make_subplots: multi-panel dashboards combining all above\n\n"

    "### Interactive dashboard pattern (multi-page with tabs):\n"
    "```\n"
    "from plotly.subplots import make_subplots\n"
    "fig = make_subplots(rows=3, cols=2, subplot_titles=['Price', 'Volume', 'RSI', 'MACD', 'Returns', 'Drawdown'],\n"
    "                    specs=[[{'secondary_y': True}, {}], [{}, {}], [{}, {}]])\n"
    "# Add traces to each subplot, then:\n"
    "fig.update_layout(template='plotly_dark', height=1200, showlegend=True,\n"
    "                  title=dict(text=f'{ticker} Analysis Dashboard', font=dict(size=24, color='#C08050')))\n"
    "fig.write_html(os.path.expanduser('~/Desktop/dashboard.html'), include_plotlyjs=True)\n"
    "```\n\n"

    # ==================== SKILL 3: Chart Types ====================
    "## SKILL 3: Chart Types (matplotlib + mplfinance)\n"
    "For static PNG charts (non-interactive), use matplotlib/mplfinance.\n"
    "Install: pip3 install matplotlib mplfinance ta-lib pandas-ta\n\n"

    "## CRITICAL DATE RANGE RULES:\n"
    "- '30天' → `timedelta(days=30)`, '1个月' → `timedelta(days=30)`, '3个月' → `timedelta(days=90)'\n"
    "- NEVER use days=60 when user says 30天. Match EXACTLY what the user requested.\n"
    "- date.today() returns a date object — use `.strftime('%Y-%m-%d')` for yfinance if needed\n\n"

    "## CRITICAL TICKER RULES:\n"
    "- NEVER add spaces: `ticker = 'AAPL'` NOT `ticker = ' AAPL'` — a leading space returns empty data\n"
    "- ALWAYS call ticker.strip() if building the ticker from user input\n\n"

    "### COMPLETE WORKING mplfinance + RSI template (copy this exactly):\n"
    "```python\n"
    "import os\n"
    "from datetime import date, timedelta\n"
    "import numpy as np\n"
    "import pandas as pd\n"
    "import yfinance as yf\n"
    "import matplotlib; matplotlib.use('Agg')  # MUST be before pyplot import\n"
    "import matplotlib.pyplot as plt\n"
    "import mplfinance as mpf  # ALWAYS import mplfinance as mpf\n\n"
    "ticker = 'AAPL'  # NO spaces\n"
    "days = 30        # match user request exactly\n"
    "start = date.today() - timedelta(days=days)\n"
    "df = yf.download(ticker, start=start, progress=False, auto_adjust=True)\n"
    "if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)\n"
    "if df.empty: raise ValueError(f'No data for {ticker}')\n\n"
    "# ALWAYS compute indicators BEFORE using them in addplot\n"
    "delta = df['Close'].diff()\n"
    "gain  = delta.clip(lower=0).rolling(14).mean()\n"
    "loss  = (-delta.clip(upper=0)).rolling(14).mean()\n"
    "df['RSI'] = 100 - (100 / (1 + gain / loss))\n"
    "df['EMA20'] = df['Close'].ewm(span=20).mean()\n"
    "df = df.dropna()\n\n"
    "ap = [\n"
    "    mpf.make_addplot(df['EMA20'], panel=0, color='#2AE8A5', width=1.5),\n"
    "    mpf.make_addplot(df['RSI'],  panel=1, color='#C08050', ylabel='RSI'),\n"
    "]\n"
    "out = os.path.expanduser(f'~/Desktop/{ticker}_{days}d_chart.png')\n"
    "mpf.plot(df, type='candle', style='charles', volume=True,\n"
    "         mav=(5, 20), addplot=ap, panel_ratios=(4, 1, 1),\n"
    "         title=f'{ticker} — Last {days} Days',\n"
    "         savefig=dict(fname=out, dpi=150, bbox_inches='tight'))\n"
    "print(f'Chart saved: {out}')\n"
    "```\n\n"
    "## FORBIDDEN mplfinance parameters (will cause TypeError):\n"
    "- `showlegend` — does NOT exist in mplfinance.plot()\n"
    "- `height` — does NOT exist in mplfinance.plot()\n"
    "- `secondary_y=dict(...)` as a top-level kwarg — not valid\n"
    "- `title=` inside `savefig=dict()` — title is a separate top-level arg\n\n"

    "### Other chart patterns:\n"
    "- Line chart: plt.plot() with plt.fill_between() for area\n"
    "- Equity curve: plt.plot(cumulative_returns) with drawdown shading\n"
    "- Heatmap: plt.imshow() or sns.heatmap() for correlation/monthly returns\n"
    "- Bar chart: plt.bar() for sector comparison, portfolio allocation\n"
    "- Dual axis: fig, ax1 = plt.subplots(); ax2 = ax1.twinx() for price+volume\n"
    "- Monthly returns: pivot table → sns.heatmap with RdYlGn cmap\n\n"

    # ==================== SKILL 4: Quantitative Strategies ====================
    "## SKILL 4: Quantitative Strategies\n"
    "Write complete, runnable backtests with proper metrics.\n"
    "Install: pip3 install yfinance pandas numpy matplotlib mplfinance\n\n"

    "### Strategy types you MUST know:\n\n"

    "#### A. Moving Average Crossover (SMA/EMA)\n"
    "```\n"
    "df['SMA_fast'] = df['Close'].rolling(20).mean()\n"
    "df['SMA_slow'] = df['Close'].rolling(50).mean()\n"
    "df['Signal'] = 0\n"
    "df.loc[df['SMA_fast'] > df['SMA_slow'], 'Signal'] = 1\n"
    "df['Position'] = df['Signal'].shift(1)\n"
    "df['Strategy_Return'] = df['Position'] * df['Close'].pct_change()\n"
    "```\n\n"

    "#### B. Mean Reversion (Bollinger Bands)\n"
    "```\n"
    "df['SMA20'] = df['Close'].rolling(20).mean()\n"
    "df['STD20'] = df['Close'].rolling(20).std()\n"
    "df['Upper'] = df['SMA20'] + 2 * df['STD20']\n"
    "df['Lower'] = df['SMA20'] - 2 * df['STD20']\n"
    "df['Signal'] = 0\n"
    "df.loc[df['Close'] < df['Lower'], 'Signal'] = 1   # Buy at lower band\n"
    "df.loc[df['Close'] > df['Upper'], 'Signal'] = -1  # Sell at upper band\n"
    "df['Position'] = df['Signal'].shift(1).ffill()     # Hold until opposite signal\n"
    "```\n\n"

    "#### C. RSI Strategy\n"
    "```\n"
    "delta = df['Close'].diff()\n"
    "gain = delta.where(delta > 0, 0).rolling(14).mean()\n"
    "loss = (-delta.where(delta < 0, 0)).rolling(14).mean()\n"
    "df['RSI'] = 100 - (100 / (1 + gain / loss))\n"
    "df['Signal'] = 0\n"
    "df.loc[df['RSI'] < 30, 'Signal'] = 1   # Oversold → buy\n"
    "df.loc[df['RSI'] > 70, 'Signal'] = -1  # Overbought → sell\n"
    "df['Position'] = df['Signal'].shift(1).ffill()\n"
    "```\n\n"

    "#### D. MACD Strategy\n"
    "```\n"
    "df['EMA12'] = df['Close'].ewm(span=12).mean()\n"
    "df['EMA26'] = df['Close'].ewm(span=26).mean()\n"
    "df['MACD'] = df['EMA12'] - df['EMA26']\n"
    "df['MACD_Signal'] = df['MACD'].ewm(span=9).mean()\n"
    "df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']\n"
    "df['Signal'] = 0\n"
    "df.loc[df['MACD'] > df['MACD_Signal'], 'Signal'] = 1\n"
    "df.loc[df['MACD'] <= df['MACD_Signal'], 'Signal'] = -1\n"
    "df['Position'] = df['Signal'].shift(1)\n"
    "```\n\n"

    "#### E. Momentum / Dual Momentum\n"
    "```\n"
    "lookback = 126  # 6 months\n"
    "df['Momentum'] = df['Close'] / df['Close'].shift(lookback) - 1\n"
    "# Absolute momentum: only long if positive\n"
    "df['Signal'] = (df['Momentum'] > 0).astype(int)\n"
    "# Relative momentum: compare multiple assets, pick top N\n"
    "```\n\n"

    "#### F. Pairs Trading\n"
    "```\n"
    "from sklearn.linear_model import LinearRegression\n"
    "# Download two correlated stocks\n"
    "s1 = yf.download(ticker1, start=start, progress=False)['Close']\n"
    "s2 = yf.download(ticker2, start=start, progress=False)['Close']\n"
    "# Calculate spread\n"
    "model = LinearRegression().fit(s1.values.reshape(-1,1), s2.values)\n"
    "hedge_ratio = model.coef_[0]\n"
    "spread = s2 - hedge_ratio * s1\n"
    "z_score = (spread - spread.rolling(60).mean()) / spread.rolling(60).std()\n"
    "# Trade signals\n"
    "signal = pd.Series(0, index=z_score.index)\n"
    "signal[z_score < -2] = 1    # Long spread\n"
    "signal[z_score > 2] = -1   # Short spread\n"
    "signal[abs(z_score) < 0.5] = 0  # Close position\n"
    "```\n\n"

    "### MANDATORY Backtest Metrics (always compute ALL of these):\n"
    "```\n"
    "returns = df['Strategy_Return'].dropna()\n"
    "cum = (1 + returns).cumprod()\n"
    "total_return = cum.iloc[-1] - 1\n"
    "days = (returns.index[-1] - returns.index[0]).days\n"
    "annual_return = (1 + total_return) ** (365.25 / days) - 1 if days > 0 else 0\n"
    "sharpe = returns.mean() / returns.std() * (252**0.5) if returns.std() > 0 else 0\n"
    "sortino = returns.mean() / returns[returns < 0].std() * (252**0.5) if len(returns[returns < 0]) > 0 else 0\n"
    "rolling_max = cum.cummax()\n"
    "drawdown = (cum - rolling_max) / rolling_max\n"
    "max_drawdown = drawdown.min()\n"
    "calmar = annual_return / abs(max_drawdown) if max_drawdown != 0 else 0\n"
    "win_rate = (returns > 0).sum() / (returns != 0).sum() if (returns != 0).sum() > 0 else 0\n"
    "profit_factor = returns[returns > 0].sum() / abs(returns[returns < 0].sum()) if returns[returns < 0].sum() != 0 else float('inf')\n"
    "trades = (df['Signal'].diff() != 0).sum()\n"
    "# Buy & hold comparison\n"
    "bh_return = (1 + df['Close'].pct_change()).cumprod().iloc[-1] - 1\n"
    "alpha = total_return - bh_return\n"
    "```\n\n"

    "### Backtest output format (always print this table):\n"
    "```\n"
    "print(f'\\n{'='*50}')\n"
    "print(f' Strategy Performance Report')\n"
    "print(f'{'='*50}')\n"
    "print(f' Total Return:     {total_return*100:>8.2f}%')\n"
    "print(f' Annual Return:    {annual_return*100:>8.2f}%')\n"
    "print(f' Sharpe Ratio:     {sharpe:>8.2f}')\n"
    "print(f' Sortino Ratio:    {sortino:>8.2f}')\n"
    "print(f' Max Drawdown:     {max_drawdown*100:>8.2f}%')\n"
    "print(f' Calmar Ratio:     {calmar:>8.2f}')\n"
    "print(f' Win Rate:         {win_rate*100:>8.1f}%')\n"
    "print(f' Profit Factor:    {profit_factor:>8.2f}')\n"
    "print(f' Total Trades:     {trades:>8d}')\n"
    "print(f' Buy&Hold Return:  {bh_return*100:>8.2f}%')\n"
    "print(f' Alpha:            {alpha*100:>8.2f}%')\n"
    "print(f'{'='*50}')\n"
    "```\n\n"

    # ==================== SKILL 5: Financial Analysis ====================
    "## SKILL 5: Financial Statement & Technical Analysis\n"
    "Install: pip3 install yfinance pandas_ta\n\n"

    "### Financial statement data (yfinance):\n"
    "```\n"
    "t = yf.Ticker(ticker)\n"
    "info = t.info          # Company info, ratios, market data\n"
    "fins = t.financials    # Income statement (annual)\n"
    "qfins = t.quarterly_financials  # Quarterly income\n"
    "bs = t.balance_sheet   # Balance sheet\n"
    "cf = t.cashflow        # Cash flow statement\n"
    "# Key fields in info:\n"
    "# currentPrice, marketCap, trailingPE, forwardPE, pegRatio, priceToBook,\n"
    "# trailingEps, forwardEps, dividendYield, beta, fiftyTwoWeekHigh/Low,\n"
    "# profitMargins, operatingMargins, returnOnEquity, returnOnAssets,\n"
    "# revenueGrowth, earningsGrowth, freeCashflow, totalRevenue, totalDebt,\n"
    "# debtToEquity, quickRatio, currentRatio, sharesOutstanding\n"
    "```\n\n"

    "### Key financial ratios to compute:\n"
    "- Valuation: P/E, P/B, P/S, PEG, EV/EBITDA\n"
    "- Profitability: ROE, ROA, gross/operating/net margins\n"
    "- Liquidity: current ratio, quick ratio, cash ratio\n"
    "- Leverage: debt/equity, interest coverage, debt/EBITDA\n"
    "- Growth: revenue growth, earnings growth, FCF growth (YoY)\n"
    "- DuPont: ROE = Net Margin × Asset Turnover × Equity Multiplier\n\n"

    "### DCF Valuation:\n"
    "```\n"
    "info = yf.Ticker(ticker).info\n"
    "fcf = info.get('freeCashflow', 0)\n"
    "shares = info.get('sharesOutstanding', 1)\n"
    "growth_rate = 0.10; discount_rate = 0.10; terminal_growth = 0.03\n"
    "projected_fcf = [fcf * (1 + growth_rate)**i for i in range(1, 11)]\n"
    "discounted = [f / (1 + discount_rate)**i for i, f in enumerate(projected_fcf, 1)]\n"
    "terminal_value = projected_fcf[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)\n"
    "terminal_pv = terminal_value / (1 + discount_rate)**10\n"
    "intrinsic_value = (sum(discounted) + terminal_pv) / shares\n"
    "current_price = info.get('currentPrice', 0)\n"
    "margin_of_safety = (intrinsic_value - current_price) / intrinsic_value * 100\n"
    "```\n\n"

    "### Technical Indicators (pandas_ta or manual):\n"
    "```\n"
    "import pandas_ta as ta\n"
    "df.ta.sma(length=20, append=True)     # SMA_20\n"
    "df.ta.ema(length=20, append=True)     # EMA_20\n"
    "df.ta.rsi(length=14, append=True)     # RSI_14\n"
    "df.ta.macd(append=True)               # MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9\n"
    "df.ta.bbands(length=20, append=True)  # BBL_20_2.0, BBM_20_2.0, BBU_20_2.0\n"
    "df.ta.stoch(append=True)              # STOCHk_14_3_3, STOCHd_14_3_3\n"
    "df.ta.atr(length=14, append=True)     # ATRr_14\n"
    "df.ta.adx(length=14, append=True)     # ADX_14, DMP_14, DMN_14\n"
    "df.ta.obv(append=True)               # OBV\n"
    "df.ta.vwap(append=True)              # VWAP\n"
    "```\n\n"

    "### Manual indicator calculation (if pandas_ta not available):\n"
    "```\n"
    "# RSI\n"
    "delta = df['Close'].diff()\n"
    "gain = delta.where(delta > 0, 0).rolling(14).mean()\n"
    "loss = (-delta.where(delta < 0, 0)).rolling(14).mean()\n"
    "df['RSI'] = 100 - (100 / (1 + gain / loss))\n"
    "# MACD\n"
    "df['EMA12'] = df['Close'].ewm(span=12).mean()\n"
    "df['EMA26'] = df['Close'].ewm(span=26).mean()\n"
    "df['MACD'] = df['EMA12'] - df['EMA26']\n"
    "df['MACD_Signal'] = df['MACD'].ewm(span=9).mean()\n"
    "# Bollinger Bands\n"
    "df['BB_Mid'] = df['Close'].rolling(20).mean()\n"
    "df['BB_Upper'] = df['BB_Mid'] + 2 * df['Close'].rolling(20).std()\n"
    "df['BB_Lower'] = df['BB_Mid'] - 2 * df['Close'].rolling(20).std()\n"
    "# ATR\n"
    "high_low = df['High'] - df['Low']\n"
    "high_close = (df['High'] - df['Close'].shift()).abs()\n"
    "low_close = (df['Low'] - df['Close'].shift()).abs()\n"
    "tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)\n"
    "df['ATR'] = tr.rolling(14).mean()\n"
    "```\n\n"

    # ==================== SKILL 7: Code Language Conversion ====================
    "## CODE LANGUAGE CONVERSION (Skill 7)\n"
    "When user asks to convert/translate/rewrite code from one language to another:\n"
    "1. **Identify the source language** from context or explicit mention (e.g., 'convert this Python to TypeScript').\n"
    "2. **Map idioms faithfully**, don't just transliterate:\n"
    "   - Python list comprehension → JS/TS Array.map/filter\n"
    "   - Python dict → JS object / TypeScript Record<K,V>\n"
    "   - Python dataclass → TypeScript interface + class\n"
    "   - pandas DataFrame → JS array of objects or TypeScript typed array\n"
    "   - Python f-string → JS template literal\n"
    "   - Python try/except → JS try/catch\n"
    "   - Python async/await → JS async/await (nearly identical)\n"
    "   - Python decorators → TypeScript decorators or wrapper functions\n"
    "3. **Common conversion pairs and rules**:\n"
    "   - Python → TypeScript: add type annotations, use `interface`, convert snake_case to camelCase\n"
    "   - Python → Go: use goroutines for async, struct for class, slice for list\n"
    "   - Python → Rust: use struct, impl, Result<T,E> for error handling\n"
    "   - SQL → pandas: SELECT→df[cols], WHERE→df[condition], GROUP BY→groupby(), JOIN→merge()\n"
    "   - pandas → SQL: df[cols]→SELECT, df[mask]→WHERE, groupby()→GROUP BY\n"
    "   - JavaScript → TypeScript: add type annotations, interfaces, generics\n"
    "4. **Output format**: show the converted code in a fenced code block with the target language tag.\n"
    "5. **Add brief comments** explaining non-obvious translation choices.\n"
    "6. If converting a large file: convert completely, do not truncate.\n\n"

    # ==================== COMBINED OUTPUT GUIDELINES ====================
    "## Output Guidelines\n"
    "- K-line / candlestick chart → save as PNG (mplfinance) AND/OR HTML (plotly)\n"
    "- Backtest → save equity curve PNG + print metrics table to stdout\n"
    "- Report → save as .html with embedded CSS (dark theme, Arthera branding #C08050)\n"
    "- Interactive dashboard → save as .html with Plotly (include_plotlyjs=True)\n"
    "- All outputs go to ~/Desktop/ with descriptive filenames\n"
    "- When user asks for \"分析\" or \"analysis\": combine chart + indicators + financials\n"
    "- When user asks for \"回测\" or \"backtest\": full strategy + metrics + equity curve\n"
    "- When user asks for \"报告\" or \"report\": comprehensive HTML with all sections\n"
    "- When user asks for \"图表\" or \"chart\": prioritize interactive HTML (Plotly)\n"
    "- Always include comparison to buy-and-hold benchmark\n"
)


def _build_coding_prompt_lite(user_message: str) -> str:
    """
    Condensed coding system prompt for small models (≤3B parameters).

    Detects whether the task needs a chart or pure analysis/strategy code,
    and serves the appropriate minimal template. No code fences in the system
    prompt — they confuse small models into copying the template literally.
    """
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y年%m月%d日")

    low = user_message.lower()
    is_chart = any(k in low for k in ("k线", "kline", "candlestick", "蜡烛", "图表", "chart", "plot", "图"))

    # Detect A-share context (Chinese stock codes, A股 keywords)
    is_ashare = any(k in low for k in (
        "a股", "a-股", "沪深", "上交所", "深交所", "akshare",
        "tushare", "600", "000", "300", "港股", "上证",
    ))

    if is_chart:
        if is_ashare:
            rules = (
                "A股图表规则（必须遵守）:\n"
                "- import akshare as ak  # A股数据用 akshare\n"
                "- import mplfinance as mpf\n"
                "- import matplotlib; matplotlib.use('Agg')\n"
                "- 获取日线数据: df = ak.stock_zh_a_hist(symbol='600519', period='daily', "
                "start_date='20230101', end_date='20241231', adjust='qfq')\n"
                "- 列名重命名: df.rename(columns={'开盘':'Open','收盘':'Close','最高':'High',"
                "'最低':'Low','成交量':'Volume'}, inplace=True)\n"
                "- df.index = pd.to_datetime(df['日期'])\n"
                "- 计算 RSI/MACD 后再传给 addplot\n"
                "- 保存到 os.path.expanduser('~/Desktop/<name>.png')\n"
            )
        else:
            rules = (
                "Chart script rules:\n"
                "- import mplfinance as mpf (required for candlestick charts)\n"
                "- import matplotlib; matplotlib.use('Agg') before importing pyplot\n"
                "- Compute RSI/MACD BEFORE passing to addplot\n"
                "- savefig to os.path.expanduser('~/Desktop/<name>.png')\n"
                "- Download: df = yf.download(ticker, start=start, progress=False, auto_adjust=True)\n"
                "- Flatten MultiIndex: if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.droplevel(1)\n"
            )
    else:
        if is_ashare:
            rules = (
                "A股策略/分析脚本规则（必须遵守）:\n"
                "- import akshare as ak  # A股数据必须用 akshare，禁止用 pandas_datareader\n"
                "- 获取日线: df = ak.stock_zh_a_hist(symbol='600519', period='daily', "
                "start_date='20200101', end_date='20241231', adjust='qfq')\n"
                "- 列名: 日期,开盘,收盘,最高,最低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率\n"
                "- 选个股（如600519贵州茅台），不要用指数——指数不可交易\n"
                "- 回测必须扣交易成本: 换仓时 收益 -= abs(仓位变化) * 0.002  # 佣金+印花税+滑点\n"
                "- 必须输出: 总收益/年化/夏普/最大回撤/交易次数/胜率 + 同期买入持有对比\n"
                "- 策略与买入持有从同一天起算（指标预热期之后）\n"
                "- 用 pandas 计算均线/因子\n"
                "- print() 输出清晰的结果\n"
                "- 不要用 yfinance、pandas_datareader 或任何境外数据源\n"
            )
        else:
            rules = (
                "Rules for strategy/analysis scripts:\n"
                "- Download data: df = yf.download(ticker, start=start, progress=False, auto_adjust=True)\n"
                "- Flatten MultiIndex columns if needed\n"
                "- Print clear results with print()\n"
                "- Use pandas for calculations\n"
                "- No matplotlib unless user asks for a chart\n"
                "- DO NOT use pandas_datareader (deprecated); use yfinance instead\n"
            )

    return (
        f"You are Aria, a quantitative finance Python coding assistant. Today is {today}.\n"
        "Your ONLY job is to write a complete, SYNTACTICALLY CORRECT, runnable Python script.\n\n"
        "Output format:\n"
        "- Output ONLY the Python code inside a single ```python ... ``` code block.\n"
        "- Do NOT explain or add text before/after the code block.\n"
        "- The code must be complete and self-contained — every variable must be defined.\n"
        "- Every import must be used; every function call must have correct arguments.\n"
        "- NEVER leave placeholder variable names like 'closePrices', 'smaValues' undefined.\n"
        "- Use the ticker, date range, and filename specified by the user.\n\n"
        + rules
    )


def _build_analysis_prompt_lite(user_message: str) -> str:
    """
    Condensed analysis prompt for small models (≤3B).

    The full ANALYSIS_SYSTEM_PROMPT has Python f-string-style placeholders like
    {real price from data} that small models copy literally instead of filling in.
    This lite version has ZERO template placeholders — only plain rules.
    """
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y年%m月%d日")
    return (
        f"你是 Aria，专业量化金融 AI。今天是 {today}。\n\n"
        "## 分析股票/指数时的规则\n"
        "1. 如果上方系统提示中已注入了「📊 实时行情」或「📈 技术指标」数据块，\n"
        "   必须直接使用这些数字作答，绝不修改或替换任何数值。\n"
        "2. ⚠️ 如果没有注入任何行情数据：\n"
        "   - 直接说：'暂无实时行情数据，请用 /quote <代码> 命令获取最新价格后再分析。'\n"
        "   - 绝对不要编造任何价格、RSI、MACD 数值，不要输出含 N/A 或占位符的模板。\n"
        "   - 🚫 同样禁止编造财务数据：收入、净利润、增速、市值、利润率等具体数字\n"
        "     一律不准凭训练记忆给出——你的训练数据已过时，编造的数字会误导投资决策。\n"
        "   - 🚫 禁止凭记忆写股票代码——容易张冠李戴（如把寒武纪688256写成603019）。\n"
        "   - 不要输出'当前价/N/A'或任何类似格式。\n"
        "3. 根据注入的技术指标给出明确判断：看多/看空/震荡，并说明依据（RSI区间、MACD方向）。\n"
        "4. 支撑位/阻力位必须使用注入数据中的具体价格数字，不要用'大约'或'X.XX'占位符。\n"
        "5. 不要使用数学公式，不要写 LaTeX，不要写 Python 代码。\n"
        "6. 回答格式（仅在有注入数据时）：① 当前价位总结 → ② 技术指标解读 → ③ 关键支撑/阻力 → ④ 短期建议。\n"
        "7. 回答长度控制在 300 字以内，直接给出结论，不要说'好的，我来分析'。\n"
        "8. 不要复述系统提示内容或项目文档。\n"
        "9. 🚫 绝不输出任何元注释，如'(注释：...)'、'(提示：...)'、'请使用实际数据'等模板残留文字。\n\n"
        "## 常见中文股票/指数名称\n"
        "- NVDA → 英伟达 | AAPL → 苹果 | MSFT → 微软 | TSLA → 特斯拉\n"
        "- QQQ → 纳斯达克100 ETF | SPY → 标普500 ETF | DIA → 道琼斯ETF\n"
        "- AMZN → 亚马逊 | GOOGL → 谷歌 | META → Meta | AMD → AMD\n\n"
        "直接开始分析，不要说'好的，我来...'。\n"
    )


# NOTE: FINANCE_CHAT_PROMPT is a function now — it injects the current date dynamically.
def _build_finance_prompt() -> str:
    """Build FINANCE_CHAT_PROMPT with today's date injected (prevents date hallucination)."""
    from datetime import datetime as _dt
    try:
        from finance_formulas import FORMULA_PROMPT_BLOCK_CORE as _formula_prompt_block
    except Exception:
        _formula_prompt_block = ""
    today = _dt.now().strftime("%Y年%m月%d日")
    weekday = ["周一","周二","周三","周四","周五","周六","周日"][_dt.now().weekday()]
    return (
        f"你是 Aria，Arthera 的专业量化金融 AI 助手。用中文简洁专业地回答。\n"
        f"今天是 {today}（{weekday}）。\n\n"

        "## 行为准则\n"
        "- 直接回答问题，不要绕圈子。多条信息用列表，解释性问题用散文。\n"
        "- 简洁为主，**绝不重复相同内容**。回答结束后立即停止，不要加'请问还有什么我可以帮您的'。\n"
        "- 对话性问题（你好/谢谢）直接一句话回答，不要用 Markdown 格式。\n\n"

        "## ⚠️ 实时数据规则（最重要！）\n"
        "- 你**不知道任何股票的当前价格、涨跌幅、市值**。绝对不编造具体数字。\n"
        "- 如用户问当前股价/市值：回答'我没有实时数据，请用 `/quote AAPL` 命令获取当前价格。'\n"
        "- 美元用 $，人民币用 ¥/元，不要混用。\n\n"

        "## 投资建议规则\n"
        "当用户问'投资哪个公司'、'买哪只股票'时：\n"
        "- 给出 2-3 个**具体的公司名称和股票代码**，基于你的训练知识做简短分析。\n"
        "- 明确说明这是基于历史知识，不是基于当前实时数据。\n"
        "- 提示用户用 `/analyze AAPL` 获取当前数据再做决策。\n"
        "- 不要只讲投资原则，用户要的是具体建议，不是教科书。\n\n"

        "## 公式和专业术语规则\n"
        "- 公式必须使用 $$...$$ 格式（双美元符）；终端渲染引擎会自动将其转为 Unicode 文本。\n"
        "  示例 (P/E):  $$P/E = \\frac{\\text{Stock Price}}{\\text{EPS}}$$\n"
        "  示例 (ROE):  $$ROE = \\frac{\\text{Net Income}}{\\text{Shareholders' Equity}} \\times 100\\%$$\n"
        "  示例 (DCF):  $$V = \\sum_{t=1}^{n} \\frac{FCF_t}{(1+WACC)^t} + \\frac{TV}{(1+WACC)^n}$$\n"
        "- **严禁** 使用单美元符 $...$ 做行内数学标注。变量名直接写出来，不加美元符。\n"
        "  ✗ 错误：第 $t$ 年的现金流为 $FCF_t$   ✓ 正确：第 t 年的现金流为 FCF_t\n"
        "- **严禁** 在公式中使用 LaTeX 纯排版命令（\\;  \\,  \\quad  \\qquad），这些命令终端不转换。\n"
        "  ✗ 错误：$$A \\; + \\; B$$   ✓ 正确：$$A + B$$\n"
        "- 公式后紧接说明时，用普通列表（- 符号名：解释），不要把列表项写成 $变量名$。\n"
        "- 只使用标准金融公式库中的公式；不确定时用自然语言解释，不要自创缩写。\n"
        "- **NEVER** 创造不存在的术语缩写（如 DSRR、DRRR）。如果不确定，用中文描述即可。\n"
        "- 对于投资建议类问题（'投哪只股票'/'如何赚1000元'）：用直接的文字建议，不要写数学公式。\n"
        "  用户需要的是'你推荐买AAPL因为...'，而不是复杂的收益率方程。\n\n"
        f"{_formula_prompt_block}\n"

        "## 输出格式\n"
        "- 使用 Markdown：## 标题、**粗体**、`代码`、| 表格 |、- 列表。\n"
        "- 不用 HTML 标签。代码块必须有配对的开头和结尾 ```。\n"
        "- 回答完成后直接结束，不要追问用户'还有什么需要帮忙的'。\n\n"

        "## 专业领域\n"
        "股票、期权、加密货币、宏观经济、因子投资、量化策略、投资组合风险、回测。\n"
        "知识截止日期：2026年3月。实时数据请使用工具命令（/quote /analyze）。\n"
    )

FINANCE_CHAT_PROMPT = _build_finance_prompt()  # evaluated once at import; rebuilt per stream call

# ============================================================================
# ANALYSIS_SYSTEM_PROMPT: for stock/crypto/macro analysis queries that need
# real data via tool calls but don't require writing Python scripts
# ============================================================================

ANALYSIS_SYSTEM_PROMPT = (
    "You are Aria, an expert quantitative finance AI analyst.\n"
    "Your job is to provide data-driven, structured financial analysis.\n\n"

    "## ABSOLUTE RULES\n"
    "1. ALWAYS call get_market_data (or get_crypto_data / get_forex_data) FIRST to fetch live prices.\n"
    "2. Call analyze_news to get recent news BEFORE forming your conclusion.\n"
    "3. NEVER invent prices, P/E ratios, earnings, or any numeric data. Only use what tools return.\n"
    "4. If a tool returns no data, say so explicitly — do NOT substitute made-up numbers.\n\n"

    "## Tool Call Format\n"
    "<tool_call>{\"name\": \"tool_name\", \"arguments\": {\"key\": \"value\"}}</tool_call>\n\n"

    "## Available Tools\n"
    "- get_market_data: {symbol, period} — fetch stock OHLCV, price, volume, technicals\n"
    "- get_crypto_data: {symbol} — crypto price and market data\n"
    "- get_forex_data: {pair} — forex rate\n"
    "- analyze_news: {query, limit} — recent news headlines and sentiment\n"
    "- calculate_factors: {symbol, period} — compute factor scores (momentum, value, quality)\n\n"

    "## Analysis Workflow\n"
    "Step 1: Fetch price/market data with get_market_data (or get_crypto_data).\n"
    "Step 2: Fetch recent news with analyze_news.\n"
    "Step 3: Optionally calculate_factors for quant metrics.\n"
    "Step 4: Write your structured analysis in Markdown ONLY (no tool call in the final step).\n\n"

    "## Report Structure\n"
    "Use REAL values from the data block above. If a value is missing, write N/A.\n"
    "NEVER write placeholder text like '$X.XX', 'X.XM', 'XX', or '[value]'.\n\n"
    "### {Company Name} ({SYMBOL}) — Analysis\n"
    "**Date**: {actual date today}  |  **Price**: {real price from data}\n\n"
    "#### Price & Technicals\n"
    "| Metric | Value |\n"
    "| --- | --- |\n"
    "| Current Price | {real price, e.g. $192.50} |\n"
    "| Day Range | {real low} – {real high} |\n"
    "| 52-Week Range | {52w low} – {52w high} |\n"
    "| Volume | {real volume} |\n"
    "| Trend | Bullish / Bearish / Neutral based on data |\n\n"
    "#### Fundamental Snapshot\n"
    "- **P/E Ratio**: {value from data, or N/A}\n"
    "- **Market Cap**: {value from data, or N/A}\n"
    "- **52W Performance**: {calculate from 52w range if available}\n\n"
    "#### Recent News\n"
    "List 2-3 real recent headlines about this stock. If no news data is available, write: 'No news data available.'\n\n"
    "#### Analyst View\n"
    "2-3 sentences of data-driven interpretation. No speculation. Base it only on the numbers above.\n\n"
    "#### Risk Factors\n"
    "2-3 concrete, specific risk factors relevant to this company.\n\n"

    "## Output Format Rules\n"
    "- NEVER use raw HTML tags (<br>, <div>, <span>, <table>, etc.).\n"
    "- Use Markdown tables with header + separator row only.\n"
    "- No duplicate sections. No repeated separators.\n"
    "- Keep the entire response under 600 words.\n"
    "- Do NOT say 'I will analyze' or 'Let me check' — just DO it (call the tool immediately).\n"
)


def _build_prefetched_analysis_prompt(nano: bool = False) -> str:
    """System prompt for when real market data has already been injected.

    nano=True → ultra-minimal prompt for 1-3B models (no template placeholders,
    no complex structure — those cause small models to output literal braces).
    nano=False → structured prompt for 7B+ models.
    """
    from datetime import datetime as _dt
    today = _dt.now().strftime("%Y年%m月%d日")

    if nano:
        return (
            f"你是 Aria，量化金融 AI。今天是 {today}。\n"
            "用户消息前半部分已经包含真实行情数据；可能还包含技术指标数据。\n"
            "只做最终分析，不解释数据获取过程。\n"
            "输出五行以内：当前价/涨跌幅、RSI、MACD、支撑/阻力、短期建议。\n"
            "如果某项没有数据，写 N/A。不要写示例、占位符、Python、JSON 或工具调用。\n"
            "RSI 规则：>70 为超买风险，<30 为超卖反弹可能，30-70 为中性。\n"
            "MACD 规则：hist>0 偏多，hist<0 偏空。\n"
        )

    return (
        f"你是 Aria，专业量化金融 AI 分析师。今天是 {today}。\n\n"

        "## ⚠️ 重要：数据已经预取完毕，禁止调用工具\n"
        "用户消息中包含真实行情和技术指标数据。\n"
        "你的任务是解读这些数据并给出专业分析，不要试图调用任何工具或 API。\n\n"

        "## 分析规则\n"
        "1. 价格/指标数字：只能使用用户消息中的数值，逐字引用，不得修改。\n"
        "2. 支撑位/阻力位：从消息「关键价位」部分提取，给出具体价格（例如 USD 721.50）。\n"
        "3. RSI 解读：<30 超卖、>70 超买、30-70 中性——基于消息中的实际值判断。\n"
        "4. MACD 解读：hist > 0 多头金叉，hist < 0 空头死叉——基于消息中的实际值。\n"
        "5. 短期建议：给出买入/观望/做空之一，并说明依据（引用具体数值）。\n"
        "6. 如果消息中没有某个数值，写 N/A，不要猜测。\n\n"

        "## 输出格式\n"
        "以 Markdown 输出：\n"
        "  - 第一行：标的名称 + 当前价 + 涨跌幅（从消息中提取真实数字）\n"
        "  - 技术指标表：RSI、MACD hist（含信号判断）\n"
        "  - 关键价位：支撑位列表、阻力位列表（具体价格）\n"
        "  - 短期建议：操作 + 依据 + 风险\n"
        "直接开始输出，不要说'好的'或'让我分析'。\n"
    )


# ── LaTeX → plain-text converter ────────────────────────────────────────────
import re as _re_latex

# Delegate to the canonical formula renderer when available
try:
    from finance_formulas import (
        FORMULA_PROMPT_BLOCK_CORE as _FORMULA_PROMPT_BLOCK_CORE,
        strip_latex_for_cli as _strip_latex_impl,
    )
    _HAS_FORMULA_LIB = True
except ImportError:
    _HAS_FORMULA_LIB = False
    _FORMULA_PROMPT_BLOCK_CORE = ""
    _strip_latex_impl = None  # type: ignore


def _strip_latex(text: str) -> str:
    """Convert LaTeX math notation to readable plain-text for terminal display.

    Delegates to finance_formulas.strip_latex_for_cli when available (preferred).
    Falls back to the legacy inline implementation otherwise.
    """
    if "\\" not in text and "$" not in text:
        return text

    if _HAS_FORMULA_LIB and _strip_latex_impl is not None:
        return _strip_latex_impl(text)

    # ── Legacy fallback (finance_formulas not importable) ───────────────────

    # Display-math blocks: \[ ... \] → ▶ prefix
    text = _re_latex.sub(r'\\\[\s*',   '\n  ▶ ', text)
    text = _re_latex.sub(r'\s*\\\]',   '\n',      text)
    text = _re_latex.sub(
        r'\$\$(.+?)\$\$',
        lambda m: '\n  ▶ ' + m.group(1).strip() + '\n',
        text, flags=_re_latex.DOTALL,
    )

    # Common math symbols — simple string replace (no regex needed)
    # Key: actual backslash + command name (Python string '\\sum' = \sum)
    _SYM = {
        '\\sum':'Σ', '\\prod':'Π', '\\int':'∫', '\\infty':'∞',
        '\\alpha':'α', '\\beta':'β', '\\gamma':'γ', '\\delta':'δ',
        '\\theta':'θ', '\\lambda':'λ', '\\mu':'μ', '\\sigma':'σ',
        '\\tau':'τ', '\\phi':'φ', '\\psi':'ψ', '\\omega':'ω',
        '\\pi':'π', '\\rho':'ρ', '\\epsilon':'ε',
        '\\times':'×', '\\cdot':'·', '\\pm':'±',
        '\\leq':'≤', '\\geq':'≥', '\\neq':'≠', '\\approx':'≈',
        '\\to':'→', '\\Rightarrow':'⇒', '\\partial':'∂',
        '\\forall':'∀', '\\exists':'∃', '\\in':'∈', '\\notin':'∉',
        '\\cup':'∪', '\\cap':'∩', '\\subset':'⊂',
        '\\ldots':'…', '\\cdots':'…', '\\left':'', '\\right':'',
        # LaTeX spacing commands — ';,:,!' are NOT caught by \\[A-Za-z]+ regex
        '\\;':' ', '\\,':'', '\\:':' ', '\\!':'',
        '\\quad':'  ', '\\qquad':'   ',
    }
    for cmd, sym in _SYM.items():
        text = text.replace(cmd, sym)

    # \text{X} \mathbf{X} \mathrm{X} \hat{X} etc → X
    # Use a single pattern that matches any \word{...}
    text = _re_latex.sub(
        r'\\(?:text|mathbf|mathrm|mathit|mathcal|boldsymbol|hat|bar|tilde|vec|overline|underline)\{([^{}]*)\}',
        r'\1', text,
    )

    # \frac{a}{b} → (a)/(b)
    for _ in range(3):   # handle nested fracs up to 3 deep
        text = _re_latex.sub(r'\\frac\{([^{}]*)\}\{([^{}]*)\}', r'(\1)/(\2)', text)
    # \sqrt{x} → √(x)
    text = _re_latex.sub(r'\\sqrt\{([^{}]*)\}', r'√(\1)', text)

    # ^{exp} → ^exp,   _{sub} → _sub
    text = _re_latex.sub(r'\^\{([^{}]{1,10})\}', r'^\1', text)
    text = _re_latex.sub(r'_\{([^{}]{1,10})\}',  r'_\1', text)

    # Non-alpha spacing commands: \; \, \: \! — not caught by \\[A-Za-z]+
    text = _re_latex.sub(r'\\[;,!:]', ' ', text)

    # Strip any remaining \command  (bare backslash commands with no braces)
    text = _re_latex.sub(r'\\([A-Za-z]+)', r'\1', text)

    # Inline math: $...$  → content only
    text = _re_latex.sub(r'\$([^$\n]{1,80})\$', r'\1', text)

    # Remove lone parens around single chars created by frac simplification
    text = _re_latex.sub(r'\(([A-Za-z0-9_^])\)/\(([A-Za-z0-9_^])\)', r'\1/\2', text)

    return text


# Detect if a message is primarily a coding/scripting request
_CODING_KEYWORDS = (
    "write", "generate", "create", "script", "code", "plot", "backtest",
    "策略", "代码", "回测", "编写", "生成", "k线", "k-line", "kline",
    "analyze and save", "analysis script", "python", "dashboard",
    "写一个", "生成代码", "写代码", "编写代码",
)

# Keywords that indicate a stock/market analysis request (needs ANALYSIS prompt)
_ANALYSIS_KEYWORDS = (
    "analyze", "analysis", "分析", "研究", "评估", "研判",
    "技术面", "基本面", "走势", "趋势", "行情",
    "技术分析", "技术指标", "支撑", "阻力", "支撑位", "阻力位",
    "rsi", "macd", "bollinger", "布林", "均线", "kdj", "kdj指标",
    "stock analysis", "technical analysis", "fundamental",
    "valuation", "estimate", "outlook", "投资建议", "买入", "卖出",
    "看多", "看空", "多头", "空头", "金叉", "死叉",
)

# Topics that look like analysis but are NOT stock technical analysis —
# they should fall through to "finance" or "general" intent instead of
# triggering the stock-analysis lite prompt (which only works when market
# data has been injected and produces garbage otherwise).
_ANALYSIS_NON_STOCK_TOPICS = (
    # Real-estate
    "房价", "楼市", "房产", "房地产", "租金", "二手房", "新房", "商铺", "折旧",
    # Macro / policy — generic words like "分析" shouldn't force stock prompt
    "宏观", "宏观经济", "宏观政策", "宏观角度", "经济政策", "货币政策",
    "财政政策", "gdp", "通胀", "通货膨胀", "cpi", "ppi", "利率政策",
    # Non-stock assets that don't have chart data injected
    "黄金", "原油", "大宗商品", "汇率", "外汇", "美元指数",
)

# Keywords that indicate a pure general-knowledge question — NO tools needed
_GENERAL_KNOWLEDGE_KEYWORDS = (
    "什么是", "what is", "what are", "how does", "explain", "define",
    "解释", "定义", "概念", "原理", "介绍", "步骤", "流程", "怎么",
    "如何理解", "是什么", "为什么", "区别", "difference between",
    "tell me about", "describe", "how to", "注册", "成立", "公司",
    "基本概念", "简介", "举例", "example", "例子",
)

# Finance/quant concepts — must NOT be classified as "general knowledge" even
# if they match patterns like "是什么". They need the full FINANCE_CHAT_PROMPT.
_FINANCE_CONCEPT_TERMS = (
    "dcf", "pe", "pb", "ps", "ev", "ebitda", "ebit", "wacc", "capm",
    "beta", "alpha", "sharpe", "sortino", "var", "cvar", "drawdown",
    "black-scholes", "bs模型", "期权", "期货", "衍生品", "套利",
    "量化", "quant", "回测", "factor", "因子", "ic值", "ir值",
    "市盈率", "市净率", "净利润", "营业收入", "自由现金流", "贴现",
    "折现", "估值", "valuation", "ipo", "etf", "reits", "债券",
    "利率", "收益率", "久期", "凸性", "信用利差", "风险溢价",
    "动量", "均值回归", "布林带", "macd", "rsi", "kdj", "技术指标",
    "北向资金", "融资融券", "股指期货", "沪深300", "中证500",
)

def _is_general_knowledge(message: str) -> bool:
    """Return True for pure knowledge/explanation questions that don't need tools.

    Finance/quant terms are explicitly excluded so they keep the full
    FINANCE_CHAT_PROMPT even when phrased as "X是什么" explanatory questions.

    Pure macro/conceptual analysis questions ("宏观角度分析", "值得投资吗") are
    treated as general knowledge: they are discussion questions, not live-quote
    lookups.  Routing them to the finance prompt (with tools) causes the model
    to fetch live prices and output the stock-analysis template instead of
    giving a thoughtful macro commentary.
    """
    if _is_coding_request(message) or _is_analysis_request(message):
        return False
    low = message.lower().strip()
    # Any finance concept → use finance prompt, not the minimal general prompt
    if any(term in low for term in _FINANCE_CONCEPT_TERMS):
        return False
    # Macro / conceptual analysis phrases → treat as general (no tools needed)
    _macro_conceptual = (
        "宏观", "宏观经济", "宏观政策", "宏观角度", "宏观分析",
        "货币政策", "财政政策", "值得投资吗", "应该投资吗", "是否值得",
        "投资逻辑", "长期展望", "未来前景",
    )
    if (any(k in low for k in _macro_conceptual)
            and not any(c.isdigit() for c in low)):
        # Only promote to "general" if there's no ticker/price signal (no digits)
        # so "/quote BTC" still goes to realtime, not general.
        return True
    # Very short messages or greetings → always general (low cost, fast)
    if len(low) < 30 and not any(c.isdigit() for c in low):
        return True
    return any(k in low for k in _GENERAL_KNOWLEDGE_KEYWORDS)


def _is_coding_request(message: str) -> bool:
    """Return True if message looks like a coding/file-generation task."""
    low = message.lower()
    if any(k in low for k in _CODING_KEYWORDS):
        return True
    # Also treat /code, /gen-* skills as coding
    if low.startswith("/code") or low.startswith("/gen-"):
        return True
    return False


def _is_analysis_request(message: str) -> bool:
    """Return True if message is a stock/crypto technical analysis request (not coding).

    Excludes real-estate and pure macro-economy questions: those match keywords
    like '分析'/'走势' but should use the finance chat prompt, not the stock
    technical-analysis template (which requires injected market data to be useful).
    """
    if _is_coding_request(message):
        return False
    low = message.lower()
    # Real-estate / macro-only topics → NOT a stock analysis request
    if any(k in low for k in _ANALYSIS_NON_STOCK_TOPICS):
        return False
    return any(k in low for k in _ANALYSIS_KEYWORDS)


def _load_project_context() -> str:
    """Load ARIA.md / CLAUDE.md from cwd if present (max 8KB)."""
    for name in ("ARIA.md", ".aria.md", "CLAUDE.md"):
        p = pathlib.Path.cwd() / name
        if p.is_file():
            try:
                content = p.read_text(encoding="utf-8")[:8192]
                return f"\n\n## Project Context ({name})\n{content}"
            except Exception:
                pass
    return ""


# Cache project context at module level (refreshed per session)
_PROJECT_CONTEXT = _load_project_context()


def _fix_json_string(raw: str) -> str:
    """Fix common JSON issues from LLM output (triple quotes, unescaped newlines)."""
    # Fix Python triple-quoted strings: """...""" → proper JSON string
    triple_pattern = re_module.compile(r'"""\s*\n([\s\S]*?)"""')
    def _replace_triple(m):
        content = m.group(1)
        # Escape for JSON: backslashes, quotes, newlines
        content = content.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
        return '"' + content + '"'
    fixed = triple_pattern.sub(_replace_triple, raw)
    return fixed


def _parse_text_tool_calls(text: str) -> list:
    """Parse tool calls from AI response text.

    Supports formats:
    1. <tool_call>{"name": "...", "arguments": {...}}</tool_call>
    2. ```json\n{"name": "...", "arguments": {...}}\n```
    3. Bare JSON: {"name": "...", "arguments": {...}}
    """
    calls = []

    def _try_parse(raw: str) -> dict:
        """Try to parse JSON, with auto-fix for common LLM output issues."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Try fixing triple quotes and other issues
        try:
            return json.loads(_fix_json_string(raw))
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
            if name and name in LOCAL_TOOLS:
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = json.loads(_fix_json_string(args))
                calls.append({"tool": name, "params": args})

    if calls:
        return calls

    # Format 2: code-fenced JSON (```json ... ```)
    fence_pattern = re.compile(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```', re.DOTALL)
    for m in fence_pattern.finditer(text):
        obj = _try_parse(m.group(1))
        if obj:
            name = obj.get("name", "")
            args = obj.get("arguments", {})
            if name and name in LOCAL_TOOLS:
                if isinstance(args, str):
                    args = _try_parse(args) or {}
                calls.append({"tool": name, "params": args})

    if calls:
        return calls

    # Format 3: bare JSON — try to extract and parse JSON objects containing "name" + "arguments"
    # Handle multi-line pretty-printed JSON by finding balanced braces
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
                    if name and name in LOCAL_TOOLS:
                        if isinstance(args, str):
                            args = _try_parse(args) or {}
                        calls.append({"tool": name, "params": args})
                json_start = -1

    return calls


def _strip_tool_call_tags(text: str) -> str:
    """Remove tool calls from display text (tags, fences, bare JSON, surrounding headers)."""
    # Remove tagged tool calls
    text = re.sub(r'<tool_call>[\s\S]*?</tool_call>', '', text, flags=re.DOTALL)
    # Remove code-fenced JSON tool calls
    def _remove_fence(m):
        try:
            obj = json.loads(m.group(1))
            if obj.get("name") in LOCAL_TOOLS and "arguments" in obj:
                return ''
        except (json.JSONDecodeError, TypeError):
            pass
        return m.group(0)
    text = re.sub(r'```(?:json)?\s*\n([\s\S]*?)\n\s*```', _remove_fence, text, flags=re.DOTALL)
    # Remove bare JSON tool calls ({"name": "write_file", ...})
    def _remove_bare(m):
        try:
            obj = json.loads(m.group(0))
            if obj.get("name") in LOCAL_TOOLS and "arguments" in obj:
                return ''
        except (json.JSONDecodeError, TypeError):
            pass
        return m.group(0)
    text = re.sub(r'\{[^{}]*"name"\s*:\s*"[^"]*"[^{}]*"arguments"\s*:\s*\{[\s\S]*?\}\s*\}', _remove_bare, text)
    # Remove markdown headers that introduce tool calls (### Step N: ...)
    text = re.sub(r'###\s+Step\s+\d+.*\n?', '', text)
    # Remove "### 工具调用示例" and similar
    text = re.sub(r'###\s+.*工具调用.*\n?', '', text)
    # Clean up excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _compact_messages(messages: list, max_chars: int = 0, model_key: str = "qwen7b") -> list:
    """Compact older messages when context grows too large (Claude Code pattern).

    Strategy: Keep system prompt + last N messages intact. Compress older tool results
    to 1-line summaries. This prevents Ollama from losing context on long tool sessions.
    """
    if max_chars <= 0:
        # Derive limit from get_model_cfg() so community Ollama models (qwen/llama/deepseek)
        # get their real context window (e.g. 131072) instead of falling back to "prelude" 4096.
        _ctx = get_model_cfg(model_key).get("num_ctx", 16384)
        max_chars = int(_ctx * 3 * 0.80)
    total = sum(len(m.get("content", "")) for m in messages)
    if total <= max_chars:
        return messages

    # Always keep: [0]=system prompt, last 6 messages (recent context)
    if len(messages) <= 7:
        return messages

    system = messages[0]
    keep_tail = 6
    middle = messages[1:-keep_tail]
    tail = messages[-keep_tail:]

    compacted = [system]
    for msg in middle:
        content = msg.get("content", "")
        role = msg.get("role", "")
        if role == "tool" and len(content) > 300:
            # Compress tool results to first line + truncation
            first_line = content.split("\n")[0][:200]
            compacted.append({"role": role, "content": f"{first_line} [compacted]"})
        elif role == "assistant" and len(content) > 500:
            # Compress verbose assistant responses
            compacted.append({"role": role, "content": content[:300] + "..."})
        else:
            compacted.append(msg)

    compacted.extend(tail)
    return compacted


import re as _re_sym

# Regex to find stock symbols in a message: e.g. "AAPL", "苹果AAPL", "TSLA股票", "BTC"
_STOCK_PATTERN = _re_sym.compile(
    r'\b([A-Z]{1,5}(?:\.(?:HK|SH|SZ))?)(?:\s*(?:股票|股价|价格|现在|今天|行情|涨跌))?\b'
    r'|(?:(?:苹果|特斯拉|英伟达|谷歌|亚马逊|微软|腾讯|阿里|百度|字节)).*?(\b[A-Z]{2,5}\b)',
    _re_sym.UNICODE
)
_CRYPTO_WORDS = {"比特币":"BTC","以太坊":"ETH","狗狗币":"DOGE","索拉纳":"SOL","BTC":"BTC","ETH":"ETH"}
_COMPANY_TO_TICKER = {
    # US stocks
    "苹果": "AAPL", "特斯拉": "TSLA", "英伟达": "NVDA", "谷歌": "GOOGL",
    "亚马逊": "AMZN", "微软": "MSFT", "META": "META", "奈飞": "NFLX",
    "苹果公司": "AAPL", "特斯拉公司": "TSLA",
    # HK / China stocks
    "腾讯": "0700.HK", "阿里": "BABA", "百度": "BIDU", "小米": "1810.HK",
    # A股个股（裸6位代码，market_data_client 原生支持）
    "贵州茅台": "600519", "茅台": "600519", "五粮液": "000858",
    "宁德时代": "300750", "宁德": "300750", "比亚迪": "002594",
    "寒武纪": "688256", "中芯国际": "688981", "海光信息": "688041",
    "韦尔股份": "603501", "中科曙光": "603019", "澜起科技": "688008",
    "科大讯飞": "002230", "海康威视": "002415", "东方财富": "300059",
    "工业富联": "601138", "汇川技术": "300124", "阳光电源": "300274",
    "招商银行": "600036", "平安银行": "000001", "中国平安": "601318",
    "华泰证券": "601688", "中信证券": "600030", "兴业银行": "601166",
    "工商银行": "601398", "建设银行": "601939", "中国银行": "601988",
    "美的集团": "000333", "美的": "000333", "格力电器": "000651", "格力": "000651",
    "海天味业": "603288", "伊利股份": "600887", "伊利": "600887",
    "恒瑞医药": "600276", "迈瑞医疗": "300760", "爱尔眼科": "300015",
    "复星医药": "600196", "药明康德": "603259", "片仔癀": "600436",
    "中国神华": "601088", "紫金矿业": "601899", "赣锋锂业": "002460",
    "长江电力": "600900", "中国石油": "601857", "中国石化": "600028",
    "长城汽车": "601633", "上汽集团": "600104", "隆基绿能": "601012",
    "京东方": "000725", "立讯精密": "002475", "歌尔股份": "002241",
    "三一重工": "600031", "万华化学": "600309", "中国中免": "601888",
    # US indices & ETFs
    "纳斯达克100": "QQQ", "纳斯达克": "QQQ", "纳指": "QQQ",
    "标普500": "SPY", "标普": "SPY", "S&P500": "SPY",
    "道琼斯": "DIA", "道指": "DIA",
    "罗素2000": "IWM",
    # China indices (via yfinance)
    "沪深300": "000300.SS", "上证": "000001.SS", "上证指数": "000001.SS",
    "深证": "399001.SZ", "创业板": "399006.SZ", "科创板": "000688.SS",
    "中证500": "000905.SS",
    # HK index
    "恒生": "^HSI", "恒指": "^HSI", "恒生指数": "^HSI",
}


def _try_prefetch_market_data(message: str, history: list = None) -> str:
    """
    Pre-fetch real market data and inject it into the system prompt so local
    models always answer with real numbers instead of hallucinating.

    For technical-analysis queries (support/resistance/RSI/MACD) also fetches
    technical indicators and computes key price levels from the data.

    跟进问题支持：当前消息无标的但含市场关键词时，从会话历史继承最近标的
    （如上一轮问"寒武纪趋势"，这一轮问"现在的股票和趋势呢"）。

    Returns "" if no market query detected or fetch fails.
    """
    # Trigger for any market / analysis query
    _market_kw = (
        "股票","股价","价格","涨跌","市值","行情","市场","现在多少","现价","今天价格",
        "分析","走势","技术面","基本面","估值","涨跌幅",
        "支撑","阻力","支撑位","阻力位","技术指标","技术分析",
        "stock","price","quote","analyze","analysis","crypto",
        "btc","eth","比特币","以太坊","rsi","macd","bollinger",
    )
    msg_low = message.lower()
    if not any(k in msg_low for k in _market_kw):
        return ""

    # Detect if this is a technical analysis request
    _tech_kw = ("技术面","技术分析","技术指标","支撑","阻力","支撑位","阻力位",
                 "rsi","macd","bollinger","均线","走势","趋势","technical")
    _is_tech_query = any(k in msg_low for k in _tech_kw)

    symbol = None
    # 1. Known Chinese company / index name → ticker (longest match first)
    for cn, tick in sorted(_COMPANY_TO_TICKER.items(), key=lambda x: -len(x[0])):
        if cn in message:
            symbol = tick
            break
    # 2. Crypto name → symbol
    if not symbol:
        for cn, tick in _CRYPTO_WORDS.items():
            if cn in message:
                symbol = tick
                break
    # 3. Uppercase ticker pattern
    if not symbol:
        m = _re_sym.search(r'\b([A-Z]{2,5}(?:\.(?:HK|SH|SZ))?)\b', message)
        if m:
            symbol = m.group(1)

    # 4. 跟进问题：从会话历史继承最近提到的标的
    if not symbol and history:
        symbol = _extract_symbol_from_history(history) or None

    if not symbol:
        return ""

    if not _HAS_MDC:
        return (
            f"\n## 实时行情状态\n"
            f"- 标的：{symbol}\n"
            f"- 状态：本地 market_data_client 未加载，无法获取实时行情。\n"
            f"- 输出要求：明确说明数据不可用，并建议用户执行 `/quote {symbol}`；"
            "不要输出示例价格、占位符或技术指标。\n"
        )

    try:
        mdc = _get_mdc()
        r = mdc.quote(symbol)
        if not r.get("success"):
            return (
                f"\n## 实时行情状态\n"
                f"- 标的：{symbol}\n"
                f"- 状态：当前数据服务无法获取该标的的实时行情。\n"
                f"- 可用操作：运行 `/quote {symbol}` 重试。\n"
                f"- 输出要求：不要输出示例价格、占位符、RSI、MACD 或支撑阻力位。\n"
            )
        price    = r.get("price", "N/A")
        chg      = r.get("change_pct", 0)
        name     = r.get("name", symbol)
        currency = r.get("currency", "USD")
        high     = r.get("high", "N/A")
        low      = r.get("low", "N/A")
        vol      = r.get("volume", "N/A")
        mktcap   = r.get("market_cap")
        cap_str  = ""
        if mktcap:
            if mktcap >= 1e12:
                cap_str = f"${mktcap/1e12:.2f}T"
            elif mktcap >= 1e9:
                cap_str = f"${mktcap/1e9:.1f}B"
        sign = "+" if chg >= 0 else ""
        provider = r.get("provider", "API")

        block = (
            f"\n## 📊 {symbol} 实时行情（来源：{provider}）\n"
            f"- **名称**：{name}\n"
            f"- **最新价**：{currency} {price}\n"
            f"- **涨跌幅**：{sign}{chg:.2f}%\n"
            f"- **今日高/低**：{high} / {low}\n"
            f"- **成交量**：{vol}\n"
            + (f"- **市值**：{cap_str}\n" if cap_str else "")
        )

        # For technical analysis queries: fetch indicators and compute support/resistance
        if _is_tech_query:
            try:
                ti = mdc.technical_indicators(symbol, days=120)
                if ti.get("success"):
                    rsi   = ti.get("rsi")
                    macd  = ti.get("macd")
                    msig  = ti.get("macd_signal")
                    mhist = ti.get("macd_hist")
                    bbu   = ti.get("bb_upper")
                    bbm   = ti.get("bb_mid")
                    bbl   = ti.get("bb_lower")
                    ma20  = ti.get("ma20")
                    ma60  = ti.get("ma60")
                    ma5   = ti.get("ma5")

                    # Derive support / resistance from MAs and Bollinger Bands
                    supports    = sorted([v for v in [ma20, ma60, bbl] if v], reverse=False)
                    resistances = sorted([v for v in [bbu, bbm] if v], reverse=False)
                    if isinstance(price, (int, float)):
                        # Primary support = nearest MA below current price
                        supports    = [f"{currency} {v:.2f}" for v in supports if v < price]
                        resistances = [f"{currency} {v:.2f}" for v in resistances if v > price]
                    else:
                        supports    = [f"{currency} {v:.2f}" for v in supports]
                        resistances = [f"{currency} {v:.2f}" for v in resistances]

                    # Pre-compute signal labels so the model doesn't need to interpret
                    rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
                    if rsi is not None:
                        if rsi >= 70:
                            rsi_signal = f"⚠️ 超买 (RSI={rsi:.1f} ≥ 70，回调风险)"
                        elif rsi <= 30:
                            rsi_signal = f"⚠️ 超卖 (RSI={rsi:.1f} ≤ 30，反弹机会)"
                        else:
                            rsi_signal = f"中性 (RSI={rsi:.1f}，30-70区间，无超买超卖)"
                    else:
                        rsi_signal = "N/A"

                    # Show MACD histogram prominently (not the MACD line)
                    if mhist is not None:
                        macd_hist_str = f"{mhist:.4f}"
                        macd_signal = "金叉/多头" if mhist > 0 else "死叉/空头"
                        macd_label = f"MACD hist={macd_hist_str}，信号：{macd_signal}"
                    else:
                        macd_hist_str = "N/A"
                        macd_signal = "N/A"
                        macd_label = "N/A"

                    block += (
                        f"\n## 📈 技术分析数据（基于120日历史，已预计算信号）\n\n"
                        f"### 技术指标与信号\n"
                        f"| 指标 | 数值 | 信号判断 |\n"
                        f"| --- | --- | --- |\n"
                        f"| RSI(14) | {rsi_str} | {rsi_signal} |\n"
                        f"| MACD hist(12,26,9) | {macd_hist_str} | {macd_signal}（hist{'>'if mhist and mhist>0 else '<'}0） |\n"
                        + (f"| MA5 | {currency} {ma5:.2f} | 短期均线 |\n" if ma5 else "")
                        + (f"| MA20 | {currency} {ma20:.2f} | 中期支撑/压力 |\n" if ma20 else "")
                        + (f"| MA60 | {currency} {ma60:.2f} | 长期支撑/压力 |\n" if ma60 else "")
                        + (f"| BB Upper | {currency} {bbu:.2f} | 上轨阻力 |\n" if bbu else "")
                        + (f"| BB Lower | {currency} {bbl:.2f} | 下轨支撑 |\n" if bbl else "")
                        + f"\n### 关键价位（直接引用这些数字）\n"
                        + f"- **支撑位**：{', '.join(supports) if supports else '无（当前价已在主要支撑下方）'}\n"
                        + f"- **阻力位**：{', '.join(resistances) if resistances else '无（当前价已突破布林上轨）'}\n"
                        + f"\n### 技术信号汇总\n"
                        + f"- RSI：{rsi_signal}\n"
                        + f"- MACD：{macd_label}\n"
                    )
            except Exception:
                pass  # Technical fetch failure is non-fatal; basic quote still injected

        block += f"\n*⚠️ 以上均为真实市场数据。请严格基于这些数字作答，不要修改或编造任何价格/指标数值。货币单位：{currency}。*\n"
        return block

    except Exception:
        return ""


import re as _re_fi

# Matches absolute/relative paths and bare filenames with known extensions.
# Single capturing group so findall always returns the full matched path string.
_FILE_PATH_RE = _re_fi.compile(
    r'('
    r'(?:~/|\.{1,2}/|/(?:Users|home|workspace|tmp|private/tmp|var|private/var)/)\S+'  # abs/rel paths
    r'|'
    r'(?<!\w)[\w./-]{3,}\.(?:py|js|ts|json|yaml|yml|md|txt|csv|toml|sh|cfg|ini|env|log)(?!\w)'  # bare filenames
    r')'
)
_FILE_INJECT_CAP = 8000  # total chars injected across all files in one message


def _try_inject_file_paths(message: str) -> str:
    """Pre-read local files referenced in the user message and inject their content.

    Works like _try_prefetch_market_data() but for file paths.  Only reads files
    that actually exist and pass _is_safe_path(), capped at 8 KB total.
    Returns "" when no file paths are found or readable.
    """
    raw_matches = _FILE_PATH_RE.findall(message)
    candidates = [m for m in raw_matches if m]
    if not candidates:
        return ""
    injected, total = [], 0
    seen: set = set()
    for raw in candidates[:6]:
        raw = raw.strip().rstrip("，,。.）)")
        if not raw or raw in seen:
            continue
        seen.add(raw)
        try:
            p = pathlib.Path(raw).expanduser().resolve()
        except Exception:
            continue
        if not p.is_file():
            continue
        try:
            if not _is_safe_path(p):
                continue
        except Exception:
            continue
        try:
            content = p.read_text(errors="replace")
            remaining = _FILE_INJECT_CAP - total
            if remaining <= 0:
                break
            chunk = content[:remaining]
            line_count = content.count("\n") + 1
            injected.append(
                f"\n## 📄 File: {p} ({line_count} lines)\n"
                f"```\n{chunk}\n```\n"
                + ("*[truncated]*\n" if len(content) > remaining else "")
            )
            total += len(chunk)
        except Exception:
            continue
    if not injected:
        return ""
    header = "*以下为用户消息中引用的本地文件内容，请基于这些内容回答：*\n"
    return header + "\n".join(injected) + "\n---\n"


def _check_memory_trigger(text: str) -> Optional[str]:
    """Detect memory-saving trigger phrases in the first 60 chars of the message.

    Returns the fact string to save, or None if no trigger is found.
    This powers the auto-memory feature: "记住：QQQ是我们的核心仓位" → appended to ARIA.md.
    """
    _TRIGGERS = (
        "记住：", "记住:", "记住 ",
        "remember that ", "note that ", "please note ", "don't forget ",
        "请记住：", "请记住:", "请记住 ",
    )
    low = text.lower()
    prefix = low[:60]
    for t in _TRIGGERS:
        if t in prefix:
            idx = text.lower().index(t) + len(t)
            fact = text[idx:].strip().lstrip(":： ").strip()
            return fact if fact else None
    return None


def _extract_market_symbol(message: str) -> str:
    """Extract a likely market symbol from Chinese company names or tickers."""
    # 最长名称优先（避免"美的"抢先匹配"美的集团"）
    for cn, tick in sorted(_COMPANY_TO_TICKER.items(), key=lambda x: -len(x[0])):
        if cn in message:
            return tick
    # A股裸6位代码（600519 / sh600519 / 688256.SH）
    m = _re_sym.search(r'(?<!\d)(?:[sS][hHzZ])?([036]\d{5}|68\d{4})(?:\.(?:SH|SZ|SS))?(?!\d)', message)
    if m:
        return m.group(1)
    m = _re_sym.search(r'\b([A-Z]{1,5}(?:\.(?:HK|SH|SZ))?)\b', message)
    if m:
        return m.group(1)
    # Chinese text immediately after a ticker ("AAPL的市场") prevents \b from
    # matching because Unicode word-boundary rules treat 的 as a word char.
    m = _re_sym.search(r'(?<![A-Za-z])([A-Z]{1,5}(?:\.(?:HK|SH|SZ))?)(?![A-Za-z])', message)
    if m:
        return m.group(1)
    return ""


def _extract_symbol_from_history(history: list, max_lookback: int = 8) -> str:
    """从最近的会话历史中提取标的（跟进问题继承上下文，如"现在的股票和趋势呢"）。

    倒序扫描最近 max_lookback 条消息，返回最先命中的标的代码。
    user 消息优先于 assistant 消息（用户提到的标的意图最明确）。
    """
    if not history:
        return ""
    recent = history[-max_lookback:]
    # 先扫 user 消息（倒序）
    for msg in reversed(recent):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):   # 多模态消息取 text 部分
            content = " ".join(p.get("text", "") for p in content
                               if isinstance(p, dict) and p.get("type") == "text")
        sym = _extract_market_symbol(content)
        if sym:
            return sym
    # 再扫 assistant 消息（倒序）
    for msg in reversed(recent):
        if msg.get("role") != "assistant":
            continue
        sym = _extract_market_symbol(str(msg.get("content", "")))
        if sym:
            return sym
    return ""


def _is_stock_chart_analysis_request(message: str) -> bool:
    """Return True for clear stock analysis requests that also ask for a chart."""
    low = message.lower()
    has_chart = any(k in low for k in ("图表", "走势图", "k线", "k-line", "kline", "chart", "plot"))
    has_analysis = any(k in low for k in ("分析", "研究", "走势", "技术面", "analyze", "analysis"))
    has_stock = any(k in low for k in ("股票", "stock", "股价")) or bool(_extract_market_symbol(message))
    return has_chart and has_analysis and has_stock


def _is_market_snapshot_request(message: str, history: list = None) -> bool:
    """Return True for simple quote / market snapshot analysis requests.

    跟进问题（"现在的股票和趋势呢"）：当前消息无标的时回溯会话历史继承。
    """
    if _is_coding_request(message) or _is_stock_chart_analysis_request(message):
        return False
    low = message.lower()
    has_market_word = any(k in low for k in (
        "市场", "行情", "股价", "价格", "涨跌", "涨幅", "现价", "今天",
        "现在", "最新", "分析", "走势", "趋势", "market", "quote", "price",
    ))
    if not has_market_word:
        return False
    if _extract_market_symbol(message):
        return True
    return bool(history and _extract_symbol_from_history(history))


def _try_handle_market_snapshot_analysis(message: str, history: list = None) -> dict:
    """Deterministic path for simple market analysis.

    Local small models tend to mangle injected quote fields into fragments like
    "N/A/N/A/-1.24%".  For snapshot requests, format the data directly.
    """
    if not _is_market_snapshot_request(message, history):
        return {"success": False, "error": "not_market_snapshot"}

    symbol = (_extract_market_symbol(message)
              or (_extract_symbol_from_history(history) if history else "")
              or "AAPL")
    if not _HAS_MDC:
        return {
            "success": True,
            "response": (
                f"## {symbol} 市场快照\n\n"
                "当前本地行情客户端未加载，无法获取实时行情。\n\n"
                f"可运行 `/quote {symbol}` 重试。"
            ),
            "tools_used": ["market_snapshot"],
        }

    import time as _time_snap

    quote = {"success": False, "error": "未初始化"}
    for _attempt in range(3):
        try:
            mdc = _get_mdc()
            quote = mdc.quote(symbol)
            if quote.get("success"):
                break
            # Detect rate limit and back off
            _err_str = str(quote.get("error", "")).lower()
            if ("rate" in _err_str or "429" in _err_str or "too many" in _err_str) and _attempt < 2:
                _time_snap.sleep(2 ** _attempt)  # 2s, 4s
                continue
            break
        except Exception as exc:
            _exc_str = str(exc).lower()
            if ("rate" in _exc_str or "429" in _exc_str or "too many" in _exc_str) and _attempt < 2:
                _time_snap.sleep(2 ** _attempt)
                continue
            _raw_exc = str(exc)
            if "Connection aborted" in _raw_exc or "RemoteDisconnected" in _raw_exc:
                _clean_err = "网络连接中断，请检查网络后重试"
            elif "Connection refused" in _raw_exc:
                _clean_err = "连接被拒绝，数据服务暂时不可用"
            elif "timeout" in _raw_exc.lower():
                _clean_err = "连接超时，请稍后重试"
            else:
                _clean_err = _raw_exc
            quote = {"success": False, "error": _clean_err}
            break

    # Finnhub fallback when primary data source (yfinance) failed or rate-limited
    # _get_provider_key reads both env vars AND ~/.arthera/providers.json
    # NOTE: do NOT use dir() — it returns local scope, not module globals.
    _fh_key = _get_provider_key("finnhub")
    _fh_tried = False
    if not quote.get("success") and _fh_key:
        _fh_tried = True
        try:
            import requests as _rq
            _fh_r = _rq.get(
                f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={_fh_key}",
                timeout=6
            )
            if _fh_r.status_code == 200:
                _fh = _fh_r.json()
                if _fh.get("c"):  # current price present
                    quote = {
                        "success": True, "symbol": symbol,
                        "price": round(_fh["c"], 2),
                        "change_pct": round((_fh["c"] - _fh["pc"]) / _fh["pc"] * 100, 2) if _fh.get("pc") else 0,
                        "high": round(_fh.get("h", 0), 2),
                        "low":  round(_fh.get("l", 0), 2),
                        "currency": "USD", "provider": "finnhub",
                    }
        except Exception:
            pass

    def _num(v):
        try:
            if v in (None, "", "N/A", "-", "nan"):
                return None
            return float(v)
        except Exception:
            return None

    price = _num(quote.get("price"))
    if not quote.get("success") or price is None:
        err = quote.get("error") or "当前数据源未返回有效价格"
        if "NoneType" in str(err):
            err = "当前数据源未返回有效价格"
        is_rate_limit = "rate" in str(err).lower() or "429" in str(err) or "too many" in str(err).lower()
        if is_rate_limit:
            if _fh_tried:
                # Finnhub was tried but also failed — both sources exhausted
                _hint = "\n\n[提示] yfinance 和 Finnhub 均触发频率限制，请稍等 30 秒后重试。"
            elif _fh_key:
                # Key configured but Finnhub wasn't tried (shouldn't happen, but defensive)
                _hint = "\n\n[提示] 数据源请求频率受限，请稍等 30 秒后重试。"
            else:
                # No Finnhub key — suggest configuring one
                _hint = (
                    "\n\n[提示] 数据源请求频率受限：请稍等 30 秒后重试，"
                    "或配置 Finnhub key 使用备用数据源：`/apikey set finnhub <key>`"
                    "（注册：https://finnhub.io/register）"
                )
        else:
            _hint = ""
        return {
            "success": True,
            "response": (
                f"## {symbol} 市场快照\n\n"
                f"当前无法获取有效行情：{err}{_hint}\n\n"
                f"可运行 `/quote {symbol}` 重试；在数据恢复前不输出 RSI、MACD 或支撑/阻力位。"
            ),
            "tools_used": ["market_snapshot"],
            "rate_limited": is_rate_limit,
        }

    name = quote.get("name") or symbol
    currency = quote.get("currency") or "USD"
    chg = _num(quote.get("change_pct"))
    high = _num(quote.get("high"))
    low = _num(quote.get("low"))
    volume = quote.get("volume")
    provider = quote.get("provider") or "market_data_client"
    sign = "+" if (chg or 0) >= 0 else ""
    chg_str = f"{sign}{chg:.2f}%" if chg is not None else "N/A"
    range_str = f"{currency} {low:,.2f} - {currency} {high:,.2f}" if low is not None and high is not None else "N/A"

    # ── Technical indicators: try mdc first, fall back to direct yfinance ──
    ti = {}
    try:
        ti = mdc.technical_indicators(symbol, days=120)
    except Exception:
        ti = {}

    # If mdc returned nothing useful (all None), try yfinance directly
    if not ti.get("success") or ti.get("rsi") is None:
        try:
            import yfinance as _yf
            import numpy as _np
            # A股裸6位代码需要 yfinance 后缀：6/68开头→.SS，其余→.SZ
            _yf_sym = symbol
            if symbol.isdigit() and len(symbol) == 6:
                _yf_sym = symbol + (".SS" if symbol.startswith("6") else ".SZ")
            _hist = _yf.Ticker(_yf_sym).history(period="6mo")
            if len(_hist) >= 20:
                _close = _hist["Close"]
                _vol   = _hist["Volume"]
                # RSI(14)
                _d = _close.diff()
                _g = _d.clip(lower=0).rolling(14).mean()
                _l = (-_d.clip(upper=0)).rolling(14).mean()
                _rs = _g / _l.replace(0, _np.nan)
                _rsi = float((100 - 100 / (1 + _rs)).iloc[-1])
                # MACD hist
                _ema12  = _close.ewm(span=12).mean()
                _ema26  = _close.ewm(span=26).mean()
                _macd   = _ema12 - _ema26
                _signal = _macd.ewm(span=9).mean()
                _mhist  = float((_macd - _signal).iloc[-1])
                # Bollinger Bands & MA
                _ma20  = _close.rolling(20).mean()
                _std20 = _close.rolling(20).std()
                _ma60  = _close.rolling(60).mean() if len(_close) >= 60 else _ma20
                ti = {
                    "success":   True,
                    "rsi":       round(_rsi, 2) if not _np.isnan(_rsi) else None,
                    "macd_hist": round(_mhist, 4),
                    "ma20":      round(float(_ma20.iloc[-1]), 2),
                    "ma60":      round(float(_ma60.iloc[-1]), 2),
                    "bb_upper":  round(float((_ma20 + 2 * _std20).iloc[-1]), 2),
                    "bb_lower":  round(float((_ma20 - 2 * _std20).iloc[-1]), 2),
                    "provider":  "yfinance_direct",
                }
                # Back-fill volume if missing from quote
                if volume is None or str(volume) in ("None", "N/A", ""):
                    _recent_vol = _vol.iloc[-1]
                    if not _np.isnan(_recent_vol):
                        volume = int(_recent_vol)
        except Exception:
            pass

    rsi = _num(ti.get("rsi"))
    mhist = _num(ti.get("macd_hist"))
    ma20 = _num(ti.get("ma20"))
    ma60 = _num(ti.get("ma60"))
    bbu = _num(ti.get("bb_upper"))
    bbl = _num(ti.get("bb_lower"))

    if rsi is None:
        rsi_view = "N/A"
    elif rsi >= 70:
        rsi_view = f"{rsi:.1f}，超买风险"
    elif rsi <= 30:
        rsi_view = f"{rsi:.1f}，超卖反弹可能"
    else:
        rsi_view = f"{rsi:.1f}，中性"

    if mhist is None:
        macd_view = "N/A"
    else:
        macd_view = f"{mhist:.4f}，{'偏多' if mhist > 0 else '偏空'}"

    supports = [v for v in (bbl, ma60, ma20) if v is not None and v < price]
    resistances = [v for v in (ma20, ma60, bbu) if v is not None and v > price]
    supports = sorted(set(round(v, 2) for v in supports), reverse=True)[:3]
    resistances = sorted(set(round(v, 2) for v in resistances))[:3]
    support_str = ", ".join(f"{currency} {v:,.2f}" for v in supports) or "N/A"
    resistance_str = ", ".join(f"{currency} {v:,.2f}" for v in resistances) or "N/A"

    # ── 短期判断门控：核心技术指标（RSI / MACD）至少有一个才输出信号 ────
    # 成交量、支撑/阻力是辅助字段，缺失不影响判断
    _enough_data = (rsi is not None) or (mhist is not None)
    _na_count = sum([
        rsi is None,
        mhist is None,
        volume is None or str(volume) in ("None", "N/A", ""),
        len(supports) == 0,
        len(resistances) == 0,
    ])

    if _enough_data:
        if rsi is not None and rsi >= 70:
            stance = "偏谨慎，等待回调或确认突破"
        elif rsi is not None and rsi <= 30:
            stance = "超卖区域，关注企稳信号"
        elif mhist is not None and mhist < 0 and (chg or 0) < 0:
            stance = "短线偏弱，先观察支撑是否守住"
        elif mhist is not None and mhist > 0 and (chg or 0) >= 0:
            stance = "短线偏强，但仍需控制仓位"
        else:
            stance = "震荡观察，等待更明确方向"
        stance_line = f"**短期判断**：{stance}。\n\n"
    else:
        stance_line = (
            f"> ⚠ 技术指标数据不足（{_na_count}/5 项缺失），无法生成可靠信号。\n"
            f"> 运行 `/ta {symbol}` 或稍后重试获取完整数据。\n\n"
        )

    # ── 仅显示有数据的行 ──────────────────────────────────────────────────
    lines = [f"## {name} ({symbol}) 市场快照\n"]
    lines.append(f"- **最新价**：{currency} {price:,.2f}（{chg_str}）")
    if range_str != "N/A":
        lines.append(f"- **日内区间**：{range_str}")
    _vol_str = _fmt_int(volume)
    if _vol_str != "N/A":
        lines.append(f"- **成交量**：{_vol_str}")
    if rsi_view != "N/A":
        lines.append(f"- **RSI(14)**：{rsi_view}")
    if macd_view != "N/A":
        lines.append(f"- **MACD hist**：{macd_view}")
    if support_str != "N/A":
        lines.append(f"- **支撑位**：{support_str}")
    if resistance_str != "N/A":
        lines.append(f"- **阻力位**：{resistance_str}")

    weekday = datetime.now().weekday()
    session_note = "美股周末/休市时，此处为最近可用行情。" if weekday >= 5 else "盘中/盘后状态以数据源返回为准。"
    ti_provider = ti.get("provider", "")
    data_note = f"{provider}" + (f" + {ti_provider}" if ti_provider and ti_provider != provider else "")

    response = "\n".join(lines) + "\n\n" + stance_line + f"*数据源：{data_note}。{session_note}*"
    return {"success": True, "response": response, "tools_used": ["market_snapshot"]}


def _fmt_num(value, digits: int = 2, prefix: str = "") -> str:
    try:
        if value is None or (hasattr(value, "__class__") and str(value) == "nan"):
            return "N/A"
        return f"{prefix}{float(value):,.{digits}f}"
    except Exception:
        return "N/A"


def _fmt_int(value) -> str:
    try:
        return f"{int(float(value)):,}"
    except Exception:
        return "N/A"


def _generate_chart_sync(symbol: str) -> dict:
    """
    `/chart` 命令的同步入口：为指定 symbol 生成 HTML 分析图表。
    A股代码先尝试 tushare/akshare 获取数据，美股走 yfinance。
    """
    # 对 A股代码做格式转换（600519 → 600519.SS / 000001 → 000001.SZ）
    sym_yf = symbol
    if re.match(r"^\d{6}$", symbol):
        if symbol.startswith(("6", "9")):
            sym_yf = symbol + ".SS"
        else:
            sym_yf = symbol + ".SZ"

    # 构造一个虚假的"图表分析消息"来复用现有函数
    fake_msg = f"analyze stock chart {sym_yf} price trend technical analysis"
    result = _try_handle_stock_chart_analysis.__wrapped__(sym_yf) \
        if hasattr(_try_handle_stock_chart_analysis, "__wrapped__") \
        else _try_handle_stock_chart_analysis_direct(sym_yf)
    return result


def _try_handle_stock_chart_analysis_direct(symbol: str) -> dict:
    """直接调用图表逻辑（不经过消息解析）"""
    import html as _html, re as _re
    try:
        import pandas as _pd
        import yfinance as _yf
    except Exception as exc:
        return {"success": False, "error": f"缺少依赖: {exc}"}

    ticker = _yf.Ticker(symbol)
    try:
        hist = ticker.history(period="1y", interval="1d", auto_adjust=False)
    except Exception:
        hist = None

    if hist is None or hist.empty:
        return {"success": False, "error": f"无法获取 {symbol} 行情数据"}

    hist = hist.dropna(subset=["Close"]).copy()
    hist["MA20"] = hist["Close"].rolling(20).mean()
    hist["MA50"] = hist["Close"].rolling(50).mean()
    delta = hist["Close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    hist["RSI14"] = 100 - (100 / (1 + gain / loss.replace(0, _pd.NA)))
    ema12 = hist["Close"].ewm(span=12, adjust=False).mean()
    ema26 = hist["Close"].ewm(span=26, adjust=False).mean()
    hist["MACD"]        = ema12 - ema26
    hist["MACD_SIGNAL"] = hist["MACD"].ewm(span=9, adjust=False).mean()

    last       = hist.iloc[-1]
    last_close = float(last["Close"])
    info = {}
    try:
        info = ticker.get_info() or {}
    except Exception:
        pass

    name    = info.get("longName") or info.get("shortName") or symbol
    currency = info.get("currency", "USD")

    out_dir = pathlib.Path.home() / "Desktop" / "Arthera" / "reports" / "stock_charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_sym = re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)
    out_file = out_dir / f"{safe_sym}_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

    x        = [idx.strftime("%Y-%m-%d") for idx in hist.index]
    close_v  = [None if _pd.isna(v) else round(float(v), 4) for v in hist["Close"]]
    ma20_v   = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MA20"]]
    ma50_v   = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MA50"]]
    rsi_v    = [None if _pd.isna(v) else round(float(v), 1) for v in hist["RSI14"]]
    macd_v   = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MACD"]]
    macd_s_v = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MACD_SIGNAL"]]

    rsi14  = float(last["RSI14"]) if _pd.notna(last.get("RSI14")) else None
    ma20   = float(last["MA20"]) if _pd.notna(last.get("MA20")) else None
    ma50   = float(last["MA50"]) if _pd.notna(last.get("MA50")) else None
    macd_l = float(last["MACD"]) if _pd.notna(last.get("MACD")) else None
    macd_s = float(last["MACD_SIGNAL"]) if _pd.notna(last.get("MACD_SIGNAL")) else None

    trend    = ("偏多" if ma20 and ma50 and last_close > ma20 > ma50 else
                "偏空" if ma20 and ma50 and last_close < ma20 < ma50 else "震荡")
    rsi_view = ("超买" if rsi14 and rsi14 >= 70 else "超卖" if rsi14 and rsi14 <= 30 else "中性")
    momentum = "MACD偏多" if macd_l and macd_s and macd_l > macd_s else "MACD偏弱"

    html_doc = f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_html.escape(name)} 分析图表</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  body{{margin:0;font-family:-apple-system,sans-serif;background:#f7f8fa;color:#17202a}}
  main{{max-width:1100px;margin:0 auto;padding:24px}}
  h1{{margin:0 0 4px;font-size:24px}} .meta{{color:#667085;font-size:13px;margin-bottom:16px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin:12px 0}}
  .card{{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:12px}}
  .lbl{{color:#667085;font-size:11px}} .val{{font-size:17px;font-weight:650;margin-top:3px}}
  .green{{color:#16a34a}} .red{{color:#dc2626}} .note{{color:#9ca3af;font-size:12px;margin-top:12px}}
</style></head>
<body><main>
<h1>{_html.escape(name)} ({_html.escape(symbol)})</h1>
<p class="meta">生成时间: {datetime.now():%Y-%m-%d %H:%M} | 数据来源: Yahoo Finance | Aria Code</p>
<div class="grid">
  <div class="card"><div class="lbl">最新收盘</div>
    <div class="val">{currency} {last_close:,.2f}</div></div>
  <div class="card"><div class="lbl">MA20</div>
    <div class="val {'green' if ma20 and last_close > ma20 else 'red'}">{f'{ma20:,.2f}' if ma20 else 'N/A'}</div></div>
  <div class="card"><div class="lbl">MA50</div>
    <div class="val {'green' if ma50 and last_close > ma50 else 'red'}">{f'{ma50:,.2f}' if ma50 else 'N/A'}</div></div>
  <div class="card"><div class="lbl">RSI(14)</div>
    <div class="val {'red' if rsi14 and rsi14>=70 else 'green' if rsi14 and rsi14<=30 else ''}">{f'{rsi14:.1f}' if rsi14 else 'N/A'} {rsi_view}</div></div>
  <div class="card"><div class="lbl">趋势</div><div class="val">{trend}</div></div>
  <div class="card"><div class="lbl">动能</div><div class="val">{momentum}</div></div>
</div>
<div id="price-chart"></div>
<div id="rsi-chart" style="margin-top:8px"></div>
<div id="macd-chart" style="margin-top:8px"></div>
<p class="note">⚠️ 本图表仅供参考，不构成投资建议。</p>
</main>
<script>
const x={x};
Plotly.newPlot('price-chart',[
  {{x,y:{close_v},type:'scatter',name:'收盘价',line:{{color:'#2563eb',width:2}}}},
  {{x,y:{ma20_v}, type:'scatter',name:'MA20', line:{{color:'#f59e0b',width:1.5,dash:'dot'}}}},
  {{x,y:{ma50_v}, type:'scatter',name:'MA50', line:{{color:'#ef4444',width:1.5,dash:'dot'}}}}
],{{title:'{_html.escape(symbol)} 价格走势',height:340,plot_bgcolor:'#fff',
   paper_bgcolor:'#fff',xaxis:{{showgrid:true,gridcolor:'#f3f4f6'}},
   yaxis:{{showgrid:true,gridcolor:'#f3f4f6',title:'价格 ({currency})'}}}},
  {{responsive:true,displaylogo:false}});
Plotly.newPlot('rsi-chart',[
  {{x,y:{rsi_v},type:'scatter',name:'RSI(14)',line:{{color:'#8b5cf6',width:1.5}}}}
],{{title:'RSI(14)',height:180,plot_bgcolor:'#fff',paper_bgcolor:'#fff',
   shapes:[{{type:'line',x0:x[0],x1:x[x.length-1],y0:70,y1:70,
              line:{{color:'#dc2626',width:1,dash:'dot'}}}},
             {{type:'line',x0:x[0],x1:x[x.length-1],y0:30,y1:30,
              line:{{color:'#16a34a',width:1,dash:'dot'}}}}]}},
  {{responsive:true,displaylogo:false}});
Plotly.newPlot('macd-chart',[
  {{x,y:{macd_v},  type:'scatter',name:'MACD', line:{{color:'#2563eb',width:1.5}}}},
  {{x,y:{macd_s_v},type:'scatter',name:'Signal',line:{{color:'#f59e0b',width:1.5,dash:'dot'}}}}
],{{title:'MACD',height:180,plot_bgcolor:'#fff',paper_bgcolor:'#fff'}},
  {{responsive:true,displaylogo:false}});
</script></body></html>"""

    out_file.write_text(html_doc, encoding="utf-8")
    return {
        "success":    True,
        "chart_path": str(out_file),
        "response":   f"图表已生成：{out_file.name}",
        "symbol":     symbol,
        "last_close": last_close,
        "trend":      trend,
        "rsi":        rsi14,
        "momentum":   momentum,
    }


def _try_handle_stock_chart_analysis(message: str) -> dict:
    """Deterministic path for stock analysis + chart requests.

    This avoids weak local models writing fake scripts or leaking pseudo tool
    calls. It fetches historical data, computes common indicators, writes a
    standalone HTML chart, and returns a concise Markdown analysis.
    """
    if not _is_stock_chart_analysis_request(message):
        return {"success": False, "error": "not_stock_chart_analysis"}

    symbol = _extract_market_symbol(message) or "AAPL"
    period = "1y"
    interval = "1d"

    try:
        import html as _html
        import pandas as _pd
        import yfinance as _yf
    except Exception as exc:
        return {
            "success": False,
            "error": f"缺少图表分析依赖：{exc}",
            "response": "当前环境缺少 `yfinance` 或 `pandas`，无法生成股票图表。",
        }

    provider = "Yahoo Finance"
    ticker = None
    try:
        ticker = _yf.Ticker(symbol)
        hist = ticker.history(period=period, interval=interval, auto_adjust=False)
    except Exception as exc:
        hist = None
        yahoo_error = str(exc)
    else:
        yahoo_error = ""

    if hist is None or hist.empty:
        try:
            import requests as _requests
            period2 = int(time.time())
            period1 = period2 - 370 * 86400
            url = (
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
                f"?period1={period1}&period2={period2}&interval=1d"
                f"&events=history&includeAdjustedClose=true"
            )
            resp = _requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            payload = resp.json()
            result = (payload.get("chart", {}).get("result") or [None])[0]
            if result:
                ts = result.get("timestamp") or []
                quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
                dates = _pd.to_datetime(ts, unit="s")
                hist = _pd.DataFrame({
                    "Open": quote.get("open", []),
                    "High": quote.get("high", []),
                    "Low": quote.get("low", []),
                    "Close": quote.get("close", []),
                    "Volume": quote.get("volume", []),
                }, index=dates).dropna(subset=["Close"])
                meta = result.get("meta") or {}
                if meta.get("currency"):
                    provider_currency = meta.get("currency")
                else:
                    provider_currency = None
                provider = "Yahoo Chart API"
            else:
                provider_currency = None
        except Exception as exc:
            hist = None
            chart_error = str(exc)
        else:
            chart_error = ""

    if hist is None or hist.empty:
        try:
            stooq_symbol = symbol.lower()
            if "." not in stooq_symbol:
                stooq_symbol = f"{stooq_symbol}.us"
            url = f"https://stooq.com/q/d/l/?s={stooq_symbol}&i=d"
            hist = _pd.read_csv(url)
            if hist is not None and not hist.empty:
                hist["Date"] = _pd.to_datetime(hist["Date"])
                hist = hist.set_index("Date").sort_index().tail(260)
                provider = "Stooq"
        except Exception as exc:
            return {
                "success": False,
                "error": f"获取 {symbol} 历史行情失败：Yahoo={yahoo_error or 'empty'}; YahooChart={chart_error or 'empty'}; Stooq={exc}",
                "response": f"无法获取 {symbol} 历史行情，图表未生成。请稍后重试，或检查网络/数据源访问。",
            }

    if hist is None or hist.empty or "Close" not in hist.columns:
        return {
            "success": False,
            "error": f"{symbol} 历史行情为空：Yahoo={yahoo_error or 'empty'}",
            "response": f"没有拿到 {symbol} 的可用历史行情，图表未生成。请稍后重试，或检查网络/数据源访问。",
        }

    hist = hist.dropna(subset=["Close"]).copy()
    hist["MA20"] = hist["Close"].rolling(20).mean()
    hist["MA50"] = hist["Close"].rolling(50).mean()
    hist["MA200"] = hist["Close"].rolling(200).mean()
    delta = hist["Close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, _pd.NA)
    hist["RSI14"] = 100 - (100 / (1 + rs))
    ema12 = hist["Close"].ewm(span=12, adjust=False).mean()
    ema26 = hist["Close"].ewm(span=26, adjust=False).mean()
    hist["MACD"] = ema12 - ema26
    hist["MACD_SIGNAL"] = hist["MACD"].ewm(span=9, adjust=False).mean()

    last = hist.iloc[-1]
    first_close = hist["Close"].iloc[0]
    last_close = float(last["Close"])
    ytd_like_return = (last_close / float(first_close) - 1) * 100 if first_close else 0
    ma20 = float(last["MA20"]) if _pd.notna(last["MA20"]) else None
    ma50 = float(last["MA50"]) if _pd.notna(last["MA50"]) else None
    ma200 = float(last["MA200"]) if _pd.notna(last["MA200"]) else None
    rsi14 = float(last["RSI14"]) if _pd.notna(last["RSI14"]) else None
    macd = float(last["MACD"]) if _pd.notna(last["MACD"]) else None
    macd_sig = float(last["MACD_SIGNAL"]) if _pd.notna(last["MACD_SIGNAL"]) else None
    high_52w = float(hist["High"].max()) if "High" in hist else float(hist["Close"].max())
    low_52w = float(hist["Low"].min()) if "Low" in hist else float(hist["Close"].min())

    info = {}
    try:
        if ticker is None:
            ticker = _yf.Ticker(symbol)
        info = ticker.get_info() or {}
    except Exception:
        info = {}
    name = info.get("longName") or info.get("shortName") or symbol
    pe = info.get("trailingPE")
    market_cap = info.get("marketCap")
    currency = info.get("currency") or locals().get("provider_currency") or "USD"

    if ma20 and ma50 and last_close > ma20 > ma50:
        trend = "偏多"
    elif ma20 and ma50 and last_close < ma20 < ma50:
        trend = "偏空"
    else:
        trend = "震荡/中性"
    momentum = "MACD偏多" if macd is not None and macd_sig is not None and macd > macd_sig else "MACD偏弱"
    rsi_view = "超买" if rsi14 is not None and rsi14 >= 70 else ("超卖" if rsi14 is not None and rsi14 <= 30 else "中性")

    out_dir = pathlib.Path.home() / "Desktop" / "Arthera" / "reports" / "stock_charts"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_symbol = re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)
    out_file = out_dir / f"{safe_symbol}_analysis_chart_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"

    x = [idx.strftime("%Y-%m-%d") for idx in hist.index]
    close = [None if _pd.isna(v) else round(float(v), 4) for v in hist["Close"]]
    volume = [None if _pd.isna(v) else int(float(v)) for v in hist.get("Volume", _pd.Series(index=hist.index, dtype=float))]
    ma20_arr = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MA20"]]
    ma50_arr = [None if _pd.isna(v) else round(float(v), 4) for v in hist["MA50"]]
    rsi_arr = [None if _pd.isna(v) else round(float(v), 4) for v in hist["RSI14"]]

    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_html.escape(symbol)} 股票分析图表</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8fa; color: #17202a; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    .meta {{ color: #667085; margin-bottom: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin: 18px 0; }}
    .metric {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px; }}
    .label {{ color: #667085; font-size: 12px; }}
    .value {{ font-size: 18px; font-weight: 650; margin-top: 4px; }}
    #chart {{ background: #fff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 8px; }}
    .note {{ color: #667085; font-size: 13px; margin-top: 14px; }}
  </style>
</head>
<body>
<main>
  <h1>{_html.escape(name)} ({_html.escape(symbol)})</h1>
  <div class="meta">生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · 数据：{_html.escape(provider)} · 周期：{period}</div>
  <section class="grid">
    <div class="metric"><div class="label">最新收盘</div><div class="value">{currency} {_fmt_num(last_close)}</div></div>
    <div class="metric"><div class="label">近一年区间</div><div class="value">{_fmt_num(low_52w)} - {_fmt_num(high_52w)}</div></div>
    <div class="metric"><div class="label">MA20 / MA50</div><div class="value">{_fmt_num(ma20)} / {_fmt_num(ma50)}</div></div>
    <div class="metric"><div class="label">RSI14</div><div class="value">{_fmt_num(rsi14)}</div></div>
    <div class="metric"><div class="label">P/E</div><div class="value">{_fmt_num(pe)}</div></div>
    <div class="metric"><div class="label">成交量</div><div class="value">{_fmt_int(last.get("Volume"))}</div></div>
  </section>
  <div id="chart"></div>
  <p class="note">图表包含收盘价、MA20、MA50、成交量和 RSI14。该文件为本地 HTML，可直接在浏览器打开。</p>
</main>
<script>
const x = {json.dumps(x)};
const close = {json.dumps(close)};
const volume = {json.dumps(volume)};
const ma20 = {json.dumps(ma20_arr)};
const ma50 = {json.dumps(ma50_arr)};
const rsi = {json.dumps(rsi_arr)};
const data = [
  {{x, y: close, type: "scatter", mode: "lines", name: "Close", line: {{color: "#2563eb", width: 2}}, yaxis: "y"}},
  {{x, y: ma20, type: "scatter", mode: "lines", name: "MA20", line: {{color: "#f59e0b", width: 1.5}}, yaxis: "y"}},
  {{x, y: ma50, type: "scatter", mode: "lines", name: "MA50", line: {{color: "#10b981", width: 1.5}}, yaxis: "y"}},
  {{x, y: volume, type: "bar", name: "Volume", marker: {{color: "rgba(100,116,139,0.35)"}}, yaxis: "y2"}},
  {{x, y: rsi, type: "scatter", mode: "lines", name: "RSI14", line: {{color: "#dc2626", width: 1.5}}, yaxis: "y3"}}
];
const layout = {{
  height: 720,
  margin: {{l: 62, r: 30, t: 28, b: 42}},
  paper_bgcolor: "#fff",
  plot_bgcolor: "#fff",
  hovermode: "x unified",
  legend: {{orientation: "h", y: 1.04}},
  xaxis: {{domain: [0, 1], rangeslider: {{visible: false}}, gridcolor: "#eef2f7"}},
  yaxis: {{domain: [0.36, 1], title: "Price", gridcolor: "#eef2f7"}},
  yaxis2: {{domain: [0.18, 0.31], title: "Volume", gridcolor: "#eef2f7"}},
  yaxis3: {{domain: [0, 0.13], title: "RSI", range: [0, 100], gridcolor: "#eef2f7"}},
  shapes: [
    {{type: "line", xref: "paper", x0: 0, x1: 1, yref: "y3", y0: 70, y1: 70, line: {{color: "#ef4444", dash: "dot"}}}},
    {{type: "line", xref: "paper", x0: 0, x1: 1, yref: "y3", y0: 30, y1: 30, line: {{color: "#22c55e", dash: "dot"}}}}
  ]
}};
Plotly.newPlot("chart", data, layout, {{responsive: true, displaylogo: false}});
</script>
</body>
</html>
"""
    out_file.write_text(html_doc, encoding="utf-8")

    market_cap_text = "N/A"
    if market_cap:
        market_cap_text = f"{currency} {market_cap / 1e12:.2f}T" if market_cap >= 1e12 else f"{currency} {market_cap / 1e9:.1f}B"

    response = (
        f"## {name} ({symbol}) 股票分析\n\n"
        f"已生成图表：[{out_file.name}]({out_file})\n\n"
        f"| 指标 | 数值 |\n"
        f"| --- | --- |\n"
        f"| 最新收盘 | {currency} {_fmt_num(last_close)} |\n"
        f"| 近一年涨跌幅 | {ytd_like_return:+.2f}% |\n"
        f"| 近一年高/低 | {_fmt_num(high_52w)} / {_fmt_num(low_52w)} |\n"
        f"| MA20 / MA50 / MA200 | {_fmt_num(ma20)} / {_fmt_num(ma50)} / {_fmt_num(ma200)} |\n"
        f"| RSI14 | {_fmt_num(rsi14)}（{rsi_view}） |\n"
        f"| MACD | {_fmt_num(macd)} / signal {_fmt_num(macd_sig)}（{momentum}） |\n"
        f"| P/E / 市值 | {_fmt_num(pe)} / {market_cap_text} |\n\n"
        f"**结论**：当前技术结构为 **{trend}**。"
        f"RSI 处于{rsi_view}区间，{momentum}。"
        f"若价格能稳定站上 MA20 和 MA50，短线结构会更健康；若跌破 MA50 或放量下行，需要降低仓位和预期。\n\n"
        f"**风险**：该分析基于 {provider} 历史行情和常用技术指标，不构成投资建议；财报、产品周期、利率和大盘风险都会影响股价。"
    )
    return {
        "success": True,
        "response": response,
        "provider": "deterministic",
        "tools_used": ["yfinance", "html_chart"],
        "chart_path": str(out_file),
    }


async def stream_ollama(ollama_url: str, message: str, history: list,
                        model: str = "qwen2.5:7b",
                        on_token=None, on_thinking=None,
                        on_tool_call=None, on_tool_result=None,
                        cancel_event: asyncio.Event = None,
                        enable_tools: bool = True) -> dict:
    """Stream chat via local Ollama with tool calling support (native + text-based)."""
    import aiohttp

    # ── Response cache: skip Ollama for repeated stateless queries ───────────
    # Only cache when there is no conversation history (stateless), the query
    # is short (likely a simple quote/concept), and no tools are being called.
    _should_cache = not history and len(message) < 300
    if _should_cache:
        _ck = _cache_key(model, message)
        _cached = _cache_get(_ck)
        if _cached:
            if on_token:
                on_token(_cached)
            return {"success": True, "response": _cached,
                    "provider": "ollama_cache", "usage": {}}

    _models_probe, _ollama_err = detect_ollama_models_rich(ollama_url)
    if _ollama_err:
        if _is_simple_greeting(message):
            return _offline_greeting_response()
        return _ollama_unavailable_result(ollama_url, _ollama_err)

    # ── 模型自动解析：确保请求的模型在 Ollama 中存在 ─────────────────────────
    try:
        from local_llm_provider import resolve_model_async
        _resolved = await resolve_model_async(ollama_url, model)
        if _resolved != model:
            model = _resolved   # silently remap to available model
    except Exception:
        pass  # resolution failed — proceed with original model name

    # ── 模型分级守卫：小模型不能处理 coding/analysis/complex-finance 任务 ──────
    # 如果分配到的模型是 small/nano 级别，但任务需要代码生成、复杂分析或长文本，
    # 自动升级到 Ollama 中最优可用模型，防止低质量/模板化输出。
    try:
        from model_capability import get_model_capability, is_router_only, can_handle_coding
        _cap_check = get_model_capability(model)
        _task_needs_upgrade = (
            is_router_only(_cap_check)
            or (not can_handle_coding(_cap_check) and _is_coding_request(message))
            # Small (1-4B) models also struggle with complex finance questions:
            # they ignore detailed system prompts and output template garbage.
            # Upgrade when the question is non-trivial and the model is "small".
            # Use 8 as the minimum length threshold (works for both Chinese and English):
            # Chinese "比特币值得投资吗" = 9 chars, English "buy or sell?" = 12 chars.
            or (_cap_check.size_class == "small" and len(message) > 8
                and not _is_simple_greeting(message))
        )
        if _task_needs_upgrade and _models_probe:
            # 按优先级寻找可用的升级模型
            # NOTE: gpt-oss 排在 deepseek-v3.1 前面，因为 deepseek-v3.1:671b-cloud
            # 在 Ollama 实例中有时超时，而 gpt-oss:120b-cloud 响应稳定。
            _upgrade_prefixes = [
                "aria-sonata-3b", "qwen2.5-coder:7b", "qwen2.5-coder:3b",
                "qwen2.5:7b", "qwen2.5:3b", "llama3.2:3b", "mistral",
                # Cloud models registered in this Ollama instance (remote but available)
                "gpt-oss", "deepseek-v3.1",
            ]
            # _models_probe is a list of dicts: {"name": str, "size_label": str, ...}
            # Must extract "name" field — do NOT call .startswith() on the dict.
            _probe_names = [
                m["name"] if isinstance(m, dict) else m
                for m in _models_probe
            ]
            for _pref in _upgrade_prefixes:
                _candidate = next(
                    (m for m in _probe_names if m.startswith(_pref)), None
                )
                if _candidate and _candidate != model:
                    model = _candidate
                    break
    except Exception:
        pass

    # ── 五档路由：通过 Prelude 意图分类器（或关键词 fallback）决定 prompt ────
    # Always rebuild finance prompt to get today's date
    _finance_prompt = _build_finance_prompt()

    try:
        from intent_classifier import (
            classify_intent_async,
            INTENT_CODING, INTENT_ANALYSIS, INTENT_REALTIME,
            INTENT_GENERAL, INTENT_FINANCE,
        )
        _intent = await classify_intent_async(message, ollama_url)
    except Exception:
        # Fallback to legacy keyword detection if intent_classifier unavailable
        if _is_coding_request(message):
            _intent = "coding"
        elif _is_analysis_request(message):
            _intent = "analysis"
        elif _is_general_knowledge(message):
            _intent = "general"
        else:
            _intent = "finance"

    _is_general = (_intent == "general")

    # ── Select prompt size based on model capability ─────────────────────────
    # Small / nano models (≤3B) cannot effectively use the full CODING_SYSTEM_PROMPT
    # (6000+ tokens of examples they mostly ignore).  Send a condensed version that
    # keeps the essential rules and the single complete working template.
    #
    # Analysis: always use the LITE prompt in Ollama mode, even for medium/large
    # cloud models relayed through Ollama.  The full ANALYSIS_SYSTEM_PROMPT
    # instructs the model to call `get_market_data`, which is a cloud-only tool
    # not available in the LOCAL_TOOLS registry — leading to "Unknown local tool"
    # errors and an infinite retry loop.  The lite prompt explicitly refuses to
    # output N/A templates when no data is injected, which is the correct
    # behaviour in local mode.
    try:
        from model_capability import get_model_capability as _gmc
        _model_size = _gmc(model).size_class
    except Exception:
        _model_size = "medium"
    _use_lite_prompt = _model_size in ("nano", "small")

    if _intent == "coding":
        _base_prompt = _build_coding_prompt_lite(message) if _use_lite_prompt else CODING_SYSTEM_PROMPT
    elif _intent == "analysis":
        # Always use lite analysis prompt in Ollama — the full prompt triggers
        # get_market_data tool calls that are not available locally.
        _base_prompt = _build_analysis_prompt_lite(message)
    elif _intent == "general":
        # 纯知识/概念问题：注入日期，但不注入工具 schema
        from datetime import datetime as _dt2
        _today_str = _dt2.now().strftime("%Y年%m月%d日")
        _base_prompt = (
            f"你是 Aria，专业金融AI助手。今天是 {_today_str}。\n"
            "用中文简洁、准确地回答问题。\n"
            "- 使用 Markdown（**粗体**、## 标题、- 列表、表格）\n"
            "- 不要编造任何实时数据（股价/汇率等），不要主动调用工具\n"
            "- 简洁，避免重复相同内容\n"
        )
    else:
        # realtime / finance: use full finance prompt with tool access
        _base_prompt = _finance_prompt

    # Project context injection: skip or condense for small/nano models.
    # A 1.5B model with a 4000-token README injected into its context will
    # either copy the README into its response or hallucinate beyond recovery.
    _small_model = _model_size in ("nano", "small")
    if not _is_general:
        if not _small_model and _PROJECT_CONTEXT:
            system_prompt = _base_prompt + _PROJECT_CONTEXT
        else:
            # For small models: skip the full README, only keep a 2-line summary
            _ctx_brief = ""
            if _PROJECT_CONTEXT:
                _first_lines = [l for l in _PROJECT_CONTEXT.split("\n") if l.strip()][:3]
                _ctx_brief = "\n# Context: " + " | ".join(_first_lines[:2]) + "\n"
            system_prompt = _base_prompt + _ctx_brief
    else:
        system_prompt = _base_prompt

    # Append ariarc project context if available — small models skip this
    if _HAS_ARIARC and not _is_general and not _small_model:
        try:
            _arc = get_ariarc()
            _arc_block = _arc.build_system_prompt_block()
            if _arc_block:
                # Hard-cap ariarc block at 800 chars to prevent context overflow
                _arc_short = _arc_block[:800] + ("…" if len(_arc_block) > 800 else "")
                system_prompt = system_prompt + "\n\n" + _arc_short
        except Exception:
            pass

    url = f"{ollama_url}/api/chat"
    _mcfg = get_model_cfg(model)

    if _HAS_MODEL_CAP:
        _cap         = get_model_capability(model)
        _num_ctx     = _cap.context_window
        _temperature = _cap.temperature
    else:
        _num_ctx     = _mcfg.get("num_ctx", 16384)
        _temperature = _mcfg.get("temperature", 0.3)
    _max_tokens = _mcfg.get("max_tokens", min(_mcfg.get("num_ctx", 8192) // 4, 8192))
    _mkey = resolve_model_key(model)

    # ── 上下文硬截断：小模型（≤3B）严格限制历史长度，防止溢出 ─────────────
    _ctx_chars_limit = max((_num_ctx * 3) - len(system_prompt) - len(message) - 512, 1000)
    # 从最新历史往前选，确保总字符数不超限
    _trimmed_history: list = []
    _hist_chars = 0
    for _hm in reversed(history):
        _hm_len = len(_hm.get("content",""))
        if _hist_chars + _hm_len > _ctx_chars_limit:
            break
        _trimmed_history.insert(0, _hm)
        _hist_chars += _hm_len

    messages = [{"role": "system", "content": system_prompt}]
    for msg in _trimmed_history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": message})

    # ── 工具注入：通识问答跳过，同时跳过无法可靠调用工具的小模型 ──────────
    # 判断模型是否具备工具调用能力（text_only / format不支持的都跳过）
    _model_can_use_tools = False
    if _HAS_MODEL_CAP and enable_tools and LOCAL_TOOL_SCHEMAS and not _is_general:
        _tool_cap = get_model_capability(model)
        # 只有明确支持工具且 context_window >= 8192 的模型才注入 tool schema
        _model_can_use_tools = (
            _tool_cap.format != "text_only"
            and _tool_cap.context_window >= 8192
        )
        if _model_can_use_tools:
            _tool_sys = build_tool_system_prompt(LOCAL_TOOL_SCHEMAS, model)
            if _tool_sys and messages:
                if messages[0].get("role") == "system":
                    messages[0]["content"] += _tool_sys
                else:
                    messages.insert(0, {"role": "system", "content": _tool_sys.strip()})

    # ── 实时数据预取：始终为分析/报价查询预取真实市场数据注入 prompt ──────────
    # 无论模型是否支持工具调用，都注入真实数据，防止模型生成占位符（$X.XX）
    # 策略：
    #   1. system prompt 替换为"数据已预取"专用 prompt
    #   2. 数据同时注入到用户消息开头（本地模型对最近的 user message 最敏感）
    if _HAS_MDC and not _is_general:
        _market_inject = _try_prefetch_market_data(message, history)
        if _market_inject:
            # 过程可见化：让用户看到实时数据已注入（类似工具调用展示）
            _inj_m = _re_sym.search(r'## 📊 (\S+) 实时行情（来源：(\S+)）', _market_inject)
            if _inj_m and HAS_RICH:
                console.print(
                    f"  [bold #C08050]market_data[/bold #C08050] "
                    f"[dim]{_inj_m.group(1)} · 实时行情已注入 · {_inj_m.group(2)}[/dim]"
                )
            # Replace system prompt with data-first prompt.
            # Use nano variant for 1-3B models (no template placeholders).
            _is_nano_model = _use_lite_prompt or _model_size in ("nano", "small")
            _prefetched_sys = _build_prefetched_analysis_prompt(nano=_is_nano_model)
            if messages and messages[0].get("role") == "system":
                messages[0]["content"] = _prefetched_sys
            else:
                messages.insert(0, {"role": "system", "content": _prefetched_sys})
            # Prepend real data to the user message so the model sees it last
            # (most recent = highest attention weight for local models).
            _augmented_user = (
                _market_inject
                + "\n---\n"
                "上面是真实实时数据。请只使用这些具体数字作答，不要引用训练记忆中的历史价格。\n\n"
                + message
            )
            for _mi in reversed(messages):
                if _mi.get("role") == "user":
                    _mi["content"] = _augmented_user
                    break

    # ── 文件路径自动注入：若用户消息引用了本地文件，预读并注入内容 ────────────
    # 无论意图是什么，只要消息里有可读的文件路径就注入（coding / analysis 均有效）
    _file_inject = _try_inject_file_paths(message)
    if _file_inject:
        for _mi in reversed(messages):
            if _mi.get("role") == "user":
                _mi["content"] = _file_inject + _mi["content"]
                break

    # ── Token budget 分级策略 ────────────────────────────────────────────────
    # 小模型（<8K ctx）防止无限延伸；通识问答分两档：
    #   · 纯问候/一句话问题 → 200 tokens（快速）
    #   · 知识解释问题（"什么是X", "如何…"） → 1500 tokens（保证完整性）
    #   · 正常问题 → 模型 max_tokens 配置值
    _is_small_model = _HAS_MODEL_CAP and get_model_capability(model).context_window < 8192
    _is_greeting    = _is_general and len(message.strip()) < 25
    if _is_greeting:
        _effective_max_tokens = 200
    elif _is_general:
        _effective_max_tokens = 1500   # 足够完整回答概念解释，不截断
    elif _use_lite_prompt:
        # Small/nano model: coding tasks need more room for complete scripts;
        # analysis/finance keep a tighter cap to prevent runaway echo generation.
        if _intent == "coding":
            _effective_max_tokens = 2000
        else:
            _effective_max_tokens = 512
    elif _is_small_model:
        _effective_max_tokens = min(_max_tokens, 2048)
    else:
        _effective_max_tokens = _max_tokens

    # 停止词：覆盖常见 hallucination 模式
    # 包含：英文求助模板、工具执行幻觉、"任务就绪"尾部幻觉（中文小模型常见）
    _stop_seqs = [
        # ── 英文求助/拒绝模板 ─────────────────────────────────────────────
        "I'm sorry, as an AI",
        "I'm sorry for any confusion",
        "I cannot perform",
        "I can't perform",
        "Do You Need Help",
        "Are There Specific Areas",
        "Let us brainstorm together",
        "AWAITING FEEDBACK",
        "Would love more context",
        "Please provide more details",
        "Could you please provide",
        "Without knowing those specifics",
        "os.system('pip install",
        "git clone https://github.com",
        "Let's download these libraries",
        # ── 中文"任务就绪"尾部幻觉（小模型在回答结束后常产生） ────────────
        "好的，我将开始执行任务",
        "好的，我已经准备好了要做的工作",
        "请告诉我您希望我在接下来做什么",
        "请问有什么我可以帮助您的吗",
        "请告诉我你需要什么帮助",
        "我会尽快为您完成这项任务",
        "如果您有任何其他问题，请随时告诉我",
        "如果你有其他问题，请随时提问",
        # ── 英文任务就绪幻觉 ─────────────────────────────────────────────
        "I'm ready to help with your next",
        "Let me know if you need anything else",
        "Is there anything else you'd like me to",
        "Feel free to ask if you have more questions",
        # ── 工具调用幻觉（声称已调用但实际没有 tool_call 事件）────────────
        "I have already called `get_market_data`",
        "I have already called `get_stock_price`",
        "I have already called get_market_data",
        "I have already fetched",
        "I have already retrieved",
        "我已经调用了",
        "我已调用工具",
        "我已经获取了最新数据",
        # ── 模板占位符输出（模型把 system prompt 模板当内容输出）────────────
        "${real_price_from_data",
        "${data['day_range']}",
        "{actual date today}",
        "{real price from data}",
        "List real recent headlines",
    ]

    payload = {
        "model": model, "messages": messages, "stream": True,
        "options": {
            "num_ctx":        _num_ctx,
            "temperature":    _temperature,
            "top_p":          0.9,
            "repeat_penalty": 1.4,
            "repeat_last_n":  256,
            "num_predict":    _effective_max_tokens,
        },
        "stop": _stop_seqs,
    }
    # Only inject native Ollama tools field for capable models.
    # Small (≤4K ctx) or text_only models must never receive tool schemas —
    # they produce malformed partial JSON that leaks into the output stream.
    if _model_can_use_tools:
        _cap2 = get_model_capability(model) if _HAS_MODEL_CAP else None
        if _cap2 and _cap2.tool_calls and _cap2.format == "ollama_native":
            payload["tools"] = LOCAL_TOOL_SCHEMAS

    full_response = ""
    tools_used = []
    tool_calls_pending = []
    max_tool_rounds = 10
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0}
    _last_tool_had_error = False  # Track if previous tool failed
    _in_error_recovery = False    # Stays True until run_command succeeds (not reset by read_file)
    _nudge_count = 0  # Limit error recovery nudges
    _consecutive_reads = 0  # Track repeated read_file without fixing
    _last_failed_cmd = ""  # Track last failed run_command to detect repeats
    _consecutive_cmd_failures = 0  # Count consecutive failures of same command
    # Repetition loop detection — check every 80 chars (was 200, too slow for 200-token responses)
    _rep_check_interval = 80
    _rep_token_count = [0]     # mutable for closure
    _rep_cancelled = [False]   # signals loop to stop

    def _check_repetition(text: str) -> bool:
        """Return True if the response is looping.

        Covers three patterns:
          A. Paragraph-level loop: same long block (50-400 chars) reappears
          B. Sentence-level tail loop: short sentence (15-50 chars) appears
             2+ times at the END — catches "好的，我已经准备好了" × 2 style tails
          C. Beginning-restart loop: model generates the full response, then
             starts again from the very beginning. Detects when the opening
             80 chars of the accumulated response reappear after the midpoint.
             This is the most common 1.5B model failure mode.
        """
        if len(text) < 100:
            return False

        # Pattern C: restart-from-beginning (fast path, checked first)
        if len(text) > 300:
            _opening = text[:80].strip()
            if _opening and len(_opening) >= 20:
                _after_half = text[len(text) // 2:]
                if _opening in _after_half:
                    return True

        tail = text[-4000:]

        # Pattern A: medium-to-large probe in trailing window
        for sub_len in (400, 250, 150, 80, 50):
            if len(tail) < sub_len * 2:
                continue
            probe = tail[-sub_len:].strip()
            if len(probe) < 20:
                continue
            if tail[:-sub_len].count(probe) >= 1:
                return True

        # Pattern B: short sentence repetition at tail (boilerplate hallucination)
        # Split by Chinese sentence-ending punctuation + newlines
        import re as _re2
        sentences = [s.strip() for s in _re2.split(r'[。！？\n]+', tail) if s.strip()]
        if len(sentences) >= 4:
            # Check if any sentence in the last 3 also appears before it in the tail
            for sent in sentences[-3:]:
                if len(sent) < 10:
                    continue
                # Must appear at least once before the last-3 segment
                if tail.count(sent) >= 2:
                    return True

        return False

    for tool_round in range(max_tool_rounds):
        # Context compaction: compress older messages if context too large
        if tool_round > 0:
            payload["messages"] = _compact_messages(payload["messages"], model_key=_mkey)

        full_response = ""
        tool_calls_this_round = []

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload,
                                        timeout=aiohttp.ClientTimeout(total=300)) as resp:
                    if resp.status != 200:
                        try:
                            _body = await resp.text()
                            _json = json.loads(_body) if _body.strip().startswith("{") else {}
                            _ollama_err = _json.get("error") or _body[:200]
                        except Exception:
                            _ollama_err = f"HTTP {resp.status}"
                        # Invalidate model cache so next call re-probes
                        try:
                            from local_llm_provider import _model_cache
                            _model_cache.clear()
                        except Exception:
                            pass
                        return {"success": False, "error": f"Ollama {resp.status}: {_ollama_err}"}
                    async for line in resp.content:
                        if cancel_event and cancel_event.is_set():
                            return {"success": True, "response": full_response,
                                    "cancelled": True, "provider": "ollama", "usage": usage}
                        text = line.decode("utf-8", errors="ignore").strip()
                        if not text:
                            continue
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError:
                            continue

                        # Check for native tool calls from Ollama
                        msg = data.get("message", {})
                        if msg.get("tool_calls"):
                            for tc in msg["tool_calls"]:
                                fn = tc.get("function", {})
                                tool_name = fn.get("name", "")
                                tool_args = fn.get("arguments", {})
                                if isinstance(tool_args, str):
                                    try:
                                        tool_args = json.loads(tool_args)
                                    except json.JSONDecodeError:
                                        tool_args = {}
                                tool_calls_this_round.append({"tool": tool_name, "params": tool_args})
                                tools_used.append(tool_name)
                                if on_tool_call:
                                    on_tool_call(tool_name, tool_args)

                        if data.get("done"):
                            # Capture Ollama usage stats from final message
                            usage["prompt_tokens"] += data.get("prompt_eval_count", 0)
                            usage["completion_tokens"] += data.get("eval_count", 0)
                            break

                        token = msg.get("content", "")
                        if token:
                            full_response += token
                            # 重复检测：先检测再流出，避免重复内容流到用户终端
                            _rep_token_count[0] += len(token)
                            if _rep_token_count[0] >= _rep_check_interval:
                                _rep_token_count[0] = 0
                                if _check_repetition(full_response):
                                    # 定位重复起始点：找到最长不重复前缀
                                    _fr = full_response
                                    _cut = len(_fr) // 2
                                    # 尝试精确裁切：找重复开始的位置
                                    for _probe_len in (300, 200, 150, 100):
                                        if len(_fr) < _probe_len * 2:
                                            continue
                                        _probe = _fr[-_probe_len:]
                                        _pos = _fr[:-_probe_len].find(_probe)
                                        if _pos > 0:
                                            _cut = _pos
                                            break
                                    full_response = _fr[:_cut].rstrip()
                                    _rep_cancelled[0] = True
                                    if cancel_event:
                                        cancel_event.set()
                                    break
                            # 流出 token — 过滤条件：
                            # 1. 以 <tool_call 开头的 XML 工具调用（内部处理，不显示）
                            # 2. 以 { 开头的裸 JSON 工具调用（小模型幻觉，直接屏蔽）
                            _fr_lstrip = full_response.lstrip()
                            _looks_like_tool_json = (
                                _fr_lstrip.startswith("{")
                                and ('"name"' in full_response or '"function"' in full_response)
                                and '"arguments"' in full_response
                            )
                            # 3. 孤立的 ``` 围栏（未配对的代码块标记，过滤掉）
                            _stripped_tok = token.strip()
                            _is_orphan_fence = (
                                _stripped_tok.startswith("```")
                                and len(_stripped_tok) <= 6   # just ``` or ```py etc
                                and full_response.count("```") % 2 == 1   # unpaired
                            )
                            if on_token and not _fr_lstrip.startswith("<tool_call") \
                                       and not _looks_like_tool_json \
                                       and not _is_orphan_fence:
                                on_token(token)
        except Exception as e:
            err_msg = str(e) or type(e).__name__
            if any(x in err_msg.lower() for x in ("cannot connect", "connect call failed", "connection refused", "errno 61")):
                return _ollama_unavailable_result(ollama_url, err_msg)
            return {"success": False, "error": f"Ollama: {err_msg}"}

        # Fallback: parse text-based tool calls if no native ones found
        if not tool_calls_this_round and full_response.strip():
            text_calls = _parse_text_tool_calls(full_response)
            if text_calls:
                tool_calls_this_round = text_calls
                for tc in text_calls:
                    tools_used.append(tc["tool"])
                    if on_tool_call:
                        on_tool_call(tc["tool"], tc["params"])

        # If repetition was detected, truncate and return cleanly
        if _rep_cancelled[0]:
            # Remove the repeated tail — keep only the first clean portion
            lines = full_response.strip().splitlines()
            # Find where repetition started: keep up to the point where unique content ends
            seen_paragraphs = set()
            clean_lines = []
            for line in lines:
                key = line.strip()
                if key and len(key) > 20:
                    if key in seen_paragraphs:
                        break  # Hit a repeated paragraph — stop here
                    seen_paragraphs.add(key)
                clean_lines.append(line)
            full_response = "\n".join(clean_lines).rstrip()
            if on_token:
                # The repetition note is appended as a final token
                on_token("\n\n*[model stopped — repetition detected]*")
                full_response += "\n\n*[model stopped — repetition detected]*"
            return {
                "success": True, "response": full_response,
                "tools_used": tools_used, "sources": [],
                "tool_calls_pending": [], "usage": usage, "provider": "ollama",
            }

        # If no tool calls this round
        if not tool_calls_this_round:
            clean_text = full_response.strip().lower()

            # Detect "intent without action" — model says it will do something
            # but didn't output a tool call
            _intent_words = [
                "let me", "i will", "i'll", "let's", "让我", "我会", "我将",
                "让我们", "我来", "接下来", "下面", "我们来", "我需要",
                "再次", "重新", "检查", "修复", "fix", "retry", "check",
            ]
            has_intent = any(w in clean_text for w in _intent_words)
            should_nudge = (_in_error_recovery or _last_tool_had_error or has_intent) and _nudge_count < 5

            if should_nudge and tool_round < max_tool_rounds - 1:
                _nudge_count += 1
                if _in_error_recovery:
                    nudge = (
                        "SYSTEM: You are in error recovery mode. The script FAILED and is NOT yet fixed. "
                        "You MUST call a tool NOW to fix it:\n"
                        "- If you already read the file: call edit_file to fix the specific error, or write_file to rewrite.\n"
                        "- If you haven't read it: call read_file first.\n"
                        "- After fixing: call run_command to retry.\n"
                        "Do NOT output text. Output ONLY a <tool_call>."
                    )
                elif _last_tool_had_error:
                    nudge = (
                        "SYSTEM: The previous step FAILED. Fix it NOW by calling a tool:\n"
                        "1. read_file to see the code.\n"
                        "2. edit_file or write_file to fix.\n"
                        "3. run_command to retry.\n"
                        "Output a <tool_call> NOW."
                    )
                else:
                    nudge = (
                        "SYSTEM: You said you would do something but did not call a tool. "
                        "Do NOT describe what you will do — just DO it. "
                        "Output a <tool_call> NOW to take the next action."
                    )
                payload["messages"].append({"role": "assistant", "content": full_response})
                payload["messages"].append({"role": "user", "content": nudge})
                continue

            # Truly done. Tokens were already streamed above; do not print the
            # accumulated response again or the terminal shows duplicate blocks.
            break

        # Tool calls present — suppress model text (tool UI provides feedback)
        # Safety: only execute ONE tool call per round (force sequential execution)
        if len(tool_calls_this_round) > 1:
            tool_calls_this_round = tool_calls_this_round[:1]

        # Execute tool calls locally and feed results back
        clean_text = _strip_tool_call_tags(full_response)
        payload["messages"].append({"role": "assistant", "content": clean_text,
                                     "tool_calls": [{"function": {"name": tc["tool"], "arguments": tc["params"]}}
                                                     for tc in tool_calls_this_round]})

        ollama_cancelled = False
        for tc in tool_calls_this_round:
            # Check cancel between tools
            if cancel_event and cancel_event.is_set():
                ollama_cancelled = True
                break

            tool_name = tc["tool"]
            # Note: _print_tool_call already called by on_tool_call during streaming

            # Ask user confirmation for destructive tools
            if tool_name in _CONFIRM_TOOLS:
                try:
                    if not _confirm_tool_execution(
                            tool_name, tc["params"],
                            config_policy=_ACTIVE_COMMAND_POLICY[0]):
                        ollama_cancelled = True
                        if HAS_RICH:
                            console.print("\n  [dim]Cancelled[/dim]")
                        break
                    # Persist "Allow & set balanced" choice
                    if tc["params"].pop("_upgrade_policy", False):
                        _ACTIVE_COMMAND_POLICY[0] = "balanced"
                        if HAS_RICH:
                            console.print("  [dim]策略已升级为 balanced（本会话）[/dim]")
                except KeyboardInterrupt:
                    ollama_cancelled = True
                    break

            try:
                tool_t0 = time.time()
                # Inject current policy for run_command so post-approval execution
                # isn't re-blocked by the default "safe" policy
                if tool_name == "run_command" and "policy" not in tc["params"]:
                    tc["params"]["policy"] = _ACTIVE_COMMAND_POLICY[0]
                result = execute_local_tool(tool_name, tc["params"])
                tool_dt = time.time() - tool_t0
            except KeyboardInterrupt:
                ollama_cancelled = True
                break
            _print_tool_result(tool_name, result, tool_dt)

            summary = _format_tool_summary(tool_name, result)

            # Track if this tool had an error (for nudge logic)
            _last_tool_had_error = not result.get("success", False)
            if result.get("success") and tool_name == "run_command":
                exit_code = result.get("data", {}).get("exit_code", 0)
                _last_tool_had_error = (exit_code != 0)

            # Error recovery state machine
            if _last_tool_had_error:
                _in_error_recovery = True
                _consecutive_reads = 0
                # Detect repeated failed run_command (same command failing 2+ times)
                if tool_name == "run_command":
                    cmd_str = tc["params"].get("command", "")
                    if cmd_str == _last_failed_cmd:
                        _consecutive_cmd_failures += 1
                    else:
                        _last_failed_cmd = cmd_str
                        _consecutive_cmd_failures = 1
                    if _consecutive_cmd_failures >= 2:
                        summary += ("\n\nSYSTEM: You have run the SAME command and it FAILED again with the same error. "
                                    "STOP re-running it. You MUST fix the code first:\n"
                                    "1. read_file to see the script content\n"
                                    "2. edit_file to fix the specific error (or write_file to rewrite entirely)\n"
                                    "3. THEN run_command to retry.\n"
                                    "Do NOT run the same command again until you have fixed the code.")
            elif tool_name in ("read_file", "list_files", "search_code"):
                # Diagnostic tools do NOT exit error recovery
                _consecutive_reads += 1
                # If model read the file 2+ times without fixing, inject directive
                if _in_error_recovery and _consecutive_reads >= 2:
                    summary += ("\n\nSYSTEM: You have read this file multiple times without fixing it. "
                                "STOP reading. Use edit_file to fix the specific error, "
                                "or use write_file to rewrite the entire script. Then run_command to retry.")
            elif tool_name in ("edit_file", "write_file"):
                # Fix was applied — stay in recovery until run_command succeeds
                _consecutive_reads = 0
                _consecutive_cmd_failures = 0  # Reset — code was changed
                _last_failed_cmd = ""
            elif tool_name == "run_command" and not _last_tool_had_error:
                # run_command succeeded — exit error recovery
                _in_error_recovery = False
                _consecutive_reads = 0
                _consecutive_cmd_failures = 0
                _last_failed_cmd = ""
                _nudge_count = 0

            if on_tool_result:
                on_tool_result(tool_name, summary)

            # Feed tool result back to Ollama for next round
            payload["messages"].append({
                "role": "tool",
                "content": summary,
            })

        if ollama_cancelled:
            return {"success": True, "response": full_response,
                    "cancelled": True, "tools_used": tools_used,
                    "sources": [], "thinking": "", "provider": "ollama", "usage": usage}

        # Continue streaming with tool results in context
        if HAS_RICH:
            console.print()  # newline before next AI response

    # Write successful stateless response to cache for future reuse
    if _should_cache and full_response and not tools_used:
        _cache_set(_ck, full_response)

    # ── Code-block executor fallback ─────────────────────────────────────────
    # Small models often ignore the <tool_call> instruction and write plain code
    # blocks instead.  When the intent is "coding" and the model produced a
    # Python block but zero tool calls, auto-extract the code and queue
    # write_file + run_command so the outer agentic loop executes it.
    _auto_tool_calls: list = []
    if _intent == "coding" and not tools_used and full_response:
        import re as _re
        # Accept both complete (``` closed) and truncated (unclosed) code blocks
        _py_blocks = _re.findall(r"```python\n(.*?)```", full_response, _re.DOTALL)
        if not _py_blocks:
            # Fallback: grab everything after the opening fence (handles truncation)
            _m = _re.search(r"```python\n(.*)", full_response, _re.DOTALL)
            if _m:
                _py_blocks = [_m.group(1)]
        if _py_blocks:
            _code = _py_blocks[-1].strip()
            # Basic sanitisation: strip leading spaces from ticker assignments
            _code = _re.sub(
                r"""(ticker\s*=\s*['"])(\s+)([A-Z]{1,10})(['"])""",
                r"\1\3\4", _code
            )
            # Auto-add missing `import mplfinance as mpf` when mpf is used
            if "mpf." in _code and "import mplfinance" not in _code:
                _code = "import mplfinance as mpf\n" + _code
            # Auto-add `import matplotlib.pyplot as plt` when plt is used
            if "plt." in _code and "import matplotlib.pyplot as plt" not in _code:
                _code = (
                    "import matplotlib; matplotlib.use('Agg')\n"
                    "import matplotlib.pyplot as plt\n" + _code
                )
            # Try to extract user-specified filename from the original message
            _fname_match = _re.search(
                r'保存(?:到|为|成)?\s*([^\s，,。]+\.py)'
                r'|save\s+(?:to\s+|as\s+)?([^\s,]+\.py)'
                r'|(?:named?|called?|filename?)\s+([^\s,]+\.py)',
                message, _re.IGNORECASE
            )
            if _fname_match:
                _fname = next(g for g in _fname_match.groups() if g)
                # Strip any path prefix from the extracted name
                _fname = os.path.basename(_fname)
            else:
                _fname = f"aria_generated_{int(time.time())}.py"
            _fpath = f"~/Desktop/{_fname}"

            # Validate Python syntax before writing — prepend warning comment if broken
            import py_compile as _pyc, tempfile as _tf2
            try:
                with _tf2.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as _stmp:
                    _stmp.write(_code)
                    _stmp_path = _stmp.name
                _pyc.compile(_stmp_path, doraise=True)
                os.unlink(_stmp_path)
            except _pyc.PyCompileError as _pce:
                # Surface the error prominently; file is still saved so user can fix it
                _err_line = str(_pce).replace(str(_stmp_path), _fname)
                _code = f"# ⚠️ SYNTAX ERROR (fix before running):\n# {_err_line}\n\n" + _code
                try:
                    os.unlink(_stmp_path)
                except Exception:
                    pass
            except Exception:
                pass

            _auto_tool_calls = [
                {"tool": "write_file",  "params": {"path": _fpath, "content": _code}},
                {"tool": "run_command", "params": {"command": f"python3 {os.path.expanduser(_fpath)}", "timeout": 120}},
            ]
            if on_tool_call:
                on_tool_call("write_file",  _auto_tool_calls[0]["params"])
                on_tool_call("run_command", _auto_tool_calls[1]["params"])

    if _auto_tool_calls:
        return {"success": True, "response": full_response,
                "tool_calls_pending": _auto_tool_calls,
                "tools_used": tools_used, "sources": [], "thinking": "",
                "provider": "ollama", "usage": usage}

    return {"success": True, "response": full_response,
            "tools_used": tools_used, "sources": [], "thinking": "", "provider": "ollama",
            "usage": usage}


# ============================================================================
# Aria SSE Stream Client — cancel + auth + user context
# ============================================================================

async def stream_chat(base_url: str, message: str, history: list,
                      model: str = "qwen2.5:7b", thinking_mode: str = "auto",
                      user_context: dict = None, auth_token: str = None,
                      on_token=None, on_thinking=None, on_tool_call=None,
                      on_tool_result=None, on_status=None,
                      cancel_event: asyncio.Event = None) -> dict:
    """Stream AI chat via SSE with cancel support and user context."""
    import aiohttp
    url = f"{base_url}/api/v2/ai/chat/stream"

    payload = {
        "message": message,
        "conversation_history": history[-20:],
        "model": model,
        "thinking_mode": thinking_mode,
        "stream": True,
    }
    if user_context:
        if _PROJECT_CONTEXT:
            user_context = {**user_context, "project_context": _PROJECT_CONTEXT}
        payload["user_context"] = user_context

    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    full_response = ""
    thinking_content = ""
    tools_used = []
    sources = []
    tool_calls_pending = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0}

    # Retry on transient connection errors (not HTTP errors, not cancellation)
    _max_connect_retries = 2
    _last_connect_error = None
    for _attempt in range(_max_connect_retries + 1):
        if cancel_event and cancel_event.is_set():
            return {"success": True, "response": "", "cancelled": True,
                    "tools_used": [], "sources": [], "usage": usage}
        # Reset per-attempt accumulators
        full_response = ""
        thinking_content = ""
        tools_used = []
        sources = []
        tool_calls_pending = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return {"success": False, "error": f"HTTP {resp.status}: {error_text[:200]}"}

                    buffer = ""
                    event_type = "delta"
                    async for chunk in resp.content:
                        if cancel_event and cancel_event.is_set():
                            try:
                                await session.post(f"{base_url}/api/v2/ai/chat/cancel",
                                                   headers=headers,
                                                   timeout=aiohttp.ClientTimeout(total=3))
                            except Exception:
                                pass
                            return {"success": True, "response": full_response,
                                    "cancelled": True, "tools_used": tools_used, "sources": sources,
                                    "usage": usage}

                        text = chunk.decode("utf-8", errors="ignore")
                        buffer += text

                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()

                            if not line or line.startswith(":"):
                                continue
                            if line.startswith("event:"):
                                event_type = line[6:].strip()
                                continue
                            if line.startswith("data:"):
                                data_str = line[5:].strip()
                                if data_str == "[DONE]":
                                    break
                                try:
                                    data = json.loads(data_str)
                                except json.JSONDecodeError:
                                    continue

                                # Detect backend error response: {"success": false, "error": "..."}
                                # These don't have a "type" field and would otherwise be
                                # silently parsed as empty "delta" events.
                                if data.get("success") is False:
                                    err_msg = data.get("error", "Backend error")
                                    return {"success": False, "error": f"Backend: {err_msg}"}

                                evt = data.get("type", event_type)

                                if evt == "delta":
                                    token = data.get("text", data.get("content", ""))
                                    if token:
                                        full_response += token
                                        usage["completion_tokens"] += 1
                                        if on_token:
                                            on_token(token)
                                elif evt == "thinking_content":
                                    tc = data.get("content", "")
                                    if tc:
                                        thinking_content += tc
                                        usage["thinking_tokens"] += 1
                                        if on_thinking:
                                            on_thinking(tc)
                                elif evt == "tool_call":
                                    tool = data.get("tool", data.get("name", ""))
                                    params = data.get("params", {})
                                    tools_used.append(tool)
                                    tool_calls_pending.append({"tool": tool, "params": params})
                                    if on_tool_call:
                                        on_tool_call(tool, params)
                                elif evt == "tool_result":
                                    if on_tool_result:
                                        on_tool_result(data.get("tool", ""), data.get("summary", ""))
                                elif evt == "status":
                                    if on_status:
                                        on_status(data.get("state", ""), data.get("message", ""))
                                elif evt == "final":
                                    full_response = data.get("answer", full_response)
                                    sources = data.get("sources", [])
                                    # Capture usage stats if provided
                                    if data.get("usage"):
                                        u = data["usage"]
                                        usage["prompt_tokens"] = u.get("prompt_tokens", usage["prompt_tokens"])
                                        usage["completion_tokens"] = u.get("completion_tokens", usage["completion_tokens"])
                                elif evt == "error":
                                    return {"success": False, "error": data.get("message", "Unknown error")}

            # Successful stream — return result
            return {
                "success": True, "response": full_response, "thinking": thinking_content,
                "tools_used": tools_used, "sources": sources,
                "tool_calls_pending": tool_calls_pending, "usage": usage,
            }

        except asyncio.TimeoutError:
            return {"success": False, "error": "Request timed out (120s)"}
        except asyncio.CancelledError:
            return {"success": True, "response": full_response, "cancelled": True,
                    "tools_used": tools_used, "sources": sources, "usage": usage}
        except aiohttp.ClientConnectorError as e:
            _last_connect_error = str(e)
            if _attempt < _max_connect_retries:
                wait = 1.5 * (_attempt + 1)
                await asyncio.sleep(wait)
                if on_status:
                    on_status("retry", f"Connection failed, retrying ({_attempt + 2}/{_max_connect_retries + 1})...")
                continue  # retry
            break
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Connection failed after {_max_connect_retries + 1} attempts: {_last_connect_error}"}


def _extract_code_block(text: str) -> Optional[str]:
    """Extract the first code block from markdown-formatted text."""
    import re
    # Match ```python ... ``` or ``` ... ```
    pattern = r'```(?:python|py)?\s*\n(.*?)```'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: try to find any code block
    pattern2 = r'```\w*\s*\n(.*?)```'
    match2 = re.search(pattern2, text, re.DOTALL)
    if match2:
        return match2.group(1).strip()
    return None


def _build_user_context(config: dict) -> Optional[dict]:
    """Build user context from config for personalized AI responses."""
    ctx = {}
    watchlist = config.get("watchlist", [])
    if watchlist:
        ctx["watchlist"] = watchlist
    user_id = config.get("user_id")
    if user_id:
        ctx["user_id"] = user_id
    # Inject current datetime and session info
    now = datetime.now()
    ctx["current_datetime"] = now.strftime("%Y-%m-%d %H:%M")
    ctx["day_of_week"] = now.strftime("%A")
    # US market session heuristic (Mon-Fri, approximate ET hours)
    weekday = now.weekday()  # 0=Mon, 6=Sun
    hour_min = now.hour * 60 + now.minute
    if weekday < 5 and 570 <= hour_min <= 960:  # 9:30am-4:00pm
        ctx["market_status"] = "open"
    elif weekday < 5 and (240 <= hour_min < 570 or 960 <= hour_min < 1200):
        ctx["market_status"] = "pre/after-hours"
    else:
        ctx["market_status"] = "closed"
    # Active model name
    model_id = config.get("model", "qwen2.5:7b")
    mkey = resolve_model_key(model_id)
    minfo = MODELS.get(mkey, {})
    ctx["ai_model"] = minfo.get("name", model_id)
    if config.get("local_mode"):
        ctx["inference_mode"] = "local"
    return ctx if ctx else None


# ============================================================================
# Tool Output Formatters
# ============================================================================

def _format_tool_summary(tool_name: str, result: dict) -> str:
    """Format tool result into a concise summary for AI follow-up context."""
    if not result.get("success"):
        return f"Error: {result.get('error', 'failed')}"
    data = result.get("data", {})
    if tool_name == "run_command":
        exit_code = data.get("exit_code", -1)
        stdout = data.get("stdout", "").strip()
        stderr = data.get("stderr", "").strip()
        out = f"exit_code={exit_code}"
        if stdout:
            out += f"\nstdout:\n{stdout[:2000]}"
        if stderr and exit_code != 0:
            out += f"\nstderr:\n{stderr[:500]}"
        # Add actionable hints for common errors
        if exit_code != 0:
            combined = (stdout + " " + stderr).lower()
            combined_raw = stdout + " " + stderr
            if "can't open file" in combined or "no such file" in combined:
                out += "\n\nHINT: The file does not exist. You must create it with write_file first, then run it."
            elif "modulenotfounderror" in combined or "no module named" in combined:
                # Extract module name
                import re as _re
                mod_match = _re.search(r"no module named ['\"]?(\w+)", combined)
                mod_name = mod_match.group(1) if mod_match else "<module_name>"
                out += f"\n\nHINT: Module '{mod_name}' is missing. Fix: run_command pip3 install {mod_name}, then run_command python3 to retry."
            elif "nameerror" in combined:
                # Extract the undefined name
                import re as _re
                name_match = _re.search(r"name ['\"](\w+)['\"] is not defined", combined_raw)
                if name_match:
                    missing_name = name_match.group(1)
                    out += (f"\n\nHINT: '{missing_name}' is not defined — you forgot to import it. "
                            f"Use edit_file to add the missing import (e.g., 'import {missing_name}') at the top of the script, then retry.")
                else:
                    out += "\n\nHINT: A variable or module is not defined. Use read_file to check imports, edit_file to add the missing import, then retry."
            elif "syntaxerror" in combined:
                import re as _re
                line_match = _re.search(r"line (\d+)", combined)
                line_hint = f" at line {line_match.group(1)}" if line_match else ""
                out += f"\n\nHINT: Syntax error{line_hint}. Use read_file to see the code, then edit_file to fix the exact line, then retry."
            elif "typeerror" in combined:
                out += "\n\nHINT: Type error — wrong argument types or wrong number of arguments. Use read_file to inspect, edit_file to fix, then retry."
            elif "keyerror" in combined or "indexerror" in combined:
                # Special hint for yfinance MultiIndex KeyError
                if any(col in combined_raw for col in ("'Close'", "'Open'", "'High'", "'Low'", "'Volume'")):
                    out += ("\n\nHINT: yfinance MultiIndex KeyError — yf.download() returns MultiIndex columns "
                            "when downloading multiple tickers. Fix: add `if isinstance(df.columns, pd.MultiIndex): "
                            "df.columns = df.columns.droplevel(1)` right after yf.download(). "
                            "Use edit_file to add this fix, then retry.")
                else:
                    out += "\n\nHINT: Data structure mismatch. Use read_file to check the code logic. The data may have different column names or fewer elements than expected."
            elif "attributeerror" in combined:
                out += "\n\nHINT: Attribute error — the object doesn't have that method/property. Check the library version or API docs. Use read_file then edit_file to fix."
            elif "valueerror" in combined:
                out += "\n\nHINT: Value error — invalid value passed to a function. Use read_file to check the data types and fix with edit_file."
            elif "permission denied" in combined:
                out += "\n\nHINT: Permission denied. Try adding chmod +x, or run with python3 explicitly."
            else:
                out += "\n\nHINT: Script failed. Use read_file to inspect the code, find the error, edit_file to fix it, then run_command to retry. Do NOT give up."
        else:
            # Script succeeded — auto-verify and auto-open output files (Claude Code verify phase)
            desktop = pathlib.Path.home() / "Desktop"
            try:
                recent_files = []
                for ext in ("*.png", "*.html", "*.csv", "*.pdf", "*.xlsx"):
                    for f in desktop.glob(ext):
                        if (time.time() - f.stat().st_mtime) < 30:
                            recent_files.append(f)
                # Also detect files mentioned in stdout (e.g., "Saved to /path/to/file.png")
                saved_pattern = re_module.findall(r'(?:saved?\s+(?:to|as|at)|wrote|output|created)[:\s]+([^\s\'"]+\.(?:png|html|csv|pdf))', stdout, re_module.IGNORECASE)
                for sp in saved_pattern:
                    p = pathlib.Path(sp).expanduser().resolve()
                    if p.exists() and p not in recent_files:
                        recent_files.append(p)
                if recent_files:
                    names = [f.name for f in recent_files]
                    out += f"\n\nVerified: output files created: {', '.join(names)}"
                    # Auto-open on macOS (non-blocking)
                    for f in recent_files[:3]:
                        try:
                            subprocess.Popen(["open", str(f)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                    if HAS_RICH:
                        console.print(f"  [dim]Opened {', '.join(names[:3])}[/dim]")
                else:
                    combined_check = (stdout + " " + stderr).lower()
                    if any(kw in combined_check for kw in ("chart", "plot", "figure", "savefig", "save")):
                        out += ("\n\nWARNING: Script ran but no output files detected on Desktop. "
                                "Check the save path uses os.path.expanduser('~/Desktop/filename.png').")
            except Exception:
                pass
        return out
    if tool_name == "write_file":
        return f"OK: {data.get('action', 'created')} {data.get('path', '')} ({data.get('lines', 0)} lines)"
    if tool_name == "edit_file":
        return f"OK: edited {data.get('path', '')} ({data.get('replacements', 0)} replacements)"
    if tool_name == "read_file":
        content = data.get("content", "")
        return f"OK: {data.get('lines', 0)} lines\n{content[:2000]}"
    if tool_name == "list_files":
        items = data.get("items", [])
        names = [it["name"] if isinstance(it, dict) else str(it) for it in items[:20]]
        return f"OK: {data.get('count', len(items))} items: {', '.join(names)}"
    if tool_name == "search_code":
        matches = data.get("matches", [])
        return f"OK: {len(matches)} matches\n" + "\n".join(str(m)[:200] for m in matches[:10])
    # Remote tools — JSON summary
    return json.dumps(data, ensure_ascii=False)[:2000]


def _format_tool_params(tool_name: str, params: dict) -> str:
    """Format tool params into a readable short string (Claude Code style)."""
    if not params:
        return ""
    if tool_name in ("read_file", "write_file", "edit_file"):
        return params.get("path", "")
    if tool_name == "run_command":
        return params.get("command", "")[:60]
    if tool_name == "list_files":
        p = params.get("path", ".")
        pat = params.get("pattern", "*")
        return f"{p}/{pat}" if pat != "*" else p
    if tool_name == "search_code":
        return params.get("pattern", "")[:40]
    if tool_name in ("get_market_data", "get_crypto_data", "get_forex_data",
                      "get_commodities_data", "get_futures_data", "get_bonds_data"):
        return params.get("symbol", params.get("symbols", ""))
    if tool_name == "backtest_strategy":
        return f"{params.get('strategy', '')} {params.get('symbol', '')}"
    if tool_name == "web_search":
        return params.get("query", "")[:50]
    if tool_name == "analyze_news":
        return params.get("symbol", params.get("query", ""))
    # Fallback: show first value
    for v in params.values():
        s = str(v)
        return s[:50] if len(s) > 50 else s
    return ""


def _print_tool_call(tool_name: str, params: dict):
    """Print tool call header — Claude Code style with copper branding."""
    hint = _format_tool_params(tool_name, params)
    if HAS_RICH:
        console.print(Rule(characters="·", style="dim"))
        if hint:
            console.print(f"  [bold #C08050]{tool_name}[/bold #C08050] [dim]{hint}[/dim]")
        else:
            console.print(f"  [bold #C08050]{tool_name}[/bold #C08050]")
    else:
        if hint:
            print(f"\n  {tool_name} {hint}", end="", flush=True)
        else:
            print(f"\n  {tool_name}", end="", flush=True)


def _fuzzy_match(query: str, candidates: list, max_results: int = 3) -> list:
    """Find closest matches using simple edit distance."""
    def _edit_dist(a, b):
        if len(a) > len(b):
            a, b = b, a
        dists = range(len(a) + 1)
        for j, cb in enumerate(b):
            new_dists = [j + 1]
            for i, ca in enumerate(a):
                cost = 0 if ca == cb else 1
                new_dists.append(min(new_dists[-1] + 1, dists[i + 1] + 1, dists[i] + cost))
            dists = new_dists
        return dists[-1]

    scored = [(c, _edit_dist(query.lower(), c.lower())) for c in candidates]
    scored.sort(key=lambda x: x[1])
    # Only suggest if edit distance is reasonable (< half the length)
    threshold = max(3, len(query) // 2)
    return [c for c, d in scored[:max_results] if d <= threshold]


def _error_hint(error: str, context: str = "") -> str:
    """Return actionable recovery hint based on error type."""
    err_lower = error.lower() if error else ""

    if "connection" in err_lower or "refused" in err_lower or "unreachable" in err_lower:
        return "Hint: Backend unreachable. Try /health or check your network."
    if "timeout" in err_lower or "timed out" in err_lower:
        return "Hint: Request timed out. Try again or check /health."
    if "401" in err_lower or "unauthorized" in err_lower or "auth" in err_lower:
        return "Hint: Authentication required. Run /login to sign in."
    if "403" in err_lower or "forbidden" in err_lower:
        return "Hint: Access denied. Check your subscription or /login again."
    if "429" in err_lower or "rate" in err_lower:
        return "Hint: Rate limited. Wait a moment and try again."

    # Ollama model-not-found — must come BEFORE generic 404 check
    if ("ollama" in err_lower or "ollama http" in err_lower) and (
        "not found" in err_lower or "404" in err_lower
    ):
        # Try to extract the model name from the error message
        import re as _re
        m = _re.search(r"model ['\"]?([^'\"]+)['\"]? not found", err_lower)
        model_hint = m.group(1) if m else "the requested model"
        # List available models as a suggestion
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

    if "404" in err_lower or "not found" in err_lower:
        if context == "tool":
            return "Hint: Tool not available. Check /tools for available tools."
        if context == "session":
            return "Hint: Session not found. Run /sessions to list available."
        return "Hint: Resource not found. Check the symbol or path."
    if "no data" in err_lower or "no result" in err_lower:
        return "Hint: No data returned. Verify the symbol spelling."
    if "500" in err_lower or "internal" in err_lower:
        return "Hint: Server error. Try again in a moment or /health to check."
    if context == "login":
        return "Hint: Check email/password. Usage: /login email password"
    return ""


def _print_error(msg: str, context: str = ""):
    """Print error message with recovery hint — Panel style."""
    if HAS_RICH:
        hint = _error_hint(msg, context)
        body = f"[red]{msg}[/red]"
        if hint:
            body += f"\n[dim]{hint}[/dim]"
        console.print(Panel(
            body,
            border_style="red",
            box=rich_box.ROUNDED,
            padding=(0, 1),
        ))
    else:
        print(msg)


from contextlib import contextmanager as _contextmanager

@_contextmanager
def _null_ctx():
    """No-op context manager for conditional `with` blocks."""
    yield


def _is_ashare_symbol(symbol: str) -> bool:
    """Quick check whether a symbol looks like a Chinese A-share code."""
    s = symbol.strip().lower()
    return (
        s.startswith("sh") or s.startswith("sz")
        or (len(s) == 6 and s.isdigit())
        or s.endswith(".ss") or s.endswith(".sz")
    )


_FINANCE_TOOL_NAMES = frozenset({
    "get_market_data", "get_crypto_data", "get_forex_data",
    "get_commodities_data", "get_futures_data", "calculate_factors",
    "backtest_strategy", "cloud_backtest", "get_risk_metrics",
    "optimize_positions", "get_sector_performance", "get_northbound_flow",
    "screen_ashare", "get_limit_up_pool", "get_market_indices",
    "analyze_news", "get_bonds_data", "get_ai_signal",
    "get_market_insights", "get_predictions",
})


def _print_tool_result(tool_name: str, result: dict, elapsed: float = 0, params: dict = None):
    """Print tool result summary — Claude Code style with timing and diffs."""
    time_suffix = f" {elapsed:.1f}s" if elapsed >= 0.1 else ""
    params = params or {}

    # Route finance tools to the rich finance formatter
    if tool_name in _FINANCE_TOOL_NAMES:
        if time_suffix and HAS_RICH:
            console.print(f"  [dim]{tool_name}{time_suffix}[/dim]")
        _print_finance_result(tool_name, result)
        return

    if result.get("success"):
        data = result.get("data", {})
        if tool_name == "write_file":
            path = params.get("path", data.get("path", ""))
            # Prefer result.data for line count (accurate); fall back to computing from params
            _res_lines = data.get("lines")
            _res_size  = data.get("size_bytes")
            if _res_lines is not None:
                lines = _res_lines
            else:
                content = params.get("content", "")
                lines = content.count("\n") + 1 if content else 0
            if _res_size is not None:
                size = _res_size
            else:
                content = params.get("content", "")
                size = len(content.encode("utf-8")) if content else 0
            size_str = f"{size}B" if size < 1024 else f"{size//1024}KB"
            label = f"{path}  {lines} lines  {size_str}{time_suffix}"
            if HAS_RICH:
                console.print(Panel(
                    f"[green]✓[/green] [dim]{label}[/dim]",
                    border_style="dim",
                    box=rich_box.ROUNDED,
                    padding=(0, 1),
                ))
            else:
                print(f" {label}")
        elif tool_name == "edit_file":
            old = params.get("old_string", "")
            new = params.get("new_string", "")
            if old and new and HAS_RICH:
                import difflib
                old_lines = old.splitlines(keepends=True)
                new_lines = new.splitlines(keepends=True)
                diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))
                if diff:
                    console.print()
                    for line in diff[2:]:  # skip @@/--- +++ header
                        if line.startswith("+"):
                            console.print(f"    [green]{line.rstrip()}[/green]")
                        elif line.startswith("-"):
                            console.print(f"    [red]{line.rstrip()}[/red]")
                        else:
                            console.print(f"    [dim]{line.rstrip()}[/dim]")
            if time_suffix and HAS_RICH:
                console.print(f"  [dim]{time_suffix.strip()}[/dim]")
            elif time_suffix:
                print(f" {time_suffix.strip()}")
        elif tool_name == "run_command":
            stdout = data.get("stdout", "").strip()
            returncode = data.get("returncode", 0)
            if HAS_RICH:
                rc_color = "green" if returncode == 0 else "red"
                rc_icon  = "✓" if returncode == 0 else "✗"
                lines_str = ""
                if stdout:
                    out_lines = stdout.splitlines()
                    preview = "\n".join(
                        f"[dim]{ol[:120]}[/dim]" for ol in out_lines[:6]
                    )
                    if len(out_lines) > 6:
                        preview += f"\n[dim]… (+{len(out_lines)-6} lines)[/dim]"
                    lines_str = "\n" + preview
                console.print(Panel(
                    f"[{rc_color}]{rc_icon} exit {returncode}[/{rc_color}][dim]{time_suffix}[/dim]{lines_str}",
                    border_style=rc_color,
                    box=rich_box.ROUNDED,
                    padding=(0, 1),
                ))
            else:
                print(f" exit {returncode}{time_suffix}")
        elif tool_name == "read_file":
            lines = data.get("lines", 0)
            content = data.get("content", "")
            path = params.get("path", "")
            if HAS_RICH:
                preview_str = ""
                if content:
                    preview_lines = content.splitlines()[:3]
                    preview_str = "\n" + "\n".join(
                        f"[dim]{pl[:100]}{'…' if len(pl) > 100 else ''}[/dim]"
                        for pl in preview_lines
                    )
                    if lines > 3:
                        preview_str += f"\n[dim]… (+{lines-3} more lines)[/dim]"
                console.print(Panel(
                    f"[dim]{path}  {lines} lines{time_suffix}[/dim]{preview_str}",
                    border_style="dim",
                    box=rich_box.ROUNDED,
                    padding=(0, 1),
                ))
            else:
                print(f" {lines} lines{time_suffix}")
        elif tool_name == "list_files":
            count = data.get("count", 0)
            if HAS_RICH:
                if count == 0:
                    console.print(f" [yellow]0 items — 未找到匹配文件{time_suffix}[/yellow]")
                else:
                    console.print(f" [dim]{count} items{time_suffix}[/dim]")
            else:
                print(f" {count} items{time_suffix}")
        elif tool_name == "search_code":
            matches = len(data.get("matches", []))
            if HAS_RICH:
                console.print(f" [dim]{matches} matches{time_suffix}[/dim]")
            else:
                print(f" {matches} matches{time_suffix}")
        else:
            # Remote tools — show concise summary
            summary = json.dumps(data, ensure_ascii=False)
            short = (summary[:60] + "...") if len(summary) > 60 else summary
            if HAS_RICH:
                console.print(f" [dim]{short}{time_suffix}[/dim]")
            else:
                print(f" done{time_suffix}")
    else:
        error = result.get("error", "failed")
        if HAS_RICH:
            console.print(f" [red]{error[:100]}[/red]")
        else:
            print(f" error: {error[:80]}")


def _print_finance_result(tool_name: str, result: dict):
    """
    Rich-formatted display for all finance tool results.
    Shows structured tables instead of raw dicts.
    """
    if not result or not isinstance(result, dict):
        return
    if not result.get("success"):
        # result.get("error") may be None even when key exists — use `or` to fallback
        err = result.get("error") or result.get("message") or "数据暂不可用（服务离线或无数据）"
        if HAS_RICH:
            console.print(f"  [yellow]⚠ {err}[/yellow]")
        else:
            print(f"  ⚠ {err}")
        return

    provider = result.get("provider", "")
    prov_tag = f" [dim][{provider}][/dim]" if provider else ""

    # ── Market data / quote ────────────────────────────────────────────
    if tool_name in ("get_market_data", "get_crypto_data", "get_forex_data"):
        sym   = result.get("symbol", "")
        px    = result.get("latest_close", result.get("price", 0))
        chg   = result.get("change_pct", result.get("change_pct_24h", 0)) or 0
        vol   = result.get("volume", 0)
        name  = result.get("name", "")
        curr  = result.get("currency", "")
        color = "green" if chg >= 0 else "red"
        arrow = "▲" if chg >= 0 else "▼"
        if HAS_RICH:
            from rich.table import Table
            t = Table(show_header=False, box=None, padding=(0, 1))
            t.add_column(style="dim", width=20)
            t.add_column()
            title_str = f"[bold]{sym}[/bold]" + (f"  {name}" if name else "")
            t.add_row("标的", title_str)
            px_disp = f"{curr} {px:,.4g}" if curr else f"{px:,.4g}"
            t.add_row("最新价", f"[bold]{px_disp}[/bold]")
            t.add_row("涨跌幅", f"[{color}]{arrow} {chg:+.2f}%[/{color}]")
            _hi = result.get("high"); _lo = result.get("low")
            if _hi and _lo:
                t.add_row("日内区间", f"{_lo:,.4g} — {_hi:,.4g}")
            if vol:
                t.add_row("成交量", f"{int(vol):,}")
            # Technical indicators from local tool
            _rsi = result.get("rsi")
            if _rsi is not None:
                _rsi_color = "red" if _rsi >= 70 else ("cyan" if _rsi <= 30 else "white")
                t.add_row("RSI(14)", f"[{_rsi_color}]{_rsi:.1f}[/{_rsi_color}]")
            _mh = result.get("macd_hist")
            if _mh is not None:
                _mh_color = "green" if _mh > 0 else "red"
                t.add_row("MACD hist", f"[{_mh_color}]{_mh:+.4f}[/{_mh_color}]")
            _ma20 = result.get("ma20"); _ma60 = result.get("ma60")
            if _ma20:
                t.add_row("MA20", f"{_ma20:,.4g}")
            if _ma60:
                t.add_row("MA60", f"{_ma60:,.4g}")
            # Legacy cloud fields
            for k in ("high_52w", "low_52w", "bid", "ask"):
                v = result.get(k)
                if v is not None:
                    t.add_row(k.replace("_", " ").title(), f"{v:,.4g}")
            console.print(t)
            if prov_tag:
                console.print(f"  {prov_tag}")
        else:
            print(f"  {sym}: {px} ({chg:+.2f}%)")
        return

    # ── Commodity data ─────────────────────────────────────────────────
    if tool_name == "get_commodities_data":
        sym  = result.get("symbol", "")
        px   = result.get("latest_close", 0)
        chg  = result.get("change_pct", 0) or 0
        unit = result.get("unit", "")
        color = "green" if chg >= 0 else "red"
        arrow = "▲" if chg >= 0 else "▼"
        if HAS_RICH:
            console.print(
                f"  [bold]{sym}[/bold]  {px:,.3g} {unit}  "
                f"[{color}]{arrow} {chg:+.3f}%[/{color}]{prov_tag}"
            )
            for k in ("pct_from_52w_high", "pct_from_52w_low", "year_return"):
                v = result.get(k)
                if v is not None:
                    console.print(f"    [dim]{k:<25s}[/dim] {v:+.3%}")
        else:
            print(f"  {sym}: {px} ({chg:+.3f}%)")
        return

    # ── AI signal ──────────────────────────────────────────────────────
    if tool_name == "get_ai_signal":
        action = result.get("action", "HOLD")
        conf   = result.get("confidence", 0)
        reason = result.get("reasoning", "")
        sl     = result.get("stop_loss")
        tp     = result.get("take_profit")
        color  = {"BUY": "green", "SELL": "red", "HOLD": "yellow"}.get(action, "white")
        if HAS_RICH:
            console.print(f"  Signal: [{color}][bold]{action}[/bold][/{color}]  "
                          f"Confidence: [bold]{conf:.1%}[/bold]{prov_tag}")
            if reason:
                console.print(f"  [dim]{reason[:120]}[/dim]")
            if sl is not None:
                console.print(f"  [dim]Stop-loss: {sl}   Take-profit: {tp}[/dim]")
        else:
            print(f"  {action} ({conf:.1%}) — {reason[:80]}")
        return

    # ── Factors ────────────────────────────────────────────────────────
    if tool_name == "calculate_factors":
        sym = result.get("symbol", "")
        if HAS_RICH:
            from rich.table import Table
            t = Table(title=f"Factors — {sym}", show_header=True, box=None, padding=(0, 1))
            t.add_column("Factor", style="dim", width=24)
            t.add_column("Value",  justify="right")
            t.add_column("Signal", width=6)
            def _sig(v, neutral_lo=-0.1, neutral_hi=0.1):
                if v is None: return ""
                return "[green]▲[/green]" if v > neutral_hi else "[red]▼[/red]" if v < neutral_lo else "[yellow]─[/yellow]"
            FACTOR_ROWS = [
                ("rsi_14",          "RSI(14)",         lambda v: "[red]OB[/red]" if v and v > 70 else "[green]OS[/green]" if v and v < 30 else "[dim]─[/dim]"),
                ("macd_hist",       "MACD Hist",       lambda v: "[green]▲[/green]" if v and v > 0 else "[red]▼[/red]"),
                ("trend_score",     "Trend Score",     lambda v: _sig(v, -0.2, 0.2)),
                ("bb_position",     "BB Position",     lambda v: "[red]OB[/red]" if v and v > 0.9 else "[green]OS[/green]" if v and v < 0.1 else "[dim]─[/dim]"),
                ("ma_20_gap",       "vs MA20",         lambda v: "[green]▲[/green]" if v and v > 0 else "[red]▼[/red]"),
                ("ma_60_gap",       "vs MA60",         lambda v: "[green]▲[/green]" if v and v > 0 else "[red]▼[/red]"),
                ("volatility_20d",  "Vol(20d)",        lambda v: ""),
                ("volume_ratio_20d","Vol Ratio",       lambda v: "[green]⬆[/green]" if v and v > 1.5 else ""),
                ("return_5d",       "Return 5d",       lambda v: "[green]▲[/green]" if v and v > 0 else "[red]▼[/red]"),
                ("return_20d",      "Return 20d",      lambda v: "[green]▲[/green]" if v and v > 0 else "[red]▼[/red]"),
            ]
            for key, label, sig_fn in FACTOR_ROWS:
                v = result.get(key)
                if v is not None:
                    val_str = f"{v:+.4f}" if isinstance(v, float) else str(v)
                    t.add_row(label, val_str, sig_fn(v))
            console.print(t)
            console.print(f"  {prov_tag}")
        else:
            for k, v in result.items():
                if k not in ("success", "symbol", "provider") and isinstance(v, (int, float)):
                    print(f"  {k:<25s} {v:.5g}")
        return

    # ── Backtest ───────────────────────────────────────────────────────
    if tool_name in ("backtest_strategy", "cloud_backtest"):
        sym  = result.get("symbol", result.get("symbols", ""))
        strat = result.get("strategy", result.get("model_type", ""))
        if HAS_RICH:
            from rich.table import Table
            t = Table(title=f"Backtest — {sym}  [{strat}]", show_header=True, box=None)
            t.add_column("Metric",    style="dim", width=24)
            t.add_column("Value",     justify="right")
            PERF_ROWS = [
                ("total_return",    "Total Return",    lambda v: f"[{'green' if v >= 0 else 'red'}]{v:+.2%}[/]"),
                ("annual_return",   "Annual Return",   lambda v: f"[{'green' if v >= 0 else 'red'}]{v:+.2%}[/]"),
                ("sharpe_ratio",    "Sharpe Ratio",    lambda v: f"[{'green' if v >= 1 else 'yellow' if v >= 0.5 else 'red'}]{v:.3f}[/]"),
                ("sortino_ratio",   "Sortino Ratio",   lambda v: f"{v:.3f}"),
                ("max_drawdown",    "Max Drawdown",    lambda v: f"[red]{v:.2%}[/red]"),
                ("win_rate",        "Win Rate",        lambda v: f"{v:.1%}"),
                ("total_trades",    "Trades",          lambda v: str(int(v))),
                ("benchmark_return","Benchmark (B&H)", lambda v: f"{v:+.2%}"),
                ("alpha",           "Alpha",           lambda v: f"[{'green' if v >= 0 else 'red'}]{v:+.2%}[/]"),
            ]
            for key, label, fmt_fn in PERF_ROWS:
                v = result.get(key)
                if v is not None:
                    t.add_row(label, fmt_fn(v))
            console.print(t)
            console.print(f"  {result.get('start', '')} → {result.get('end', '')}  "
                          f"[dim]{result.get('bars', '')} bars[/dim]{prov_tag}")
        else:
            for k in ("total_return", "sharpe_ratio", "max_drawdown", "win_rate"):
                v = result.get(k)
                if v is not None:
                    print(f"  {k:<20s} {v:.4g}")
        return

    # ── Predictions ────────────────────────────────────────────────────
    if tool_name == "get_predictions":
        preds = result.get("predictions", [])
        days  = result.get("prediction_days", 5)
        if HAS_RICH and preds:
            from rich.table import Table
            t = Table(title=f"ML Predictions ({days}d)", show_header=True, box=None)
            t.add_column("Symbol",   style="bold", width=12)
            t.add_column("Predicted Return", justify="right")
            t.add_column("Confidence",       justify="right")
            for p in preds:
                ret  = p.get("predicted_return", 0)
                conf = p.get("confidence", 0)
                color = "green" if ret >= 0 else "red"
                t.add_row(p["symbol"], f"[{color}]{ret:+.2%}[/{color}]", f"{conf:.0%}")
            console.print(t)
            console.print(f"  {prov_tag}")
        else:
            for p in preds:
                print(f"  {p.get('symbol')}: {p.get('predicted_return',0):+.2%}")
        return

    # ── Northbound flow ────────────────────────────────────────────────
    if tool_name == "get_northbound_flow":
        latest = result.get("latest_net_buy_yi", 0)
        total  = result.get("total_net_buy_yi", 0)
        trend  = result.get("trend", "")
        color  = "green" if latest >= 0 else "red"
        if HAS_RICH:
            console.print(f"  北向资金  Today: [{color}][bold]{latest:+.2f}亿[/bold][/{color}]  "
                          f"Period Total: {total:+.2f}亿  [{trend}]{prov_tag}")
        else:
            print(f"  北向 Today: {latest:+.2f}亿  Period: {total:+.2f}亿")
        return

    # ── Market indices ────────────────────────────────────────────────
    if tool_name == "get_market_indices":
        indices = result.get("indices", result)
        if HAS_RICH:
            from rich.table import Table
            t = Table(show_header=True, box=None, padding=(0, 1))
            t.add_column("Index",  style="bold", width=16)
            t.add_column("Price",  justify="right")
            t.add_column("Change", justify="right")
            for idx_name, idx_data in (indices.items() if isinstance(indices, dict) else []):
                if isinstance(idx_data, dict):
                    px  = idx_data.get("price", idx_data.get("latest_close", 0))
                    chg = idx_data.get("change_pct", 0) or 0
                    color = "green" if chg >= 0 else "red"
                    t.add_row(idx_name, f"{px:,.2f}", f"[{color}]{chg:+.2f}%[/{color}]")
            console.print(t)
        else:
            print(json.dumps(indices, indent=2, ensure_ascii=False, default=str)[:400])
        return

    # ── Generic fallback ──────────────────────────────────────────────
    if HAS_RICH:
        # Show key=value pairs, skip large nested objects
        out = Text()
        for k, v in result.items():
            if k in ("success", "provider", "history_tail", "equity_curve", "trades"):
                continue
            if isinstance(v, (int, float)):
                color = "green" if v > 0 else "red" if v < 0 else ""
                out.append(f"  {k.replace('_',' ').title():<24s}", style="dim")
                out.append(f"{v:,.5g}\n", style=color)
            elif isinstance(v, str) and len(v) < 80:
                out.append(f"  {k.replace('_',' ').title():<24s}", style="dim")
                out.append(f"{v}\n")
        if str(out):
            console.print(out)
        if provider:
            console.print(f"  {prov_tag}")
    else:
        print(json.dumps({k: v for k, v in result.items()
                          if k not in ("success",) and not isinstance(v, list)},
                         indent=2, ensure_ascii=False, default=str)[:400])


def format_quote_output(data: dict):
    """Format market data as clean two-column rows."""
    if not HAS_RICH:
        return json.dumps(data, indent=2, ensure_ascii=False)

    d = data.get("data", data)
    symbol = d.get("symbol", "???")
    price = d.get("current_price", d.get("price", 0))
    change = d.get("change_percent", d.get("changePercent", 0))
    high52 = d.get("high_52w", d.get("yearHigh", "-"))
    low52 = d.get("low_52w", d.get("yearLow", "-"))
    volume = d.get("volume", "-")
    market_cap = d.get("market_cap", d.get("marketCap", "-"))

    color = "green" if change >= 0 else "red"
    arrow = "+" if change >= 0 else ""

    out = Text()
    out.append(f"  {symbol}\n", style="bold")
    price_str = f"${price:,.2f}" if isinstance(price, (int, float)) else str(price)
    out.append(f"  {'Price':<16s}", style="dim")
    out.append(f"{price_str}\n")
    out.append(f"  {'Change':<16s}", style="dim")
    out.append(f"{arrow}{change:.2f}%\n", style=color)
    if isinstance(high52, (int, float)):
        out.append(f"  {'52W High':<16s}", style="dim")
        out.append(f"${high52:,.2f}\n")
    if isinstance(low52, (int, float)):
        out.append(f"  {'52W Low':<16s}", style="dim")
        out.append(f"${low52:,.2f}\n")
    if volume != "-":
        vol_str = f"{volume:,}" if isinstance(volume, (int, float)) else str(volume)
        out.append(f"  {'Volume':<16s}", style="dim")
        out.append(f"{vol_str}\n")
    if market_cap and market_cap != "-":
        mc = market_cap
        if isinstance(mc, (int, float)):
            mc_str = f"${mc/1e12:.2f}T" if mc >= 1e12 else f"${mc/1e9:.2f}B" if mc >= 1e9 else f"${mc/1e6:.0f}M"
        else:
            mc_str = str(mc)
        out.append(f"  {'Market Cap':<16s}", style="dim")
        out.append(f"{mc_str}\n")
    # Sparkline from chart_prices
    chart_prices = d.get("chart_prices", [])
    if chart_prices and len(chart_prices) >= 2:
        prices = [p.get("close", p.get("price", 0)) if isinstance(p, dict) else p
                  for p in chart_prices]
        prices = [p for p in prices if isinstance(p, (int, float)) and p > 0]
        if len(prices) >= 2:
            spark = format_sparkline(prices, width=24)
            out.append(f"  {'1M':<16s}", style="dim")
            out.append(f"{spark}\n", style=color)
    return out


def format_backtest_output(data: dict):
    """Format backtest results as clean rows."""
    if not HAS_RICH:
        return json.dumps(data, indent=2, ensure_ascii=False)

    d = data.get("data", data.get("backtest", data))
    total_ret = d.get("total_return", 0)
    ann_ret = d.get("annualized_return", 0)
    sharpe = d.get("sharpe_ratio", 0)
    max_dd = d.get("max_drawdown", 0)
    win_rate = d.get("win_rate", 0)
    trades = d.get("num_trades", 0)
    bh_ret = d.get("buy_hold_return", 0)
    outperf = d.get("outperformance", 0)

    def _c(v): return "green" if v >= 0 else "red"

    out = Text()
    out.append("  Backtest Results\n", style="bold")
    out.append(f"  {'Total Return':<18s}", style="dim")
    out.append(f"{total_ret*100:+.2f}%", style=_c(total_ret))
    out.append(f"  vs B&H ", style="dim")
    out.append(f"{bh_ret*100:+.2f}%\n", style=_c(bh_ret))
    out.append(f"  {'Annualized':<18s}", style="dim")
    out.append(f"{ann_ret*100:+.2f}%\n")
    out.append(f"  {'Sharpe Ratio':<18s}", style="dim")
    out.append(f"{sharpe:.2f}\n")
    out.append(f"  {'Max Drawdown':<18s}", style="dim")
    out.append(f"{max_dd*100:.2f}%\n", style="red")
    out.append(f"  {'Win Rate':<18s}", style="dim")
    out.append(f"{win_rate*100:.1f}%\n")
    out.append(f"  {'Trades':<18s}", style="dim")
    out.append(f"{trades}\n")
    out.append(f"  {'Outperformance':<18s}", style="dim")
    out.append(f"{outperf*100:+.2f}%\n", style=_c(outperf))
    return out


def format_sparkline(prices: list, width: int = 30) -> str:
    """Generate Unicode sparkline from price data."""
    if not prices or len(prices) < 2:
        return ""
    blocks = "▁▂▃▄▅▆▇█"
    mn, mx = min(prices), max(prices)
    rng = mx - mn or 1
    result = ""
    step = max(1, len(prices) // width)
    for i in range(0, len(prices), step):
        idx = int((prices[i] - mn) / rng * (len(blocks) - 1))
        result += blocks[idx]
    return result[:width]


# ============================================================================
# Session Manager — local persistence + cloud sync
# ============================================================================

class SessionManager:
    """Manage chat sessions with local file persistence."""

    def __init__(self):
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    def save_session(self, session_id: str, conversation: list, metadata: dict = None):
        meta = metadata or {}
        if not meta.get("created_at"):
            meta["created_at"] = datetime.now().isoformat()
        for msg in conversation:
            if msg["role"] == "user":
                meta.setdefault("title", msg["content"][:60])
                break
        data = {
            "id": session_id,
            "messages": conversation,
            "metadata": meta,
            "updated_at": datetime.now().isoformat(),
        }
        path = SESSIONS_DIR / f"{session_id}.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_session(self, session_id: str) -> Optional[dict]:
        path = SESSIONS_DIR / f"{session_id}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        return None

    def list_sessions(self, limit: int = 20) -> list:
        sessions = []
        for path in sorted(SESSIONS_DIR.glob("*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                with open(path) as f:
                    data = json.load(f)
                sessions.append({
                    "id": data.get("id", path.stem),
                    "title": data.get("metadata", {}).get("title", "Untitled"),
                    "messages": len(data.get("messages", [])),
                    "updated": data.get("updated_at", ""),
                })
            except Exception:
                continue
            if len(sessions) >= limit:
                break
        return sessions

    def delete_session(self, session_id: str) -> bool:
        path = SESSIONS_DIR / f"{session_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False


# ============================================================================
# Tab Completer — commands, skills, stock symbols
# ============================================================================

class ArtheraCompleter:
    """Tab completion for basic readline fallback."""

    def __init__(self, commands: list, skills: list, watchlist: list):
        self.tokens = list(commands) + [s["command"] for s in skills]
        self.tokens.extend([
            "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX",
            "AMD", "INTC", "SPY", "QQQ", "DIA", "IWM", "BTC", "ETH", "SOL",
            "JPM", "BAC", "GS", "V", "MA", "UNH", "JNJ", "PFE", "XOM", "CVX",
        ])
        self.tokens.extend(watchlist)
        self.matches = []

    def complete(self, text: str, state: int):
        if state == 0:
            self.matches = [t for t in self.tokens
                            if t.lower().startswith(text.lower())] if text else []
        try:
            return self.matches[state]
        except IndexError:
            return None


if HAS_PT:
    class AriaPTCompleter(Completer):
        """prompt_toolkit completer: slash commands + skills with category labels."""

        def __init__(self, commands_dict: dict, skills: list, watchlist: list):
            self.commands = commands_dict
            self.skills = skills
            self.symbols = sorted(set([
                "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX",
                "AMD", "INTC", "SPY", "QQQ", "DIA", "IWM", "BTC", "ETH", "SOL",
                "JPM", "BAC", "GS", "V", "MA", "UNH", "JNJ", "PFE", "XOM", "CVX",
            ] + list(watchlist)))

        def get_completions(self, document, complete_event):
            text = document.text_before_cursor.lstrip()
            word = document.get_word_before_cursor(WORD=True)

            # Only show completions when input starts with /
            if not text.startswith("/"):
                return

            prefix = word if word.startswith("/") else "/" + word

            # Built-in commands first
            for name, (_, desc) in self.commands.items():
                if name.startswith(prefix) or prefix == "/":
                    yield Completion(
                        name, start_position=-len(prefix),
                        display=name,
                        display_meta=desc,
                    )

            # Skills
            for s in self.skills:
                cmd = s["command"]
                if cmd.startswith(prefix) or prefix == "/":
                    yield Completion(
                        cmd, start_position=-len(prefix),
                        display=cmd,
                        display_meta=s["description"],
                    )

    # prompt_toolkit style — uses ANSI colors only (adapts to light/dark terminals)
    ARIA_PT_STYLE = PTStyle.from_dict({
        "prompt": "bold #C08050",
        "placeholder": "#888888 italic",
        "bottom-toolbar": "#888888",
        "bottom-toolbar.text": "#888888",
        "completion-menu": "bg:#2a2a2a #cccccc",
        "completion-menu.completion": "bg:#2a2a2a #cccccc",
        "completion-menu.completion.current": "bg:#555555 #ffffff bold",
        "completion-menu.meta": "bg:#2a2a2a #888888",
        "completion-menu.meta.current": "bg:#555555 #cccccc",
        "scrollbar.background": "bg:#2a2a2a",
        "scrollbar.button": "bg:#555555",
    })


# ============================================================================
# Interactive model picker
# Runs _arrow_select inside run_in_executor so it gets its own thread and
# doesn't conflict with the prompt_toolkit event loop or kqueue on macOS.
# ============================================================================

async def _run_picker_in_thread(options: list, current_idx: int,
                                title: str, max_visible: int = 14) -> int:
    """
    Run _arrow_select in a dedicated executor thread.

    Why a thread and not run_async / app.run:
      • prompt_toolkit Application.run_async() calls loop.add_reader(stdin_fd)
        which fails on macOS (kqueue rejects TTY fds) → OSError EINVAL
      • Calling _arrow_select synchronously in the async context races against
        prompt_toolkit's terminal-cleanup thread → arrows land in the wrong reader
      • run_in_executor gives _arrow_select full TTY ownership in its own thread,
        matching exactly how _pt_session.prompt() works (no conflict)
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _arrow_select(options, current_idx, title, max_visible),
    )


# ============================================================================
# Slash Commands
# ============================================================================

class SlashCommands:
    """Claude Code-style slash command system."""

    def __init__(self, terminal: 'ArtheraTerminal'):
        self.terminal = terminal
        self.commands = {
            "/help":      (self.cmd_help,      "Show all commands and skills"),
            "/quote":     (self.cmd_quote,     "Quick quote: /quote AAPL MSFT"),
            "/analyze":   (self.cmd_analyze,   "AI analysis: /analyze AAPL"),
            "/backtest":  (self.cmd_backtest,  "Backtest: /backtest momentum SPY [start] [end]"),
            "/wf":        (self.cmd_walk_forward, "Walk-Forward: /wf SPY [momentum] [rolling]"),
            "/compare":   (self.cmd_compare,   "Strategy compare: /compare SPY [start] [end]"),
            "/watch":     (self.cmd_watch,     "Watchlist: /watch add AAPL | /watch list"),
            "/portfolio": (self.cmd_portfolio, "Portfolio risk assessment"),
            "/screen":    (self.cmd_screen,    "Screen stocks: /screen tech"),
            "/model":     (self.cmd_model,     "Select AI model (interactive picker)"),
            "/thinking":  (self.cmd_thinking,  "Toggle thinking: /thinking on"),
            "/tools":     (self.cmd_tools,     "List all Aria tools"),
            "/services":  (self.cmd_services,  "Show CLI service tiers and workflows"),
            "/plan":      (self.cmd_plan,      "Draft executable plan: /plan step1 ; step2"),
            "/apply-plan":(self.cmd_apply_plan,"Execute pending plan steps"),
            "/plan-report":(self.cmd_plan_report,"Show/export last plan execution report"),
            "/git":       (self.cmd_git,       "Git helper: /git status|diff|summary"),
            "/gh":        (self.cmd_gh,        "GitHub CLI: /gh prs|issues|pr N|create-pr|search"),
            "/skills":    (self.cmd_skills,    "List all available skills"),
            "/health":    (self.cmd_health,    "Check backend health"),
            "/clear":     (self.cmd_clear,     "Clear conversation"),
            "/history":   (self.cmd_history,   "Show conversation history"),
            "/compact":   (self.cmd_compact,   "Smart compact: /compact [--hard]"),
            "/regen":     (self.cmd_regen,     "Regenerate last AI response"),
            "/undo":      (self.cmd_undo,      "Undo last message pair"),
            "/fork":      (self.cmd_fork,      "Fork conversation at current point: /fork [name]"),
            "/copy":      (self.cmd_copy,      "Copy last response to clipboard"),
            "/cost":      (self.cmd_cost,      "Show session token usage and estimated cost"),
            "/todo":      (self.cmd_todo,      "Task tracking: /todo add|done|list|clear"),
            "/doctor":    (self.cmd_doctor,    "Diagnose installation, models, API keys"),
            "/hooks":     (self.cmd_hooks,     "Manage event hooks: /hooks list|edit|run"),
            "/login":     (self.cmd_login,     "Login: /login <email>"),
            "/logout":    (self.cmd_logout,    "Logout current user"),
            "/whoami":    (self.cmd_whoami,    "Show current user and token status"),
            "/sessions":  (self.cmd_sessions,  "List/search sessions: /sessions [keyword]"),
            "/save":      (self.cmd_save,      'Save session: /save ["name"]'),
            "/load":      (self.cmd_load,      "Load session: /load <id>"),
            "/rename":    (self.cmd_rename,     'Rename session: /rename "title"'),
            "/export":    (self.cmd_export,    "Export: /export json|csv|md [file]"),
            "/feedback":  (self.cmd_feedback,  "Feedback last reply: /feedback up|down"),
            "/code":      (self.cmd_code,      "Generate & save code: /code <description> [--save file.py]"),
            "/scaffold":  (self.cmd_scaffold,  "Scaffold project: /scaffold <name> [--template strategy|analysis|pipeline]"),
            "/read":      (self.cmd_read,      "Read file: /read <path> [offset] [limit]"),
            "/write":     (self.cmd_write,     "Write file: /write <path> (then paste content)"),
            "/edit":      (self.cmd_edit,      "Edit file: /edit <path>"),
            "/ls":        (self.cmd_ls,        "List files: /ls [path] [pattern]"),
            "/search":    (self.cmd_search,    "Search code: /search <pattern> [path] [glob]"),
            "/run":       (self.cmd_run,       "Run command: /run <command>"),
            "/apply":     (self.cmd_apply,     "Extract & save code from last AI response"),
            "/news":      (self.cmd_news,      "Latest news: /news [topic|symbol]"),
            "/config":    (self.cmd_config,    "Show/set config: /config set key=value"),
            "/context":   (self.cmd_context,   "Show current AI context & session"),
            "/crypto":    (self.cmd_crypto,    "Crypto data: /crypto BTC ETH"),
            "/forex":     (self.cmd_forex,     "Forex rates: /forex EUR/USD"),
            "/commodity": (self.cmd_commodity, "Commodities: /commodity gold oil"),
            "/risk":      (self.cmd_risk,      "Risk metrics: /risk AAPL | /risk portfolio"),
            "/market":    (self.cmd_market,    "Market overview: /market [indices|sectors]"),
            "/optimize":  (self.cmd_optimize,  "Optimize portfolio: /optimize AAPL MSFT"),
            "/stress":    (self.cmd_stress,    "Stress test: /stress <strategy> [symbol]"),
            "/factors":   (self.cmd_factors,   "Factor analysis (local+remote): /factors AAPL"),
            "/compliance":(self.cmd_compliance,"Compliance check: /compliance <strategy>"),
            "/web":       (self.cmd_search_web,"Web search: /web <query>"),
            "/local":     (self.cmd_local,     "Toggle local-only mode (skip AWS): /local [on|off]"),
            "/orcl":      (self.cmd_orcl,      "Oracle Corp analysis: /orcl [deep]"),
            # ── New: MCP / ariarc / provider / recommend ─────────────────
            "/mcp":       (self.cmd_mcp,       "MCP servers: /mcp status | /mcp tools | /mcp reload"),
            "/ariarc":    (self.cmd_ariarc,    "Show .ariarc project config: /ariarc [reload]"),
            "/providers":  (self.cmd_providers, "List all local LLM backend providers and status"),
            "/recommend":  (self.cmd_recommend, "Recommend best local models for finance work"),
            # ── Finance shortcuts (local tools) ────────────────────────────
            "/screen-cn": (self.cmd_screen_cn, "A股选股筛选: /screen-cn [max_pe=50] [limit=20]"),
            "/limitup":   (self.cmd_limitup,   "A股涨停板池: /limitup [date YYYY-MM-DD]"),
            "/north":     (self.cmd_north,     "北向资金净流入: /north [days=10]"),
            "/optimize-port": (self.cmd_optimize_port, "Portfolio optimisation: /optimize-port AAPL MSFT GOOGL"),
            # ── Alibaba Cloud services ─────────────────────────────────────
            "/cloud":     (self.cmd_cloud,     "Aliyun cloud config: /cloud status|set|data|token|health|reset"),
            "/signal":    (self.cmd_signal,    "AI signal (BUY/SELL/HOLD): /signal sh600519 [CN|US]"),
            "/predict":   (self.cmd_predict,   "ML predictions: /predict sh600519 sh601318 [d=5]"),
            "/cloudbt":   (self.cmd_cloudbt,   "Cloud ML backtest: /cloudbt sh600519 [model=lightgbm] [months=12]"),
            "/insights":  (self.cmd_insights,  "AI market insights: /insights sh600519 sh601318"),
            # ── 金融 Agent 团队 ────────────────────────────────────────────────
            "/team":      (self.cmd_team,      "多Agent研究团队: /team NVDA [--agents macro,technical]"),
            "/chart":     (self.cmd_chart,     "生成股票图表(HTML): /chart AAPL | /chart 600519"),
            "/report":    (self.cmd_report,    "综合投资报告(图表+分析): /report AAPL"),
            "/shortterm": (self.cmd_shortterm, "A股短线分析(日线): /shortterm [000333 601138]"),
            "/longterm":  (self.cmd_longterm,  "A股长线分析(月线): /longterm [--quick]"),
            "/indices":   (self.cmd_indices,   "全球指数实时行情: /indices"),
            "/hot":       (self.cmd_hot,       "热门股榜单: /hot [cn|us] [top=20]"),
            "/ta":        (self.cmd_ta,        "技术指标: /ta NVDA [days=120]"),
            # ── 策略金库 ───────────────────────────────────────────────────────
            "/strategy":  (self.cmd_strategy,  "策略版本管理: /strategy save|list|diff|load|review"),
            # ── 记忆 / 项目引导 / 代码审查 ────────────────────────────────────
            "/note":      (self.cmd_note,      "追加笔记到 ARIA.md: /note <内容>"),
            "/memory":    (self.cmd_memory,    "记忆管理: /memory [show|add <内容>|clear|search]"),
            "/init":      (self.cmd_init,      "为当前项目生成 ARIA.md: /init [--force]"),
            "/review":    (self.cmd_review,    "AI 代码审查: /review [file] | /review --staged"),
            # ── Provider / 模型配置（Open Interpreter 风格）───────────────────
            "/apikey":    (self.cmd_apikey,    "Cloud API Key 管理: /apikey set|list|remove|test"),
            "/setup":     (self.cmd_setup,     "首次配置向导: /setup"),
            # ── 量化专属（Aria 独有）────────────────────────────────────────────
            "/auto-strategy": (self.cmd_auto_strategy, "AI 策略自动优化闭环: /auto-strategy momentum SPY --target sharpe=1.5"),
            "/factor-lab":    (self.cmd_factor_lab,    "因子分析工作台: /factor-lab AAPL [days=252]"),
            # ── financial-services 风格 workflow 命令 ────────────────────────────
            "/research":  (self.cmd_research,  "Market Researcher 工作流: /research <symbol>"),
            "/earnings":  (self.cmd_earnings_workflow, "财报分析工作流: /earnings <symbol> [quarter]"),
            # ── 经营权共创平台 Agent 命令 ────────────────────────────────────────────
            "/asset-diag":    (self.cmd_asset_diag,    "资产诊断 Agent: /asset-diag <asset_id|项目名>"),
            "/contract-draft":(self.cmd_contract_draft,"合同规则草案: /contract-draft <project_id>"),
            "/revenue-calc":  (self.cmd_revenue_calc,  "分账测算: /revenue-calc <project_id> <流水金额>"),
            "/risk-scan":     (self.cmd_realty_risk_scan, "项目风险扫描: /risk-scan [project_id]"),
            "/ops-report":    (self.cmd_ops_report,    "运营汇报生成: /ops-report <project_id>"),
            "/exit-calc":     (self.cmd_exit_calc,     "退出清算草案: /exit-calc <project_id>"),
            "/load-fork":     (self.cmd_load_fork,    "Restore forked conversation: /load-fork <id>"),
            # ── Vision / image input ──────────────────────────────────────────
            "/vision":    (self.cmd_vision,    "Load image for visual analysis: /vision <path>"),
        }
        # Register skills as slash commands
        self.skill_map = {}
        for skill in SKILLS:
            self.skill_map[skill["command"]] = skill

    def is_command(self, text: str) -> bool:
        if not text.startswith("/"):
            return False
        # Only match registered commands and skills, not file paths like /Users/...
        cmd = text.split(maxsplit=1)[0].lower()
        return cmd in self.commands or cmd in self.skill_map

    async def execute(self, text: str):
        parts = text.split(maxsplit=1)
        cmd_name = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        if cmd_name in self.commands:
            handler, _ = self.commands[cmd_name]
            result = handler(args)
            if asyncio.iscoroutine(result):
                await result
        elif cmd_name in self.skill_map:
            await self._execute_skill(self.skill_map[cmd_name], args)
        else:
            # Fuzzy match: suggest closest command
            all_cmds = list(self.commands.keys()) + list(self.skill_map.keys())
            suggestions = _fuzzy_match(cmd_name, all_cmds, max_results=3)
            if HAS_RICH:
                console.print(f"[red]Unknown command: {cmd_name}[/red]")
                if suggestions:
                    console.print(f"  [dim]Did you mean: {', '.join(suggestions)}?[/dim]")
            else:
                print(f"Unknown command: {cmd_name}")
                if suggestions:
                    print(f"  Did you mean: {', '.join(suggestions)}?")

    # Per-command detailed help: (usage, examples)
    _COMMAND_HELP = {
        "/quote":     ("Usage: /quote [SYMBOL...]", ["/quote AAPL", "/quote AAPL MSFT GOOGL", "/quote  (uses watchlist)"]),
        "/analyze":   ("Usage: /analyze [SYMBOL]", ["/analyze AAPL", "/analyze TSLA"]),
        "/backtest":  ("Usage: /backtest [strategy] [symbol] [start] [end]", ["/backtest momentum SPY", "/backtest mean_reversion AAPL 2024-01-01 2025-01-01"]),
        "/wf":        ("Usage: /wf [symbol] [strategy] [method]", ["/wf SPY momentum rolling", "/wf QQQ breakout anchored"]),
        "/compare":   ("Usage: /compare [symbol] [start] [end]", ["/compare SPY", "/compare AAPL 2022-01-01 2025-01-01"]),
        "/watch":     ("Usage: /watch [add|remove|list] [SYMBOL]", ["/watch add AAPL", "/watch remove TSLA", "/watch list"]),
        "/crypto":    ("Usage: /crypto [SYMBOL...]", ["/crypto BTC", "/crypto ETH SOL"]),
        "/forex":     ("Usage: /forex [PAIR...]", ["/forex EUR/USD", "/forex GBP/USD JPY/USD"]),
        "/commodity": ("Usage: /commodity [NAME...]", ["/commodity gold", "/commodity oil silver"]),
        "/risk":      ("Usage: /risk [SYMBOL|portfolio]", ["/risk AAPL", "/risk portfolio"]),
        "/market":    ("Usage: /market [indices|sectors]", ["/market", "/market sectors"]),
        "/optimize":  ("Usage: /optimize [SYMBOL...]", ["/optimize AAPL MSFT GOOGL", "/optimize  (uses watchlist)"]),
        "/stress":    ("Usage: /stress [strategy] [symbol]", ["/stress momentum SPY"]),
        "/factors":   ("Usage: /factors [SYMBOL]", ["/factors AAPL"]),
        "/compliance":("Usage: /compliance [strategy]", ["/compliance momentum"]),
        "/web":       ("Usage: /web <query>", ["/web AAPL earnings Q4 2025", "/web Fed rate decision"]),
        "/services":  ("Usage: /services", ["/services"]),
        "/plan":      ("Usage: /plan <step1 ; step2 ; step3>", ["/plan git status ; rg TODO src ; pytest -q"]),
        "/apply-plan":("Usage: /apply-plan [--resume] [--from N]", ["/apply-plan", "/apply-plan --resume", "/apply-plan --from 2"]),
        "/plan-report":("Usage: /plan-report [md|json] [file] [--open]", ["/plan-report", "/plan-report md plan_report.md --open", "/plan-report json plan_report.json"]),
        "/git":       ("Usage: /git [status|diff|summary|patch|commit <msg>]", ["/git status", "/git patch apps/cli/aria_cli.py", '/git commit "feat: improve planner"']),
        "/gh":        ("Usage: /gh [prs|issues|pr N|issue N|search <q>|create-pr]", ["/gh prs", "/gh issues", "/gh pr 42", "/gh search 'async def'", "/gh create-pr"]),
        "/news":      ("Usage: /news [topic|symbol]", ["/news", "/news AAPL", "/news technology"]),
        "/config":    ("Usage: /config [show] | /config set key=value", ["/config", "/config set model=aria-sonata:4.5"]),
        "/context":   ("Usage: /context", ["/context"]),
        "/model":     ("Usage: /model [name|number|id]", ["/model", "/model qwen7b", "/model 2", "/model qwen2.5:7b"]),
        "/thinking":  ("Usage: /thinking [on|off|auto]", ["/thinking on", "/thinking off"]),
        "/login":     ("Usage: /login <email>  (password prompted securely)", ["/login user@example.com"]),
        "/whoami":    ("Usage: /whoami", ["/whoami"]),
        "/export":    ("Usage: /export [json|csv|md] [file]", ["/export md report.md", "/export json"]),
        "/save":      ("Usage: /save [name]", ["/save", '/save "AAPL Strategy Research"']),
        "/load":      ("Usage: /load <session_id>", ["/load abc123"]),
        "/sessions":  ("Usage: /sessions", ["/sessions"]),
        "/clear":     ("Usage: /clear", ["/clear"]),
        "/code":      ("Usage: /code <description> [--save file.py]", ["/code AAPL momentum backtest --save bt.py"]),
    }

    def cmd_help(self, args: str):
        # Contextual help: /help <command>
        target = args.strip().lower()
        if target:
            cmd_key = target if target.startswith("/") else f"/{target}"
            if cmd_key in self.commands:
                _, desc = self.commands[cmd_key]
                if HAS_RICH:
                    console.print()
                    console.print(f"  [bold #C08050]{cmd_key}[/bold #C08050]  [dim]{desc}[/dim]")
                    h = self._COMMAND_HELP.get(cmd_key)
                    if h:
                        console.print(f"  {h[0]}")
                        console.print()
                        console.print("  [dim]Examples:[/dim]")
                        for ex in h[1]:
                            console.print(f"    [bold]{ex}[/bold]")
                    console.print()
                else:
                    print(f"\n  {cmd_key}  {desc}")
                return
            # Check skills
            for s in SKILLS:
                if s["command"] == cmd_key:
                    if HAS_RICH:
                        console.print()
                        console.print(f"  [bold #C08050]{s['command']}[/bold #C08050]  [dim]{s['description']}[/dim]")
                        console.print(f"  [dim]Category:[/dim] {s['category']}")
                        console.print()
                    else:
                        print(f"\n  {s['command']}  {s['description']}")
                    return
            console.print(f"[dim]No help for: {target}. Try /help[/dim]" if HAS_RICH else f"No help for: {target}")
            return

        # Full help listing
        if HAS_RICH:
            console.print()
            console.print("[bold]Commands[/bold]  [dim](/help <command> for details)[/dim]")
            console.print()
            for name, (_, desc) in self.commands.items():
                console.print(f"  [bold #C08050]{name:18s}[/bold #C08050][dim]{desc}[/dim]")
            console.print()

            # --- Skills (grouped by category) ---
            categories = {}
            for s in SKILLS:
                cat = s["category"]
                if cat not in categories:
                    categories[cat] = []
                categories[cat].append(s)

            console.print("[bold]Skills[/bold]")
            console.print()
            for cat, skills in categories.items():
                console.print(f"  [dim]{cat}[/dim]")
                for s in skills:
                    console.print(f"    [bold #C08050]{s['command']:20s}[/bold #C08050][dim]{s['description']}[/dim]")
            console.print()

            # Keyboard shortcuts
            console.print("[bold]Keyboard Shortcuts[/bold]")
            console.print()
            shortcuts = [
                ("ESC",        "Cancel current generation"),
                ("Ctrl+D",     "Exit"),
                ("Ctrl+C",     "Cancel / exit"),
                ("↑  ↓",       "History navigation"),
                ("Tab",        "Command autocomplete"),
                ('"""',        "Enter multi-line input mode"),
            ]
            for key, desc in shortcuts:
                console.print(f"  [bold #C08050]{key:14s}[/bold #C08050][dim]{desc}[/dim]")
            console.print()

            # Footer
            console.print("[dim]Type any message to chat with Aria · /model to switch models[/dim]")
        else:
            print("\nCommands:")
            for name, (_, desc) in self.commands.items():
                print(f"  {name:18s} {desc}")
            print("\nSkills:")
            for s in SKILLS:
                print(f"  {s['command']:20s} {s['description']}")

    async def cmd_quote(self, args: str):
        symbols = args.upper().split() if args else self.terminal.config.get("watchlist", ["AAPL"])

        # 优先使用 MarketDataClient（真实实时数据，代理绕过）
        if _HAS_MDC:
            mdc = _get_mdc()
            if HAS_RICH:
                console.print()
            for symbol in symbols:
                if HAS_RICH:
                    with console.status(f"[dim]{symbol}...[/dim]", spinner="dots"):
                        loop = asyncio.get_event_loop()
                        r = await loop.run_in_executor(None, mdc.quote, symbol)
                else:
                    r = mdc.quote(symbol)

                if r.get("success"):
                    price   = r.get("price", "-")
                    chg     = r.get("change_pct", 0)
                    name    = r.get("name", symbol)
                    mktcap  = r.get("market_cap")
                    curr    = r.get("currency","")
                    cap_str = ""
                    if mktcap:
                        cap_str = (f"  Mkt Cap: ${mktcap/1e12:.2f}T" if mktcap >= 1e12
                                   else f"  Mkt Cap: ${mktcap/1e9:.1f}B" if mktcap >= 1e9
                                   else f"  Mkt Cap: ¥{mktcap/1e8:.0f}亿" if curr == "CNY"
                                   else "")
                    # Format high/low to 2 decimal places
                    _hi = r.get("high", "-")
                    _lo = r.get("low", "-")
                    _hi_str = f"{float(_hi):.2f}" if isinstance(_hi, (int, float)) and _hi else str(_hi)
                    _lo_str = f"{float(_lo):.2f}" if isinstance(_lo, (int, float)) and _lo else str(_lo)
                    if HAS_RICH:
                        color = "green" if chg >= 0 else "red"
                        sign  = "+" if chg >= 0 else ""
                        console.print(
                            f"  [bold]{symbol:<8}[/bold] [dim]{name[:20]:<22}[/dim]"
                            f"  [bold]{curr} {price}[/bold]"
                            f"  [{color}]{sign}{chg:.2f}%[/{color}]"
                            f"  [dim]Hi:{_hi_str}  Lo:{_lo_str}{cap_str}[/dim]"
                        )
                    else:
                        sign = "+" if chg >= 0 else ""
                        print(f"  {symbol:<8} {price:<10} {sign}{chg:.2f}%  {name}")
                else:
                    err = r.get("error", "failed")
                    if HAS_RICH:
                        console.print(f"  [red]{symbol}: {err}[/red]")
                    else:
                        print(f"  {symbol}: {err}")
            if HAS_RICH:
                console.print()
            return

        # Fallback：原有 Aria 工具
        for symbol in symbols:
            if HAS_RICH:
                with console.status(f"[dim]Fetching {symbol}...[/dim]", spinner="dots"):
                    result = await execute_aria_tool(self.terminal.api_url, "get_market_data", {
                        "symbol": symbol, "market": "US", "period": "1mo"
                    })
            else:
                print(f"Fetching {symbol}...")
                result = await execute_aria_tool(self.terminal.api_url, "get_market_data", {
                    "symbol": symbol, "market": "US", "period": "1mo"
                })
            if result.get("success") and result.get("data"):
                output = format_quote_output(result)
                console.print(output)
            else:
                _print_error(f"Failed: {result.get('error', 'No data')}")

    async def cmd_analyze(self, args: str):
        symbol = args.strip().upper() or "AAPL"
        console.print(f"[dim]Analyzing {symbol}...[/dim]" if HAS_RICH
                      else f"Analyzing {symbol}...")
        await self.terminal.send_message(f"Provide a comprehensive analysis of {symbol} stock including technicals, fundamentals, and risk assessment.")

    async def cmd_backtest(self, args: str):
        """Direct REST backtest → /api/v1/backtest (falls back to Aria tool)."""
        parts = args.split() if args else ["momentum", "SPY"]
        strategy = parts[0] if len(parts) > 0 else "momentum"
        symbol = parts[1].upper() if len(parts) > 1 else "SPY"
        start_date = parts[2] if len(parts) > 2 else "2023-01-01"
        today = __import__("datetime").date.today().isoformat()
        end_date = parts[3] if len(parts) > 3 else today

        label = f"Backtesting {strategy} on {symbol} ({start_date}→{end_date})"
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")

        async def _do_backtest():
            import aiohttp
            payload = {
                "symbols": [symbol],
                "strategy_type": strategy,
                "start_date": start_date,
                "end_date": end_date,
                "initial_capital": 100000,
                "commission_rate": 0.0003,
                "include_monte_carlo": False,
            }
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.post(f"{api_url}/api/v1/backtest", json=payload, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status == 200:
                            body = await resp.json()
                            return {"success": True, "data": body.get("data", body), "_source": "rest"}
            except Exception:
                pass
            # Fallback to Aria tool
            return await execute_aria_tool(api_url, "backtest_strategy", {
                "symbol": symbol, "strategy": strategy,
                "start_date": start_date, "end_date": end_date,
                "initial_capital": 100000,
            })

        if HAS_RICH:
            with console.status(f"[dim]{label}...[/dim]", spinner="dots"):
                result = await _do_backtest()
        else:
            print(label)
            result = await _do_backtest()

        if result.get("success"):
            d = result.get("data", result)
            src = result.get("_source", "aria")
            if HAS_RICH:
                from rich.table import Table
                tbl = Table(title=f"[bold]{symbol} · {strategy.upper()}[/bold]", show_header=True, header_style="bold")
                tbl.add_column("Metric", style="dim")
                tbl.add_column("Value", justify="right")
                tbl.add_column("vs B&H", justify="right", style="dim")
                bh = d.get("buy_hold_return", d.get("benchmark_return", 0))
                rows = [
                    ("Total Return", f"{d.get('total_return', 0)*100:.1f}%", f"{bh*100:.1f}%"),
                    ("Ann. Return",  f"{d.get('annualized_return', 0)*100:.1f}%", ""),
                    ("Sharpe Ratio", f"{d.get('sharpe_ratio', 0):.2f}", ""),
                    ("Max Drawdown", f"{d.get('max_drawdown', 0)*100:.1f}%", ""),
                    ("Win Rate",     f"{d.get('win_rate', 0)*100:.1f}%", ""),
                    ("# Trades",     str(d.get('num_trades', d.get('n_trades', 0))), ""),
                ]
                if d.get("calmar_ratio"):
                    rows.append(("Calmar Ratio", f"{d['calmar_ratio']:.2f}", ""))
                if d.get("sortino_ratio"):
                    rows.append(("Sortino Ratio", f"{d['sortino_ratio']:.2f}", ""))
                for r in rows:
                    tbl.add_row(*r)
                console.print(tbl)
                console.print(f"  [dim]source: {src} · {start_date} → {end_date}[/dim]")
            else:
                print(f"Total Return: {d.get('total_return',0)*100:.1f}%  Sharpe: {d.get('sharpe_ratio',0):.2f}  MaxDD: {d.get('max_drawdown',0)*100:.1f}%")

            eq = d.get("equity_curve", [])
            if eq:
                strat_vals = [p.get("strategy", p.get("portfolio_value", 0)) for p in eq if isinstance(p, dict)]
                if strat_vals:
                    spark = format_sparkline(strat_vals)
                    console.print(f"  [dim]Equity:[/dim] [green]{spark}[/green]" if HAS_RICH else f"  Equity: {spark}")
        else:
            _print_error(f"Backtest failed: {result.get('error', 'Unknown')}", "tool")

    async def cmd_walk_forward(self, args: str):
        """Walk-Forward 滚动回测 → /api/v1/backtest/walk-forward"""
        parts = args.split() if args else ["SPY"]
        symbol = parts[0].upper() if parts else "SPY"
        strategy = parts[1] if len(parts) > 1 else "momentum"
        method = parts[2] if len(parts) > 2 else "rolling"
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")

        label = f"Walk-Forward ({method}) · {strategy} · {symbol}"
        import aiohttp

        async def _do_wf():
            payload = {
                "symbol": symbol, "strategy_type": strategy, "method": method,
                "start_date": "2020-01-01",
                "end_date": __import__("datetime").date.today().isoformat(),
                "train_period_days": 252, "test_period_days": 63, "step_days": 21,
            }
            async with aiohttp.ClientSession() as sess:
                async with sess.post(f"{api_url}/api/v1/backtest/walk-forward", json=payload, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status}")
                    body = await resp.json()
                    return body.get("data", body)

        if HAS_RICH:
            with console.status(f"[dim]{label}...[/dim]", spinner="dots"):
                try:
                    data = await _do_wf()
                except Exception as e:
                    _print_error(str(e), "tool"); return
        else:
            print(label)
            try:
                data = await _do_wf()
            except Exception as e:
                _print_error(str(e), "tool"); return

        summary = data.get("summary", data)
        folds = data.get("folds", [])
        verdict = summary.get("verdict", "?")
        verdict_color = "green" if verdict == "PASS" else "red"

        if HAS_RICH:
            from rich.table import Table
            # Summary
            console.print(f"\n[bold]{symbol} · {strategy} · {method}[/bold]  Verdict: [bold {verdict_color}]{verdict}[/bold {verdict_color}]")
            console.print(f"  Folds: {summary.get('n_folds')}  "
                          f"Avg OOS Sharpe: [bold]{summary.get('avg_oos_sharpe', 0):.3f}[/bold]  "
                          f"Consistency: {summary.get('consistency_ratio_pct', 0):.0f}%  "
                          f"Robustness: {summary.get('robustness_score', 0):.3f}  "
                          f"p-value: {summary.get('p_value', 1):.4f}")
            # Fold table
            if folds:
                tbl = Table(title="Fold Results", show_header=True, header_style="bold dim")
                for col in ["Fold", "Test Period", "OOS Return", "OOS Sharpe", "OOS MaxDD", "Win%"]:
                    tbl.add_column(col, justify="right")
                for f in folds[:12]:
                    ret = f.get("test_return_pct", 0)
                    tbl.add_row(
                        str(f.get("fold_id", "")),
                        f.get("test_period", ""),
                        f"{'+'if ret>=0 else ''}{ret:.1f}%",
                        f"{f.get('test_sharpe', 0):.3f}",
                        f"{f.get('test_max_drawdown_pct', 0):.1f}%",
                        f"{f.get('test_win_rate_pct', 0):.0f}%",
                    )
                console.print(tbl)
        else:
            print(f"Verdict: {verdict}  Folds: {summary.get('n_folds')}  Avg OOS Sharpe: {summary.get('avg_oos_sharpe',0):.3f}")

    async def cmd_compare(self, args: str):
        """多策略横向对比 → /api/v1/backtest/compare-strategies"""
        parts = args.split() if args else ["SPY"]
        symbol = parts[0].upper() if parts else "SPY"
        start = parts[1] if len(parts) > 1 else "2020-01-01"
        end = parts[2] if len(parts) > 2 else __import__("datetime").date.today().isoformat()
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        import aiohttp

        async def _do():
            payload = {"symbol": symbol, "strategies": ["momentum","mean_reversion","breakout","turtle","ma_crossover"],
                       "start_date": start, "end_date": end, "initial_capital": 100000, "commission_rate": 0.0003}
            async with aiohttp.ClientSession() as sess:
                async with sess.post(f"{api_url}/api/v1/backtest/compare-strategies", json=payload, timeout=aiohttp.ClientTimeout(total=90)) as resp:
                    if resp.status != 200: raise RuntimeError(f"HTTP {resp.status}")
                    body = await resp.json()
                    return body.get("data", body)

        if HAS_RICH:
            with console.status(f"[dim]Comparing strategies on {symbol}...[/dim]", spinner="dots"):
                try: data = await _do()
                except Exception as e: _print_error(str(e), "tool"); return
        else:
            print(f"Comparing strategies on {symbol}...")
            try: data = await _do()
            except Exception as e: _print_error(str(e), "tool"); return

        strategies = data.get("strategies", [])
        bh = data.get("benchmark", {})
        if HAS_RICH:
            from rich.table import Table
            tbl = Table(title=f"[bold]{symbol} Strategy Comparison[/bold]  {start} → {end}", show_header=True, header_style="bold")
            for col in ["Rank", "Strategy", "Ann.Ret%", "Sharpe", "MaxDD%", "Calmar", "Sortino", "Win%", "Trades"]:
                tbl.add_column(col, justify="right")
            for s in strategies:
                tbl.add_row(
                    str(s.get("rank_by_sharpe", "")),
                    s["name"],
                    f"{s.get('annualized_return_pct',0):+.1f}%",
                    f"{s.get('sharpe_ratio',0):.3f}",
                    f"{s.get('max_drawdown_pct',0):.1f}%",
                    f"{s.get('calmar_ratio',0):.2f}",
                    f"{s.get('sortino_ratio',0):.2f}",
                    f"{s.get('win_rate_pct',0):.0f}%",
                    str(s.get("n_trades",0)),
                )
            tbl.add_row("—", "[dim]Buy & Hold[/dim]",
                f"{bh.get('annualized_return_pct',0):+.1f}%",
                f"{bh.get('sharpe_ratio',0):.3f}",
                f"{bh.get('max_drawdown_pct',0):.1f}%", "—","—","—","2")
            console.print(tbl)
        else:
            for s in strategies:
                print(f"{s['name']}: Ann={s.get('annualized_return_pct',0):+.1f}% Sharpe={s.get('sharpe_ratio',0):.2f} DD={s.get('max_drawdown_pct',0):.1f}%")

    def cmd_watch(self, args: str):
        parts = args.split() if args else ["list"]
        action = parts[0].lower() if parts else "list"
        watchlist = self.terminal.config.get("watchlist", [])

        if action == "add" and len(parts) > 1:
            symbol = parts[1].upper()
            if symbol not in watchlist:
                watchlist.append(symbol)
                self.terminal.config["watchlist"] = watchlist
                save_config(self.terminal.config)
                console.print(f"[green]Added {symbol} to watchlist[/green]" if HAS_RICH
                              else f"Added {symbol}")
            else:
                console.print(f"[dim]{symbol} already in watchlist[/dim]" if HAS_RICH
                              else f"{symbol} already in watchlist")

        elif action == "remove" and len(parts) > 1:
            symbol = parts[1].upper()
            if symbol in watchlist:
                watchlist.remove(symbol)
                self.terminal.config["watchlist"] = watchlist
                save_config(self.terminal.config)
                console.print(f"[dim]Removed {symbol} from watchlist[/dim]" if HAS_RICH
                              else f"Removed {symbol}")
            else:
                console.print(f"[red]{symbol} not in watchlist[/red]" if HAS_RICH
                              else f"{symbol} not in watchlist")

        else:  # list
            if HAS_RICH:
                if watchlist:
                    console.print(f"  [dim]Watchlist:[/dim] {', '.join(watchlist)}")
                else:
                    console.print("  [dim]Watchlist: Empty[/dim]")
            else:
                print(f"Watchlist: {', '.join(watchlist)}")

    async def cmd_portfolio(self, args: str):
        console.print("[dim]Assessing portfolio risk...[/dim]" if HAS_RICH else "Assessing portfolio risk...")
        watchlist = self.terminal.config.get("watchlist", ["AAPL", "MSFT", "GOOGL"])
        result = await execute_aria_tool(self.terminal.api_url, "assess_portfolio_risk", {
            "symbols": watchlist[:10],
        })
        if result.get("success") and result.get("data"):
            if HAS_RICH:
                console.print(f"\n  [bold]Portfolio Risk[/bold]\n")
                console.print(f"[dim]{json.dumps(result['data'], indent=2, ensure_ascii=False)[:1000]}[/dim]")
            else:
                print(json.dumps(result.get("data", {}), indent=2, ensure_ascii=False))
        else:
            console.print(f"[dim]No data: {result.get('error', '')}[/dim]" if HAS_RICH
                          else f"No data: {result.get('error', '')}")

    async def cmd_screen(self, args: str):
        criteria = args.strip() or "tech growth"
        await self.terminal.send_message(f"Screen stocks matching: {criteria}. Show top 10 with key metrics.")

    async def cmd_model(self, args: str):
        name = args.strip()

        # ── "provider/model" format (Open Interpreter style) ─────────────────
        # Examples: /model deepseek/deepseek-chat  /model ollama/qwen2.5:7b
        #           /model openai/gpt-4o           /model groq/llama-3.3-70b
        if "/" in name and not name.startswith("http"):
            _prov, _mod = name.split("/", 1)
            _prov = _prov.strip().lower()
            _mod  = _mod.strip()
            _local_backends = {"ollama", "lmstudio", "vllm", "llamacpp", "jan", "custom"}
            if _prov not in _local_backends:
                # Cloud provider — check API key
                _key = _get_provider_key(_prov)
                if not _key:
                    msg = (f"⚠ {_prov} API key 未配置。"
                           f"运行: /apikey set {_prov} <key>")
                    console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                    return
            self.terminal.config["local_provider"] = _prov
            self.terminal.config["model"] = _mod
            save_config(self.terminal.config)
            msg = f"✓ 已切换到 {_prov}/{_mod}"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
            return

        # Direct selection by number: /model 1 /model 2 … (Codex style)
        if name.isdigit():
            idx = int(name) - 1
            keys = list(MODELS.keys())
            if 0 <= idx < len(keys):
                self._set_model(keys[idx])
            else:
                console.print(f"[dim]No model #{name}[/dim]" if HAS_RICH else f"No model #{name}")
            return

        # Direct selection by key (case-insensitive): /model qwen7b
        if name.lower() in MODELS:
            self._set_model(name.lower())
            return

        # Direct selection by alias: /model st / s / p / coder
        if name.lower() in MODEL_ALIASES:
            self._set_model(MODEL_ALIASES[name.lower()])
            return

        # Direct selection by full Ollama model ID: /model qwen2.5-coder:1.5b
        if name and ":" in name:
            self._set_model_by_id(name)
            return

        # ── Interactive picker (Codex style: numbered list + descriptions) ────
        ollama_url  = self.terminal.config.get("ollama_url", "http://localhost:11434")
        current_id  = self.terminal.config.get("model", "qwen2.5:7b")

        rich_models, ollama_err = detect_ollama_models_rich(ollama_url)
        installed_names = {m["name"] for m in rich_models}
        aria_ids        = {m["id"] for m in MODELS.values()}

        # ── Print header (Codex style) ─────────────────────────────────────
        if HAS_RICH:
            console.print()
            console.print("  [bold]Select Model[/bold]")
            if ollama_err:
                console.print(f"  [yellow dim]Ollama: {ollama_err[:60]}[/yellow dim]")
            else:
                n_installed = sum(1 for m in MODELS.values() if m["id"] in installed_names)
                console.print(
                    f"  [dim]Use /model <id> or number · "
                    f"{n_installed}/{len(MODELS)} Aria models installed[/dim]"
                )
            console.print()

        def _status_tag(mid: str, badge: str) -> str:
            """Return short status: ● installed / ○ not installed / ☁ cloud"""
            if badge == "Cloud":
                return "☁"
            return "●" if mid in installed_names else "○"

        # Get terminal width for safe label truncation
        try:
            _term_cols = os.get_terminal_size().columns
        except Exception:
            _term_cols = 80

        def _cjk_width(s: str) -> int:
            """Display-column width (CJK = 2 cols each)."""
            w = 0
            for ch in s:
                cp = ord(ch)
                w += 2 if (0x2E80 <= cp <= 0xA4CF or 0xAC00 <= cp <= 0xD7AF or
                           0xFF01 <= cp <= 0xFF60 or 0x3000 <= cp <= 0x303F) else 1
            return w

        def _cjk_truncate(s: str, max_cols: int) -> str:
            """Truncate s so its display width ≤ max_cols, adding … if cut."""
            w, out = 0, ""
            for ch in s:
                cw = 2 if (0x2E80 <= ord(ch) <= 0xA4CF or
                           0xAC00 <= ord(ch) <= 0xD7AF or
                           0xFF01 <= ord(ch) <= 0xFF60 or
                           0x3000 <= ord(ch) <= 0x303F) else 1
                if w + cw > max_cols:
                    return out + "…"
                out += ch
                w += cw
            return out

        def _short_desc(m: dict) -> str:
            """Single-line description — CJK-aware width limit prevents wrapping."""
            desc  = m.get("description", "")
            badge = m.get("badge", "")
            extras = []
            if _HAS_MODEL_CAP:
                cap = get_model_capability(m["id"])
                ctx = f"ctx={cap.context_window//1024}K"
                extras.append(ctx)
                if cap.tool_calls:   extras.append("tools✓")
                if cap.thinking:     extras.append("think")
            else:
                extras.append(f"ctx={m.get('num_ctx', 8192)//1024}K")
            if badge in ("Fast", "Code", "Think", "Cloud"):
                extras.insert(0, badge)
            meta = "  " + " · ".join(extras) if extras else ""
            # Reserve space for prefix ("  N. ☁ ModelName  ") ≈ 22 cols
            # + meta ("  Cloud · ctx=128K · tools✓ · think") ≈ 38 cols
            # Remaining budget for description text
            _prefix_budget = 22
            _meta_budget   = len(meta)
            _desc_budget   = max(10, _term_cols - _prefix_budget - _meta_budget - 4)
            desc = _cjk_truncate(desc, _desc_budget)
            return desc + meta

        # Build option list (Codex: numbered, no separators within Aria section)
        options: list = []   # (label_str, desc_str)  for _arrow_select
        all_ids: list = []

        # ── Print numbered list only in non-interactive (-p) mode ────────────
        # In interactive TTY mode the arrow picker below already shows all items.
        # Printing twice causes the visual duplication seen in the session log.
        _is_tty = sys.stdin.isatty()
        idx_counter = 1
        if not _is_tty:
            # Non-interactive (-p mode): show static numbered list then return.
            # The arrow picker cannot run without a TTY.
            community_list = [cm for cm in rich_models if cm["name"] not in aria_ids]
            for key, m in MODELS.items():
                mid    = m["id"]
                is_cur = mid == current_id
                status = _status_tag(mid, m.get("badge", ""))
                cur_tag = "  (current)" if is_cur else ""
                desc = _short_desc(m)
                line = f"  {idx_counter}. {status} {m['name']:<14s}  {desc}{cur_tag}"
                console.print(line) if HAS_RICH else print(line)
                idx_counter += 1
            if community_list:
                console.print() if HAS_RICH else print()
                lbl = "  Community (Ollama)"
                console.print(f"[dim]{lbl}[/dim]") if HAS_RICH else print(lbl)
                for cm in community_list:
                    mid    = cm["name"]
                    is_cur = mid == current_id
                    cur_tag = "  (current)" if is_cur else ""
                    line = f"  {idx_counter}. ● {mid}{cur_tag}"
                    console.print(line) if HAS_RICH else print(line)
                    idx_counter += 1
            console.print() if HAS_RICH else print()
            console.print("  [dim]Use /model <id> to switch. E.g. /model deepseek/deepseek-chat[/dim]") if HAS_RICH else print("  Use /model <id> to switch.")
            return

        # ── Build compact options for _arrow_select ────────────────────────
        # In TTY mode: include short description (static list is suppressed above).
        # In non-TTY: descriptions already shown in static list, keep labels short.
        num = 1
        for key, m in MODELS.items():
            mid    = m["id"]
            status = _status_tag(mid, m.get("badge", ""))
            is_cur = " ◀" if mid == current_id else ""
            if _is_tty:
                desc_part = f"  {_short_desc(m)}"
            else:
                desc_part = ""
            label  = f"  {num}. {status} {m['name']}{is_cur}{desc_part}"
            options.append((label, ""))
            all_ids.append(mid)
            num += 1

        community = [cm for cm in rich_models if cm["name"] not in aria_ids]
        if community:
            options.append(("  ── Community ─────────────────", ""))
            all_ids.append(None)
            for cm in community:
                mid    = cm["name"]
                is_cur = " ◀" if mid == current_id else ""
                options.append((f"  {num}. ● {mid}{is_cur}", ""))
                all_ids.append(mid)
                num += 1

        if ollama_err and not rich_models:
            options.append(("  ── Ollama unreachable ─────────", ""))
            all_ids.append(None)

        # ── Run thread-based arrow picker (short labels = no line wrap) ────
        current_idx = next((i for i, mid in enumerate(all_ids) if mid == current_id), 0)

        while True:
            choice = await _run_picker_in_thread(
                options, current_idx,
                "",                          # _arrow_select already prints ↑↓/j·k hint
                max_visible=len(options),    # show all models at once
            )
            if choice < 0:
                console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
                return
            if all_ids[choice] is None:
                current_idx = min(choice + 1, len(options) - 1)
                continue
            break

        self._set_model_by_id(all_ids[choice])

    def _set_model(self, key: str):
        """Set model by MODELS key."""
        m = MODELS[key]
        self._set_model_by_id(m["id"])

    def _set_model_by_id(self, model_id: str):
        """Set model by Ollama model ID (works for both built-in and community models)."""
        self.terminal.config["model"] = model_id
        self.terminal._actual_model = None  # reset: new config model, no known fallback yet
        save_config(self.terminal.config)
        # Pretty label
        for m in MODELS.values():
            if m["id"] == model_id:
                if HAS_RICH:
                    console.print(f"[bold]Model:[/bold] [bold]{m['name']} {m['version']}[/bold] "
                                  f"[dim]{m['tag']}[/dim]")
                else:
                    print(f"Model: {m['name']} {m['version']} ({m['tag']})")
                return
        # Community / unknown model
        if HAS_RICH:
            console.print(f"[bold]Model:[/bold] [bold]{model_id}[/bold]  [dim](local)[/dim]")
        else:
            print(f"Model: {model_id} (local)")

    def cmd_thinking(self, args: str):
        mode = args.strip().lower()

        # Direct set: /thinking on
        if mode in ("on", "thinking"):
            self.terminal.config["thinking_mode"] = "thinking"
        elif mode in ("off", "instant"):
            self.terminal.config["thinking_mode"] = "instant"
        elif mode == "auto":
            self.terminal.config["thinking_mode"] = "auto"
        elif mode:
            # Unknown mode, show picker
            pass
        else:
            # Interactive picker
            current = self.terminal.config.get("thinking_mode", "auto")
            mode_keys = list(THINKING_MODES.keys())
            current_idx = mode_keys.index(current) if current in mode_keys else 0
            options = [(info["label"], info["description"]) for info in THINKING_MODES.values()]
            choice = _arrow_select(options, selected=current_idx, title="Thinking Mode")
            if 0 <= choice < len(mode_keys):
                self.terminal.config["thinking_mode"] = mode_keys[choice]
            else:
                if HAS_RICH:
                    console.print("[dim]No change[/dim]")
                else:
                    print("No change")
                return

        save_config(self.terminal.config)
        result = self.terminal.config["thinking_mode"]
        info = THINKING_MODES.get(result, {})
        if HAS_RICH:
            console.print(f"[green]Thinking: {info.get('label', result)}[/green]  [dim]{info.get('description', '')}[/dim]")
        else:
            print(f"Thinking: {result}")

    def cmd_skills(self, args: str):
        """List all available skills grouped by category."""
        categories = {}
        for s in SKILLS:
            cat = s["category"]
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(s)

        cat_labels = {
            "research": "Research",
            "analysis": "Analysis",
            "strategy": "Strategy",
            "risk": "Risk Management",
            "quant": "Quantitative",
            "crypto": "Crypto",
            "tools": "Tools",
            "code": "Code Generation",
        }

        if HAS_RICH:
            console.print()
            for cat, skills in categories.items():
                label = cat_labels.get(cat, cat.title())
                console.print(f"  [bold]{label}[/bold]")
                for s in skills:
                    args_hint = f"  [dim]{s.get('args', '')}[/dim]" if s.get("args") else ""
                    console.print(f"    [bold]{s['command']:20s}[/bold][dim]{s['description']}[/dim]{args_hint}")
                console.print()

            console.print("[dim]  Type a skill command to execute, e.g. /deep-analysis AAPL[/dim]\n")
        else:
            print("\nSkills:")
            for cat, skills in categories.items():
                label = cat_labels.get(cat, cat.title())
                print(f"\n  [{label}]")
                for s in skills:
                    print(f"    {s['command']:20s} {s['description']}")

    async def _execute_skill(self, skill: dict, args: str):
        """Execute a skill by expanding its prompt template and sending to AI."""
        parts = args.strip().upper().split() if args.strip() else []
        cmd = skill["command"]

        # Build the prompt from template
        template = skill["prompt"]

        if cmd == "/deep-analysis":
            symbol = parts[0] if parts else "AAPL"
            prompt = template.format(symbol=symbol)

        elif cmd == "/trade-idea":
            context = f" in {' '.join(parts)}" if parts else " in the US market"
            prompt = template.format(context=context)

        elif cmd == "/risk-report":
            if parts:
                symbols = ", ".join(parts)
            else:
                symbols = ", ".join(self.terminal.config.get("watchlist", ["AAPL", "MSFT", "GOOGL"]))
            prompt = template.format(symbols=symbols)

        elif cmd == "/factor-screen":
            factor = " ".join(parts).lower() if parts else "momentum"
            prompt = template.format(factor=factor)

        elif cmd == "/backtest-report":
            strategy = parts[0].lower() if len(parts) > 0 else "momentum"
            symbol = parts[1] if len(parts) > 1 else "SPY"
            start = parts[2] if len(parts) > 2 else "2023-01-01"
            end = parts[3] if len(parts) > 3 else "2025-01-01"
            prompt = template.format(strategy=strategy, symbol=symbol, start=start, end=end)

        elif cmd == "/morning-brief":
            extra = f"\nFocus on: {' '.join(parts)}" if parts else ""
            prompt = template.format(extra=extra)

        elif cmd == "/macro-outlook":
            context = f" for {' '.join(parts)}" if parts else " for the US and global economy"
            prompt = template.format(context=context)

        elif cmd == "/crypto-scan":
            extra = f"\nFocus on: {' '.join(parts)}" if parts else ""
            prompt = template.format(extra=extra)

        elif cmd == "/watchlist-scan":
            symbols = ", ".join(self.terminal.config.get("watchlist", ["AAPL", "MSFT", "GOOGL"]))
            prompt = template.format(symbols=symbols)

        elif cmd == "/sector-rotation":
            prompt = template

        elif cmd == "/gen-strategy":
            strategy = parts[0].lower() if len(parts) > 0 else "momentum"
            symbol = parts[1] if len(parts) > 1 else "SPY"
            prompt = template.format(strategy=strategy, symbol=symbol)

        elif cmd == "/gen-analysis":
            topic = " ".join(parts[:2]).lower() if parts else "technical analysis"
            symbols = ", ".join(parts[2:]) if len(parts) > 2 else "SPY"
            prompt = template.format(topic=topic, symbols=symbols)

        elif cmd == "/gen-bot":
            exchange = parts[0].lower() if len(parts) > 0 else "binance"
            strategy = " ".join(parts[1:]).lower() if len(parts) > 1 else "grid trading"
            prompt = template.format(exchange=exchange, strategy=strategy)

        else:
            prompt = template

        # Show skill activation
        if HAS_RICH:
            tools = ", ".join(skill.get("tools_hint", [])[:3])
            console.print(f"[bold]Skill:[/bold] [bold]{skill['name']}[/bold]  [dim]tools: {tools}[/dim]")
        else:
            print(f"Skill: {skill['name']}")

        await self.terminal.send_message(prompt)

    def cmd_tools(self, args: str):
        if HAS_RICH:
            console.print()
            console.print("  [bold]Local Tools[/bold] [dim](Code Agent)[/dim]")
            for i, (name, (_, desc)) in enumerate(LOCAL_TOOLS.items(), 1):
                console.print(f"    [bold]{name:28s}[/bold][dim]{desc}[/dim]")
            console.print()

            console.print(f"  [bold]Remote Tools[/bold] [dim]({len(ARIA_TOOLS)})[/dim]")
            for i, (name, desc) in enumerate(ARIA_TOOLS, 1):
                console.print(f"    [bold]{name:28s}[/bold][dim]{desc}[/dim]")
            console.print()
        else:
            print("\nLocal Tools (Code Agent):")
            for i, (name, (_, desc)) in enumerate(LOCAL_TOOLS.items(), 1):
                print(f"  {i:2d}. {name:30s} {desc}")
            print("\nRemote Aria Tools (22):")
            for i, (name, desc) in enumerate(ARIA_TOOLS, 1):
                print(f"  {i:2d}. {name:30s} {desc}")

    def cmd_services(self, args: str):
        """Show CLI service tiers and core workflows."""
        service_groups = [
            (
                "CORE (Standard)",
                [
                    "Code agent with local tools (read/write/edit/search/run)",
                    "Slash command workflows for quote/analyze/backtest/risk/screen",
                    "Session save/load/export and interactive history management",
                    "Model switching + thinking mode controls for response depth",
                ],
            ),
            (
                "QUANTUM Automation",
                [
                    "Agentic multi-step loop (auto read -> analyze -> edit -> execute)",
                    "Auto-recovery guidance for failed commands and code fixes",
                    "Strategy generation, backtest reporting, and risk analysis skills",
                    "Cross-workspace research sync hooks (session + export pipeline)",
                ],
            ),
            (
                "ENTERPRISE Controls (included in Quantum)",
                [
                    "Service health diagnostics (/health) for backend + local model stack",
                    "Governed command execution with dangerous-command blocking",
                    "Audit-friendly session logs and reproducible command trails",
                    "MCP-ready service integration path via external tool endpoints",
                ],
            ),
        ]

        quick_flow = [
            "/model",
            "/gen-strategy momentum AAPL",
            "/backtest momentum AAPL 2024-01-01 2025-01-01",
            "/risk AAPL",
            "/export md strategy_report.md",
        ]

        if HAS_RICH:
            console.print()
            console.print("[bold]CLI Services[/bold] [dim](tiers + workflow)[/dim]")
            console.print()
            for group_name, items in service_groups:
                console.print(f"  [bold #C08050]{group_name}[/bold #C08050]")
                for item in items:
                    console.print(f"    [dim]> {item}[/dim]")
                console.print()

            console.print("  [bold]Quick Start Flow[/bold]")
            for cmd in quick_flow:
                console.print(f"    [bold]{cmd}[/bold]")
            console.print()
        else:
            print("\nCLI Services (tiers + workflow)\n")
            for group_name, items in service_groups:
                print(f"  {group_name}")
                for item in items:
                    print(f"    > {item}")
                print()

            print("  Quick Start Flow")
            for cmd in quick_flow:
                print(f"    {cmd}")
            print()

    def cmd_plan(self, args: str):
        """Create an executable plan and store it for /apply-plan.

        Supports multiple input styles:
            /plan 1. Fetch quote  2. Generate chart  3. Output report
            /plan fetch quote -> generate chart -> output report
            /plan step one; step two; step three
        """
        raw = args.strip()
        if not raw:
            if HAS_RICH:
                console.print("[dim]Usage: /plan <steps>  — see examples below[/dim]")
                console.print("[dim]  /plan fetch AAPL quote -> generate chart -> write report[/dim]")
                console.print("[dim]  /plan 1. Analyze sentiment  2. Build model  3. Backtest[/dim]")
            else:
                print("Usage: /plan <steps>")
                print("  /plan fetch AAPL quote -> generate chart -> write report")
                print("  /plan 1. Analyze sentiment  2. Build model  3. Backtest")
            return

        from plan_utils import parse_plan, format_plan
        plan_steps = parse_plan(raw)
        if not plan_steps:
            console.print("[dim]No valid steps found[/dim]" if HAS_RICH else "No valid steps found")
            return

        # Store plain descriptions for /apply-plan (backwards compatible)
        self.terminal.pending_plan = [s.description for s in plan_steps]

        if HAS_RICH:
            console.print()
            console.print(f"[bold]Execution Plan[/bold]  [dim]({len(plan_steps)} steps)[/dim]")
            console.print()
            for s in plan_steps:
                dep_str = f"  [dim](after {', '.join(str(d) for d in s.deps)})[/dim]" if s.deps else ""
                label   = f" [dim][{s.name}][/dim]" if s.name else ""
                console.print(f"  [dim]{s.index}.[/dim]{label} [bold]{s.description}[/bold]{dep_str}")
            console.print()
            console.print("[dim]Run /apply-plan to execute these steps.[/dim]")
            console.print()
        else:
            print(f"\nExecution Plan ({len(plan_steps)} steps)")
            for s in plan_steps:
                dep_str = f"  (after {', '.join(str(d) for d in s.deps)})" if s.deps else ""
                label   = f" [{s.name}]" if s.name else ""
                print(f"  {s.index}.{label} {s.description}{dep_str}")
            print("Run /apply-plan to execute these steps.\n")

    def cmd_apply_plan(self, args: str):
        """Execute the pending command plan sequentially."""
        plan = list(getattr(self.terminal, "pending_plan", []) or [])
        arg_tokens = args.split()
        start_idx = 0
        if "--from" in arg_tokens:
            idx = arg_tokens.index("--from")
            if idx + 1 >= len(arg_tokens):
                msg = "Usage: /apply-plan --from <step_number>"
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return
            try:
                start_idx = max(0, int(arg_tokens[idx + 1]) - 1)
            except ValueError:
                msg = "Invalid step number for --from"
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return

        if not plan:
            console.print("[dim]No pending plan. Use /plan first.[/dim]" if HAS_RICH
                          else "No pending plan. Use /plan first.")
            return
        if start_idx > 0:
            if start_idx >= len(plan):
                msg = f"--from {start_idx + 1} exceeds available steps ({len(plan)})"
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return
            plan = plan[start_idx:]
        if "--resume" in arg_tokens and HAS_RICH:
            console.print(f"[dim]Resuming execution from step 1 of remaining {len(plan)} step(s).[/dim]")

        policy = self.terminal.config.get("command_policy", "safe")
        results = []
        failed = None
        for i, step in enumerate(plan, 1):
            started_at = time.time()
            if HAS_RICH:
                console.print(f"[dim]Step {i}/{len(plan)}:[/dim] [bold]{step}[/bold]")
            else:
                print(f"Step {i}/{len(plan)}: {step}")

            step_decision = evaluate_command_policy(step, policy)
            if step_decision.risk == "high":
                if not self._confirm_high_risk_command(step_decision.normalized_command, step_decision.risk, policy):
                    failed = (i, step, "Cancelled by user at high-risk step confirmation")
                    results.append({
                        "step": step,
                        "status": "blocked",
                        "duration": round(time.time() - started_at, 3),
                        "exit_code": None,
                        "error": failed[2],
                    })
                    break

            res = _tool_run_command({"command": step, "policy": policy})
            duration = time.time() - started_at
            exit_code = res.get("data", {}).get("exit_code", None) if res.get("success") else None
            status = "completed" if res.get("success") and exit_code == 0 else "failed"
            results.append({
                "step": step,
                "status": status,
                "duration": round(duration, 3),
                "exit_code": exit_code,
                "error": None if status == "completed" else (res.get("error") or f"Command exited {exit_code}"),
            })
            if not res.get("success"):
                failed = (i, step, res.get("error", "Unknown error"))
                break
            exit_code = res.get("data", {}).get("exit_code", 0)
            if exit_code != 0:
                failed = (i, step, f"Command exited {exit_code}")
                break

        self.terminal.last_plan_results = results

        if failed:
            idx, step, err = failed
            self.terminal.pending_plan = plan[idx - 1:]
            if HAS_RICH:
                console.print(f"[red]Plan failed at step {idx}[/red]: [bold]{step}[/bold]")
                console.print(f"[red]{err}[/red]")
                console.print("[dim]Recovery hints:[/dim]")
                if "blocked by policy" in (err or "").lower():
                    console.print("  [dim]> /run --dry-run <command> to inspect risk[/dim]")
                    console.print("  [dim]> /config set command_policy=balanced (or full) if needed[/dim]")
                else:
                    console.print("  [dim]> Fix code/config, then rerun /apply-plan[/dim]")
                    console.print("  [dim]> Use /git diff to inspect changes[/dim]")
            else:
                print(f"Plan failed at step {idx}: {step}\n{err}")
                if "blocked by policy" in (err or "").lower():
                    print("Recovery: /run --dry-run <command> and /config set command_policy=balanced")
                else:
                    print("Recovery: fix issue, then rerun /apply-plan")
        else:
            if HAS_RICH:
                console.print(f"[green]Plan completed ({len(plan)} steps)[/green]")
                for i, row in enumerate(results, 1):
                    console.print(f"  [dim]{i}. {row['step']} ({row['duration']}s)[/dim]")
            else:
                print(f"Plan completed ({len(plan)} steps)")
            self.terminal.pending_plan = []

    def cmd_plan_report(self, args: str):
        """Show or export last plan execution report."""
        rows = list(getattr(self.terminal, "last_plan_results", []) or [])
        if not rows:
            msg = "No plan report available. Run /apply-plan first."
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return

        parts = args.split()
        open_after = "--open" in parts
        parts = [p for p in parts if p != "--open"]
        fmt = parts[0].lower() if parts else "show"
        out_file = parts[1] if len(parts) > 1 else None

        if fmt == "show":
            if HAS_RICH:
                console.print()
                console.print("[bold]Last Plan Report[/bold]")
                for idx, row in enumerate(rows, 1):
                    status_color = "green" if row["status"] == "completed" else ("yellow" if row["status"] == "blocked" else "red")
                    console.print(
                        f"  [dim]{idx}.[/dim] [{status_color}]{row['status']}[/{status_color}] "
                        f"[bold]{row['step']}[/bold] [dim]({row['duration']}s, exit={row.get('exit_code')})[/dim]"
                    )
                    if row.get("error"):
                        console.print(f"     [red]{row['error']}[/red]")
                console.print()
            else:
                print("\nLast Plan Report")
                for idx, row in enumerate(rows, 1):
                    print(f"  {idx}. {row['status']}  {row['step']} ({row['duration']}s, exit={row.get('exit_code')})")
                    if row.get("error"):
                        print(f"     ERROR: {row['error']}")
            return

        if fmt not in {"md", "json"}:
            msg = "Usage: /plan-report [md|json] [file] [--open]"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return

        if not out_file:
            out_file = f"plan_report.{fmt}"

        try:
            if fmt == "json":
                content = json.dumps(rows, ensure_ascii=False, indent=2)
            else:
                md_lines = ["# Plan Execution Report", ""]
                for idx, row in enumerate(rows, 1):
                    md_lines.append(
                        f"{idx}. **{row['status']}** `{row['step']}` "
                        f"({row['duration']}s, exit={row.get('exit_code')})"
                    )
                    if row.get("error"):
                        md_lines.append(f"   - Error: {row['error']}")
                md_lines.append("")
                content = "\n".join(md_lines)

            result = _tool_write_file({"path": out_file, "content": content})
            if result.get("success"):
                saved_path = result['data']['path']
                msg = f"Plan report saved to {saved_path}"
                console.print(f"[green]{msg}[/green]" if HAS_RICH else msg)
                if open_after:
                    self._open_file(saved_path)
            else:
                err = result.get("error", "Failed to save report")
                console.print(f"[red]{err}[/red]" if HAS_RICH else err)
        except Exception as e:
            console.print(f"[red]{e}[/red]" if HAS_RICH else str(e))

    def cmd_git(self, args: str):
        """Git helper shortcuts."""
        policy = self.terminal.config.get("command_policy", "safe")
        raw = args.strip()
        if not raw:
            sub = "status"
            sub_args = ""
        else:
            parts = raw.split(maxsplit=1)
            sub = parts[0].lower()
            sub_args = parts[1].strip() if len(parts) > 1 else ""

        mapping = {
            "status":  "git status --short --branch",
            "diff":    "git diff --stat",
            "summary": "git status --short --branch && git diff --stat",
            "branch":  "git branch -v",
            "stash":   "git stash list",
            "remote":  "git remote -v",
        }
        if sub == "patch":
            cmd = "git diff" if not sub_args else f"git diff -- {sub_args}"
        elif sub == "log":
            limit = sub_args if sub_args and sub_args.isdigit() else "15"
            cmd = f"git log --oneline --graph --decorate -{limit}"
        elif sub == "commit":
            status_probe = _tool_run_command({"command": "git status --porcelain", "policy": policy})
            if not status_probe.get("success"):
                err = status_probe.get("error", "Failed to inspect git status")
                console.print(f"[red]{err}[/red]" if HAS_RICH else err)
                return

            status_out = status_probe.get("data", {}).get("stdout", "").strip()
            if not status_out:
                msg = "No changes to commit."
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return

            changed_files = []
            for line in status_out.splitlines():
                if len(line) >= 4:
                    changed_files.append(line[3:].strip())
            unique_files = [f for f in changed_files if f]
            total_files = len(unique_files)
            file_preview = ", ".join(unique_files[:5]) if unique_files else "workspace"
            body_summary = f"Files changed: {total_files}"
            body_preview = f"Top files: {file_preview}"

            if not sub_args:
                files = []
                for line in status_out.splitlines()[:3]:
                    if len(line) >= 4:
                        files.append(line[3:].strip())
                sample = ", ".join(files) if files else "workspace"
                total = len(status_out.splitlines())
                sub_args = f"chore: update {total} file(s) ({sample})"
                if HAS_RICH:
                    console.print(f"[dim]Auto commit message:[/dim] {sub_args}")
                else:
                    print(f"Auto commit message: {sub_args}")

            cmd = (
                f"git add -A && git commit "
                f"-m {shlex.quote(sub_args)} "
                f"-m {shlex.quote(body_summary)} "
                f"-m {shlex.quote(body_preview)}"
            )
        elif sub in mapping:
            cmd = mapping[sub]
        else:
            msg = "Usage: /git [status|diff|summary|patch|log [N]|branch|stash|remote|commit <msg>]"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return
        result = _tool_run_command({"command": cmd, "policy": policy})
        if not result.get("success"):
            console.print(f"[red]{result.get('error', 'Command failed')}[/red]" if HAS_RICH
                          else result.get("error", "Command failed"))
            return
        data = result.get("data", {})
        out = (data.get("stdout", "") + ("\n" + data.get("stderr", "") if data.get("stderr") else "")).strip()
        if out:
            if HAS_RICH:
                console.print(Syntax(out, "text", theme=_SYNTAX_THEME))
            else:
                print(out)

    def cmd_gh(self, args: str):
        """GitHub CLI helper — prs | issues | pr N | issue N | search | create-pr | diff N | checks N"""
        raw = args.strip()
        if not raw or raw in ("help", "--help"):
            lines = [
                "Usage: /gh <command>",
                "  prs            List open pull requests",
                "  issues         List open issues",
                "  pr <N>         View pull request #N",
                "  issue <N>      View issue #N",
                "  diff <N>       Show PR #N diff",
                "  checks <N>     Show PR #N CI checks",
                "  search <q>     Search code in this repo",
                "  create-pr      Create a PR (follow prompts)",
                "  commits [N]    Show last N commits (default 10)",
            ]
            for ln in lines:
                console.print(f"  [dim]{ln}[/dim]" if HAS_RICH else ln)
            return

        parts  = raw.split(maxsplit=1)
        sub    = parts[0].lower()
        subarg = parts[1].strip() if len(parts) > 1 else ""

        def _run(action: str, extra: dict = None):
            p = {"action": action}
            if extra:
                p.update(extra)
            r = _tool_github(p)
            if not r.get("success"):
                msg = r.get("error", "GitHub command failed")
                console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                return
            data = r.get("data", {})
            out = data.get("stdout", "") if isinstance(data, dict) else str(data)
            if out.strip():
                if HAS_RICH:
                    # Pretty-print JSON if possible
                    try:
                        import json as _jj
                        parsed = _jj.loads(out)
                        from rich.pretty import pprint as _pp
                        _pp(parsed, expand_all=False)
                    except Exception:
                        console.print(Syntax(out, "text", theme=_SYNTAX_THEME))
                else:
                    print(out)

        if sub in ("prs", "pr_list"):
            _run("list_prs")
        elif sub in ("issues", "issue_list"):
            _run("list_issues")
        elif sub == "pr" and subarg.isdigit():
            _run("view_pr", {"number": int(subarg)})
        elif sub == "issue" and subarg.isdigit():
            _run("view_issue", {"number": int(subarg)})
        elif sub == "diff" and subarg.isdigit():
            _run("pr_diff", {"number": int(subarg)})
        elif sub == "checks" and subarg.isdigit():
            _run("pr_checks", {"number": int(subarg)})
        elif sub in ("commits", "log"):
            n = int(subarg) if subarg.isdigit() else 10
            _run("list_commits", {"limit": n})
        elif sub == "search":
            if not subarg:
                console.print("[dim]Usage: /gh search <query>[/dim]" if HAS_RICH else "Usage: /gh search <query>")
                return
            _run("search", {"q": subarg, "kind": "code"})
        elif sub in ("create-pr", "createpr", "create_pr"):
            # Interactive prompts
            try:
                title = (console.input("  PR title: ") if HAS_RICH else input("  PR title: ")).strip()
                body  = (console.input("  PR body (optional): ") if HAS_RICH else input("  PR body (optional): ")).strip()
                base  = (console.input("  Base branch [main]: ") if HAS_RICH else input("  Base branch [main]: ")).strip() or "main"
                _run("create_pr", {"title": title, "body": body, "base": base})
            except (EOFError, KeyboardInterrupt):
                console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
        else:
            console.print(f"[dim]Unknown /gh sub-command: {sub}. Try /gh help[/dim]" if HAS_RICH
                          else f"Unknown /gh sub-command: {sub}. Try /gh help")

    def _confirm_high_risk_command(self, command: str, risk: str, policy: str) -> bool:
        """Double-confirm high-risk commands even if policy allows them."""
        msg = f"High-risk command under policy '{policy}' (risk={risk}): {command}\nRun it? [y/N]: "
        try:
            answer = console.input(msg) if HAS_RICH else input(msg)
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in {"y", "yes"}

    def _open_file(self, path: str):
        """Open a local file using platform default app."""
        path_q = shlex.quote(path)
        if sys.platform == "darwin":
            os.system(f"open {path_q}")
        elif os.name == "nt":
            os.system(f'start "" {path_q}')
        else:
            os.system(f"xdg-open {path_q} >/dev/null 2>&1")

    async def cmd_health(self, args: str):
        import aiohttp
        if HAS_RICH:
            console.print()
        urls = [
            ("AWS Backend", self.terminal.api_url, "/health"),
            ("Local Server", self.terminal.config.get("local_url", "http://localhost:8001"), "/health"),
            ("Ollama", self.terminal.config.get("ollama_url", "http://localhost:11434"), "/api/tags"),
        ]
        for label, url, path in urls:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{url}{path}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        data = await resp.json()
                        if label == "Ollama":
                            models = [m.get("name", "?") for m in data.get("models", [])[:3]]
                            detail = ", ".join(models)
                        else:
                            detail = f"v{data.get('version', '?')}"
                        if HAS_RICH:
                            console.print(f"  [green]●[/green] [dim]{label}[/dim]  {detail}")
                        else:
                            print(f"  + {label}  {detail}")
            except Exception:
                if HAS_RICH:
                    console.print(f"  [red]●[/red] [dim]{label}[/dim]  offline")
                else:
                    print(f"  - {label}  offline")
        if HAS_RICH:
            console.print()

    def cmd_clear(self, args: str):
        self.terminal.conversation = []
        os.system("clear" if os.name == "posix" else "cls")
        console.print("[dim]Conversation cleared[/dim]" if HAS_RICH else "Cleared")

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
        """Smart compact: summarise conversation with AI then trim.

        Usage:
            /compact           — AI-powered summarisation (keeps context intact)
            /compact --hard    — hard trim to last 6 messages (old behavior)
        """
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
        # Smart compact via async helper
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            loop.run_until_complete(self._smart_compact_async(silent=False))
        except RuntimeError:
            # Already inside an event loop (shouldn't happen in sync context but defensive)
            if len(self.terminal.conversation) > 6:
                self.terminal.conversation = self.terminal.conversation[-6:]
                console.print("[dim]Compacted (fallback)[/dim]")

    async def _smart_compact_async(self, silent: bool = False):
        """AI-powered conversation compaction (inspired by Claude Code).

        Sends all messages to the current model and asks for a dense summary,
        then replaces conversation with [system summary] + last 2 message pairs.
        Falls back to hard trim if the summary call fails.
        """
        conv = self.terminal.conversation
        if len(conv) <= 4:
            if not silent:
                console.print("[dim]Context small enough — no compaction needed[/dim]" if HAS_RICH
                              else "Context small enough")
            return

        if not silent and HAS_RICH:
            console.print("[dim]Summarising conversation...[/dim]")

        # Build a dense transcript for the summariser
        transcript_parts = []
        for m in conv:
            role_label = "User" if m["role"] == "user" else "Aria"
            # Truncate very long messages for the summary prompt
            content = m.get("content", "")[:2000]
            transcript_parts.append(f"{role_label}: {content}")
        transcript = "\n\n".join(transcript_parts)

        summary_prompt = (
            "You are a context compressor. Given the conversation transcript below, "
            "produce a DENSE SUMMARY in 300 words or fewer. "
            "Preserve: key decisions, code written, symbols/assets discussed, "
            "important facts and user preferences. "
            "Write in third-person present tense.\n\n"
            f"TRANSCRIPT:\n{transcript}\n\nSUMMARY:"
        )

        summary = ""
        try:
            ollama_url = self.terminal.config.get("ollama_url", "http://localhost:11434")
            result = await stream_ollama(
                ollama_url,
                summary_prompt,
                history=[],   # no history — pure summarisation task
                model=self.terminal.config.get("model", "qwen2.5:7b"),
                enable_tools=False,
            )
            if result.get("success") and result.get("response"):
                summary = result["response"].strip()
        except Exception:
            pass

        if not summary:
            # Fallback: hard trim
            self.terminal.conversation = conv[-6:]
            if not silent:
                console.print("[dim]Compacted (summary failed, kept last 6 messages)[/dim]" if HAS_RICH
                              else "Compacted (summary fallback)")
            return

        # Build compacted conversation: summary message + last 2 pairs (4 msgs)
        kept_tail = conv[-4:] if len(conv) >= 4 else conv[:]
        self.terminal.conversation = [
            {
                "role": "user",
                "content": (
                    f"[Context Summary — earlier conversation compacted]\n\n{summary}"
                )
            },
            {
                "role": "assistant",
                "content": "I have the summary. Continuing from where we left off."
            },
            *kept_tail,
        ]
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

    # ── Fork conversation ────────────────────────────────────────────────────

    def cmd_fork(self, args: str):
        """Fork conversation at this point — save snapshot, continue independently.

        Usage:
            /fork              — create fork with auto-name
            /fork my-analysis  — create fork with given name
        """
        import time as _t
        name = args.strip() or f"fork-{_t.strftime('%H%M%S')}"
        snapshot = {
            "name":   name,
            "ts":     _t.strftime("%Y-%m-%d %H:%M:%S"),
            "conv":   [dict(m) for m in self.terminal.conversation],
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
        """Restore a previously forked conversation snapshot.

        Usage: /load-fork <index>
        """
        forks = self.terminal._forks
        if not forks:
            console.print("[dim]No forks yet — use /fork to create one[/dim]" if HAS_RICH
                          else "No forks")
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

    # ── Copy last response to clipboard ──────────────────────────────────────

    def cmd_copy(self, args: str):
        """Copy Aria's last response to clipboard.

        Usage: /copy
        """
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
                _sp.run(["xclip", "-selection", "clipboard"],
                        input=text.encode(), check=True, timeout=3)
                copied = True
            except Exception:
                pass
        if not copied:
            try:
                import subprocess as _sp
                _sp.run(["xdotool", "type", "--clearmodifiers", text],
                        check=True, timeout=3)
                copied = True
            except Exception:
                pass
        if copied:
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

    # ── Cost / usage display ─────────────────────────────────────────────────

    def cmd_cost(self, args: str):
        """Show session token usage and estimated cost.

        Token pricing (rough estimates, OpenAI/DeepSeek comparable tier):
          - Input:   $0.14 / 1M tokens
          - Output:  $0.28 / 1M tokens
          - Thinking: $1.10 / 1M tokens  (if thinking model)
        Local Ollama models: $0 (free).
        """
        import time as _t
        elapsed = _t.time() - self.terminal._session_start
        inp = self.terminal._session_input_tokens
        out = self.terminal._session_output_tokens
        think = self.terminal._session_thinking_tokens
        turns = self.terminal._session_turns
        total = inp + out + think

        # Estimate cost (USD) — only meaningful for cloud providers
        is_local = self.terminal._last_provider in ("ollama", "ollama_cache", "local")
        cost_usd = 0.0
        if not is_local:
            cost_usd = (inp * 0.14 + out * 0.28 + think * 1.10) / 1_000_000

        hh = int(elapsed // 3600)
        mm = int((elapsed % 3600) // 60)
        ss = int(elapsed % 60)
        duration = f"{hh}h {mm:02d}m {ss:02d}s" if hh else f"{mm}m {ss:02d}s"

        if HAS_RICH:
            console.print()
            console.print("[bold]Session Usage[/bold]")
            console.print()
            console.print(f"  [dim]{'Duration':<22}[/dim]{duration}")
            console.print(f"  [dim]{'Turns':<22}[/dim]{turns}")
            console.print(f"  [dim]{'Input tokens':<22}[/dim]{inp:,}")
            console.print(f"  [dim]{'Output tokens':<22}[/dim]{out:,}")
            if think:
                console.print(f"  [dim]{'Thinking tokens':<22}[/dim]{think:,}")
            console.print(f"  [dim]{'Total tokens':<22}[/dim][bold]{total:,}[/bold]")
            if is_local:
                console.print(f"  [dim]{'Est. cost':<22}[/dim][green]$0.00 (local)[/green]")
            elif total > 0:
                console.print(f"  [dim]{'Est. cost':<22}[/dim]${cost_usd:.4f} USD")
            console.print(f"  [dim]{'Provider':<22}[/dim]{self.terminal._last_provider}")
            console.print()
        else:
            print(f"  Session: {duration}  Turns: {turns}")
            print(f"  Tokens: {inp:,} in / {out:,} out / {total:,} total")
            if not is_local and total > 0:
                print(f"  Est. cost: ${cost_usd:.4f}")

    # ── Todo / task tracking ─────────────────────────────────────────────────

    def cmd_todo(self, args: str):
        """Persistent task tracking for the current session.

        Usage:
            /todo                 — list all tasks
            /todo add <task>      — add a new task
            /todo done <id>       — mark task done
            /todo remove <id>     — remove task
            /todo clear           — wipe all tasks

        Inspired by Claude Code's TodoRead / TodoWrite tools.
        Tasks are stored in ~/.arthera/todos.json and injected into context.
        """
        import json as _json
        todo_file = CONFIG_DIR / "todos.json"

        def _load():
            try:
                if todo_file.exists():
                    return _json.loads(todo_file.read_text(encoding="utf-8"))
            except Exception:
                pass
            return []

        def _save(tasks):
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            todo_file.write_text(_json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

        parts = args.strip().split(maxsplit=1)
        sub  = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""
        tasks = _load()

        if sub in ("", "list", "ls"):
            if not tasks:
                console.print("[dim]No tasks. Add with: /todo add <task>[/dim]" if HAS_RICH
                              else "No tasks")
                return
            if HAS_RICH:
                console.print()
                for i, t in enumerate(tasks):
                    status_icon = "[green]✓[/green]" if t.get("done") else "[yellow]○[/yellow]"
                    style = "dim" if t.get("done") else ""
                    text = t.get("text", "")
                    console.print(f"  {status_icon} [dim]{i}[/dim]  [{style}]{text}[/{style}]" if style
                                  else f"  {status_icon} [dim]{i}[/dim]  {text}")
                pending = sum(1 for t in tasks if not t.get("done"))
                console.print(f"\n  [dim]{pending}/{len(tasks)} pending[/dim]")
                console.print()
            else:
                for i, t in enumerate(tasks):
                    mark = "✓" if t.get("done") else "○"
                    print(f"  {mark} {i}  {t.get('text', '')}")

        elif sub == "add":
            if not rest:
                console.print("[dim]Usage: /todo add <task text>[/dim]" if HAS_RICH
                              else "Usage: /todo add <task>")
                return
            task = {"text": rest, "done": False, "id": len(tasks)}
            tasks.append(task)
            _save(tasks)
            console.print(f"  [dim]✓ Added: {rest}[/dim]" if HAS_RICH else f"Added: {rest}")

        elif sub in ("done", "check", "complete"):
            try:
                idx = int(rest)
                tasks[idx]["done"] = True
                _save(tasks)
                console.print(f"  [dim]✓ Done: {tasks[idx]['text']}[/dim]" if HAS_RICH
                              else f"Done: {tasks[idx]['text']}")
            except (ValueError, IndexError):
                console.print("[dim]Usage: /todo done <id>[/dim]" if HAS_RICH else "Usage: /todo done <id>")

        elif sub in ("remove", "rm", "delete", "del"):
            try:
                idx = int(rest)
                removed = tasks.pop(idx)
                _save(tasks)
                console.print(f"  [dim]Removed: {removed['text']}[/dim]" if HAS_RICH
                              else f"Removed: {removed['text']}")
            except (ValueError, IndexError):
                console.print("[dim]Usage: /todo remove <id>[/dim]" if HAS_RICH else "bad index")

        elif sub == "clear":
            _save([])
            console.print("[dim]All tasks cleared[/dim]" if HAS_RICH else "Cleared")

        else:
            # Treat unrecognised subcommand as shorthand for /todo add
            full_text = (sub + " " + rest).strip()
            task = {"text": full_text, "done": False, "id": len(tasks)}
            tasks.append(task)
            _save(tasks)
            console.print(f"  [dim]✓ Added: {full_text}[/dim]" if HAS_RICH else f"Added: {full_text}")

    # ── Doctor diagnostic ────────────────────────────────────────────────────

    def cmd_doctor(self, args: str):
        """Diagnose Aria Code installation: models, API keys, backends, tools.

        Inspired by Claude Code's /doctor command.
        """
        import importlib as _il, subprocess as _sp, shutil as _sh

        cfg = self.terminal.config
        ollama_url = cfg.get("ollama_url", "http://localhost:11434")
        api_url    = cfg.get("api_url", "http://localhost:8000")

        checks: List[tuple] = []  # (label, status, detail)

        def _ok(label, detail=""): checks.append(("ok",   label, detail))
        def _warn(label, detail=""): checks.append(("warn", label, detail))
        def _err(label, detail=""): checks.append(("err",  label, detail))

        # 1. Python version
        import sys as _sys
        pyver = f"{_sys.version_info.major}.{_sys.version_info.minor}.{_sys.version_info.micro}"
        if _sys.version_info >= (3, 9):
            _ok("Python", pyver)
        else:
            _warn("Python", f"{pyver} (3.9+ recommended)")

        # 2. Ollama connectivity
        try:
            import urllib.request as _ur
            _opener = _ur.build_opener(_ur.ProxyHandler({}))
            _r = _opener.open(f"{ollama_url}/api/tags", timeout=3)
            _data = json.loads(_r.read())
            models = [m["name"] for m in _data.get("models", [])]
            if models:
                _ok("Ollama", f"{len(models)} models: {', '.join(models[:4])}")
            else:
                _warn("Ollama", "running but no models installed (ollama pull qwen2.5-coder:1.5b)")
        except Exception as e:
            _err("Ollama", f"not reachable at {ollama_url} ({e})")

        # 3. Backend API
        try:
            import urllib.request as _ur
            _opener = _ur.build_opener(_ur.ProxyHandler({}))
            _r = _opener.open(f"{api_url}/health", timeout=3)
            _ok("Backend", f"running at {api_url}")
        except Exception as e:
            _warn("Backend", f"offline at {api_url} — local Ollama mode will be used")

        # 4. API keys
        key_checks = [
            ("finnhub",      "股票行情"),
            ("alphavantage", "历史数据"),
            ("newsapi",      "新闻"),
            ("brave",        "网络搜索"),
            ("coingecko",    "加密货币"),
        ]
        for svc, desc in key_checks:
            k = _get_provider_key(svc)
            if k:
                _ok(f"API key: {svc}", f"{desc} ({'*'*6}{k[-4:]})")
            else:
                _warn(f"API key: {svc}", f"{desc} 未配置 (/apikey set {svc} <key>)")

        # Check LLM cloud keys
        llm_keys = [("deepseek","DeepSeek"),("openai","OpenAI"),
                    ("siliconflow","SiliconFlow"),("moonshot","Moonshot")]
        _has_any_llm = False
        for svc, name in llm_keys:
            k = _get_provider_key(svc)
            if k:
                _ok(f"LLM key: {svc}", f"{name} configured")
                _has_any_llm = True
        if not _has_any_llm:
            _warn("LLM keys", "No cloud LLM keys — Ollama must be running for AI responses")

        # 5. Core Python packages
        _pkgs = [
            ("aiohttp",     "async HTTP"),
            ("rich",        "terminal UI"),
            ("prompt_toolkit", "autocomplete"),
            ("yfinance",    "market data"),
            ("pandas",      "data processing"),
            ("requests",    "HTTP client"),
        ]
        for pkg, desc in _pkgs:
            try:
                m = _il.import_module(pkg)
                ver = getattr(m, "__version__", "?")
                _ok(f"pkg: {pkg}", f"{desc} v{ver}")
            except ImportError:
                _warn(f"pkg: {pkg}", f"{desc} not installed (pip install {pkg})")

        # 6. ARIA.md / project context
        aria_md = pathlib.Path.cwd() / "ARIA.md"
        if aria_md.exists():
            lines = len(aria_md.read_text(encoding="utf-8").splitlines())
            _ok("ARIA.md", f"{lines} lines of project context")
        else:
            _warn("ARIA.md", f"not found in {pathlib.Path.cwd()} (use /init to create)")

        # 7. MCP servers
        if _HAS_MCP:
            try:
                reg = self.terminal._mcp_registry
                if reg and hasattr(reg, "list_tools"):
                    tools = reg.list_tools()
                    _ok("MCP", f"{len(tools)} tools from MCP servers")
                else:
                    _warn("MCP", "registry not started yet")
            except Exception:
                _warn("MCP", "loaded but no active servers")
        else:
            _warn("MCP", "mcp_client not found — MCP support disabled")

        # 8. Tools count
        tool_count = len(ARIA_TOOLS) + len(LOCAL_TOOLS)
        _ok("Aria tools", f"{tool_count} tools loaded")

        # Render results
        console.print() if HAS_RICH else None
        if HAS_RICH:
            console.print("[bold]Aria Code — Diagnostics[/bold]")
            console.print()
            icons = {"ok": "[green]✓[/green]", "warn": "[yellow]⚠[/yellow]", "err": "[red]✗[/red]"}
            for status, label, detail in checks:
                icon = icons[status]
                detail_str = f"  [dim]{detail}[/dim]" if detail else ""
                console.print(f"  {icon}  {label:<28}{detail_str}")
            console.print()
            n_ok = sum(1 for s, *_ in checks if s == "ok")
            n_w  = sum(1 for s, *_ in checks if s == "warn")
            n_e  = sum(1 for s, *_ in checks if s == "err")
            summary_color = "green" if n_e == 0 and n_w == 0 else ("yellow" if n_e == 0 else "red")
            console.print(f"  [{summary_color}]{n_ok} passed, {n_w} warnings, {n_e} errors[/{summary_color}]")
            console.print()
        else:
            print("Aria Code Diagnostics")
            for status, label, detail in checks:
                mark = "✓" if status == "ok" else ("⚠" if status == "warn" else "✗")
                print(f"  {mark}  {label}  {detail}")

    # ── Hooks management ─────────────────────────────────────────────────────

    def cmd_hooks(self, args: str):
        """Manage Aria event hooks — scripts run on specific events.

        Hooks live in ~/.arthera/hooks/ or .aria/hooks/ (project-local).
        Events: prompt_submit, response_done, tool_use, compact

        Usage:
            /hooks list         — show all configured hooks
            /hooks edit <event> — open hook script in $EDITOR
            /hooks run <event>  — manually trigger a hook
        """
        hooks_dirs = [
            CONFIG_DIR / "hooks",
            pathlib.Path.cwd() / ".aria" / "hooks",
        ]
        parts = args.strip().split(maxsplit=1)
        sub  = parts[0].lower() if parts else "list"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "list":
            found: List[tuple] = []
            for hdir in hooks_dirs:
                if hdir.exists():
                    for f in sorted(hdir.iterdir()):
                        if f.is_file() and not f.name.startswith("."):
                            found.append((str(hdir), f.name, str(f)))
            if not found:
                if HAS_RICH:
                    console.print(f"  [dim]No hooks found.[/dim]")
                    console.print(f"  [dim]Hook dirs:[/dim]")
                    for d in hooks_dirs:
                        console.print(f"    [dim]{d}[/dim]")
                    console.print(f"  [dim]Events: prompt_submit  response_done  tool_use  compact[/dim]")
                else:
                    print("No hooks. Dirs:", [str(d) for d in hooks_dirs])
                return
            if HAS_RICH:
                console.print()
                for hdir, name, path in found:
                    console.print(f"  [dim]{name:<28}[/dim]  {path}")
                console.print()
            else:
                for hdir, name, path in found:
                    print(f"  {name}  {path}")

        elif sub == "edit":
            event = rest or "prompt_submit"
            hdir = CONFIG_DIR / "hooks"
            hdir.mkdir(parents=True, exist_ok=True)
            script = hdir / f"{event}.sh"
            if not script.exists():
                script.write_text(
                    f"#!/bin/bash\n# Aria hook: {event}\n# "
                    f"Env vars: ARIA_EVENT, ARIA_MESSAGE, ARIA_PROVIDER\n\n"
                    f'echo "Hook {event}: $ARIA_MESSAGE"\n',
                    encoding="utf-8"
                )
                script.chmod(0o755)
            editor = os.getenv("EDITOR", "nano")
            try:
                import subprocess as _sp
                _sp.run([editor, str(script)])
            except Exception as e:
                console.print(f"[red]Could not open editor: {e}[/red]" if HAS_RICH else str(e))

        elif sub == "run":
            event = rest or "prompt_submit"
            _run_event_hook(event, {"ARIA_EVENT": event, "ARIA_MESSAGE": "", "ARIA_PROVIDER": self.terminal._last_provider})
            console.print(f"  [dim]Hook '{event}' triggered[/dim]" if HAS_RICH else f"Hook '{event}' triggered")

        else:
            console.print("[dim]Usage: /hooks list|edit <event>|run <event>[/dim]" if HAS_RICH
                          else "Usage: /hooks list|edit|run")

    # ---- Regen / Undo commands ----

    async def cmd_regen(self, args: str):
        """Regenerate last AI response by re-sending the last user message."""
        # Find and remove last assistant message
        last_user_msg = None
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "assistant":
                self.terminal.conversation.pop(i)
                break
        # Find the last user message
        for msg in reversed(self.terminal.conversation):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break
        if last_user_msg:
            # Remove it from conversation (send_message will re-add it)
            for i in range(len(self.terminal.conversation) - 1, -1, -1):
                if self.terminal.conversation[i]["role"] == "user" and self.terminal.conversation[i]["content"] == last_user_msg:
                    self.terminal.conversation.pop(i)
                    break
            console.print("[dim]Regenerating...[/dim]" if HAS_RICH else "Regenerating...")
            await self.terminal.send_message(last_user_msg)
        else:
            console.print("[dim]No message to regenerate[/dim]" if HAS_RICH else "Nothing to regenerate")

    def cmd_undo(self, args: str):
        """Remove last user+assistant message pair from conversation."""
        if len(self.terminal.conversation) < 2:
            console.print("[dim]Nothing to undo[/dim]" if HAS_RICH else "Nothing to undo")
            return
        removed = 0
        # Remove last assistant, then last user
        for role in ("assistant", "user"):
            for i in range(len(self.terminal.conversation) - 1, -1, -1):
                if self.terminal.conversation[i]["role"] == role:
                    self.terminal.conversation.pop(i)
                    removed += 1
                    break
        if HAS_RICH:
            console.print(f"[dim]Undone ({removed} messages removed, {len(self.terminal.conversation)} remaining)[/dim]")
        else:
            print(f"Undone ({removed} removed)")

    async def cmd_retry(self, args: str):
        """Re-run last user message with higher temperature (more creative response)."""
        last_user_msg = None
        # Remove last assistant message
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "assistant":
                self.terminal.conversation.pop(i)
                break
        # Find last user message
        for msg in reversed(self.terminal.conversation):
            if msg["role"] == "user":
                last_user_msg = msg["content"]
                break
        if not last_user_msg:
            console.print("[dim]No message to retry[/dim]" if HAS_RICH else "Nothing to retry")
            return
        # Remove last user msg too (send_message will re-add)
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "user" and \
               self.terminal.conversation[i]["content"] == last_user_msg:
                self.terminal.conversation.pop(i)
                break
        # Temporarily bump temperature
        orig_model_key = resolve_model_key(self.terminal.config.get("model", "qwen2.5:7b"))
        _fallback_model = MODELS.get("qwen-fast") or MODELS.get("qwen7b") or next(iter(MODELS.values()))
        orig_temp = MODELS.get(orig_model_key, _fallback_model).get("temperature", 0.3)
        MODELS[orig_model_key]["temperature"] = min(0.9, orig_temp + 0.3)
        if HAS_RICH:
            console.print(f"[dim]Retrying with temperature {MODELS[orig_model_key]['temperature']:.1f}...[/dim]")
        else:
            print(f"Retrying (temp +0.3)...")
        try:
            await self.terminal.send_message(last_user_msg)
        finally:
            MODELS[orig_model_key]["temperature"] = orig_temp  # restore

    def cmd_note(self, args: str):
        """Save a persistent note to ARIA.md in current directory.

        Usage: /note <text>
        Notes are appended to ARIA.md and injected as project context in future sessions.
        """
        text = args.strip()
        if not text:
            console.print("[dim]Usage: /note <text>[/dim]" if HAS_RICH else "Usage: /note <text>")
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
        # Refresh in-memory project context
        global _PROJECT_CONTEXT
        _PROJECT_CONTEXT = _load_project_context()
        if HAS_RICH:
            console.print(f"[dim]Note saved to {aria_md.name}[/dim]")
        else:
            print(f"Saved to {aria_md.name}")

    def cmd_memory(self, args: str):
        """Manage persistent ARIA.md project memory.

        Usage:
            /memory show          — display current ARIA.md
            /memory add <fact>    — append a fact (same as /note)
            /memory clear         — wipe ARIA.md memory section
        """
        global _PROJECT_CONTEXT
        aria_md = pathlib.Path.cwd() / "ARIA.md"
        parts = args.strip().split(maxsplit=1)
        sub  = parts[0].lower() if parts else "show"
        rest = parts[1].strip() if len(parts) > 1 else ""

        if sub == "show":
            if not aria_md.exists():
                msg = f"No ARIA.md in {pathlib.Path.cwd()}"
                console.print(f"[dim]{msg}[/dim]") if HAS_RICH else print(msg)
                return
            content = aria_md.read_text(encoding="utf-8")
            if HAS_RICH:
                try:
                    from rich.markdown import Markdown as _RMd
                    console.print(_RMd(content))
                except Exception:
                    console.print(content)
            else:
                print(content)

        elif sub == "add":
            if not rest:
                console.print("[dim]Usage: /memory add <fact>[/dim]") if HAS_RICH else print("Usage: /memory add <fact>")
                return
            self.cmd_note(rest)

        elif sub == "clear":
            if aria_md.exists():
                aria_md.write_text("# Memory\n\n", encoding="utf-8")
                _PROJECT_CONTEXT = _load_project_context()
                console.print("[dim]Memory cleared.[/dim]") if HAS_RICH else print("Memory cleared.")
            else:
                console.print("[dim]Nothing to clear.[/dim]") if HAS_RICH else print("Nothing to clear.")

        elif sub == "search":
            # Semantic search in ARIA.md and strategy vault using simple grep
            # (ChromaDB RAG upgrade planned for Phase 2)
            if not rest:
                console.print("[dim]Usage: /memory search <query>[/dim]") if HAS_RICH else print("Usage: /memory search <query>")
                return
            query_low = rest.lower()
            results = []
            # 1. Search ARIA.md
            if aria_md.exists():
                for line in aria_md.read_text(encoding="utf-8").splitlines():
                    if query_low in line.lower() and line.strip():
                        results.append(("ARIA.md", line.strip()))
            # 2. Search session history titles
            for sess_file in sorted(SESSIONS_DIR.glob("*.json"), key=lambda p: -p.stat().st_mtime)[:20]:
                try:
                    sess = json.loads(sess_file.read_text(encoding="utf-8"))
                    title = sess.get("metadata", {}).get("title", "")
                    if query_low in title.lower():
                        results.append(("Session", title[:80]))
                except Exception:
                    pass
            # 3. Search strategy vault
            try:
                from strategy_vault import get_vault as _gv
                vault = _gv()
                for s in (vault.list() or []):
                    name = str(s.get("name", ""))
                    msg  = str(s.get("message", ""))
                    if query_low in name.lower() or query_low in msg.lower():
                        results.append(("Strategy", f"{name}: {msg[:60]}"))
            except Exception:
                pass

            if results:
                if HAS_RICH:
                    console.print()
                    console.print(f"  [bold]记忆搜索: '{rest}'[/bold]  [dim]{len(results)} 条结果[/dim]")
                    console.print()
                    for src, text in results[:15]:
                        console.print(f"  [dim]{src:<12s}[/dim]  {text}")
                    console.print()
                else:
                    print(f"  Search '{rest}': {len(results)} results")
                    for src, text in results[:15]:
                        print(f"  [{src}] {text}")
            else:
                msg = f"未找到与 '{rest}' 相关的记忆"
                console.print(f"[dim]{msg}[/dim]") if HAS_RICH else print(msg)

        else:
            console.print("[dim]Usage: /memory [show|add <fact>|clear|search <query>][/dim]") if HAS_RICH else print("Usage: /memory [show|add <fact>|clear|search <query>]")

    async def cmd_review(self, args: str):
        """AI code review for a file or git diff.

        Usage:
            /review                — review git diff HEAD (staged + unstaged)
            /review <file>         — review a specific file
            /review --staged       — review only staged changes
        """
        raw = args.strip()
        policy = self.terminal.config.get("command_policy", "safe")

        if raw and not raw.startswith("--"):
            # File review
            p = pathlib.Path(raw).expanduser()
            if not p.exists():
                msg = f"File not found: {raw}"
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            try:
                content = p.read_text(errors="replace")[:12000]
            except Exception as e:
                console.print(f"[red]Cannot read file: {e}[/red]") if HAS_RICH else print(f"Cannot read: {e}")
                return
            prompt = (
                f"请对以下 `{p.name}` 的代码进行专业审查，查找 Bug、安全问题和改进点。\n"
                f"每条发现用严重程度标签开头：**BUG**、**IMPROVEMENT**、**NIT**。\n"
                f"按文件组织输出，直接给结论，不要重复贴出全部代码。\n\n"
                f"```\n{content}\n```"
            )
        else:
            # Git diff review
            diff_cmd = "git diff --staged" if raw.startswith("--staged") else "git diff HEAD"
            tr = _tool_run_command({"command": diff_cmd})
            if not tr.get("success"):
                msg = tr.get("error", "git diff failed")
                console.print(f"[red]{msg}[/red]") if HAS_RICH else print(msg)
                return
            diff_text = (tr.get("data") or {}).get("stdout", "").strip()
            if not diff_text:
                console.print("[dim]No changes to review.[/dim]") if HAS_RICH else print("No changes to review.")
                return
            diff_text = diff_text[:12000]
            prompt = (
                "请审查以下 git diff，找出 Bug、潜在回归、安全问题和代码质量问题。\n"
                "每条发现用严重程度标签开头：**BUG**、**IMPROVEMENT**、**NIT**。\n"
                "按文件分组，直接给出结论。\n\n"
                f"```diff\n{diff_text}\n```"
            )

        await self.terminal.send_message(prompt)

    async def cmd_init(self, args: str):
        """Bootstrap an ARIA.md memory file for the current project.

        Scans the current directory for key files and asks the AI to generate
        a structured ARIA.md with project name, stack, entry point and conventions.

        Usage:
            /init           — generate ARIA.md (skip if already exists)
            /init --force   — regenerate even if ARIA.md already exists
        """
        global _PROJECT_CONTEXT
        cwd = pathlib.Path.cwd()
        aria_md = cwd / "ARIA.md"
        force = "--force" in args

        if aria_md.exists() and not force:
            msg = f"ARIA.md already exists. Use /init --force to regenerate."
            console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
            return

        # Scan for common project signal files
        _SCAN_FILES = [
            "README.md", "README.rst", "README.txt",
            "package.json", "pyproject.toml", "setup.py", "setup.cfg",
            "requirements.txt", "Pipfile", "poetry.lock",
            "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
            "Makefile", "Dockerfile", ".env.example",
            "CLAUDE.md", ".ariarc",
        ]
        snippets, found_files = [], []
        for fname in _SCAN_FILES:
            fp = cwd / fname
            if fp.exists():
                found_files.append(fname)
                try:
                    snippets.append(f"### {fname}\n{fp.read_text(errors='replace')[:1200]}")
                except Exception:
                    pass

        code_exts = {".py", ".ts", ".js", ".go", ".rs", ".java", ".cpp", ".c"}
        code_files = sorted(
            f.name for f in cwd.iterdir()
            if f.is_file() and f.suffix in code_exts
        )[:10]

        scan_summary = "\n\n".join(snippets[:5])

        prompt = (
            f"分析以下项目信息，生成一个 ARIA.md 记忆文件。\n\n"
            f"目录: {cwd}\n"
            f"发现的配置文件: {', '.join(found_files) or '无'}\n"
            f"代码文件: {', '.join(code_files) or '无'}\n\n"
            f"文件内容:\n{scan_summary}\n\n"
            f"请生成符合以下格式的 ARIA.md（只输出文件内容本身，不加任何解释）:\n\n"
            f"# Memory\n\n"
            f"- **Project**: <项目名称>\n"
            f"- **Stack**: <语言/框架>\n"
            f"- **Entry**: <主入口文件>\n"
            f"- **Conventions**: <代码规范或约定>\n"
            f"- **Notes**: <其他重要信息>\n"
        )

        console.print("[dim]分析项目结构中...[/dim]") if HAS_RICH else print("Analyzing project...")
        await self.terminal.send_message(prompt)

        # Extract the last assistant response and write to ARIA.md
        if self.terminal.conversation:
            last_ai = next(
                (m["content"] for m in reversed(self.terminal.conversation)
                 if m.get("role") == "assistant"),
                None,
            )
            if last_ai:
                content = _strip_markdown_fences(last_ai).strip()
                # Strip injected market-data blocks (lines starting with ##  📊 or *⚠️*)
                # that the market-data prefetch may have appended to the AI response.
                import re as _re_init
                content = _re_init.sub(
                    r'\n*## 📊.*?(?=\n#|\Z)', '', content, flags=_re_init.DOTALL
                ).strip()
                content = _re_init.sub(r'\n*\*⚠️.*?\*\n*', '\n', content).strip()
                if not content.startswith("# Memory"):
                    content = "# Memory\n\n" + content
                aria_md.write_text(content + "\n", encoding="utf-8")
                _PROJECT_CONTEXT = _load_project_context()
                msg = f"ARIA.md created at {aria_md}"
                console.print(f"\n[green]{msg}[/green]") if HAS_RICH else print(f"\n{msg}")

    # ---- Aria-exclusive quant features ----

    async def cmd_auto_strategy(self, args: str):
        """AI strategy auto-optimization loop (unique to Aria).

        Generates a strategy, runs backtest, reads results, iterates until
        the target metric is reached or max rounds exhausted.

        Usage:
            /auto-strategy momentum SPY
            /auto-strategy momentum SPY --target sharpe=1.5
            /auto-strategy meanrev AAPL --target sharpe=1.2 --rounds 3
        """
        import re as _re, time as _time

        parts = args.split()
        strategy_type = parts[0].lower() if parts else "momentum"
        symbol = parts[1].upper() if len(parts) > 1 else "SPY"
        target_sharpe = 1.0
        max_rounds = 3
        for p in parts[2:]:
            m = _re.match(r"--target\s*sharpe=([0-9.]+)", p)
            if m:
                target_sharpe = float(m.group(1))
            m = _re.match(r"--rounds=?([0-9]+)", p)
            if m:
                max_rounds = int(m.group(1))

        if HAS_RICH:
            console.print()
            console.print(f"  [bold cyan]🔄 策略自动优化[/bold cyan]  [dim]{strategy_type} / {symbol}  目标 Sharpe≥{target_sharpe}  最多{max_rounds}轮[/dim]")
            console.print()

        best_sharpe = 0.0
        best_version = None

        for round_num in range(1, max_rounds + 1):
            console.print(f"  [bold]第 {round_num}/{max_rounds} 轮[/bold]") if HAS_RICH else print(f"  Round {round_num}/{max_rounds}")

            # ── Step 1: Generate strategy code ──────────────────────────────
            feedback_ctx = ""
            if round_num > 1 and best_version:
                feedback_ctx = (
                    f"\n\nPrevious backtest Sharpe={best_sharpe:.2f} (target={target_sharpe})."
                    " Modify the strategy to improve Sharpe: adjust lookback period, "
                    "add momentum filter, tighten stop-loss, or change position sizing."
                )

            gen_prompt = (
                f"Generate a complete, self-contained Python backtest strategy script.\n"
                f"Strategy type: {strategy_type}\n"
                f"Symbol: {symbol}\n"
                f"Requirements:\n"
                f"1. Use yfinance to download 2 years of daily OHLCV data\n"
                f"2. Implement the {strategy_type} strategy with clear entry/exit signals\n"
                f"3. Simulate trades: track portfolio value, returns, Sharpe ratio\n"
                f"4. Print EXACTLY this at the end (machine-parseable):\n"
                f"   BACKTEST_RESULT: sharpe=X.XX annual_return=X.XX% max_drawdown=X.XX% trades=N\n"
                f"5. All code in one file, no external dependencies except yfinance/pandas/numpy\n"
                f"{feedback_ctx}\n"
                f"Output ONLY the Python code in ```python``` fences."
            )

            _fname = f"auto_strat_{strategy_type}_{symbol}_r{round_num}_{int(_time.time())}.py"
            _fpath = pathlib.Path.home() / "Desktop" / _fname

            console.print(f"  [dim]生成策略代码...[/dim]") if HAS_RICH else print("  Generating strategy...")
            await self.terminal.send_message(gen_prompt)

            # Extract code from last response
            last_ai = next(
                (m["content"] for m in reversed(self.terminal.conversation)
                 if m.get("role") == "assistant"), ""
            )
            import re as _re2
            py_blocks = _re2.findall(r"```python\n(.*?)```", last_ai, _re2.DOTALL)
            if not py_blocks:
                # fallback: grab after fence
                m = _re2.search(r"```python\n(.*)", last_ai, _re2.DOTALL)
                if m:
                    py_blocks = [m.group(1)]

            if not py_blocks:
                console.print("  [yellow]⚠ 未生成代码，跳过本轮[/yellow]") if HAS_RICH else print("  No code generated, skipping")
                continue

            code = py_blocks[-1].strip()
            _tool_write_file({"path": str(_fpath), "content": code, "_skip_confirm": True})
            console.print(f"  [dim]策略已保存: {_fpath.name}[/dim]") if HAS_RICH else print(f"  Saved: {_fpath.name}")

            # ── Step 2: Run backtest ─────────────────────────────────────────
            console.print(f"  [dim]运行回测...[/dim]") if HAS_RICH else print("  Running backtest...")
            bt_result = _tool_run_command({
                "command": f"python3 {_fpath}",
                "timeout": 120,
            })
            stdout = bt_result.get("data", {}).get("stdout", "") or ""
            stderr = bt_result.get("data", {}).get("stderr", "") or ""

            # ── Step 3: Parse backtest metrics ──────────────────────────────
            sharpe = 0.0
            ann_return = 0.0
            max_dd = 0.0
            n_trades = 0
            m = _re2.search(r"BACKTEST_RESULT:.*?sharpe=([0-9.-]+)", stdout)
            if m:
                sharpe = float(m.group(1))
            m = _re2.search(r"annual_return=([0-9.-]+)%", stdout)
            if m:
                ann_return = float(m.group(1))
            m = _re2.search(r"max_drawdown=([0-9.-]+)%", stdout)
            if m:
                max_dd = float(m.group(1))
            m = _re2.search(r"trades=([0-9]+)", stdout)
            if m:
                n_trades = int(m.group(1))

            # Update best
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_version = _fpath

            # Display round result
            sharpe_color = "green" if sharpe >= target_sharpe else ("yellow" if sharpe > 0 else "red")
            if HAS_RICH:
                console.print(
                    f"  [dim]回测结果:[/dim]  "
                    f"Sharpe=[{sharpe_color}]{sharpe:.2f}[/{sharpe_color}]  "
                    f"年化={ann_return:.1f}%  "
                    f"最大回撤={max_dd:.1f}%  "
                    f"交易次数={n_trades}"
                )
            else:
                print(f"  Backtest: Sharpe={sharpe:.2f}  Return={ann_return:.1f}%  MaxDD={max_dd:.1f}%  Trades={n_trades}")

            if stderr and "Error" in stderr:
                console.print(f"  [red]执行错误: {stderr[:200]}[/red]") if HAS_RICH else print(f"  Error: {stderr[:200]}")

            # ── Step 4: Check convergence ────────────────────────────────────
            if sharpe >= target_sharpe:
                console.print(f"\n  [green]✅ 目标达成！Sharpe={sharpe:.2f} ≥ {target_sharpe}[/green]") if HAS_RICH else print(f"\n  ✓ Target reached: Sharpe={sharpe:.2f}")
                break
            elif round_num < max_rounds:
                console.print(f"  [dim]Sharpe={sharpe:.2f} < 目标{target_sharpe}，继续优化...[/dim]\n") if HAS_RICH else print(f"  Sharpe={sharpe:.2f} < {target_sharpe}, optimizing...\n")

        # ── Summary ──────────────────────────────────────────────────────────
        if HAS_RICH:
            console.print()
            console.print(f"  [bold]优化完成[/bold]  最佳 Sharpe=[{'green' if best_sharpe >= target_sharpe else 'yellow'}]{best_sharpe:.2f}[/{'green' if best_sharpe >= target_sharpe else 'yellow'}]")
            if best_version:
                console.print(f"  最优策略文件: [dim]{best_version}[/dim]")
                console.print(f"  [dim]运行: python3 {best_version}[/dim]")
            console.print()
        else:
            print(f"\n  Best Sharpe={best_sharpe:.2f}  File: {best_version}")

    async def cmd_factor_lab(self, args: str):
        """Factor analysis workstation — compute IC, ICIR, factor returns (Aria exclusive).

        Usage:
            /factor-lab AAPL
            /factor-lab QQQ --days 252
            /factor-lab SPY --factors momentum,value,quality
        """
        import re as _re

        parts = args.split()
        symbol = parts[0].upper() if parts else "SPY"
        days = 252
        for p in parts[1:]:
            m = _re.match(r"--days=?(\d+)", p)
            if m:
                days = int(m.group(1))

        if HAS_RICH:
            console.print()
            console.print(f"  [bold cyan]🔬 因子分析工作台[/bold cyan]  [dim]{symbol}  {days}天数据[/dim]")
            console.print()

        if not _HAS_MDC:
            console.print("[red]需要 market_data_client 模块[/red]") if HAS_RICH else print("market_data_client not available")
            return

        try:
            import numpy as np
            import pandas as pd

            mdc = _get_mdc()

            # ── Fetch data ────────────────────────────────────────────────────
            console.print("  [dim]拉取行情数据...[/dim]") if HAS_RICH else print("  Fetching data...")
            hist = mdc.history(symbol, days=days)
            if not hist.get("success") or not hist.get("data"):
                console.print(f"[red]无法获取 {symbol} 历史数据[/red]") if HAS_RICH else print(f"No data for {symbol}")
                return

            df = pd.DataFrame(hist["data"])
            df["close"] = pd.to_numeric(df["close"], errors="coerce")
            df["volume"] = pd.to_numeric(df.get("volume", pd.Series()), errors="coerce")
            df = df.dropna(subset=["close"])
            close = df["close"]
            returns = close.pct_change().dropna()

            # ── Compute factors ───────────────────────────────────────────────
            factors: dict = {}

            # 1. Momentum (1M, 3M, 6M, 12M)
            for months, label in [(21, "Mom1M"), (63, "Mom3M"), (126, "Mom6M"), (252, "Mom12M")]:
                if len(close) > months:
                    factors[label] = close.pct_change(months)

            # 2. Mean Reversion (short-term)
            if len(close) > 5:
                factors["MeanRev5D"] = -close.pct_change(5)

            # 3. Volatility (annualized)
            if len(returns) > 20:
                factors["Vol20D"] = returns.rolling(20).std() * np.sqrt(252)

            # 4. Volume trend
            if "volume" in df.columns and df["volume"].notna().sum() > 20:
                vol_series = df["volume"].astype(float)
                factors["VolTrend"] = vol_series.pct_change(20)

            # 5. RSI factor
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rs    = gain / loss.replace(0, np.nan)
            factors["RSI14"] = 100 - 100 / (1 + rs)

            # ── Compute IC (Information Coefficient) for each factor ──────────
            # IC = correlation between factor value at t and next-period return
            fwd_returns = returns.shift(-1)  # 1-day forward return

            ic_results = {}
            for fname, fseries in factors.items():
                try:
                    aligned = pd.concat([fseries, fwd_returns], axis=1).dropna()
                    aligned.columns = ["factor", "fwd"]
                    if len(aligned) < 20:
                        continue
                    ic = aligned["factor"].corr(aligned["fwd"])
                    if np.isnan(ic):
                        continue
                    # Rolling IC (window=20) — compute manually to avoid rolling.apply issues
                    roll_ics = []
                    for start in range(0, len(aligned) - 20, 5):
                        chunk = aligned.iloc[start:start + 20]
                        chunk_ic = chunk["factor"].corr(chunk["fwd"])
                        if not np.isnan(chunk_ic):
                            roll_ics.append(chunk_ic)
                    icir = ic / (np.std(roll_ics) + 1e-9) if len(roll_ics) >= 3 else 0.0
                    ic_results[fname] = {"ic": ic, "icir": float(icir), "abs_ic": abs(ic)}
                except Exception:
                    continue

            # ── Current factor values (latest bar) ───────────────────────────
            latest = {fname: float(fseries.dropna().iloc[-1]) if not fseries.dropna().empty else None
                      for fname, fseries in factors.items()}

            # ── Display results ───────────────────────────────────────────────
            if HAS_RICH:
                console.print(f"  [bold]{symbol}[/bold]  [dim]当前价: {close.iloc[-1]:.2f}  数据: {len(df)}天[/dim]")
                console.print()
                console.print("  [bold]因子分析[/bold]")
                console.print()
                console.print(f"  [dim]{'因子':<14s}{'IC':>8s}{'|IC|':>8s}{'ICIR':>8s}{'当前值':>12s}  信号[/dim]")
                console.print("  " + "─" * 60)
                for fname, metrics in sorted(ic_results.items(), key=lambda x: -abs(x[1]["ic"])):
                    ic   = metrics["ic"]
                    icir = metrics["icir"]
                    curr = latest.get(fname)
                    curr_str = f"{curr:.3f}" if curr is not None else "N/A"
                    signal = ""
                    if abs(ic) > 0.03:
                        signal = "↑ 看多" if ic > 0 else "↓ 看空"
                    ic_color = "green" if ic > 0.03 else ("red" if ic < -0.03 else "dim")
                    console.print(
                        f"  [{ic_color}]{fname:<14s}[/{ic_color}]"
                        f"[{ic_color}]{ic:>8.3f}[/{ic_color}]"
                        f"{abs(ic):>8.3f}"
                        f"{icir:>8.2f}"
                        f"{curr_str:>12s}"
                        f"  [dim]{signal}[/dim]"
                    )
                console.print()
                # AI interpretation
                top_factors = sorted(ic_results.items(), key=lambda x: -abs(x[1]["ic"]))[:3]
                if top_factors:
                    console.print("  [bold]AI 解读[/bold]")
                    fac_summary = ", ".join(f"{f}(IC={m['ic']:.3f})" for f, m in top_factors)
                    console.print(f"  [dim]最有效因子: {fac_summary}[/dim]")
                    console.print(f"  [dim]使用 /deep-analysis {symbol} 获取完整 AI 投研分析[/dim]")
                    console.print()
            else:
                print(f"  {symbol} Factor Analysis ({len(df)} days)")
                print(f"  {'Factor':<14} {'IC':>8} {'|IC|':>8} {'ICIR':>8} {'Current':>12}")
                for fname, metrics in sorted(ic_results.items(), key=lambda x: -abs(x[1]["ic"])):
                    curr = latest.get(fname)
                    curr_str = f"{curr:.3f}" if curr is not None else "N/A"
                    print(f"  {fname:<14} {metrics['ic']:>8.3f} {abs(metrics['ic']):>8.3f} {metrics['icir']:>8.2f} {curr_str:>12}")

        except ImportError as e:
            console.print(f"[red]需要 numpy/pandas: {e}[/red]") if HAS_RICH else print(f"Missing: {e}")
        except Exception as e:
            console.print(f"[red]因子分析失败: {e}[/red]") if HAS_RICH else print(f"Error: {e}")

    # ── financial-services workflow 命令 ────────────────────────────────────────

    async def cmd_research(self, args: str):
        """Market Researcher 工作流（参考 anthropics/financial-services market-researcher agent）。

        触发完整研究流程：行情 → 技术图表 → 近期新闻 → 信号摘要 → 研究报告。

        Usage:
            /research AAPL
            /research BTC-USD
            /research 600519.SS
        """
        sym = args.strip().upper() or "AAPL"
        prompt = (
            f"请对 {sym} 进行完整的 Market Researcher 分析：\n"
            f"1. 获取实时行情并显示报价卡片\n"
            f"2. 生成 6 个月技术图表（含 SMA20、SMA50、BB、RSI）\n"
            f"3. 抓取最新 5 条相关新闻\n"
            f"4. 分析主要技术信号（趋势、超买/超卖、关键支撑/阻力）\n"
            f"5. 输出一份简明研究报告（结论 + 风险提示）\n\n"
            f"标的代码：{sym}"
        )
        await self.terminal.handle_user_input(prompt)

    async def cmd_earnings_workflow(self, args: str):
        """财报分析工作流（参考 anthropics/financial-services earnings-reviewer agent）。

        工具链：SEC Edgar → Finnhub financials → AI 摘要 → 财报 table card + 报告。

        Usage:
            /earnings AAPL
            /earnings MSFT Q1 2026
        """
        parts  = args.strip().split()
        sym    = parts[0].upper() if parts else "AAPL"
        period = " ".join(parts[1:]) if len(parts) > 1 else "最近一个季度"
        prompt = (
            f"请对 {sym} 进行 Earnings Reviewer 财报分析（{period}）：\n"
            f"1. 获取最新季报关键指标（EPS、营收、毛利率、同比增速）\n"
            f"2. 对比市场预期与实际结果（beat/miss 分析）\n"
            f"3. 提取管理层展望与主要风险因素\n"
            f"4. 以结构化 table card 呈现核心财务数据\n"
            f"5. 输出一份简明财报评论（3-5 段）\n\n"
            f"标的：{sym}，报告期：{period}"
        )
        await self.terminal.handle_user_input(prompt)

    # ── 经营权共创平台 Agent 命令 ─────────────────────────────────────────────────

    async def cmd_asset_diag(self, args: str):
        """资产诊断 Agent: /asset-diag <资产ID>

        对指定资产运行 AssetDiagnosisAgent，判断处置方式（出租/共创/出售）。
        优先从后端 API 拉取完整资产数据；无数据时以 ID 作为位置标识演示。

        Usage:
            /asset-diag asset_000001
            /asset-diag 中关村创业大街101号
        """
        asset_id = args.strip()
        if not asset_id:
            _p("用法: /asset-diag <资产ID或名称>  例: /asset-diag asset_000001", "dim")
            return

        # 1. 先尝试从后端拉取资产详情
        asset_info = {}
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    f"{api_url}/api/realty/assets/{asset_id}",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        raw = body.get("data", {})
                        # 映射 API 字段 → Agent 期望字段
                        asset_info = {
                            "area":             raw.get("area_sqm", 0),
                            "location":         raw.get("address", asset_id),
                            "vacancy_days":     raw.get("vacancy_days", 0),
                            "expected_rent":    raw.get("monthly_rent_market", 0),
                            "allowed_business": raw.get("allowed_business_types", []),
                            "property_state":   raw.get("property_state", "正常"),
                            "floor_height":     raw.get("floor_height", 0),
                        }
                        _p(f"已从 API 加载资产: {raw.get('name', asset_id)}", "ok")
        except Exception:
            pass

        # 2. 无 API 数据时用最小演示集并提示
        if not asset_info:
            _p("[dim]提示: 未找到资产数据，以 ID 作为位置标识演示（结果仅供参考）[/dim]")
            asset_info = {
                "location": asset_id,
                "area": 0, "vacancy_days": 0,
                "expected_rent": 0, "allowed_business": [],
                "property_state": "正常",
            }

        await self._run_realty_agent("asset_diagnosis", asset_id, {
            "asset_info": asset_info,
        })

    async def cmd_contract_draft(self, args: str):
        """合同规则草案 Agent: /contract-draft <project_id>

        运行 ContractRulesAgent，将谈判结果转化为结构化合同条款草案。

        Usage:
            /contract-draft proj_001
            /contract-draft proj_001 --guaranteed 50000 --share 10
        """
        parts = args.split() if args else []
        project_id = parts[0] if parts else "demo_project"

        # 简单参数解析
        nego = {"guaranteed_amount": 0, "revenue_share_pct": 0}
        for i, p in enumerate(parts):
            if p == "--guaranteed" and i+1 < len(parts):
                try: nego["guaranteed_amount"] = float(parts[i+1])
                except ValueError: pass
            elif p == "--share" and i+1 < len(parts):
                try: nego["revenue_share_pct"] = float(parts[i+1])
                except ValueError: pass

        await self._run_realty_agent("contract_rules", project_id, {
            "negotiation": nego,
            "asset_info":  {"name": project_id},
            "operator_info": {},
        })

    async def cmd_revenue_calc(self, args: str):
        """分账测算: /revenue-calc <project_id> <总流水金额> [退款金额]

        运行 RevenueShareAgent，精确计算本期各方分账金额。

        Usage:
            /revenue-calc proj_001 200000
            /revenue-calc proj_001 200000 5000
        """
        parts = args.split() if args else []
        if len(parts) < 2:
            _p("用法: /revenue-calc <project_id> <总流水> [退款]  "
               "例: /revenue-calc proj_001 200000", "dim")
            return

        project_id = parts[0]
        try:
            gross   = float(parts[1])
            refunds = float(parts[2]) if len(parts) > 2 else 0.0
        except ValueError:
            _p("流水金额必须为数字", "error")
            return

        # 尝试从后端获取合同规则
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        rules = {}
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(f"{api_url}/api/realty/contracts/{project_id}",
                                    timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        rules = body.get("data", {})
        except Exception:
            pass

        if not rules:
            _p(f"[dim]未找到 {project_id} 的合同规则，使用默认值演示[/dim]")
            rules = {"guaranteed_monthly": 30000, "revenue_share_pct": 10,
                     "revenue_share_base": 0, "platform_fee_pct": 5,
                     "risk_reserve_pct": 3, "settlement_cycle": "monthly"}

        await self._run_realty_agent("revenue_share", project_id, {
            "contract_rules":  rules,
            "transaction_data":{"gross_revenue": gross, "refunds": refunds},
        })

    async def cmd_realty_risk_scan(self, args: str):
        """项目风险扫描: /risk-scan [project_id]

        并行运行 cashflow_verify + energy_anomaly + fulfillment_risk 三个 Agent，
        生成综合风险报告。无 project_id 时扫描所有项目。

        Usage:
            /risk-scan
            /risk-scan proj_001
        """
        project_id = args.strip() or "demo_project"

        if HAS_RICH:
            console.print(f"\n  [bold]风险扫描[/bold]  项目: [cyan]{project_id}[/cyan]")

        # 先尝试从后端 API 扫描
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    f"{api_url}/api/realty/risks/scan/{project_id}",
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        _print_risk_scan(data)
                        return
        except Exception:
            pass

        # 降级：本地 Agent 并行运行
        await self._run_realty_team(
            ["cashflow_verify", "energy_anomaly", "fulfillment_risk"],
            project_id, {}
        )

    async def cmd_ops_report(self, args: str):
        """运营汇报生成: /ops-report <project_id>

        运行 OpsOptimizeAgent，分析坪效/客流/营销效果，生成运营优化建议报告。
        优先从后端 API 拉取运营数据，无数据时生成空模板（供人工填写）。

        Usage:
            /ops-report proj_001
        """
        project_id = args.strip() or "demo_project"
        api_url = self.terminal.config.get("api_url", "http://localhost:8000")

        project_info     = {"name": project_id, "area": 0, "business_type": "未知"}
        performance_data = {}
        marketing_data   = {}

        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                # 拉取项目基础信息
                async with sess.get(
                    f"{api_url}/api/realty/assets/{project_id}",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        raw = (await resp.json()).get("data", {})
                        project_info = {
                            "name":          raw.get("name", project_id),
                            "area":          raw.get("area_sqm", 0),
                            "business_type": raw.get("current_business_type", "未知"),
                            "open_date":     raw.get("open_date", ""),
                        }
                # 拉取最近分账数据估算坪效
                async with sess.get(
                    f"{api_url}/api/realty/revenue/splits?project_id={project_id}&page_size=3",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp2:
                    if resp2.status == 200:
                        splits = (await resp2.json()).get("data", {}).get("splits", [])
                        if splits:
                            revenues = [s["split_result"].get("gross_revenue", 0) for s in splits]
                            avg_rev = sum(revenues) / len(revenues)
                            performance_data = {
                                "monthly_revenue": avg_rev,
                                "daily_visits": 0,   # IoT 数据，暂无
                            }
                            _p(f"已加载近 {len(splits)} 期分账数据，月均流水 {avg_rev:,.0f}元", "ok")
        except Exception:
            pass

        if not performance_data:
            _p("[dim]提示: 未找到运营数据，建议先录入分账记录后再运行此命令[/dim]")

        await self._run_realty_agent("ops_optimize", project_id, {
            "project_info":     project_info,
            "performance_data": performance_data,
            "marketing_data":   marketing_data,
            "peer_benchmarks":  {"revenue_per_sqm": 300},
        })

    async def cmd_exit_calc(self, args: str):
        """退出清算草案: /exit-calc <project_id> [--reason <原因>]

        运行 ExitSettlementAgent，生成退出清算方案和交接清单草案。
        从后端 API 读取合同规则和未结账单，生成精确清算草案。

        Usage:
            /exit-calc proj_001
            /exit-calc proj_001 --reason 提前退出
        """
        parts = args.split() if args else []
        project_id = parts[0] if parts else "demo_project"
        reason = "到期终止"
        for i, p in enumerate(parts):
            if p == "--reason" and i+1 < len(parts):
                reason = " ".join(parts[i+1:])
                break

        api_url = self.terminal.config.get("api_url", "http://localhost:8000")
        project_info  = {"name": project_id}
        financials    = {"deposit_amount": 0, "unpaid_invoices": 0,
                         "guaranteed_monthly": 0, "exit_penalty_months": 3,
                         "prepayment_received": 0, "renovation_cost": 0}

        try:
            import aiohttp
            async with aiohttp.ClientSession() as sess:
                # 拉取合同规则
                async with sess.get(
                    f"{api_url}/api/realty/contracts/{project_id}",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        ctr = (await resp.json()).get("data", {})
                        from datetime import date
                        start = ctr.get("start_date", "")
                        used_months = 0
                        if start:
                            try:
                                from dateutil.relativedelta import relativedelta
                                d0 = date.fromisoformat(start)
                                delta = relativedelta(date.today(), d0)
                                used_months = delta.years * 12 + delta.months
                            except Exception:
                                pass
                        project_info.update({
                            "contract_years":  ctr.get("contract_years", 1),
                            "used_months":     used_months,
                            "contract_end":    ctr.get("end_date", ""),
                        })
                        financials.update({
                            "deposit_amount":     ctr.get("deposit_amount", 0),
                            "guaranteed_monthly": ctr.get("guaranteed_monthly", 0),
                            "exit_penalty_months":ctr.get("exit_penalty_months", 3),
                        })
                        _p(f"已加载合同规则: 保底 {ctr.get('guaranteed_monthly',0):,}元/月", "ok")
                # 拉取未结账单
                async with sess.get(
                    f"{api_url}/api/realty/invoices?project_id={project_id}&status=unpaid",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp2:
                    if resp2.status == 200:
                        body2 = await resp2.json()
                        summary = body2.get("data", {}).get("summary", {})
                        unpaid = summary.get("total_amount", 0) - summary.get("paid_amount", 0)
                        financials["unpaid_invoices"] = unpaid
                        if unpaid > 0:
                            _p(f"发现未结账单合计: {unpaid:,.2f}元", "ok")
        except Exception:
            pass

        await self._run_realty_agent("exit_settlement", project_id, {
            "project_info": project_info,
            "financials":   financials,
            "asset_condition": {},
            "exit_reason":  reason,
        })

    # ── 经营权共创 Agent 辅助方法 ─────────────────────────────────────────────

    async def _run_realty_agent(self, agent_name: str, project_id: str,
                                input_data: dict):
        """运行单个 realty Agent，打印结果（本地直接调用，不经过后端）"""
        if HAS_RICH:
            with console.status(
                f"[dim]运行 {agent_name} Agent...[/dim]", spinner="dots"
            ):
                result = await self._call_realty_agent(agent_name, project_id, input_data)
        else:
            print(f"Running {agent_name}...")
            result = await self._call_realty_agent(agent_name, project_id, input_data)

        if result:
            _print_realty_result(result, agent_name)

    async def _run_realty_team(self, agents: list, project_id: str, input_data: dict):
        """并行运行多个 realty Agent"""
        import asyncio
        if HAS_RICH:
            with console.status(
                f"[dim]并行扫描 {', '.join(agents)}...[/dim]", spinner="dots"
            ):
                tasks = [self._call_realty_agent(n, project_id, input_data) for n in agents]
                results = await asyncio.gather(*tasks, return_exceptions=False)
        else:
            tasks = [self._call_realty_agent(n, project_id, input_data) for n in agents]
            results = await asyncio.gather(*tasks, return_exceptions=False)

        for res, name in zip(results, agents):
            if res:
                _print_realty_result(res, name)

    async def _call_realty_agent(self, agent_name: str, project_id: str,
                                  input_data: dict):
        """从 registry 加载并调用 realty Agent"""
        try:
            from agents.registry import get_registry
            cls = get_registry().get(agent_name)
            if not cls:
                _p(f"Agent '{agent_name}' 未注册", "error")
                return None

            # 尝试获取 LLM provider
            llm = None
            try:
                from providers.llm.registry import list_available_providers, get_provider
                avail = [p for p in list_available_providers() if p.get("available")]
                if avail:
                    llm = get_provider(avail[0]["name"])
            except Exception:
                pass

            agent = cls(llm_provider=llm)
            result = await agent.analyze(project_id, input_data)
            return result
        except Exception as e:
            _p(f"Agent {agent_name} 执行失败: {e}", "error")
            return None



    # ---- Provider / API Key management (Open Interpreter style) ----

    def cmd_apikey(self, args: str):
        """Manage Cloud API keys (stored in ~/.arthera/providers.json).

        Usage:
            /apikey set <provider> <key>    — save API key
            /apikey list                    — show configured providers (key masked)
            /apikey remove <provider>       — delete a key
            /apikey test <provider>         — verify key with a ping request
        """
        parts = args.strip().split()
        sub   = parts[0].lower() if parts else "list"

        pjson = _load_providers_json()   # dict of {provider: {api_key, base_url, ...}}

        if sub == "set-url":
            # /apikey set-url <provider> <base_url>
            # 允许自定义端点（中转代理、国内镜像等），示例：
            #   /apikey set-url openai https://my-proxy.com
            #   /apikey set-url siliconflow https://api.siliconflow.cn
            if len(parts) < 3:
                msg = "Usage: /apikey set-url <provider> <base_url>"
                console.print(f"[dim]{msg}[/dim]") if HAS_RICH else print(msg)
                return
            provider = parts[1].lower()
            url      = parts[2].rstrip("/")
            entry    = pjson.get(provider, {})
            entry["base_url"] = url
            pjson[provider]   = entry
            _save_providers_json(pjson)
            msg = f"✓ {provider.capitalize()} base_url 已更新: {url}"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
            return

        if sub == "set":
            if len(parts) < 3:
                msg = ("Usage: /apikey set <provider> <key>  (e.g. /apikey set deepseek sk-...)\n"
                       "       /apikey set-url <provider> <base_url>  (自定义代理端点)")
                console.print(f"[dim]{msg}[/dim]") if HAS_RICH else print(msg)
                return
            provider = parts[1].lower()
            key      = parts[2]
            _all_known = set(_PROVIDER_KEY_MAP) | set(_DATA_KEY_MAP) | set(_PROVIDER_BASE_URLS)
            if provider not in _all_known:
                known_llm  = ", ".join(sorted(_PROVIDER_KEY_MAP.keys()))
                known_data = ", ".join(sorted(_DATA_KEY_MAP.keys()))
                msg = (f"Unknown provider '{provider}'.\n"
                       f"  LLM providers: {known_llm}\n"
                       f"  Data services: {known_data}")
                console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                return

            # ── Data service key ──────────────────────────────────────────────
            if provider in _DATA_KEY_MAP:
                _save_data_key(provider, key)
                env_var = _DATA_KEY_MAP[provider]
                os.environ[env_var] = key  # take effect immediately
                masked = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"
                signup = _DATA_SIGNUP_URLS.get(provider, "")
                msg = f"✓ {provider.capitalize()} 数据服务 key 已保存 ({masked})"
                console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
                return

            # ── LLM provider key (original logic) ────────────────────────────
            # Persist to providers.json
            entry = pjson.get(provider, {})
            entry["api_key"] = key
            if provider in _PROVIDER_BASE_URLS:
                entry.setdefault("base_url", _PROVIDER_BASE_URLS[provider])
            pjson[provider] = entry
            _save_providers_json(pjson)
            # Also set in current process env so it works immediately
            env_var = _PROVIDER_KEY_MAP.get(provider)
            if env_var:
                os.environ[env_var] = key
            masked = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"
            msg = f"✓ {provider.capitalize()} API key 已保存 ({masked})"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)

        elif sub == "list":
            _CLOUD_PROVIDERS = list(_PROVIDER_KEY_MAP.keys())
            # Deduplicate (aliyun == dashscope)
            seen: set = set()
            rows = []
            for prov in _CLOUD_PROVIDERS:
                env_var = _PROVIDER_KEY_MAP[prov]
                if env_var in seen:
                    continue
                seen.add(env_var)
                key = (os.getenv(env_var) or
                       pjson.get(prov, {}).get("api_key", "") if isinstance(pjson, dict) else "")
                if key:
                    masked = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"
                    rows.append((prov, masked, True))
                else:
                    rows.append((prov, "未配置", False))
            data_configured = _load_data_keys()

            if HAS_RICH:
                console.print()
                console.print("  [bold]🤖 LLM Provider Keys[/bold]")
                console.print()
                for prov, display, configured in rows:
                    icon  = "🔑" if configured else "○"
                    color = "green" if configured else "dim"
                    hint  = "" if configured else f"  → [dim]/apikey set {prov} <key>[/dim]"
                    console.print(f"  {icon} [{color}]{prov:14s}[/{color}] {display}{hint}")
                console.print()
                console.print("  [bold]📊 数据服务 Keys[/bold]  [dim](后端离线时使用)[/dim]")
                console.print()
                for svc in sorted(_DATA_KEY_MAP.keys()):
                    key_val = data_configured.get(svc, "")
                    signup  = _DATA_SIGNUP_URLS.get(svc, "")
                    if key_val:
                        masked = key_val[:6] + "****" + key_val[-4:] if len(key_val) > 10 else "****"
                        console.print(f"  🔑 [green]{svc:16s}[/green] {masked}")
                    else:
                        console.print(f"  ○ [dim]{svc:16s} 未配置  → /apikey set {svc} <key>[/dim]")
                console.print()
            else:
                print("\n  LLM Providers:")
                for prov, display, _ in rows:
                    print(f"  {prov:14s} {display}")
                print("\n  Data Services:")
                for svc in sorted(_DATA_KEY_MAP.keys()):
                    key_val = data_configured.get(svc, "")
                    status  = key_val[:6] + "****" if key_val else "未配置"
                    print(f"  {svc:16s} {status}")

        elif sub == "remove":
            if len(parts) < 2:
                console.print("[dim]Usage: /apikey remove <provider>[/dim]") if HAS_RICH else print("Usage: /apikey remove <provider>")
                return
            provider = parts[1].lower()
            # LLM section
            if provider in pjson:
                pjson[provider].pop("api_key", None)
                if not pjson[provider]:
                    del pjson[provider]
                _save_providers_json(pjson)
            # Data section
            if provider in _DATA_KEY_MAP:
                try:
                    if PROVIDERS_FILE.exists():
                        raw = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
                        if provider in raw.get("data", {}):
                            del raw["data"][provider]
                            PROVIDERS_FILE.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
                except Exception:
                    pass
            # Clear from env
            env_var = _PROVIDER_KEY_MAP.get(provider) or _DATA_KEY_MAP.get(provider)
            if env_var and env_var in os.environ:
                del os.environ[env_var]
            msg = f"✓ {provider.capitalize()} key 已删除"
            console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)

        elif sub == "test":
            if len(parts) < 2:
                console.print("[dim]Usage: /apikey test <provider>[/dim]") if HAS_RICH else print("Usage: /apikey test <provider>")
                return
            provider = parts[1].lower()
            key = _get_provider_key(provider)
            if not key:
                msg = f"⚠ {provider} API key 未配置，先运行 /apikey set {provider} <key>"
                console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)
                return
            base_url = _PROVIDER_BASE_URLS.get(provider, "")
            # Quick connectivity test: GET /v1/models (most OpenAI-compat providers support this)
            import urllib.request as _ur, urllib.error as _ue
            test_url = base_url.rstrip("/") + "/v1/models"
            try:
                req = _ur.Request(test_url, headers={"Authorization": f"Bearer {key}"})
                with _ur.urlopen(req, timeout=5) as r:
                    status = r.status
            except _ue.HTTPError as e:
                status = e.code
            except Exception as e:
                status = str(e)
            ok = status == 200 if isinstance(status, int) else False
            icon = "✅" if ok else "⚠"
            msg = f"{icon} {provider.capitalize()}: HTTP {status}"
            console.print(f"{'[green]' if ok else '[yellow]'}{msg}{'[/green]' if ok else '[/yellow]'}")  if HAS_RICH else print(msg)

        else:
            console.print("[dim]Usage: /apikey [set|list|remove|test][/dim]") if HAS_RICH else print("Usage: /apikey [set|list|remove|test]")

    async def cmd_setup(self, args: str):
        """Guided first-run setup wizard (Open Interpreter style).

        Usage: /setup
        """
        import getpass as _gp

        _is_interactive = sys.stdin.isatty()

        if HAS_RICH:
            console.print()
            console.print("[bold cyan]━━ Aria Setup Wizard ━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]")
            console.print()

        # ── Step 1: Detect LOCAL backends only (not cloud LLM providers) ───────
        _LOCAL_BACKENDS_ONLY = {"ollama", "lmstudio", "vllm", "llamacpp", "jan"}
        try:
            from local_llm_provider import probe_all_backends, BACKEND_DEFAULTS
            _all_backends = probe_all_backends()
            # Filter to only true local backends — cloud providers appear in Step 3
            backends = {k: v for k, v in _all_backends.items() if k in _LOCAL_BACKENDS_ONLY}
        except ImportError:
            backends = {}

        console.print("  [bold]Step 1/4 · 本地 Backend[/bold]") if HAS_RICH else print("Step 1: Local Backends")
        ollama_online = backends.get("ollama", False)
        for name, ok in backends.items():
            icon  = "✅" if ok else "○"
            color = "green" if ok else "dim"
            url   = BACKEND_DEFAULTS.get(name, {}).get("default_url", "") if "BACKEND_DEFAULTS" in dir() else ""
            if HAS_RICH:
                console.print(f"  {icon} [{color}]{name:12s}[/{color}] [dim]{url}[/dim]")
            else:
                print(f"  {'✓' if ok else '✗'} {name:12s} {url}")
        console.print() if HAS_RICH else print()

        # ── Step 2: Pick default Ollama model (if Ollama online) ────────────
        if ollama_online and _is_interactive:
            console.print("  [bold]Step 2/4 · 选择默认本地模型[/bold]") if HAS_RICH else print("Step 2: Default model")
            rich_models, _ = detect_ollama_models_rich(
                self.terminal.config.get("ollama_url", "http://localhost:11434")
            )
            if rich_models:
                model_names = [m["name"] for m in rich_models]
                current_id  = self.terminal.config.get("model", "")
                sel_idx     = next((i for i, n in enumerate(model_names) if n == current_id), 0)
                options     = [(f"  {n}", "") for n in model_names]
                picked      = _arrow_select(options, sel_idx, "选择默认模型")
                if picked is not None:
                    chosen = model_names[picked]
                    self.terminal.config["model"] = chosen
                    save_config(self.terminal.config)
                    msg = f"✓ 默认模型设为 {chosen}"
                    console.print(f"  [green]{msg}[/green]") if HAS_RICH else print(f"  {msg}")
            console.print() if HAS_RICH else print()
        else:
            console.print("  [dim]Step 2/4 · (Ollama 未运行，跳过模型选择)[/dim]") if HAS_RICH else print("  Skipping model select (Ollama offline)")
            console.print() if HAS_RICH else print()

        # ── Step 3: Cloud API keys ───────────────────────────────────────────
        console.print("  [bold]Step 3/4 · Cloud API Key 配置[/bold]") if HAS_RICH else print("Step 3: Cloud API Keys")
        _SETUP_PROVIDERS = [
            ("deepseek",  "DeepSeek",  "推荐：deepseek-chat，性价比最高"),
            ("openai",    "OpenAI",    "GPT-4o，o1等"),
            ("groq",      "Groq",      "免费 llama/mixtral 推理，极速"),
            ("anthropic", "Anthropic", "Claude 3.5/3.7"),
        ]
        for prov, label, desc in _SETUP_PROVIDERS:
            existing_key = _get_provider_key(prov)
            if existing_key:
                masked = existing_key[:6] + "****" + existing_key[-4:]
                console.print(f"  🔑 {label:12s} [dim]已配置 ({masked})[/dim]") if HAS_RICH else print(f"  {label}: 已配置")
                continue
            if _is_interactive:
                console.print(f"  [cyan]{label}[/cyan] [dim]({desc})[/dim]") if HAS_RICH else print(f"  {label}: {desc}")
                try:
                    key = _gp.getpass(f"  Enter {label} API key (留空跳过): ").strip()
                except Exception:
                    key = ""
                if key:
                    self.cmd_apikey(f"set {prov} {key}")
            else:
                console.print(f"  ○ {label:12s} [dim]未配置  → /apikey set {prov} <key>[/dim]") if HAS_RICH else print(f"  {label}: not configured")
        console.print() if HAS_RICH else print()

        # ── Step 3.5: Data Service API keys ──────────────────────────────────
        console.print("  [bold]Step 3.5/4 · 市场数据服务 Key（后端离线时使用）[/bold]") if HAS_RICH else print("Step 3.5: Data Service Keys")
        _SETUP_DATA = [
            ("finnhub",      "Finnhub",      "股票实时行情+新闻",     "https://finnhub.io/register"),
            ("newsapi",      "NewsAPI",       "财经新闻聚合",          "https://newsapi.org/register"),
            ("brave",        "Brave Search",  "网页搜索",             "https://api.search.brave.com/app/keys"),
            ("alphavantage", "Alpha Vantage", "股票历史数据",          "https://www.alphavantage.co/support/#api-key"),
        ]
        _existing_data = _load_data_keys()
        for svc, label, desc, signup_url in _SETUP_DATA:
            existing_key = _existing_data.get(svc, "")
            if existing_key:
                masked = existing_key[:6] + "****" + existing_key[-4:]
                console.print(f"  🔑 {label:16s} [dim]已配置 ({masked})[/dim]") if HAS_RICH else print(f"  {label}: configured")
                continue
            if _is_interactive:
                console.print(f"  [cyan]{label}[/cyan] [dim]({desc})[/dim]") if HAS_RICH else print(f"  {label}: {desc}")
                console.print(f"  [dim]注册：{signup_url}[/dim]") if HAS_RICH else print(f"  Register: {signup_url}")
                try:
                    key = _gp.getpass(f"  Enter {label} API key (留空跳过): ").strip()
                except Exception:
                    key = ""
                if key:
                    self.cmd_apikey(f"set {svc} {key}")
            else:
                if HAS_RICH:
                    console.print(f"  ○ {label:16s} [dim]未配置  → /apikey set {svc} <key>[/dim]")
                    console.print(f"    [dim]注册：{signup_url}[/dim]")
                else:
                    print(f"  {label}: not configured  → /apikey set {svc} <key>")
        console.print() if HAS_RICH else print()

        # ── Step 4: Summary ─────────────────────────────────────────────────
        console.print("  [bold]Step 4/4 · 配置完成[/bold]") if HAS_RICH else print("Step 4: Done")
        model = self.terminal.config.get("model", "?")
        provider = self.terminal.config.get("local_provider", "ollama")
        console.print(f"  模型: [cyan]{model}[/cyan]  Provider: [cyan]{provider}[/cyan]") if HAS_RICH else print(f"  Model: {model}  Provider: {provider}")
        console.print()  if HAS_RICH else print()
        console.print("  [dim]提示: /model — 切换模型   /providers — 查看所有 provider[/dim]") if HAS_RICH else print("  Tip: /model  /providers")
        console.print("[bold cyan]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/bold cyan]") if HAS_RICH else print("─" * 50)
        console.print() if HAS_RICH else print()

    # ---- Auth commands ----

    async def cmd_login(self, args: str):
        """Login to Arthera backend.

        Usage: /login <email>           — prompts for password securely
               /login                   — prompts for both email and password
        """
        import getpass as _getpass
        import aiohttp

        parts = args.split()
        if parts:
            email = parts[0]
        else:
            try:
                prompt_fn = console.input if HAS_RICH else input
                email = prompt_fn("  Email: ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
                return
        if not email:
            console.print("[dim]Usage: /login <email>[/dim]" if HAS_RICH else "Usage: /login <email>")
            return

        # Always prompt for password — never accept it as a CLI argument (security)
        try:
            _esc_watcher.pause()
            password = _getpass.getpass("  Password: ")
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
            return
        finally:
            _esc_watcher.resume()

        if not password:
            console.print("[red]Password cannot be empty[/red]" if HAS_RICH else "Password cannot be empty")
            return

        if HAS_RICH:
            console.print("[dim]Authenticating...[/dim]")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.terminal.api_url}/auth/login",
                    json={"email": email, "password": password},
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    try:
                        data = await resp.json()
                    except Exception:
                        data = {}
                    if resp.status == 200 and data.get("token"):
                        self.terminal.config["auth_token"] = data["token"]
                        user_id = data.get("user_id", data.get("uid", email))
                        self.terminal.config["user_id"] = user_id
                        # Store token expiry if provided
                        if data.get("expires_at"):
                            self.terminal.config["token_expires_at"] = data["expires_at"]
                        save_config(self.terminal.config)
                        console.print(f"[green]✓ Logged in as {user_id}[/green]" if HAS_RICH
                                      else f"Logged in as {user_id}")
                    elif resp.status == 401:
                        _print_error("Invalid email or password", "login")
                    elif resp.status == 429:
                        _print_error("Too many login attempts — please wait before retrying", "login")
                    else:
                        err = data.get("error", data.get("message", f"Login failed (HTTP {resp.status})"))
                        _print_error(err, "login")
        except aiohttp.ClientConnectorError:
            _print_error(
                f"Cannot reach {self.terminal.api_url} — check your network connection or use /local on",
                "login"
            )
        except asyncio.TimeoutError:
            _print_error("Login request timed out (15s) — server may be unavailable", "login")
        except Exception as e:
            _print_error(f"Login error: {e}", "login")

    def cmd_logout(self, args: str):
        self.terminal.config["auth_token"] = None
        self.terminal.config["user_id"] = None
        self.terminal.config.pop("token_expires_at", None)
        save_config(self.terminal.config)
        console.print("[dim]Logged out[/dim]" if HAS_RICH else "Logged out")

    def cmd_whoami(self, args: str):
        """Show current authentication status."""
        cfg = self.terminal.config
        user_id = cfg.get("user_id")
        token = cfg.get("auth_token")
        expires = cfg.get("token_expires_at")

        if not token:
            console.print("[dim]Not logged in — use /login <email>[/dim]" if HAS_RICH
                          else "Not logged in")
            return

        if HAS_RICH:
            console.print()
            console.print(f"  [dim]User:[/dim]    {user_id or 'unknown'}")
            console.print(f"  [dim]Token:[/dim]   {token[:12]}...")
            if expires:
                # Check expiry
                try:
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    now = datetime.now(exp_dt.tzinfo)
                    if now > exp_dt:
                        console.print(f"  [dim]Expires:[/dim] [red]EXPIRED ({expires[:10]})[/red]")
                        console.print("  [dim]Run /login to refresh your session[/dim]")
                    else:
                        delta = exp_dt - now
                        hours = int(delta.total_seconds() // 3600)
                        console.print(f"  [dim]Expires:[/dim] {expires[:10]} [dim](in {hours}h)[/dim]")
                except Exception:
                    console.print(f"  [dim]Expires:[/dim] {expires}")
            console.print()
        else:
            print(f"User: {user_id or 'unknown'}")
            print(f"Token: {token[:12]}...")
            if expires:
                print(f"Expires: {expires}")

    # ---- Session commands ----

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
            # Try to load by index from /sessions listing
            sessions = self.terminal.session_mgr.list_sessions()
            if not sessions:
                console.print("[dim]No sessions. Usage: /load <session_id>[/dim]" if HAS_RICH
                              else "No sessions")
                return
            # Arrow-key picker for sessions
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

    # ---- Export command ----

    async def cmd_export(self, args: str):
        parts = args.split()
        fmt = parts[0].lower() if parts else "json"
        filename = parts[1] if len(parts) > 1 else None

        if not self.terminal.conversation:
            console.print("[dim]Nothing to export[/dim]" if HAS_RICH else "Nothing to export")
            return

        if fmt == "json":
            content = json.dumps(self.terminal.conversation, indent=2, ensure_ascii=False)
            ext = "json"
        elif fmt == "csv":
            lines = ["role,content"]
            for msg in self.terminal.conversation:
                escaped = msg["content"].replace('"', '""').replace('\n', ' ')
                lines.append(f'{msg["role"]},"{escaped}"')
            content = "\n".join(lines)
            ext = "csv"
        elif fmt == "md":
            lines = [f"# Aria Code Chat Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
            for msg in self.terminal.conversation:
                prefix = "**You:**" if msg["role"] == "user" else "**Aria:**"
                lines.append(f"{prefix}\n{msg['content']}\n")
            content = "\n".join(lines)
            ext = "md"
        elif fmt == "sft":
            # Export as Alpaca-format SFT training data (user→assistant pairs)
            conv = self.terminal.conversation
            pairs = []
            i = 0
            while i < len(conv) - 1:
                if conv[i]["role"] == "user" and conv[i + 1]["role"] == "assistant":
                    user_text = conv[i]["content"].strip()
                    assistant_text = conv[i + 1]["content"].strip()
                    # Skip very short or tool-result messages
                    if len(user_text) > 10 and len(assistant_text) > 20:
                        if not user_text.startswith("Tool results:"):
                            pairs.append({
                                "instruction": user_text,
                                "input": "",
                                "output": assistant_text,
                                "source": "aria_cli_export",
                                "timestamp": datetime.now().strftime("%Y-%m-%d"),
                            })
                    i += 2
                else:
                    i += 1
            if not pairs:
                console.print("[dim]No user→assistant pairs to export[/dim]" if HAS_RICH
                              else "No pairs to export")
                return
            content = json.dumps(pairs, indent=2, ensure_ascii=False)
            ext = "json"
            if not filename:
                filename = f"aria_sft_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            if HAS_RICH:
                console.print(f"[dim]{len(pairs)} training pairs extracted[/dim]")
            else:
                print(f"{len(pairs)} training pairs")
        else:
            console.print("[dim]Format: json, csv, md, or sft (SFT training data)[/dim]" if HAS_RICH
                          else "Format: json, csv, md, sft")
            return

        if not filename:
            filename = f"aria_code_chat_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{ext}"
        with open(filename, "w") as f:
            f.write(content)
        console.print(f"[green]Exported to {filename}[/green]" if HAS_RICH
                      else f"Exported: {filename}")

    # ---- File operation commands (Claude Code-style) ----

    def cmd_read(self, args: str):
        """Read a file: /read <path> [offset] [limit]"""
        parts = args.split()
        if not parts:
            console.print("[dim]Usage: /read <file_path> [start_line] [num_lines][/dim]" if HAS_RICH
                          else "Usage: /read <path> [offset] [limit]")
            return
        params = {"path": parts[0]}
        if len(parts) > 1:
            try:
                params["offset"] = int(parts[1])
            except ValueError:
                pass
        if len(parts) > 2:
            try:
                params["limit"] = int(parts[2])
            except ValueError:
                pass
        result = _tool_read_file(params)
        if result["success"]:
            content = result["data"]["content"]
            if HAS_RICH:
                # Use Syntax for code files
                path = result["data"]["path"]
                ext = pathlib.Path(path).suffix
                lang_map = {".py": "python", ".js": "javascript", ".ts": "typescript",
                            ".tsx": "typescript", ".jsx": "javascript", ".json": "json",
                            ".yaml": "yaml", ".yml": "yaml", ".md": "markdown",
                            ".swift": "swift", ".html": "html", ".css": "css",
                            ".sh": "bash", ".sql": "sql", ".rs": "rust", ".go": "go"}
                lang = lang_map.get(ext, "text")
                # Strip line numbers we added, use Syntax's own
                raw = "\n".join(line.split("│ ", 1)[1] if "│ " in line else line
                                for line in content.split("\n"))
                console.print(f"\n[dim]{path} ({result['data']['lines']} lines)[/dim]")
                console.print(Syntax(raw, lang, line_numbers=True, theme=_SYNTAX_THEME))
            else:
                print(f"\n{result['data']['path']} ({result['data']['lines']} lines)")
                print(content)
        else:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    def cmd_write(self, args: str):
        """Write a file: /write <path> then paste content, end with EOF line."""
        path = args.strip()
        if not path:
            console.print("[dim]Usage: /write <file_path>[/dim]" if HAS_RICH
                          else "Usage: /write <path>")
            console.print("[dim]Then paste content, end with a line containing only 'EOF'[/dim]" if HAS_RICH
                          else "Paste content, end with EOF")
            return
        if HAS_RICH:
            console.print(f"[dim]Writing to {path} — paste content, end with 'EOF' on a new line:[/dim]")
        else:
            print(f"Writing to {path} — paste content, end with EOF:")
        lines = []
        try:
            while True:
                line = input()
                if line.strip() == "EOF":
                    break
                lines.append(line)
        except (EOFError, KeyboardInterrupt):
            console.print("[dim]Cancelled[/dim]" if HAS_RICH else "Cancelled")
            return
        content = "\n".join(lines) + "\n"
        result = _tool_write_file({"path": path, "content": content})
        if not result["success"]:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    async def cmd_edit(self, args: str):
        """Edit a file interactively: /edit <path> — AI edits based on instruction."""
        parts = args.strip().split(maxsplit=1)
        if not parts:
            console.print("[dim]Usage: /edit <file_path> <instruction>[/dim]" if HAS_RICH
                          else "Usage: /edit <path> <instruction>")
            return
        path = parts[0]
        instruction = parts[1] if len(parts) > 1 else None

        # Read the file first
        read_result = _tool_read_file({"path": path})
        if not read_result["success"]:
            console.print(f"[red]{read_result['error']}[/red]" if HAS_RICH else read_result["error"])
            return

        if not instruction:
            # Show file and ask for instruction
            if HAS_RICH:
                console.print(f"[dim]{read_result['data']['path']} ({read_result['data']['lines']} lines)[/dim]")
            try:
                instruction = (console.input("[bold]>[/bold] What to change: ") if HAS_RICH
                               else input("What to change: ")).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not instruction:
                return

        # Send to AI with file context and ask for edit
        file_content = read_result["data"]["content"]
        prompt = (
            f"I need you to edit the file `{path}`.\n\n"
            f"Current file content:\n```\n{file_content[:8000]}\n```\n\n"
            f"Instruction: {instruction}\n\n"
            f"Use the edit_file tool to make the changes. Remember to use the exact old_string from the file."
        )
        await self.terminal.send_message(prompt)

    def cmd_ls(self, args: str):
        """List files: /ls [path] [pattern]"""
        parts = args.split()
        path = parts[0] if parts else "."
        pattern = parts[1] if len(parts) > 1 else "*"
        result = _tool_list_files({"path": path, "pattern": pattern})
        if result["success"]:
            items = result["data"]["items"]
            if HAS_RICH:
                console.print(f"\n[dim]{result['data']['path']} ({result['data']['count']} items)[/dim]\n")
                for item in items:
                    if item["type"] == "dir":
                        console.print(f"  [bold]{item['name']}/[/bold]")
                    else:
                        size = item["size"]
                        size_str = f"{size:,}" if size < 10000 else f"{size/1024:.1f}K"
                        console.print(f"  {item['name']}  [dim]{size_str}[/dim]")
            else:
                for item in items:
                    suffix = "/" if item["type"] == "dir" else ""
                    print(f"  {item['name']}{suffix}")
        else:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    def cmd_search(self, args: str):
        """Search code: /search <pattern> [path] [glob]

        If the second word doesn't look like a file path (no / or .), the whole
        args string is treated as the pattern and CWD is searched.
        """
        args = args.strip().strip('"\'')
        parts = args.split()
        if not parts:
            console.print("[dim]Usage: /search <pattern> [path] [file_glob][/dim]" if HAS_RICH
                          else "Usage: /search <pattern> [path] [glob]")
            return

        # Determine if second token looks like a file path or directory
        def _looks_like_path(s: str) -> bool:
            return bool(s) and any(c in s for c in "/\\.")

        _QUOTES = '"\'`'
        if len(parts) == 1:
            # Single token: use as pattern, search CWD
            params = {"pattern": parts[0].strip(_QUOTES)}
        elif len(parts) >= 2 and _looks_like_path(parts[1]):
            # Second token is a path
            params = {"pattern": parts[0].strip(_QUOTES)}
            params["path"] = parts[1]
            if len(parts) > 2:
                params["glob"] = parts[2]
        else:
            # Multi-word pattern with no path (e.g. /search def cmd_model)
            # Find where the path arg starts (if any)
            path_idx = None
            for i, p in enumerate(parts[1:], 1):
                if _looks_like_path(p):
                    path_idx = i
                    break
            if path_idx:
                params = {"pattern": " ".join(parts[:path_idx]).strip(_QUOTES)}
                params["path"] = parts[path_idx]
                if path_idx + 1 < len(parts):
                    params["glob"] = parts[path_idx + 1]
            else:
                # Whole args is the pattern
                params = {"pattern": args.strip(_QUOTES)}
        result = _tool_search_code(params)
        if result["success"]:
            matches = result["data"]["matches"]
            if HAS_RICH:
                console.print(f"\n[dim]{result['data']['count']} matches for '{result['data']['pattern']}'[/dim]\n")
                for m in matches[:30]:
                    console.print(f"  [dim]{m['file']}:{m['line']}[/dim]  {m['content'][:100]}")
            else:
                print(f"\n{result['data']['count']} matches:")
                for m in matches[:30]:
                    print(f"  {m['file']}:{m['line']}  {m['content'][:100]}")
        else:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    def cmd_run(self, args: str):
        """Run a command: /run <command>"""
        if not args.strip():
            console.print("[dim]Usage: /run [--dry-run] <command>[/dim]" if HAS_RICH
                          else "Usage: /run [--dry-run] <command>")
            return
        text = args.strip()
        dry_run = False
        if text.startswith("--dry-run "):
            dry_run = True
            text = text[len("--dry-run "):].strip()
        if not text:
            console.print("[dim]Usage: /run [--dry-run] <command>[/dim]" if HAS_RICH
                          else "Usage: /run [--dry-run] <command>")
            return

        policy = self.terminal.config.get("command_policy", "safe")
        decision = evaluate_command_policy(text, policy)
        if not dry_run and decision.allowed and decision.risk == "high":
            if not self._confirm_high_risk_command(decision.normalized_command, decision.risk, decision.policy):
                msg = "Cancelled by user."
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return
        result = _tool_run_command({"command": text, "policy": policy, "dry_run": dry_run})
        if result["success"]:
            data = result["data"]
            if dry_run:
                msg = f"Dry run: risk={data.get('risk', '?')} policy={data.get('policy', '?')} command={data.get('command', '')}"
                console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
                return
            if data["stdout"]:
                if HAS_RICH:
                    console.print(Syntax(data["stdout"], "text", theme=_SYNTAX_THEME))
                else:
                    print(data["stdout"])
            if data["stderr"]:
                if HAS_RICH:
                    console.print(f"[red]{data['stderr']}[/red]")
                else:
                    print(data["stderr"], file=sys.stderr)
        else:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    def cmd_apply(self, args: str):
        """Extract code from last AI response and save to file."""
        filename = args.strip()
        last_response = ""
        for msg in reversed(self.terminal.conversation):
            if msg["role"] == "assistant":
                last_response = msg["content"]
                break
        if not last_response:
            console.print("[dim]No AI response to extract from[/dim]" if HAS_RICH
                          else "No response")
            return

        code = _extract_code_block(last_response)
        if not code:
            console.print("[dim]No code block found in last response[/dim]" if HAS_RICH
                          else "No code block found")
            return

        if not filename:
            # Show code preview and ask for filename
            preview = code[:500] + ("..." if len(code) > 500 else "")
            if HAS_RICH:
                console.print(f"\n[dim]Found code block ({len(code.splitlines())} lines):[/dim]")
                console.print(Syntax(preview, "python", theme=_SYNTAX_THEME))
            else:
                print(f"\nFound code ({len(code.splitlines())} lines):")
                print(preview)
            try:
                filename = (console.input("\n[bold]>[/bold] Save to: ") if HAS_RICH
                            else input("\nSave to: ")).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not filename:
                return

        result = _tool_write_file({"path": filename, "content": code})
        if not result["success"]:
            console.print(f"[red]{result['error']}[/red]" if HAS_RICH else result["error"])

    # ---- Code generation command ----

    async def cmd_code(self, args: str):
        """Generate code and optionally save to file. Usage: /code <description> [--save file.py]"""
        if not args.strip():
            if HAS_RICH:
                console.print("[dim]Usage: /code <description> [--save file.py][/dim]")
                console.print("[dim]Examples:[/dim]")
                console.print("[dim]  /code momentum strategy for AAPL[/dim]")
                console.print("[dim]  /code portfolio optimizer --save optimizer.py[/dim]")
                console.print("[dim]  /code backtest report generator --save report.py[/dim]")
            else:
                print("Usage: /code <description> [--save file.py]")
            return

        # Parse --save flag
        save_path = None
        description = args
        if "--save" in args:
            parts = args.split("--save")
            description = parts[0].strip()
            save_path = parts[1].strip() if len(parts) > 1 else None

        # Build code generation prompt
        prompt = (
            f"Generate complete, production-ready Python code for: {description}\n\n"
            "Requirements:\n"
            "- Include all necessary imports\n"
            "- Add clear inline comments\n"
            "- Include error handling\n"
            "- Use type hints where appropriate\n"
            "- Make it runnable as a standalone script\n\n"
            "Return the code wrapped in ```python``` fences."
        )

        if HAS_RICH:
            console.print(f"[bold]Generating code:[/bold] [bold]{description}[/bold]")
        else:
            print(f"Generating: {description}")

        # Use best available model for code gen
        original_model = self.terminal.config.get("model", "qwen2.5:7b")
        self.terminal.config["model"] = "qwen2.5:7b"

        await self.terminal.send_message(prompt)

        # Restore model
        self.terminal.config["model"] = original_model

        # Extract code from last AI response and save if requested
        if save_path:
            last_response = ""
            for msg in reversed(self.terminal.conversation):
                if msg["role"] == "assistant":
                    last_response = msg["content"]
                    break
            code = _extract_code_block(last_response)
            if code:
                if not save_path.endswith(".py"):
                    save_path += ".py"
                with open(save_path, "w") as f:
                    f.write(code)
                if HAS_RICH:
                    console.print(f"\n[green]Code saved to {save_path}[/green] "
                                  f"[dim]({len(code.splitlines())} lines)[/dim]")
                else:
                    print(f"\nSaved: {save_path} ({len(code.splitlines())} lines)")
            else:
                if HAS_RICH:
                    console.print("[dim]No code block found in response to save[/dim]")
                else:
                    print("No code block found to save")

    # ---- Scaffold command ----

    def cmd_scaffold(self, args: str):
        """Generate a project folder structure with files, with user approval.

        Usage:
          /scaffold <project_name> [template]
          /scaffold my-quant-bot
          /scaffold aapl-analysis --template analysis
          /scaffold momentum-strat --template strategy
          /scaffold data-pipeline --template pipeline
        """
        import textwrap

        parts = args.strip().split()
        if not parts:
            if HAS_RICH:
                console.print("[dim]Usage: /scaffold <project_name> [--template analysis|strategy|pipeline|blank][/dim]")
                console.print("[dim]Examples:[/dim]")
                console.print("[dim]  /scaffold aapl-analysis --template analysis[/dim]")
                console.print("[dim]  /scaffold momentum-strat --template strategy[/dim]")
                console.print("[dim]  /scaffold my-quant-bot[/dim]")
            else:
                print("Usage: /scaffold <project_name> [--template analysis|strategy|pipeline|blank]")
            return

        # Parse project name and template
        project_name = parts[0]
        template = "blank"
        if "--template" in parts:
            idx = parts.index("--template")
            if idx + 1 < len(parts):
                template = parts[idx + 1]

        # Resolve base directory: always ~/Desktop/<project_name>/
        base_dir = pathlib.Path.home() / "Desktop" / project_name

        # Built-in templates
        TEMPLATES = {
            "analysis": {
                "description": "Stock/asset analysis project",
                "files": {
                    "main.py": textwrap.dedent("""\
                        #!/usr/bin/env python3
                        \"\"\"
                        {project} — market analysis entry point.
                        Usage: python3 main.py AAPL
                        \"\"\"
                        import sys
                        import os
                        import numpy as np
                        import pandas as pd
                        import yfinance as yf
                        import matplotlib; matplotlib.use('Agg')
                        import matplotlib.pyplot as plt
                        from analysis import run_analysis
                        from report import generate_report

                        if __name__ == "__main__":
                            symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
                            data = run_analysis(symbol)
                            generate_report(symbol, data)
                        """),
                    "analysis.py": textwrap.dedent("""\
                        \"\"\"Core analysis logic for {project}.\"\"\"
                        import numpy as np
                        import pandas as pd
                        import yfinance as yf


                        def run_analysis(symbol: str, period: str = "1y") -> dict:
                            ticker = yf.Ticker(symbol)
                            hist = ticker.history(period=period, auto_adjust=True, progress=False)
                            if hist.empty:
                                raise ValueError(f"No data for {{symbol}}")
                            hist.columns = hist.columns.droplevel(1) if hasattr(hist.columns, 'droplevel') and hist.columns.nlevels > 1 else hist.columns
                            close = hist["Close"]
                            returns = close.pct_change().dropna()
                            sma20 = close.rolling(20).mean()
                            sma50 = close.rolling(50).mean()
                            rsi = _calc_rsi(close)
                            return {{
                                "symbol": symbol,
                                "current_price": round(float(close.iloc[-1]), 2),
                                "sma20": round(float(sma20.iloc[-1]), 2),
                                "sma50": round(float(sma50.iloc[-1]), 2),
                                "rsi": round(float(rsi.iloc[-1]), 1),
                                "annual_return": round(float(returns.mean() * 252), 4),
                                "volatility": round(float(returns.std() * (252 ** 0.5)), 4),
                                "hist": hist,
                                "returns": returns,
                            }}


                        def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
                            delta = close.diff()
                            gain = delta.clip(lower=0).rolling(period).mean()
                            loss = (-delta.clip(upper=0)).rolling(period).mean()
                            rs = gain / loss.replace(0, float("nan"))
                            return 100 - 100 / (1 + rs)
                        """),
                    "report.py": textwrap.dedent("""\
                        \"\"\"Report generation for {project}.\"\"\"
                        import os
                        import matplotlib; matplotlib.use('Agg')
                        import matplotlib.pyplot as plt


                        def generate_report(symbol: str, data: dict):
                            fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
                            hist = data["hist"]
                            close = hist["Close"]
                            # Price + SMAs
                            axes[0].plot(close.index, close, label="Close", color="#C08050", linewidth=1.5)
                            axes[0].plot(close.index, hist["Close"].rolling(20).mean(), label="SMA20", color="#2AE8A5", linewidth=1)
                            axes[0].plot(close.index, hist["Close"].rolling(50).mean(), label="SMA50", color="#EF4444", linewidth=1)
                            axes[0].set_title(f"{{symbol}} — Price & Moving Averages", fontsize=14)
                            axes[0].legend(); axes[0].grid(alpha=0.3)
                            # Volume
                            axes[1].bar(hist.index, hist["Volume"], color="#C08050", alpha=0.5, label="Volume")
                            axes[1].set_title("Volume"); axes[1].grid(alpha=0.3)
                            plt.tight_layout()
                            out = os.path.expanduser(f"~/Desktop/{symbol}_analysis.png")
                            plt.savefig(out, dpi=150, bbox_inches="tight")
                            plt.close()
                            print(f"Chart saved: {{out}}")
                            print(f"Price: ${{data['current_price']}}  RSI: {{data['rsi']}}  "
                                  f"Annual Return: {{data['annual_return']*100:.1f}}%  Vol: {{data['volatility']*100:.1f}}%")
                        """),
                    "requirements.txt": "numpy\npandas\nyfinance\nmatplotlib\n",
                    "README.md": textwrap.dedent("""\
                        # {project}
                        Stock analysis project generated by Aria CLI.

                        ## Usage
                        ```bash
                        pip3 install -r requirements.txt
                        python3 main.py AAPL
                        ```
                        """),
                },
            },
            "strategy": {
                "description": "Quant trading strategy with backtesting",
                "files": {
                    "main.py": textwrap.dedent("""\
                        #!/usr/bin/env python3
                        \"\"\"
                        {project} — backtest entry point.
                        Usage: python3 main.py AAPL 2022-01-01 2024-01-01
                        \"\"\"
                        import sys
                        from strategy import MomentumStrategy
                        from backtest import run_backtest

                        if __name__ == "__main__":
                            symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
                            start  = sys.argv[2] if len(sys.argv) > 2 else "2022-01-01"
                            end    = sys.argv[3] if len(sys.argv) > 3 else "2024-01-01"
                            strat  = MomentumStrategy(lookback=20)
                            result = run_backtest(strat, symbol, start, end)
                            print(result)
                        """),
                    "strategy.py": textwrap.dedent("""\
                        \"\"\"Strategy definitions for {project}.\"\"\"
                        import pandas as pd


                        class MomentumStrategy:
                            def __init__(self, lookback: int = 20):
                                self.lookback = lookback
                                self.name = f"Momentum({{lookback}})"

                            def generate_signals(self, prices: pd.Series) -> pd.Series:
                                \"\"\"Return +1 (long), -1 (short), 0 (flat) signals.\"\"\"
                                momentum = prices.pct_change(self.lookback)
                                signals = pd.Series(0, index=prices.index)
                                signals[momentum > 0] = 1
                                signals[momentum < 0] = -1
                                return signals.shift(1).fillna(0)  # avoid lookahead
                        """),
                    "backtest.py": textwrap.dedent("""\
                        \"\"\"Backtest engine for {project}.\"\"\"
                        import os
                        import numpy as np
                        import pandas as pd
                        import yfinance as yf
                        import matplotlib; matplotlib.use('Agg')
                        import matplotlib.pyplot as plt


                        def run_backtest(strategy, symbol: str, start: str, end: str) -> dict:
                            ticker = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
                            if ticker.empty:
                                raise ValueError(f"No data for {{symbol}}")
                            prices = ticker["Close"].squeeze()
                            signals = strategy.generate_signals(prices)
                            returns = prices.pct_change().fillna(0)
                            strat_returns = signals * returns
                            equity = (1 + strat_returns).cumprod()
                            bh_equity = (1 + returns).cumprod()
                            # Metrics
                            ann_return = strat_returns.mean() * 252
                            ann_vol    = strat_returns.std() * (252 ** 0.5)
                            sharpe     = ann_return / ann_vol if ann_vol > 0 else 0
                            max_dd     = (equity / equity.cummax() - 1).min()
                            # Plot
                            fig, ax = plt.subplots(figsize=(14, 6))
                            ax.plot(equity.index, equity, label=strategy.name, color="#C08050", linewidth=2)
                            ax.plot(bh_equity.index, bh_equity, label="Buy & Hold", color="#2AE8A5", linewidth=1.5, linestyle="--")
                            ax.set_title(f"{{symbol}} — {{strategy.name}} Backtest"); ax.legend(); ax.grid(alpha=0.3)
                            out = os.path.expanduser(f"~/Desktop/{{symbol}}_backtest.png")
                            plt.savefig(out, dpi=150, bbox_inches="tight"); plt.close()
                            result = {{
                                "symbol": symbol, "strategy": strategy.name,
                                "ann_return": round(ann_return * 100, 2),
                                "ann_vol": round(ann_vol * 100, 2),
                                "sharpe": round(sharpe, 3),
                                "max_drawdown": round(max_dd * 100, 2),
                                "chart": out,
                            }}
                            print(f"Sharpe: {{result['sharpe']}}  Return: {{result['ann_return']}}%  "
                                  f"MaxDD: {{result['max_drawdown']}}%  Chart: {{out}}")
                            return result
                        """),
                    "requirements.txt": "numpy\npandas\nyfinance\nmatplotlib\n",
                    "README.md": textwrap.dedent("""\
                        # {project}
                        Quant strategy backtest project generated by Aria CLI.

                        ## Usage
                        ```bash
                        pip3 install -r requirements.txt
                        python3 main.py SPY 2022-01-01 2024-01-01
                        ```
                        """),
                },
            },
            "pipeline": {
                "description": "Market data pipeline (fetch → process → store)",
                "files": {
                    "main.py": textwrap.dedent("""\
                        #!/usr/bin/env python3
                        \"\"\"
                        {project} — data pipeline entry point.
                        Usage: python3 main.py AAPL MSFT TSLA
                        \"\"\"
                        import sys
                        from pipeline import DataPipeline

                        if __name__ == "__main__":
                            symbols = sys.argv[1:] or ["AAPL", "MSFT", "TSLA"]
                            pipe = DataPipeline(symbols)
                            pipe.run()
                        """),
                    "pipeline.py": textwrap.dedent("""\
                        \"\"\"Data pipeline for {project}.\"\"\"
                        import os
                        import pandas as pd
                        import yfinance as yf


                        class DataPipeline:
                            def __init__(self, symbols: list, period: str = "1y", output_dir: str = "~/Desktop/data"):
                                self.symbols = symbols
                                self.period = period
                                self.output_dir = os.path.expanduser(output_dir)
                                os.makedirs(self.output_dir, exist_ok=True)

                            def fetch(self, symbol: str) -> pd.DataFrame:
                                df = yf.download(symbol, period=self.period, auto_adjust=True, progress=False)
                                df.columns = df.columns.droplevel(1) if df.columns.nlevels > 1 else df.columns
                                return df

                            def process(self, df: pd.DataFrame) -> pd.DataFrame:
                                df = df.copy()
                                df["Returns"] = df["Close"].pct_change()
                                df["SMA20"]   = df["Close"].rolling(20).mean()
                                df["SMA50"]   = df["Close"].rolling(50).mean()
                                df["Volatility"] = df["Returns"].rolling(20).std() * (252 ** 0.5)
                                return df.dropna()

                            def store(self, symbol: str, df: pd.DataFrame):
                                path = os.path.join(self.output_dir, f"{{symbol}}.csv")
                                df.to_csv(path)
                                print(f"  Saved {{len(df)}} rows → {{path}}")

                            def run(self):
                                print(f"Running pipeline for: {{self.symbols}}")
                                for symbol in self.symbols:
                                    try:
                                        raw = self.fetch(symbol)
                                        processed = self.process(raw)
                                        self.store(symbol, processed)
                                    except Exception as e:
                                        print(f"  Error {{symbol}}: {{e}}")
                                print("Pipeline complete.")
                        """),
                    "requirements.txt": "pandas\nyfinance\n",
                    "README.md": textwrap.dedent("""\
                        # {project}
                        Market data pipeline generated by Aria CLI.

                        ## Usage
                        ```bash
                        pip3 install -r requirements.txt
                        python3 main.py AAPL MSFT TSLA
                        # Output CSVs saved to ~/Desktop/data/
                        ```
                        """),
                },
            },
            "blank": {
                "description": "Blank project scaffold",
                "files": {
                    "main.py": textwrap.dedent("""\
                        #!/usr/bin/env python3
                        \"\"\"
                        {project} — main entry point.
                        \"\"\"
                        import os
                        import sys
                        import numpy as np
                        import pandas as pd


                        def main():
                            print("Hello from {project}!")


                        if __name__ == "__main__":
                            main()
                        """),
                    "requirements.txt": "numpy\npandas\n",
                    "README.md": "# {project}\n\nProject generated by Aria CLI.\n",
                },
            },
        }

        if template not in TEMPLATES:
            msg = f"Unknown template '{template}'. Available: {', '.join(TEMPLATES)}"
            console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
            return

        tmpl = TEMPLATES[template]
        files = {
            k: v.format(project=project_name) if isinstance(v, str) else v
            for k, v in tmpl["files"].items()
        }

        # ── Preview: show tree + file summaries ──────────────────────────────
        if HAS_RICH:
            console.print()
            console.print(f"  [bold]Scaffold:[/bold] [cyan]{project_name}[/cyan]  "
                          f"[dim]({tmpl['description']}, {template} template)[/dim]")
            console.print(f"  [dim]Location:[/dim] {base_dir}")
            console.print()
            console.print(f"  [dim]{base_dir.name}/[/dim]")
            for fname, fcontent in files.items():
                lines = fcontent.count("\n") + 1 if fcontent else 0
                exists_tag = " [yellow](exists)[/yellow]" if (base_dir / fname).exists() else ""
                console.print(f"  [dim]  ├── {fname:<24s}[/dim] {lines} lines{exists_tag}")
            console.print()
        else:
            print(f"\nScaffold: {project_name}  ({template} template)")
            print(f"Location: {base_dir}")
            print(f"\n  {base_dir.name}/")
            for fname, fcontent in files.items():
                lines = fcontent.count("\n") + 1 if fcontent else 0
                exists_tag = " (exists)" if (base_dir / fname).exists() else ""
                print(f"    ├── {fname:<24s}  {lines} lines{exists_tag}")
            print()

        # ── Ask: approve all / approve each / cancel ─────────────────────────
        # In non-interactive mode (-p flag / piped stdin) auto-approve all files.
        if not sys.stdin.isatty():
            choice = "y"
            console.print("  [dim](非交互模式：自动确认创建所有文件)[/dim]") if HAS_RICH else print("  (Auto-approved: non-interactive mode)")
        elif HAS_RICH:
            choice = console.input(
                "  [bold]Create these files?[/bold] "
                "[dim]\\[y=all / n=cancel / r=review each][/dim] "
            ).strip().lower()
        else:
            choice = input("  Create these files? [y=all / n=cancel / r=review each] ").strip().lower()

        if choice in ("n", "no"):
            console.print("[dim]Scaffold cancelled.[/dim]" if HAS_RICH else "Cancelled.")
            return

        approve_each = choice in ("r", "review")
        created, skipped = [], []

        for fname, fcontent in files.items():
            target = base_dir / fname
            if approve_each:
                if HAS_RICH:
                    console.print(f"\n  [dim]{fname}[/dim]  ({fcontent.count(chr(10))+1} lines)")
                    sub = console.input(
                        "  [dim]Write this file? [y/n] [/dim]"
                    ).strip().lower()
                else:
                    print(f"\n  {fname}  ({fcontent.count(chr(10))+1} lines)")
                    sub = input("  Write? [y/n] ").strip().lower()
                if sub not in ("y", "yes", ""):
                    skipped.append(fname)
                    continue

            result = _tool_write_file({"path": str(target), "content": fcontent, "_skip_confirm": True})
            if result["success"]:
                created.append(fname)
            else:
                err = result.get("error", "?")
                if HAS_RICH:
                    console.print(f"  [red]Failed {fname}: {err}[/red]")
                else:
                    print(f"  Failed {fname}: {err}")

        # ── Summary ───────────────────────────────────────────────────────────
        if HAS_RICH:
            console.print()
            if created:
                console.print(f"  [green]✓[/green] Created {len(created)} file(s) in [bold]{base_dir}[/bold]")
                for f in created:
                    console.print(f"    [dim]{f}[/dim]")
            if skipped:
                console.print(f"  [dim]Skipped: {', '.join(skipped)}[/dim]")
            console.print()
            console.print(f"  [dim]Run:  cd ~/Desktop/{project_name} && python3 main.py[/dim]")
            console.print()
        else:
            print(f"\nCreated {len(created)} files in {base_dir}")
            if skipped:
                print(f"Skipped: {', '.join(skipped)}")
            print(f"Run: cd ~/Desktop/{project_name} && python3 main.py")

    # ---- Feedback command ----

    async def cmd_feedback(self, args: str):
        """Rate the last AI response and log feedback locally and remotely.

        Usage: /feedback up|down [optional comment]
        """
        parts = args.strip().split(maxsplit=1)
        vote = parts[0].lower() if parts else ""
        comment = parts[1].strip() if len(parts) > 1 else ""

        if vote not in ("up", "down", "1", "0"):
            console.print("[dim]Usage: /feedback up|down [comment][/dim]" if HAS_RICH
                          else "Usage: /feedback up|down [comment]")
            return
        is_positive = vote in ("up", "1")

        # Find last assistant message and its position
        last_msg = None
        msg_idx = None
        for i in range(len(self.terminal.conversation) - 1, -1, -1):
            if self.terminal.conversation[i]["role"] == "assistant":
                last_msg = self.terminal.conversation[i]["content"][:500]
                msg_idx = i
                break
        if not last_msg:
            console.print("[dim]No AI response to rate[/dim]" if HAS_RICH else "No response to rate")
            return

        rating = "positive" if is_positive else "negative"
        feedback_payload = {
            "message": last_msg,
            "rating": rating,
            "comment": comment,
            "model": self.terminal.config.get("model", ""),
            "timestamp": datetime.now().isoformat(),
            "session_id": self.terminal.session_id,
        }

        # --- 1. Persist locally first (always works, even offline) ---
        feedback_log = CONFIG_DIR / "feedback_log.jsonl"
        try:
            with open(feedback_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(feedback_payload, ensure_ascii=False) + "\n")
        except Exception:
            pass  # Non-fatal: don't block UI if disk write fails

        # --- 2. Send to backend (best-effort, fire-and-forget) ---
        import aiohttp
        api_success = False
        try:
            headers = {}
            if self.terminal.config.get("auth_token"):
                headers["Authorization"] = f"Bearer {self.terminal.config['auth_token']}"
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.terminal.api_url}/api/v2/ai/feedback",
                    json=feedback_payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=8)
                ) as resp:
                    api_success = resp.status in (200, 201, 204)
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
            pass  # Offline or server down — local log is the fallback
        except Exception:
            pass

        icon = "↑" if is_positive else "↓"
        sync_note = "" if api_success else " [dim](saved locally)[/dim]"
        if HAS_RICH:
            comment_note = f" — {comment}" if comment else ""
            console.print(f"[green]Feedback {icon}[/green]{comment_note}{sync_note}")
        else:
            print(f"Feedback {icon}" + (f" — {comment}" if comment else "") +
                  ("" if api_success else " (saved locally)"))

    # ---- Market data commands (expose unused Aria tools) ----

    async def _run_tool_cmd(self, tool_name: str, params: dict, label: str = ""):
        """Generic helper: run tool with spinner and formatted output.

        Routing priority:
          1. LOCAL_TOOLS (via executor — never blocks event loop)
          2. Remote Aria backend (AWS) — if local not available
          3. Graceful error if both fail
        """
        display = label or tool_name

        # ── 1. Try LOCAL_TOOLS first (run in executor to avoid blocking) ──
        if tool_name in LOCAL_TOOLS:
            handler, _ = LOCAL_TOOLS[tool_name]
            if HAS_RICH:
                with console.status(f"[dim]{display}...[/dim]", spinner="dots"):
                    result = await asyncio.get_event_loop().run_in_executor(
                        None, handler, params
                    )
            else:
                print(f"Running {display}...")
                result = await asyncio.get_event_loop().run_in_executor(
                    None, handler, params
                )
        else:
            # ── 2. Fall through to remote Aria backend ────────────────────
            local_mode = self.terminal.config.get("local_mode", False)
            if local_mode:
                result = {
                    "success": False,
                    "error":   f"Tool '{tool_name}' has no local implementation. "
                               "Run '/local off' to use the Aria backend, or "
                               "add a handler in aria_tools.py.",
                }
            else:
                if HAS_RICH:
                    with console.status(f"[dim]Running {display}...[/dim]", spinner="dots"):
                        result = await execute_aria_tool(self.terminal.api_url, tool_name, params)
                else:
                    print(f"Running {display}...")
                    result = await execute_aria_tool(self.terminal.api_url, tool_name, params)

        if result.get("success"):
            data = result.get("data", {})
            if isinstance(data, dict) and HAS_RICH:
                out = Text()
                for k, v in data.items():
                    if k in ("chart_prices", "raw", "metadata"):
                        continue
                    label_str = k.replace("_", " ").title()
                    val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
                    if isinstance(v, (int, float)):
                        color = "green" if v >= 0 else "red" if v < 0 else ""
                        out.append(f"  {label_str:<20s}", style="dim")
                        out.append(f"{val_str}\n", style=color if color else "")
                    else:
                        out.append(f"  {label_str:<20s}", style="dim")
                        out.append(f"{val_str}\n")
                console.print(out)
            else:
                console.print(f"  [dim]{json.dumps(data, ensure_ascii=False)[:500]}[/dim]" if HAS_RICH
                              else json.dumps(data, ensure_ascii=False)[:500])
        else:
            _print_error(f"Failed: {result.get('error', 'No data')}")

    async def _run_parallel(self, tool_name: str,
                             param_list: list,
                             label_fn=None):
        """Run a tool in parallel for multiple param dicts, display each result."""
        tasks = [
            asyncio.create_task(
                asyncio.get_event_loop().run_in_executor(
                    None, LOCAL_TOOLS[tool_name][0], p
                ) if tool_name in LOCAL_TOOLS
                else execute_aria_tool(self.terminal.api_url, tool_name, p)
            )
            for p in param_list
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for p, r in zip(param_list, results):
            lbl = label_fn(p) if label_fn else tool_name
            if isinstance(r, Exception):
                _print_error(f"{lbl}: {r}")
            else:
                _print_finance_result(tool_name, r)

    async def _fetch_and_display_finance(self, tool_name: str, params: dict, label: str,
                                          mdc_fallback_symbol: str = "") -> bool:
        """Try tool → local finance tool → market_data_client fallback. Returns True if data shown."""
        result = None
        # 1. LOCAL_TOOLS (ccxt / local finance)
        if tool_name in LOCAL_TOOLS:
            fn = LOCAL_TOOLS[tool_name][0]
            result = await asyncio.get_event_loop().run_in_executor(None, fn, params)
        # 2. Remote backend
        if not (result and result.get("success")):
            result = await execute_aria_tool(self.terminal.api_url, tool_name, params)
        # 3. MarketDataClient yfinance fallback
        if not (result and result.get("success")) and _HAS_MDC and mdc_fallback_symbol:
            try:
                mdc = _get_mdc()
                result = mdc.quote(mdc_fallback_symbol)
                if result.get("success"):
                    result["provider"] = "yfinance"
            except Exception:
                pass

        if result and result.get("success"):
            _print_finance_result(tool_name, result)
            # Also show basic price line if _print_finance_result didn't handle this tool
            if tool_name not in ("get_market_data", "get_crypto_data", "get_forex_data"):
                px   = result.get("price", result.get("rate", 0))
                chg  = result.get("change_pct", 0)
                sign = "+" if chg >= 0 else ""
                color = "green" if chg >= 0 else "red"
                prov  = result.get("provider", "")
                if HAS_RICH and px:
                    console.print(f"  [bold]{label:<12}[/bold]  {px}  [{color}]{sign}{chg:.2f}%[/{color}]  [dim]{prov}[/dim]")
            return True
        else:
            err = (result or {}).get("error") or "数据暂不可用"
            if HAS_RICH:
                console.print(f"  [yellow]⚠ {label}: {err}[/yellow]")
            else:
                print(f"  ⚠ {label}: {err}")
            return False

    async def cmd_crypto(self, args: str):
        """Crypto data: /crypto BTC ETH (with yfinance fallback)"""
        symbols = args.upper().split() if args else ["BTC"]
        if HAS_RICH:
            console.print()
        for sym in symbols:
            # yfinance crypto symbol: BTC → BTC-USD, ETH → ETH-USD
            yf_sym = sym + "-USD" if not sym.endswith("-USD") and "/" not in sym else sym
            await self._fetch_and_display_finance(
                "get_crypto_data", {"symbol": sym},
                label=sym, mdc_fallback_symbol=yf_sym
            )
        if HAS_RICH:
            console.print()

    async def cmd_forex(self, args: str):
        """Forex rates: /forex EUR/USD USD/CNY (with yfinance fallback)"""
        pairs = args.upper().split() if args else ["EUR/USD"]
        if HAS_RICH:
            console.print()
        for pair in pairs:
            # yfinance forex symbol: EUR/USD → EURUSD=X
            yf_pair = pair.replace("/", "") + "=X"
            await self._fetch_and_display_finance(
                "get_forex_data", {"pair": pair},
                label=pair, mdc_fallback_symbol=yf_pair
            )
        if HAS_RICH:
            console.print()

    async def cmd_commodity(self, args: str):
        """Commodities: /commodity gold oil silver (parallel fetch)"""
        items = args.lower().split() if args else ["gold"]
        await self._run_parallel(
            "get_commodities_data",
            [{"commodity": c} for c in items],
            label_fn=lambda p: f"commodity {p['commodity']}",
        )

    async def cmd_risk(self, args: str):
        """Risk metrics: /risk AAPL or /risk portfolio"""
        target = args.strip().upper() or "AAPL"
        if target == "PORTFOLIO":
            await self._run_tool_cmd("assess_portfolio_risk", {
                "holdings": self.terminal.config.get("watchlist", ["AAPL", "MSFT"]),
            }, "portfolio risk")
            return

        # Try remote tool; fall back to local get_risk_metrics if backend unavailable
        result = await execute_aria_tool(self.terminal.api_url, "get_risk_metrics", {"symbol": target})
        if result.get("success"):
            data = result.get("data", {})
            if HAS_RICH:
                console.print()
                for k, v in (data.items() if isinstance(data, dict) else {}.items()):
                    val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
                    color = "green" if isinstance(v, float) and v >= 0 else ("red" if isinstance(v, float) and v < 0 else "")
                    console.print(f"  [dim]{k.replace('_',' ').title():<24s}[/dim] [{color}]{val_str}[/{color}]" if color
                                  else f"  [dim]{k.replace('_',' ').title():<24s}[/dim] {val_str}")
                console.print()
        elif "get_risk_metrics" in LOCAL_TOOLS:
            # Local fallback
            local_fn = LOCAL_TOOLS["get_risk_metrics"][0]
            local_result = await asyncio.get_event_loop().run_in_executor(None, local_fn, {"symbol": target})
            if local_result.get("success"):
                data = local_result.get("data", {})
                if HAS_RICH:
                    console.print()
                    console.print(f"  [bold]{target} Risk Metrics[/bold]  [dim](local calculation)[/dim]")
                    console.print()
                    for k, v in (data.items() if isinstance(data, dict) else {}.items()):
                        val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
                        console.print(f"  [dim]{k.replace('_',' ').title():<24s}[/dim] {val_str}")
                    console.print()
                else:
                    print(f"  {target} Risk Metrics (local):")
                    for k, v in (data.items() if isinstance(data, dict) else {}.items()):
                        print(f"  {k}: {v}")
            else:
                console.print(f"[dim]Risk metrics unavailable for {target}: {local_result.get('error','')}[/dim]") if HAS_RICH else print(f"Risk unavailable: {local_result.get('error','')}")
        else:
            msg = f"⚠ 风险指标服务暂不可用 ({result.get('error','')[:60]})"
            console.print(f"[yellow]{msg}[/yellow]") if HAS_RICH else print(msg)

    async def cmd_market(self, args: str):
        """Market overview: /market [indices|sectors]"""
        sub = args.strip().lower()
        if sub == "sectors":
            await self._run_tool_cmd("get_sector_performance", {}, "sector performance")
        else:
            # Try remote tool first; fall back to local MarketDataClient if backend unavailable
            result = await execute_aria_tool(self.terminal.api_url, "get_market_indices", {})
            if result.get("success"):
                await self._run_tool_cmd("get_market_indices", {}, "market indices")
            elif _HAS_MDC:
                # Local fallback via MarketDataClient.indices()
                try:
                    mdc = _get_mdc()
                    idx_result = mdc.indices()
                    if idx_result.get("success") and idx_result.get("indices"):
                        if HAS_RICH:
                            console.print()
                            console.print("  [bold]Global Indices[/bold]  [dim](local data)[/dim]")
                            console.print()
                        for name, d in idx_result["indices"].items():
                            price = d.get("price", "N/A")
                            chg   = d.get("change_pct", 0)
                            sign  = "+" if chg >= 0 else ""
                            color = "green" if chg >= 0 else "red"
                            if HAS_RICH:
                                console.print(f"  [dim]{name:<20s}[/dim]  {price:>10}  [{color}]{sign}{chg:.2f}%[/{color}]")
                            else:
                                print(f"  {name:<20s}  {price:>10}  {sign}{chg:.2f}%")
                    else:
                        console.print("[dim]市场数据暂不可用。请检查网络连接。[/dim]") if HAS_RICH else print("Market data unavailable.")
                except Exception as _e:
                    console.print(f"[dim]本地数据获取失败: {_e}[/dim]") if HAS_RICH else print(f"Local data error: {_e}")
            else:
                console.print("[dim]后端不可用，本地数据模块未加载。使用 /indices 命令查看实时行情。[/dim]") if HAS_RICH else print("Backend unavailable. Try /indices.")

    async def cmd_optimize(self, args: str):
        """Optimize portfolio: /optimize [symbols...]"""
        symbols = args.upper().split() if args else self.terminal.config.get("watchlist", ["AAPL", "MSFT", "GOOGL"])
        await self._run_tool_cmd("optimize_positions", {
            "symbols": symbols, "objective": "max_sharpe",
        }, f"optimizing {len(symbols)} positions")

    async def cmd_stress(self, args: str):
        """Stress test: /stress <strategy> [symbol]"""
        parts = args.split() if args else ["momentum", "SPY"]
        strategy = parts[0] if parts else "momentum"
        symbol = parts[1].upper() if len(parts) > 1 else "SPY"
        await self._run_tool_cmd("stress_test_strategy", {
            "strategy": strategy, "symbol": symbol,
        }, f"stress test {strategy}/{symbol}")

    async def cmd_factors(self, args: str):
        """Factor analysis: /factors AAPL"""
        symbol = args.strip().upper() or "AAPL"
        await self._run_tool_cmd("calculate_factors", {"symbol": symbol}, f"factors {symbol}")

    async def cmd_compliance(self, args: str):
        """Compliance check: /compliance <strategy>"""
        strategy = args.strip() or "momentum"
        await self._run_tool_cmd("check_strategy_compliance", {
            "strategy": strategy,
        }, f"compliance {strategy}")

    async def cmd_search_web(self, args: str):
        """Web search: /web <query>"""
        query = args.strip()
        if not query:
            console.print("[dim]Usage: /web <search query>[/dim]" if HAS_RICH else "Usage: /web <query>")
            return
        await self._run_tool_cmd("web_search", {"query": query}, f"searching: {query[:30]}")

    # ---- Local mode toggle ----

    def cmd_local(self, args: str):
        """Toggle local-only mode (skip AWS, always use Ollama)."""
        cfg = self.terminal.config
        arg = args.strip().lower()
        if arg in ("on", "1", "true", "yes"):
            cfg["local_mode"] = True
        elif arg in ("off", "0", "false", "no"):
            cfg["local_mode"] = False
        else:
            cfg["local_mode"] = not cfg.get("local_mode", False)
        save_config(cfg)
        state = "ON" if cfg["local_mode"] else "OFF"
        model = cfg.get("model", "qwen2.5:7b")
        if HAS_RICH:
            color = "green" if cfg["local_mode"] else "yellow"
            console.print(f"  [{color}]Local mode {state}[/{color}]  model=[bold]{model}[/bold]  ollama={cfg.get('ollama_url','http://localhost:11434')}")
        else:
            print(f"  Local mode {state}  model={model}")

    # ---- Models list ----

    # ---- MCP server management ----

    async def cmd_mcp(self, args: str):
        """Manage MCP servers: /mcp status | /mcp tools | /mcp reload"""
        if not _HAS_MCP:
            console.print("  [dim]mcp_client.py not available[/dim]" if HAS_RICH else "MCP not available")
            return
        sub = args.strip().lower()
        reg = self.terminal._mcp_registry

        if sub in ("reload", "restart"):
            if reg:
                await reg.stop_all()
            self.terminal._mcp_started = False
            self.terminal._mcp_registry = None
            if HAS_RICH:
                console.print("  [dim]Restarting MCP servers…[/dim]")
            from mcp_client import MCPToolRegistry
            self.terminal._mcp_registry = MCPToolRegistry()
            results = await self.terminal._mcp_registry.start_all()
            n = self.terminal._mcp_registry.register_into(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS, overwrite=True)
            if HAS_RICH:
                console.print(f"  [green]MCP reloaded: {n} tools from {len(results)} servers[/green]")
            return

        if sub == "tools":
            if not reg:
                console.print("  [dim]No MCP servers running[/dim]" if HAS_RICH else "No MCP servers")
                return
            tools = reg.all_tools()
            if HAS_RICH:
                console.print(f"\n  [bold]MCP Tools[/bold] ({len(tools)} total)\n")
                for t in tools:
                    console.print(f"    [bold]{t['qualified_name']:40s}[/bold][dim]{t.get('description','')[:60]}[/dim]")
                console.print()
            else:
                for t in tools:
                    print(f"  {t['qualified_name']:40s} {t.get('description','')[:60]}")
            return

        # Default: status
        if HAS_RICH:
            console.print()
            console.print("  [bold]MCP Servers[/bold]")
            if not _HAS_MCP:
                console.print("  [dim]Not available (mcp_client.py missing)[/dim]")
            elif not reg:
                config_path = str(MCP_CONFIG_PATH)
                console.print(f"  [dim]No servers started. Configure: {config_path}[/dim]")
                console.print(f"  [dim]Example: add quant_engine MCP server pointing to your mcp_server.py[/dim]")
            else:
                for s in reg.status():
                    color = "green" if s["running"] else "red"
                    icon  = "●" if s["running"] else "○"
                    console.print(
                        f"  [{color}]{icon}[/{color}] [bold]{s['name']:20s}[/bold]"
                        f" [dim]{s['tool_count']} tools  {s['description'][:50]}[/dim]"
                    )
            console.print()
        else:
            if not reg:
                print("  No MCP servers. Configure ~/.arthera/mcp_servers.json")
            else:
                for s in reg.status():
                    print(f"  {'●' if s['running'] else '○'} {s['name']:20s} {s['tool_count']} tools")

    # ---- .ariarc project config ----

    def cmd_ariarc(self, args: str):
        """Show or reload .ariarc project configuration."""
        if not _HAS_ARIARC:
            console.print("  [dim]ariarc.py not available[/dim]" if HAS_RICH else "ariarc not available")
            return
        if "reload" in args.lower():
            arc = reload_ariarc()
            self.terminal.ariarc = arc
            if HAS_RICH:
                if arc.found:
                    console.print(f"  [green]ariarc reloaded: {arc.source_path}[/green]")
                else:
                    console.print("  [yellow]No .ariarc found in current directory tree[/yellow]")
            return

        arc = self.terminal.ariarc or get_ariarc()
        if HAS_RICH:
            console.print()
            if not arc.found:
                console.print("  [dim]No .ariarc found (create .ariarc in your project root)[/dim]")
                console.print()
                _example = """{
  "project": "My Quant Strategy",
  "description": "A-share momentum + mean-reversion strategy",
  "market": "cn",
  "default_symbols": ["sh600519", "sh601318", "sz000858"],
  "system_prompt": "Focus on A-share market mechanics and T+1 constraints.",
  "context_files": ["README.md"],
  "auto_context": ["strategy/main.py"],
  "commands": {
    "/morning-cn": "生成A股早盘简报，重点关注 {default_symbols}"
  }
}"""
                console.print(f"  [dim]Example .ariarc:[/dim]\n{_example}")
            else:
                d = arc.to_dict()
                console.print(f"  [bold]Project:[/bold] {arc.project or '(unnamed)'}")
                console.print(f"  [bold]Source:[/bold]  [dim]{d['source_path']}[/dim]")
                console.print(f"  [bold]Market:[/bold]  {arc.market}")
                if arc.default_symbols:
                    console.print(f"  [bold]Symbols:[/bold] {', '.join(arc.default_symbols)}")
                if arc.commands:
                    console.print(f"  [bold]Commands:[/bold] {', '.join(arc.commands.keys())}")
                if arc.tools_blacklist:
                    console.print(f"  [bold]Blocked tools:[/bold] {', '.join(arc.tools_blacklist)}")
                if arc.auto_context:
                    console.print(f"  [bold]Auto context:[/bold] {', '.join(arc.auto_context)}")
            console.print()
        else:
            if arc.found:
                import json as _j
                print(_j.dumps(arc.to_dict(), indent=2, ensure_ascii=False))

    # ---- Local LLM provider status ----

    def cmd_providers(self, args: str):
        """Show all LLM providers: local backends + cloud API status (Open Interpreter style)."""
        if HAS_RICH:
            console.print()

        # ── Section 1: Local backends ────────────────────────────────────────
        try:
            from local_llm_provider import probe_all_backends, BACKEND_DEFAULTS
            results = probe_all_backends()
            current_provider = self.terminal.config.get("local_provider", "ollama")
            # Count Ollama models if online
            _ollama_count = ""
            if results.get("ollama"):
                try:
                    _omodels, _ = detect_ollama_models_rich(
                        self.terminal.config.get("ollama_url", "http://localhost:11434"))
                    _ollama_count = f"  [dim]{len(_omodels)} 个模型[/dim]" if _omodels else ""
                except Exception:
                    pass

            if HAS_RICH:
                console.print("  [bold]本地 Backend[/bold]")
                console.print()
            else:
                print("  == Local Backends ==")

            for name, available in results.items():
                info   = BACKEND_DEFAULTS.get(name, {})
                url    = info.get("default_url", "")
                color  = "green" if available else "dim"
                icon   = "✅" if available else "○"
                active = " ◀ active" if name == current_provider else ""
                extra  = _ollama_count if (name == "ollama" and available) else ""
                if HAS_RICH:
                    console.print(
                        f"  {icon} [{color}]{name:12s}[/{color}]"
                        f" [dim]{url:30s}[/dim]{extra}"
                        f"[green]{active}[/green]"
                    )
                else:
                    status = "✓" if available else "✗"
                    print(f"  {status} {name:12s} {url}{active}")
        except ImportError:
            pass

        # ── Section 2: Cloud provider API keys ───────────────────────────────
        pjson = _load_providers_json()
        _CLOUD_LIST = [
            ("deepseek",    "DeepSeek",   "deepseek/deepseek-chat"),
            ("openai",      "OpenAI",     "openai/gpt-4o"),
            ("groq",        "Groq",       "groq/llama-3.3-70b-versatile"),
            ("anthropic",   "Anthropic",  "anthropic/claude-3-5-sonnet"),
            ("together",    "Together",   "together/meta-llama/Meta-Llama-3-70B"),
            ("siliconflow", "SiliconFlow","siliconflow/deepseek-ai/DeepSeek-V3"),
            ("moonshot",    "Moonshot",   "moonshot/moonshot-v1-8k"),
        ]
        if HAS_RICH:
            console.print()
            console.print("  [bold]Cloud Provider API[/bold]")
            console.print()
        else:
            print()
            print("  == Cloud Providers ==")

        for prov, label, example_model in _CLOUD_LIST:
            env_var = _PROVIDER_KEY_MAP.get(prov, "")
            key = (os.getenv(env_var, "") if env_var else "") or \
                  (pjson.get(prov, {}).get("api_key", "") if isinstance(pjson, dict) else "")
            if key:
                masked = key[:6] + "****" + key[-4:] if len(key) > 10 else "****"
                if HAS_RICH:
                    console.print(f"  🔑 [green]{label:14s}[/green] [dim]{masked}[/dim]")
                else:
                    print(f"  ✓ {label:14s} {masked}")
            else:
                hint = f"/apikey set {prov} <key>"
                if HAS_RICH:
                    console.print(f"  ○ [dim]{label:14s} 未配置  →  {hint}[/dim]")
                else:
                    print(f"  ✗ {label:14s} {hint}")

        # ── Custom endpoint ──────────────────────────────────────────────────
        custom_ep = self.terminal.config.get("custom_endpoint", "")
        custom_m  = self.terminal.config.get("custom_model", "")
        if custom_ep:
            if HAS_RICH:
                console.print()
                console.print(f"  🔧 [bold]Custom endpoint[/bold]  [dim]{custom_ep}[/dim]  model=[cyan]{custom_m or '?'}[/cyan]")
            else:
                print(f"\n  Custom: {custom_ep}  model={custom_m}")

        # ── Data service keys section ─────────────────────────────────────────
        _data_keys = _load_data_keys()
        _DATA_DISPLAY = [
            ("finnhub",      "Finnhub",       "股票+新闻"),
            ("newsapi",      "NewsAPI",        "财经新闻"),
            ("brave",        "Brave Search",   "网页搜索"),
            ("alphavantage", "Alpha Vantage",  "历史数据"),
            ("coingecko",    "CoinGecko Pro",  "加密数据"),
            ("twelvedata",   "Twelve Data",    "全球行情"),
        ]
        if HAS_RICH:
            console.print()
            console.print("  [bold]📊 数据服务 API[/bold]  [dim](后端离线时的本地数据源)[/dim]")
            console.print()
        else:
            print("\n  == Data Service APIs ==")
        for svc, label, desc in _DATA_DISPLAY:
            key_val = _data_keys.get(svc, "")
            if key_val:
                masked = key_val[:6] + "****" + key_val[-4:] if len(key_val) > 10 else "****"
                signup = _DATA_SIGNUP_URLS.get(svc, "")
                if HAS_RICH:
                    console.print(f"  🔑 [green]{label:18s}[/green] [dim]{masked}  {desc}[/dim]")
                else:
                    print(f"  ✓ {label:18s} {masked}")
            else:
                hint   = f"/apikey set {svc} <key>"
                signup = _DATA_SIGNUP_URLS.get(svc, "")
                if HAS_RICH:
                    console.print(f"  ○ [dim]{label:18s} 未配置  →  {hint}[/dim]")
                else:
                    print(f"  ✗ {label:18s} {hint}")

        # ── Free data source registry (akshare / yfinance / tushare) ────────────
        try:
            from datasources.router import DataRouter as _DR
            free_sources = _DR().list_sources()
        except Exception:
            free_sources = []

        if free_sources:
            if HAS_RICH:
                console.print()
                console.print("  [bold]免费行情数据源[/bold]  [dim](datasources/router — no API key required)[/dim]")
                console.print()
            else:
                print("\n  == Free Market Data Sources ==")
            for s in free_sources:
                ok_icon = "[green]✓[/green]" if s["configured"] else "[dim]○[/dim]"
                key_tag = " [dim](no key)[/dim]" if not s["needs_key"] else " [dim](API key)[/dim]"
                mkts    = ", ".join(s.get("markets", []))
                if HAS_RICH:
                    console.print(
                        f"  {ok_icon} [bold]{s['name']:12s}[/bold]  "
                        f"[dim]{mkts:22s}[/dim]{key_tag}"
                    )
                else:
                    ok   = "✓" if s["configured"] else "○"
                    key  = "(no key)" if not s["needs_key"] else "(key)"
                    print(f"  {ok} {s['name']:12s}  {mkts:22s}  {key}")
            if HAS_RICH:
                console.print("  [dim]Config: ~/.aria/datasources.yaml[/dim]")

        if HAS_RICH:
            console.print()
            console.print("  [dim]配置 LLM Key:   /apikey set deepseek <key>[/dim]")
            console.print("  [dim]配置数据 Key:   /apikey set finnhub <key>[/dim]")
            console.print("  [dim]切换模型:       /model deepseek/deepseek-chat[/dim]")
            console.print("  [dim]首次向导:       /setup[/dim]")
            console.print("  [dim]自定义端点:     /config set custom_endpoint=http://...[/dim]")
            console.print()

    # ---- Alibaba Cloud data service config ----

    async def cmd_cloud(self, args: str):
        """
        Manage Alibaba Cloud data service connection.

        Usage:
          /cloud status              — show connection status & circuit breaker state
          /cloud set <url>           — set cloud_api_server URL (e.g. http://your-aliyun-ip:8000)
          /cloud data <url>          — set akshare_data_server URL (e.g. http://your-aliyun-ip:8002)
          /cloud token <jwt-token>   — set API token
          /cloud health              — live health-check both services
          /cloud reset               — reset circuit breakers
        """
        try:
            from aliyun_data_client import AliyunDataClient, save_cloud_config
        except ImportError:
            if HAS_RICH:
                console.print("  [red]aliyun_data_client.py not found[/red]")
            else:
                print("  aliyun_data_client.py not found")
            return

        parts = args.strip().split(None, 2)
        sub   = parts[0].lower() if parts else "status"

        if sub == "set" and len(parts) >= 2:
            url = parts[1]
            save_cloud_config(cloud_url=url)
            AliyunDataClient.reset()
            if HAS_RICH:
                console.print(f"  [green]Cloud API URL set to: {url}[/green]")
                console.print(f"  [dim]Saved to ~/.arthera/config.json[/dim]")
            return

        if sub == "data" and len(parts) >= 2:
            url = parts[1]
            save_cloud_config(data_url=url)
            AliyunDataClient.reset()
            if HAS_RICH:
                console.print(f"  [green]AKShare Data URL set to: {url}[/green]")
                console.print(f"  [dim]Saved to ~/.arthera/config.json[/dim]")
            return

        if sub == "token" and len(parts) >= 2:
            token = parts[1]
            save_cloud_config(api_token=token)
            AliyunDataClient.reset()
            if HAS_RICH:
                console.print(f"  [green]API token saved (length {len(token)})[/green]")
            return

        if sub == "reset":
            AliyunDataClient.reset()
            if HAS_RICH:
                console.print("  [green]Circuit breakers reset, config reloaded[/green]")
            return

        client = AliyunDataClient.get()

        if sub == "health":
            if HAS_RICH:
                console.print("  [dim]Checking health…[/dim]")
            with console.status("[dim]Checking cloud services…[/dim]", spinner="dots") if HAS_RICH else _null_ctx():
                cloud_h = await client.health_cloud()
                data_h  = await client.health_data()

            if HAS_RICH:
                console.print()
                _c = "green" if cloud_h.get("status") == "healthy" else "red"
                _d = "green" if data_h.get("status") not in ("unreachable", None) else "red"
                console.print(f"  [{_c}]● cloud_api_server[/{_c}]  {client.cloud_url}")
                console.print(f"    status: {cloud_h.get('status', '?')}")
                if cloud_h.get("services"):
                    for svc, st in cloud_h["services"].items():
                        icon = "✓" if "online" in str(st) or "ready" in str(st) else "○"
                        console.print(f"    [dim]{icon} {svc}: {st}[/dim]")
                console.print()
                console.print(f"  [{_d}]● akshare_data_server[/{_d}]  {client.data_url}")
                console.print(f"    status: {data_h.get('status', '?')}")
                console.print()
            else:
                print(f"  cloud: {cloud_h.get('status')} | data: {data_h.get('status')}")
            return

        # Default: /cloud status
        st = client.status()
        if HAS_RICH:
            console.print()
            console.print("  [bold]Alibaba Cloud Data Services[/bold]")
            console.print()
            _c = "green" if st["cloud_cb"] == "closed" else "red"
            _d = "green" if st["data_cb"]  == "closed" else "red"
            console.print(f"  [{_c}]●[/{_c}] cloud_api_server   [dim]{st['cloud_url']}[/dim]"
                          f"  [{_c}]{st['cloud_cb']}[/{_c}]")
            console.print(f"  [{_d}]●[/{_d}] akshare_data_server [dim]{st['data_url']}[/dim]"
                          f"  [{_d}]{st['data_cb']}[/{_d}]")
            tok_str = "[green]set[/green]" if st["has_token"] else "[dim]not set[/dim]"
            console.print(f"  Auth token: {tok_str}")
            console.print()
            console.print("  [dim]Configure: /cloud set <url>  /cloud data <url>  /cloud token <jwt>[/dim]")
            console.print("  [dim]Health:    /cloud health[/dim]")
            console.print()
        else:
            print(f"  Cloud: {st['cloud_url']} ({st['cloud_cb']})")
            print(f"  Data:  {st['data_url']} ({st['data_cb']})")
            print(f"  Token: {'set' if st['has_token'] else 'not set'}")

    # ---- AI Signal from cloud ----

    async def cmd_signal(self, args: str):
        """
        AI trading signal (BUY/SELL/HOLD) from Alibaba Cloud.
        Usage: /signal sh600519   /signal AAPL US
        """
        parts  = args.strip().split()
        symbol = parts[0].upper() if parts else "sh600519"
        market = parts[1].upper() if len(parts) > 1 else ("CN" if _is_ashare_symbol(symbol) else "US")
        await self._run_tool_cmd("get_ai_signal", {"symbol": symbol, "market": market},
                                 f"AI signal {symbol}")

    # ---- ML Predictions from cloud ----

    async def cmd_predict(self, args: str):
        """
        ML return predictions for a list of symbols.
        Usage: /predict sh600519 sh601318 sz000858
        """
        parts   = args.strip().split() if args.strip() else ["sh600519"]
        symbols = [s for s in parts if not s.isdigit() or len(s) == 6]
        days    = 5
        for p in parts:
            if p.startswith("d="):
                try:
                    days = int(p[2:])
                except ValueError:
                    pass
        await self._run_tool_cmd("get_predictions",
                                 {"symbols": symbols, "prediction_days": days},
                                 f"ML predict {len(symbols)} stocks")

    # ---- Cloud backtest ----

    async def cmd_cloudbt(self, args: str):
        """
        Full ML-powered backtest on Alibaba Cloud.
        Usage: /cloudbt sh600519 sh601318 [model=lightgbm] [months=12] [freq=weekly] [top=3]
        """
        parts   = args.strip().split() if args.strip() else []
        symbols = []
        kwargs: Dict[str, Any] = {}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                kwargs[k] = v
            else:
                symbols.append(p)
        if not symbols:
            symbols = ["sh600519"]
        params = {
            "symbols":        symbols,
            "model_type":     kwargs.get("model", "lightgbm"),
            "months":         int(kwargs.get("months", 12)),
            "rebalance_freq": kwargs.get("freq", "weekly"),
            "top_k":          int(kwargs.get("top", 3)),
        }
        await self._run_tool_cmd("cloud_backtest", params,
                                 f"cloud backtest {len(symbols)} stocks")

    # ---- Market insights ----

    async def cmd_insights(self, args: str):
        """
        AI market insights for a basket of stocks.
        Usage: /insights sh600519 sh601318 sz000858
        """
        parts   = args.strip().split() if args.strip() else ["sh600519"]
        symbols = parts
        await self._run_tool_cmd("get_market_insights",
                                 {"symbols": symbols},
                                 f"market insights {len(symbols)} stocks")

    # ---- Recommend local models ----

    def cmd_recommend(self, args: str):
        """Recommend best local models for financial analysis."""
        if HAS_RICH:
            console.print()
            console.print("  [bold]Recommended Local Models for Finance[/bold]")
            console.print()
            try:
                available = detect_ollama_models(
                    self.terminal.config.get("ollama_url", "http://localhost:11434")
                )
                for rec in RECOMMENDED_FINANCE_MODELS:
                    model_id = rec["model"]
                    installed = any(a.startswith(model_id.split(":")[0]) for a in available)
                    icon  = "[green]●[/green]" if installed else "[dim]○[/dim]"
                    vram  = rec.get("vram_gb", "?")
                    console.print(
                        f"  {icon} [bold]{model_id:30s}[/bold] "
                        f"[dim]VRAM≈{vram}GB  {rec['reason'][:60]}[/dim]"
                    )
                    if not installed:
                        console.print(f"    [dim]Install: {rec['install']}[/dim]")
                console.print()
            except Exception:
                console.print("  [dim]Could not check installed models[/dim]")
        else:
            for rec in RECOMMENDED_FINANCE_MODELS:
                print(f"  {rec['model']:30s} {rec['reason']}")
                print(f"    Install: {rec['install']}")

    # ---- Finance local tool shortcuts ----

    async def cmd_screen_cn(self, args: str):
        """A股选股筛选器 (local, akshare)."""
        params: Dict[str, Any] = {}
        for tok in args.split():
            if "=" in tok:
                k, v = tok.split("=", 1)
                params[k.strip()] = v.strip()
        tool_name = "screen_ashare"
        if tool_name in LOCAL_TOOLS:
            await self._run_local_tool(tool_name, params, "A股选股筛选")
        else:
            await self.terminal.send_message(f"帮我筛选A股股票，条件：{args or '市值>50亿，非ST，流动性好'}")

    async def cmd_limitup(self, args: str):
        """A股涨停板池."""
        params = {"date": args.strip()} if args.strip() else {}
        tool_name = "get_limit_up_pool"
        if tool_name in LOCAL_TOOLS:
            await self._run_local_tool(tool_name, params, "涨停板池")
        else:
            await self.terminal.send_message("查询今天A股涨停板池，列出涨停股票和连续涨停次数")

    async def cmd_north(self, args: str):
        """北向资金净流入."""
        params = {"days": int(args.strip())} if args.strip().isdigit() else {"days": 10}
        tool_name = "get_northbound_flow"
        if tool_name in LOCAL_TOOLS:
            await self._run_local_tool(tool_name, params, "北向资金")
        else:
            await self.terminal.send_message("查询最近10天北向资金（沪深港通）净买入情况")

    async def cmd_optimize_port(self, args: str):
        """Portfolio weight optimisation."""
        symbols = [s.strip().upper() for s in args.split() if s.strip()]
        if not symbols:
            console.print("  [dim]Usage: /optimize-port AAPL MSFT GOOGL [method=max_sharpe][/dim]" if HAS_RICH
                          else "Usage: /optimize-port AAPL MSFT [method=max_sharpe]")
            return
        # Check if last token is method=X
        method = "max_sharpe"
        if symbols and "=" in symbols[-1]:
            k, v = symbols.pop().split("=", 1)
            if k == "method":
                method = v
        params = {"symbols": symbols, "method": method}
        tool_name = "optimize_positions"
        if tool_name in LOCAL_TOOLS:
            await self._run_local_tool(tool_name, params, f"优化持仓 {method}")
        else:
            await self.terminal.send_message(
                f"帮我用 {method} 方法优化以下资产组合的仓位权重：{', '.join(symbols)}"
            )

    async def _run_local_tool(self, tool_name: str, params: dict, label: str = ""):
        """Run a LOCAL_TOOLS entry, display result with Rich formatting."""
        if tool_name not in LOCAL_TOOLS:
            if HAS_RICH:
                console.print(f"  [dim]Tool {tool_name!r} not available[/dim]")
            return
        handler, _ = LOCAL_TOOLS[tool_name]
        label_text = label or tool_name
        if HAS_RICH:
            with console.status(f"[dim]{label_text}…[/dim]", spinner="dots"):
                result = handler(params)
        else:
            print(f"  {label_text}…")
            result = handler(params)

        if not result.get("success", True):
            err = result.get("error", "unknown error")
            if HAS_RICH:
                console.print(f"  [red]Error:[/red] {err}")
            else:
                print(f"  Error: {err}")
            return

        # Pretty-print result
        _print_tool_result(tool_name, result, elapsed=0)

    # ════════════════════════════════════════════════════════════════════════
    # 金融 Agent 团队命令
    # ════════════════════════════════════════════════════════════════════════

    async def cmd_team(self, args: str):
        """
        多 Agent 金融研究团队：宏观 + 基本面 + 技术 + 风控 → 综合报告
        Usage: /team NVDA
               /team 000333 --agents technical,risk
               /team watchlist
        """
        import sys as _sys
        parts = args.strip().split()

        # 解析参数
        agent_names = None
        symbols_raw = []
        i = 0
        while i < len(parts):
            if parts[i] == "--agents" and i + 1 < len(parts):
                agent_names = [a.strip() for a in parts[i+1].split(",")]
                i += 2
            else:
                symbols_raw.append(parts[i])
                i += 1

        if not symbols_raw or symbols_raw[0].lower() == "watchlist":
            symbols = self.terminal.config.get("watchlist", ["AAPL", "MSFT", "NVDA"])[:3]
        else:
            symbols = [p.upper() for p in symbols_raw[:3]]

        # 优先使用新 agents/team.py，回退到旧 financial_agents.py
        _use_new_agents = False
        try:
            from agents.team import run_team as _new_run_team
            from agents.registry import get_registry as _get_reg
            from providers.llm.registry import get_provider as _get_prov
            from datasources.router import get_router as _get_ds
            _use_new_agents = True
        except ImportError:
            pass

        for sym in symbols:
            if HAS_RICH:
                console.print()
                console.print(f"  [bold cyan]━━━ /team {sym} ━━━[/bold cyan]")
                console.print()
            else:
                print(f"\n  ━━━ /team {sym} ━━━\n")

            if _use_new_agents:
                # ── 新 Agent 系统（无 Ollama 依赖）────────────────────────
                tokens = []
                def _on_tok(t):
                    tokens.append(t)
                    _sys.stdout.write(t); _sys.stdout.flush()

                def _on_agent_done(name, result):
                    icon = "✅" if result.success else "⚠️ "
                    msg  = f"  {icon} [{name}] {result.signal} ({result.confidence:.0%})"
                    if HAS_RICH:
                        console.print(msg)
                    else:
                        print(msg)

                # LLM provider — prefer local Ollama, fall back to cloud
                _llm = None
                try:
                    from providers.llm.registry import list_available_providers
                    all_avail = [p for p in list_available_providers() if p["available"]]
                    # Prefer local (Ollama) → avoids API costs for team analysis
                    local_avail  = [p for p in all_avail if p["local"]]
                    cloud_avail  = [p for p in all_avail if not p["local"]]
                    chosen = (local_avail or cloud_avail)
                    if chosen:
                        _llm = _get_prov(chosen[0]["name"])
                except Exception:
                    pass

                try:
                    team_result = await _new_run_team(
                        symbol     = sym,
                        agents     = agent_names,
                        llm_provider = _llm,
                        data_router  = _get_ds(),
                        on_token     = _on_tok,
                        on_agent_done= _on_agent_done,
                    )
                    print()  # 换行
                    if HAS_RICH:
                        console.print(f"\n  [dim]耗时 {team_result.elapsed_sec:.1f}s  "
                                      f"Signal: [bold]{team_result.final_signal}[/bold]  "
                                      f"置信度: {team_result.confidence:.0%}[/dim]")
                    else:
                        print(f"\n  耗时 {team_result.elapsed_sec:.1f}s  "
                              f"Signal: {team_result.final_signal}  "
                              f"置信度: {team_result.confidence:.0%}")

                    # 保存报告
                    await self._save_team_report(sym, team_result)

                except Exception as e:
                    msg = f"团队分析失败: {e}"
                    console.print(f"\n  [red]{msg}[/red]") if HAS_RICH else print(f"\n  {msg}")

            elif _HAS_AGENTS:
                # ── 旧 Ollama Agent（兜底）────────────────────────────────
                ollama_url = self.terminal.config.get("ollama_url", "http://localhost:11434")
                model      = self.terminal.config.get("model", "qwen2.5:7b")
                tokens = []
                def on_token(tok):
                    tokens.append(tok)
                    _sys.stdout.write(tok); _sys.stdout.flush()
                try:
                    result = await _run_team(
                        symbol=sym, ollama_url=ollama_url, model=model,
                        portfolio_symbols=self.terminal.config.get("watchlist",[]),
                        on_token=on_token,
                    )
                except Exception as e:
                    print(f"\n  Error: {e}")
            else:
                print("  agents 模块未找到，请检查 apps/cli/agents/ 目录")

    async def _save_team_report(self, symbol: str, team_result) -> None:
        """将 /team 分析结果保存为 Markdown 报告"""
        import pathlib as _pl
        from datetime import datetime as _dt
        out_dir = _pl.Path.home() / "Desktop" / "Arthera" / "reports" / "team"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts      = _dt.now().strftime("%Y%m%d_%H%M")
        out_f   = out_dir / f"{symbol}_team_{ts}.md"

        lines = [
            f"# {symbol} 多 Agent 研究报告",
            f"> 生成时间: {_dt.now():%Y-%m-%d %H:%M}  |  最终信号: **{team_result.final_signal}**"
            f"  |  置信度: {team_result.confidence:.0%}  |  耗时: {team_result.elapsed_sec:.1f}s",
            "", "---", "",
        ]
        for r in team_result.results:
            if r.success:
                lines += [
                    f"## {r.agent.upper()} ({r.signal}, {r.confidence:.0%})",
                    "",
                    r.analysis or "*(无分析文本)*",
                    "",
                ]
        lines += ["---", "", "## 综合结论", "", team_result.synthesis or "*(无综合结论)*", ""]
        out_f.write_text("\n".join(lines), encoding="utf-8")
        msg = f"  📄 报告已保存: {out_f}"
        console.print(f"  [dim]{msg}[/dim]") if HAS_RICH else print(msg)

    async def cmd_chart(self, args: str):
        """
        生成股票分析图表（HTML，含K线/均线/RSI/MACD）。
        Usage: /chart AAPL
               /chart 600519   (A股，用6位代码)
               /chart BTC-USD
        """
        symbol = args.strip().upper() or "AAPL"
        msg = f"生成 {symbol} 分析图表..."
        if HAS_RICH:
            with console.status(f"[dim]{msg}[/dim]", spinner="dots"):
                result = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _generate_chart_sync(symbol)
                )
        else:
            print(f"  {msg}")
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _generate_chart_sync(symbol)
            )

        if result.get("success"):
            path = result.get("chart_path", "")
            if HAS_RICH:
                console.print(f"\n  ✅ 图表已生成: [link={path}]{path}[/link]")
                console.print(f"  [dim]浏览器打开: open \"{path}\"[/dim]")
            else:
                print(f"\n  ✅ 图表已生成: {path}")
                print(f"  浏览器打开: open \"{path}\"")
            import subprocess, sys
            try:
                subprocess.Popen(["open", path])
            except Exception:
                pass
        else:
            err = result.get("error") or result.get("response", "未知错误")
            _print_error(f"图表生成失败: {err[:120]}")

    async def cmd_report(self, args: str):
        """生成综合投资报告（图表 + 多 Agent 分析 → HTML / Markdown 文件）。

        Usage:
            /report AAPL
            /report 000333
            /report AAPL --format md      # Markdown 投研报告（离线可用）
            /report AAPL --type deep      # 深度研报（8页）
            /report AAPL --type brief     # 简评（1页）
        """
        import pathlib as _pl
        from datetime import datetime as _dt
        import re as _re_rpt

        parts = args.split()
        symbol = "AAPL"
        fmt = "html"
        report_type = "standard"
        skip_next = False
        for i, p in enumerate(parts):
            if skip_next:
                skip_next = False
                continue
            if p.startswith("--format="):
                fmt = p.split("=", 1)[1].lower()
            elif p == "--format" and i + 1 < len(parts):
                fmt = parts[i + 1].lower()
                skip_next = True
            elif p.startswith("--type="):
                report_type = p.split("=", 1)[1].lower()
            elif p == "--type" and i + 1 < len(parts):
                report_type = parts[i + 1].lower()
                skip_next = True
            elif not p.startswith("-"):
                symbol = p.upper()

        out_dir = _pl.Path.home() / "Desktop" / "Arthera" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M")

        # ── Markdown report mode (works fully offline) ────────────────────────
        if fmt in ("md", "markdown"):
            console.print(f"\n  📄 生成 [bold]{symbol}[/bold] Markdown 投研报告 ({report_type})...") if HAS_RICH else print(f"\n  Generating {symbol} Markdown report...")

            # Fetch real data
            mdc_data = {}
            if _HAS_MDC:
                try:
                    mdc = _get_mdc()
                    q = mdc.quote(symbol)
                    ti = mdc.technical_indicators(symbol, days=120)
                    mdc_data = {**q, **ti}
                except Exception:
                    pass

            price    = mdc_data.get("price", "N/A")
            chg      = mdc_data.get("change_pct", 0)
            rsi      = mdc_data.get("rsi", "N/A")
            macd     = mdc_data.get("macd", "N/A")
            ma20     = mdc_data.get("ma20", "N/A")
            ma60     = mdc_data.get("ma60", "N/A")
            bb_upper = mdc_data.get("bb_upper", "N/A")
            bb_lower = mdc_data.get("bb_lower", "N/A")
            sign     = "+" if isinstance(chg, (int, float)) and chg >= 0 else ""

            depth_prompt = (
                "深度（8页）版本：包含估值模型（DCF + 相对估值）、财务分析（3年P&L）、管理层分析、行业竞争格局" if report_type == "deep"
                else "简评版本：1页，核心观点 + 关键数据 + 1句话结论" if report_type == "brief"
                else "标准版本：封面、技术分析、基本面概览、风险因素"
            )

            ai_prompt = (
                f"为 {symbol} 生成一份专业 Markdown 投研报告（{depth_prompt}）。\n\n"
                f"**实时数据（必须使用这些数字）**：\n"
                f"- 当前价: {price}  涨跌: {sign}{chg:.2f}%\n"
                f"- RSI(14): {rsi}  MACD: {macd}\n"
                f"- MA20: {ma20}  MA60: {ma60}\n"
                f"- 布林上轨: {bb_upper}  布林下轨: {bb_lower}\n\n"
                f"报告结构（Markdown）：\n"
                f"# {symbol} 投资研究报告\n"
                f"**评级**: 买入/中性/减持  **目标价**: X.XX  **日期**: {_dt.now().strftime('%Y-%m-%d')}\n\n"
                f"## 核心观点\n"
                f"## 技术面分析\n"
                f"## 基本面概况\n"
                f"## 风险因素\n"
                f"## 投资建议\n\n"
                f"请用真实数据，不要使用占位符，用中文输出。"
            )

            await self.terminal.send_message(ai_prompt)

            # Extract last AI response and save as markdown
            last_ai = next(
                (m["content"] for m in reversed(self.terminal.conversation)
                 if m.get("role") == "assistant"), ""
            )
            if last_ai:
                out_f = out_dir / f"{symbol}_report_{ts}.md"
                # Clean up any market data injection blocks
                clean = _re_rpt.sub(r'\n*## 📊.*?(?=\n#|\Z)', '', last_ai, flags=_re_rpt.DOTALL).strip()
                out_f.write_text(clean, encoding="utf-8")
                if HAS_RICH:
                    console.print(f"\n  [green]✅ 报告已保存: {out_f}[/green]")
                    console.print(f"  [dim]预览: open {out_f}[/dim]\n")
                else:
                    print(f"\n  Saved: {out_f}")
            return

        import pathlib as _pl
        from datetime import datetime as _dt

        out_f   = out_dir / f"{symbol}_report_{ts}.html"

        if HAS_RICH:
            console.print(f"\n  🔍 正在生成 [bold]{symbol}[/bold] 综合报告...")
        else:
            print(f"\n  正在生成 {symbol} 综合报告...")

        # 1. 图表
        chart_html = ""
        chart_result = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _generate_chart_sync(symbol)
        )
        if chart_result.get("success"):
            chart_path = chart_result.get("chart_path", "")
            try:
                with open(chart_path, encoding="utf-8") as f:
                    raw = f.read()
                # 提取 chart div + script
                import re as _re
                body = _re.search(r"<main>(.*?)</main>", raw, _re.DOTALL)
                chart_html = body.group(1) if body else raw[raw.find("<body>"):]
            except Exception:
                chart_html = f'<p><a href="{chart_path}" target="_blank">打开图表</a></p>'
        else:
            chart_html = f'<p style="color:#888">图表获取失败: {chart_result.get("error","")}</p>'

        # 2. Agent 分析
        agent_html = ""
        try:
            from agents.team import run_team as _new_run_team
            from datasources.router import get_router as _get_ds
            team_result = await _new_run_team(
                symbol=symbol, data_router=_get_ds()
            )
            agent_lines = []
            for r in team_result.results:
                if r.success:
                    bg = {"BUY":"#e8f5e9","SELL":"#fce4ec","HOLD":"#f5f5f5",
                          "REDUCE":"#fff8e1","STRONG_BUY":"#c8e6c9"}.get(r.signal,"#f5f5f5")
                    agent_lines.append(
                        f'<div style="background:{bg};border-radius:8px;padding:12px;margin:8px 0">'
                        f'<strong>{r.agent.upper()}</strong> &nbsp; '
                        f'<span style="color:#555">{r.signal} ({r.confidence:.0%})</span>'
                        f'<hr style="margin:6px 0;opacity:.2">'
                        f'<pre style="white-space:pre-wrap;font-size:13px;margin:0">'
                        f'{r.analysis[:600] if r.analysis else "(无分析)"}</pre></div>'
                    )
            synthesis_html = (
                f'<div style="background:#e3f2fd;border-radius:8px;padding:14px;margin-top:12px">'
                f'<strong>综合结论</strong>: {team_result.final_signal} '
                f'(置信度 {team_result.confidence:.0%})<hr style="margin:6px 0;opacity:.2">'
                f'<pre style="white-space:pre-wrap;font-size:13px;margin:0">'
                f'{team_result.synthesis[:800] if team_result.synthesis else ""}</pre></div>'
            )
            agent_html = "\n".join(agent_lines) + synthesis_html
        except Exception as e:
            agent_html = f'<p style="color:#888">Agent 分析失败: {e}</p>'

        # 3. 输出 HTML
        from datetime import datetime as _dt2
        html = f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{symbol} — Aria 综合投资报告</title>
<style>
  body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
        background:#f7f8fa;color:#17202a}}
  .wrap{{max-width:1100px;margin:0 auto;padding:28px}}
  h1{{margin:0 0 4px;font-size:26px}}
  .meta{{color:#667085;font-size:13px;margin-bottom:20px}}
  .section{{background:#fff;border:1px solid #e5e7eb;border-radius:10px;
            padding:20px;margin-bottom:20px}}
  h2{{font-size:16px;margin:0 0 12px;color:#374151}}
  pre{{font-family:"SF Mono","Menlo",monospace;font-size:12px}}
  .footer{{text-align:center;color:#999;font-size:12px;margin-top:20px}}
</style></head>
<body><div class="wrap">
  <h1>{symbol} 综合投资报告</h1>
  <p class="meta">生成时间: {_dt2.now():%Y-%m-%d %H:%M} &nbsp;|&nbsp; Aria Code</p>
  <div class="section"><h2>📈 技术图表</h2>{chart_html}</div>
  <div class="section"><h2>🤖 多 Agent 分析</h2>{agent_html}</div>
  <p class="footer">⚠️ 本报告仅供参考，不构成投资建议。</p>
</div></body></html>"""

        out_f.write_text(html, encoding="utf-8")
        path = str(out_f)
        if HAS_RICH:
            console.print(f"\n  ✅ 报告已保存: [link={path}]{path}[/link]")
        else:
            print(f"\n  ✅ 报告已保存: {path}")
        import subprocess
        try:
            subprocess.Popen(["open", path])
        except Exception:
            pass

    async def cmd_shortterm(self, args: str):
        """
        运行 A股短线分析（日线级别，3-15交易日）并输出报告。
        Usage: /shortterm
               /shortterm 000333 601138 300750
        """
        import subprocess, sys as _sys
        _base = pathlib.Path(__file__).parent.parent.parent / "research" / "shortterm"
        script = _base / "run_shortterm.py"
        if not script.exists():
            _print_error(f"短线分析脚本未找到: {script}")
            return
        codes = args.strip().split()
        cmd   = [_sys.executable, str(script)]
        if codes:
            cmd += ["--code"] + codes
        if HAS_RICH:
            console.print("\n  📊 运行短线分析...\n")
        else:
            print("\n  📊 运行短线分析...\n")
        result = subprocess.run(cmd, text=True, capture_output=False)
        if result.returncode != 0:
            _print_error("短线分析执行失败，请检查 research/shortterm/")

    async def cmd_longterm(self, args: str):
        """
        运行 A股长线分析（月线级别，3-18个月目标）并输出报告。
        Usage: /longterm
               /longterm --quick   (只分析 core 级标的)
               /longterm 600519 000858
        """
        import subprocess, sys as _sys
        _base = pathlib.Path(__file__).parent.parent.parent / "research" / "longterm"
        script = _base / "run_longterm.py"
        if not script.exists():
            _print_error(f"长线分析脚本未找到: {script}")
            return
        parts = args.strip().split()
        cmd   = [_sys.executable, str(script)]
        if "--quick" in parts:
            cmd.append("--quick")
            parts.remove("--quick")
        if parts:
            cmd += ["--code"] + parts
        if HAS_RICH:
            console.print("\n  📈 运行长线分析...\n")
        else:
            print("\n  📈 运行长线分析...\n")
        result = subprocess.run(cmd, text=True, capture_output=False)
        if result.returncode != 0:
            _print_error("长线分析执行失败，请检查 research/longterm/")

    async def cmd_indices(self, args: str):
        """全球主要指数实时行情."""
        if not _HAS_MDC:
            console.print("  [dim]market_data_client 未加载[/dim]" if HAS_RICH else "market_data_client not loaded")
            return
        mdc = _get_mdc()
        if HAS_RICH:
            with console.status("[dim]获取全球指数...[/dim]", spinner="dots"):
                r = mdc.indices()
        else:
            print("  获取全球指数...")
            r = mdc.indices()

        if not r.get("success"):
            console.print(f"  [red]{r.get('error','failed')}[/red]" if HAS_RICH else r.get('error'))
            return

        if HAS_RICH:
            console.print()
            console.print("  [bold]全球指数行情[/bold]  "
                          f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]")
            console.print()
            for name, d in r["indices"].items():
                chg = d.get("change_pct", 0)
                color = "green" if chg >= 0 else "red"
                sign  = "+" if chg >= 0 else ""
                console.print(
                    f"  [bold]{name:<14s}[/bold]"
                    f"  {str(d.get('price','')):<12}"
                    f"  [{color}]{sign}{chg:.2f}%[/{color}]"
                )
            console.print()
        else:
            for name, d in r["indices"].items():
                chg = d.get("change_pct", 0)
                sign = "+" if chg >= 0 else ""
                print(f"  {name:<16} {str(d.get('price','')):<12} {sign}{chg:.2f}%")

    async def cmd_hot(self, args: str):
        """热门/活跃股票榜单.  Usage: /hot [cn|us] [top=20]"""
        if not _HAS_MDC:
            console.print("  [dim]market_data_client 未加载[/dim]" if HAS_RICH else "market_data_client not loaded")
            return
        parts  = args.strip().lower().split()
        market = "us" if "us" in parts else "cn"
        top_n  = 20
        for p in parts:
            if p.startswith("top="):
                try: top_n = int(p.split("=")[1])
                except: pass

        mdc = _get_mdc()
        if HAS_RICH:
            with console.status(f"[dim]获取{market.upper()}热门股...[/dim]", spinner="dots"):
                r = mdc.hot_stocks(market=market, top_n=top_n)
        else:
            r = mdc.hot_stocks(market=market, top_n=top_n)

        if not r.get("success"):
            console.print(f"  [red]{r.get('error','failed')}[/red]" if HAS_RICH else r.get('error'))
            return

        stocks = r.get("stocks", [])
        if HAS_RICH:
            console.print()
            console.print(f"  [bold]{market.upper()} 热门股 Top {len(stocks)}[/bold]  "
                          f"[dim]provider: {r.get('provider','')}[/dim]")
            console.print()
            for i, s in enumerate(stocks, 1):
                sym  = s.get("code") or s.get("symbol","")
                name = s.get("name", sym)
                p    = s.get("price", "-")
                chg  = s.get("change_pct", 0)
                color = "green" if chg >= 0 else "red"
                sign  = "+" if chg >= 0 else ""
                console.print(
                    f"  [dim]{i:2d}.[/dim] [bold]{name:<8s}[/bold] "
                    f"[dim]{sym:<8s}[/dim] {str(p):<8} "
                    f"[{color}]{sign}{chg:.2f}%[/{color}]"
                )
            console.print()
        else:
            for s in stocks:
                sym = s.get("code") or s.get("symbol","")
                print(f"  {s.get('name',sym):<10} {sym:<8} {s.get('price','-'):<8} {s.get('change_pct',0):+.2f}%")

    async def cmd_ta(self, args: str):
        """技术指标分析.  Usage: /ta NVDA [days=120]"""
        if not _HAS_MDC:
            console.print("  [dim]market_data_client 未加载[/dim]" if HAS_RICH else "market_data_client not loaded")
            return
        parts  = args.strip().split()
        symbol = parts[0].upper() if parts else "AAPL"
        days   = 120
        for p in parts[1:]:
            if p.startswith("days="):
                try: days = int(p.split("=")[1])
                except: pass

        mdc = _get_mdc()
        if HAS_RICH:
            with console.status(f"[dim]计算 {symbol} 技术指标...[/dim]", spinner="dots"):
                r = mdc.technical_indicators(symbol, days=days)
        else:
            r = mdc.technical_indicators(symbol, days=days)

        if not r.get("success"):
            console.print(f"  [red]{r.get('error','failed')}[/red]" if HAS_RICH else r.get('error'))
            return

        rsi = r.get("rsi")
        bb_pos = r.get("bb_position", 0.5)

        def rsi_color(v):
            if v is None: return "dim"
            return "red" if v > 70 else "green" if v < 30 else "white"

        def macd_color(v):
            return "green" if v and v > 0 else "red"

        if HAS_RICH:
            console.print()
            console.print(f"  [bold]{symbol}[/bold] 技术指标  "
                          f"[dim]{days}日数据  "
                          f"provider:{r.get('provider','')}[/dim]")
            console.print()
            console.print(f"  当前价格  [bold]{r.get('price','N/A')}[/bold]")
            console.print(
                f"  RSI(14)  [{rsi_color(rsi)}]{rsi if rsi else 'N/A'}[/{rsi_color(rsi)}]"
                f"  {'超买⚠️' if rsi and rsi>70 else '超卖⚠️' if rsi and rsi<30 else '中性'}"
            )
            mh = r.get("macd_hist", 0)
            console.print(
                f"  MACD     {r.get('macd','N/A')}  Signal:{r.get('macd_signal','N/A')}  "
                f"[{macd_color(mh)}]Hist:{mh}  "
                f"{'金叉↑' if mh and mh>0 else '死叉↓'}[/{macd_color(mh)}]"
            )
            console.print(f"  布林带   上:{r.get('bb_upper','N/A')}  "
                          f"中:{r.get('bb_mid','N/A')}  下:{r.get('bb_lower','N/A')}  "
                          f"位置:{bb_pos:.2f}")
            console.print()
            for ma in ["ma5","ma10","ma20","ma60","ma120"]:
                if r.get(ma):
                    console.print(f"  {ma.upper():<7} {r[ma]}", end="  ")
            console.print()
            console.print()
        else:
            print(f"\n  {symbol} 技术指标")
            print(f"  价格: {r.get('price')}  RSI: {rsi}  MACD_hist: {r.get('macd_hist')}")
            for ma in ["ma5","ma10","ma20","ma60"]:
                if r.get(ma): print(f"  {ma.upper()}: {r[ma]}", end="  ")
            print()

    # ════════════════════════════════════════════════════════════════════════
    # 策略金库命令
    # ════════════════════════════════════════════════════════════════════════

    async def cmd_strategy(self, args: str):
        """
        策略版本管理系统 (Strategy Vault)

        /strategy save [name] [message]   — 保存当前对话中最后一段代码
        /strategy list [name]             — 列出所有版本
        /strategy diff [name] [v1] [v2]   — 查看版本差异
        /strategy load [name] [tag/id]    — 加载版本到上下文
        /strategy review                  — AI审查+静态检测
        """
        if not _HAS_VAULT:
            console.print("  [yellow]strategy_vault.py 未找到[/yellow]" if HAS_RICH
                          else "  strategy_vault.py not found")
            return

        parts = args.strip().split(None, 3)
        sub   = parts[0].lower() if parts else "list"

        vault = _get_vault()

        # ── save ──────────────────────────────────────────────────────────
        if sub == "save":
            # 从对话历史中提取最后一段 Python 代码
            code = self._extract_last_code()
            if not code:
                if HAS_RICH:
                    console.print("  [yellow]未在对话中找到代码块。先让 Aria 生成策略代码。[/yellow]")
                else:
                    print("  No code found in conversation. Generate strategy code first.")
                return
            name    = parts[1] if len(parts) > 1 and not parts[1].startswith('"') else "strategy"
            message = " ".join(parts[2:]).strip('"') if len(parts) > 2 else ""
            sv = vault.save(code, name=name, message=message)
            if HAS_RICH:
                console.print(
                    f"\n  [green]✓[/green] 策略已保存  "
                    f"[bold]{sv.name}[/bold] [dim]{sv.version_tag}[/dim]  "
                    f"hash={sv.code_hash}  {sv.created_at[:16]}"
                )
            else:
                print(f"  Saved: {sv.name} {sv.version_tag} ({sv.created_at[:16]})")

        # ── list ──────────────────────────────────────────────────────────
        elif sub == "list":
            name = parts[1] if len(parts) > 1 else None
            if name:
                versions = vault.list(name)
                title = f"  策略: {name}"
            else:
                # Show all strategies
                all_names = vault.list_all_names()
                if not all_names:
                    console.print("  [dim]策略金库为空。使用 /strategy save 保存策略。[/dim]" if HAS_RICH
                                  else "  Vault is empty.")
                    return
                if HAS_RICH:
                    console.print("\n  [bold]策略金库[/bold]\n")
                    for n in all_names:
                        vs = vault.list(n, limit=3)
                        latest = vs[0] if vs else None
                        if latest:
                            bt = ""
                            if latest.backtest_result:
                                br = latest.backtest_result
                                bt = f"  sharpe={br.get('sharpe_ratio','?')} ret={br.get('total_return_pct','?')}%"
                            console.print(
                                f"  [bold]{n}[/bold]  [dim]{len(vs)}个版本  "
                                f"最新:{latest.version_tag}  {latest.created_at[:10]}{bt}[/dim]"
                            )
                    console.print()
                else:
                    for n in all_names:
                        print(f"  {n}")
                return
            if not versions:
                console.print(f"  [dim]没有找到策略 '{name}'[/dim]" if HAS_RICH else f"  Not found: {name}")
                return
            if HAS_RICH:
                console.print(f"\n  [bold]{title}[/bold]\n")
                for v in versions:
                    bt = ""
                    if v.backtest_result:
                        br = v.backtest_result
                        sharpe = br.get("sharpe_ratio")
                        ret    = br.get("total_return_pct")
                        bt = f"  [green]sharpe={sharpe:.2f}  ret={ret:.1f}%[/green]" if sharpe else ""
                    reviewed = "  [dim]✓reviewed[/dim]" if v.review_result else ""
                    msg = f"  [dim]{v.message[:50]}[/dim]" if v.message else ""
                    console.print(
                        f"  [dim]{v.id:4d}[/dim]  [bold]{v.version_tag}[/bold]  "
                        f"[dim]{v.created_at[:16]}[/dim]{msg}{bt}{reviewed}"
                    )
                console.print()
            else:
                for v in versions:
                    print(v.summary_line())

        # ── diff ──────────────────────────────────────────────────────────
        elif sub == "diff":
            name  = parts[1] if len(parts) > 1 else "strategy"
            tag_a = parts[2] if len(parts) > 2 else None
            tag_b = parts[3] if len(parts) > 3 else None
            diff_text = vault.diff(name, tag_a, tag_b)
            if HAS_RICH:
                console.print()
                # Simple color: + lines green, - lines red
                for line in diff_text.splitlines():
                    if line.startswith("+++") or line.startswith("---"):
                        console.print(f"  [bold]{line}[/bold]")
                    elif line.startswith("+"):
                        console.print(f"  [green]{line}[/green]")
                    elif line.startswith("-"):
                        console.print(f"  [red]{line}[/red]")
                    elif line.startswith("@@"):
                        console.print(f"  [cyan]{line}[/cyan]")
                    else:
                        console.print(f"  {line}")
                console.print()
            else:
                print(diff_text)

        # ── load ──────────────────────────────────────────────────────────
        elif sub == "load":
            name    = parts[1] if len(parts) > 1 else "strategy"
            tag     = parts[2] if len(parts) > 2 else None
            version = vault.load(name, version_tag=tag)
            if not version:
                console.print(f"  [red]未找到: {name} {tag or '(latest)'}[/red]" if HAS_RICH
                              else f"  Not found: {name} {tag}")
                return
            # Inject code into conversation context as a user message
            code_msg = f"以下是策略 {version.name} {version.version_tag} 的代码：\n\n```python\n{version.code}\n```"
            self.terminal.conversation.append({"role": "assistant", "content": code_msg})
            if HAS_RICH:
                console.print(
                    f"\n  [green]✓[/green] 已加载 [bold]{version.name} {version.version_tag}[/bold]  "
                    f"[dim]{len(version.code)} chars  {version.created_at[:16]}[/dim]"
                )
                console.print(f"  [dim]{version.message}[/dim]" if version.message else "")
                lines = version.code.count("\n")
                console.print(f"  [dim]代码 {lines} 行已注入上下文，可继续对话修改。[/dim]")
            else:
                print(f"  Loaded: {version.name} {version.version_tag}")

        # ── review ────────────────────────────────────────────────────────
        elif sub == "review":
            name    = parts[1] if len(parts) > 1 else "strategy"
            tag     = parts[2] if len(parts) > 2 else None
            version = vault.load(name, version_tag=tag)
            if not version:
                code = self._extract_last_code()
                if not code:
                    console.print("  [yellow]未找到策略，请先 /strategy save 或生成代码[/yellow]" if HAS_RICH
                                  else "  No strategy found.")
                    return
                ver_id = None
            else:
                code   = version.code
                ver_id = version.id

            if HAS_RICH:
                console.print()
                console.print("  [bold]🔬 策略审查中...[/bold]")
                console.print()

            ollama_url = self.terminal.config.get("ollama_url", "http://localhost:11434")
            model      = self.terminal.config.get("model", "qwen2.5:7b")
            bt_result  = version.backtest_result if version else None

            import sys
            def on_token(tok):
                sys.stdout.write(tok)
                sys.stdout.flush()

            review = await _ai_review(code, bt_result, ollama_url, model, on_token=on_token)

            # Print static results
            static = review.get("static", {})
            if HAS_RICH:
                console.print()
                console.print(f"\n  [bold]静态检测[/bold]  评级:{static.get('grade','?')}  "
                              f"{static.get('summary','')}")
                for e in static.get("errors", []):
                    console.print(f"  [red]❌ {e['detail']}[/red]")
                for w in static.get("warnings", []):
                    console.print(f"  [yellow]⚠️  {w['detail']}[/yellow]")
                for q in static.get("quality_checks", []):
                    console.print(f"  [dim]💡 {q}[/dim]")
                console.print()
            else:
                print(f"\n  Static: {static.get('summary','')}")

            if ver_id:
                vault.save_review(ver_id, review)
                if HAS_RICH:
                    console.print("  [dim]审查结果已保存到策略金库[/dim]")

        else:
            if HAS_RICH:
                console.print(
                    "\n  [bold]Strategy Vault 命令[/bold]\n\n"
                    "  /strategy save [name] [message]   保存当前代码快照\n"
                    "  /strategy list [name]              列出版本历史\n"
                    "  /strategy diff [name] [v1] [v2]   查看版本差异\n"
                    "  /strategy load [name] [tag]        加载版本到上下文\n"
                    "  /strategy review [name] [tag]      AI + 静态代码审查\n"
                )
            else:
                print("  Usage: /strategy save|list|diff|load|review [name] [tag]")

    def _extract_last_code(self) -> str:
        """从对话历史中提取最后一段 Python 代码块."""
        import re
        for msg in reversed(self.terminal.conversation):
            content = msg.get("content", "")
            # Match ```python ... ``` blocks
            matches = re.findall(r"```(?:python)?\n(.*?)```", content, re.DOTALL)
            if matches:
                # Return the longest code block
                return max(matches, key=len)
        return ""

    # ---- ORCL analysis ----

    async def cmd_orcl(self, args: str):
        """Oracle Corporation (ORCL) analysis."""
        deep = "deep" in args.lower()
        if deep:
            prompt = (
                "Perform a comprehensive multi-factor analysis of Oracle Corporation (ORCL):\n"
                "1. Technical: trend, RSI, MACD, key support/resistance levels\n"
                "2. Fundamental: revenue growth, cloud transition progress, margins, PE vs peers (MSFT, SAP, NOW)\n"
                "3. Competitive: OCI vs AWS/Azure/GCP market share, Autonomous DB moat\n"
                "4. AI angle: Oracle's AI infrastructure deals (NVIDIA partnership, xAI, OpenAI cloud)\n"
                "5. Risks: debt load from cloud capex, Cerner integration, FX exposure\n"
                "6. Verdict: Bull/Bear/Neutral with price target and conviction level"
            )
        else:
            prompt = (
                "Give me a quick snapshot of Oracle (ORCL):\n"
                "1. Current price, YTD performance vs S&P500\n"
                "2. Key metrics: PE, forward PE, revenue growth, cloud ARR\n"
                "3. Recent news and catalysts\n"
                "4. Technical signal: Buy/Hold/Sell\n"
                "5. One-line thesis"
            )
        await self.terminal._handle_ai_message(prompt)

    # ---- News command ----

    async def cmd_news(self, args: str):
        """Fetch latest financial news for a topic or symbol.

        Usage: /news [topic|symbol] [--limit N]
        Examples:
          /news AAPL
          /news earnings --limit 10
          /news crypto --limit 3
        """
        parts = args.split()
        limit = 5
        topic_parts = []
        i = 0
        while i < len(parts):
            if parts[i] == "--limit" and i + 1 < len(parts):
                try:
                    limit = max(1, min(20, int(parts[i + 1])))
                    i += 2
                    continue
                except ValueError:
                    pass
            topic_parts.append(parts[i])
            i += 1
        topic = " ".join(topic_parts) or "market"

        console.print(f"[dim]Fetching {limit} news items for '{topic}'...[/dim]" if HAS_RICH
                      else f"Fetching news for {topic}...")

        # Try backend first, then local tools (Finnhub / NewsAPI / AKShare fallback chain)
        result = await execute_aria_tool(self.terminal.api_url, "analyze_news", {
            "query": topic, "limit": limit,
        })
        if not result.get("success") and "analyze_news" in LOCAL_TOOLS:
            # Local fallback: uses Finnhub → NewsAPI → AKShare depending on configured keys
            local_fn = LOCAL_TOOLS["analyze_news"][0]
            result = await asyncio.get_event_loop().run_in_executor(
                None, local_fn, {"query": topic, "symbol": topic, "limit": limit}
            )
        if result.get("success"):
            data = result.get("data", {})
            articles = data.get("articles", data.get("news", data if isinstance(data, list) else []))
            sentiment = data.get("sentiment", data.get("overall_sentiment", "")) if isinstance(data, dict) else ""
            if isinstance(articles, list) and articles:
                if HAS_RICH:
                    console.print()
                    if sentiment:
                        sent_color = "green" if "positive" in sentiment.lower() or "bullish" in sentiment.lower() else (
                            "red" if "negative" in sentiment.lower() or "bearish" in sentiment.lower() else "yellow"
                        )
                        console.print(f"  Sentiment: [{sent_color}]{sentiment}[/{sent_color}]")
                        console.print()
                for idx, a in enumerate(articles[:limit], 1):
                    if isinstance(a, dict):
                        title = a.get("title", "Untitled")
                        source = a.get("source", a.get("publisher", ""))
                        url_item = a.get("url", a.get("link", ""))
                        pub_date = a.get("published_at", a.get("date", a.get("publishedAt", "")))
                        if pub_date:
                            pub_date = pub_date[:10] if len(pub_date) >= 10 else pub_date
                    else:
                        title = str(a)
                        source = pub_date = url_item = ""
                    if HAS_RICH:
                        console.print(f"  [bold]{idx}.[/bold] {title}")
                        meta_parts = [p for p in [source, pub_date] if p]
                        if meta_parts:
                            console.print(f"     [dim]{' · '.join(meta_parts)}[/dim]")
                    else:
                        meta = f" ({source})" if source else ""
                        print(f"  {idx}. {title}{meta}")
                if HAS_RICH:
                    console.print()
            else:
                # Empty articles — show helpful config guidance
                _data_keys = _load_data_keys()
                if HAS_RICH:
                    console.print()
                    console.print(f"  [dim]未找到 '{topic}' 的相关新闻。[/dim]")
                    if not _data_keys.get("finnhub") and not _data_keys.get("newsapi"):
                        console.print("  [dim]配置数据服务 key 可获取更多新闻来源：[/dim]")
                        console.print("  [dim]  /apikey set finnhub <key>   →  https://finnhub.io/register[/dim]")
                        console.print("  [dim]  /apikey set newsapi <key>   →  https://newsapi.org/register[/dim]")
                    console.print()
        else:
            # Backend + all local fallbacks unavailable — show actionable config guide
            err = result.get("error", "")
            _data_keys = _load_data_keys()
            _has_finnhub = bool(_data_keys.get("finnhub"))
            _has_newsapi = bool(_data_keys.get("newsapi"))
            if HAS_RICH:
                console.print()
                console.print(f"  [yellow]⚠ 新闻服务不可用[/yellow]")
                if not _has_finnhub and not _has_newsapi:
                    console.print("  [dim]配置以下任意一个数据服务 key 即可获取新闻：[/dim]")
                    console.print("  [dim]  Finnhub  (免费60次/分) → /apikey set finnhub <key>   注册: https://finnhub.io/register[/dim]")
                    console.print("  [dim]  NewsAPI  (免费100次/天) → /apikey set newsapi <key>   注册: https://newsapi.org/register[/dim]")
                else:
                    console.print(f"  [dim]错误: {err[:120] if err else '获取失败'}[/dim]")
                console.print(f"  [dim]或使用: /web {topic} latest news — 通过 Brave 搜索[/dim]")
                console.print()
            else:
                print(f"  News unavailable. Configure: /apikey set finnhub <key>")

    # ---- Vision / image input command ----

    def cmd_vision(self, args: str):
        """Load an image for visual analysis in the next message: /vision <path>"""
        from pathlib import Path as _Path
        import base64 as _b64

        path_str = args.strip().strip("\"'")
        if not path_str:
            msg = "Usage: /vision <image_path>  (e.g. /vision ~/Desktop/chart.png)"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
            return

        path = _Path(path_str).expanduser().resolve()
        if not path.exists():
            _print_error(f"File not found: {path}", "vision")
            return

        suffix = path.suffix.lstrip(".").lower()
        mime_map = {
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "gif": "image/gif",
            "webp": "image/webp",
        }
        mime = mime_map.get(suffix)
        if not mime:
            _print_error(
                f"Unsupported image type: .{suffix}",
                "vision — supported: .png .jpg .jpeg .gif .webp",
            )
            return

        try:
            data = _b64.b64encode(path.read_bytes()).decode()
        except OSError as e:
            _print_error(f"Cannot read image: {e}", "vision")
            return

        self.terminal._pending_image = {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{data}"},
        }
        size_kb = path.stat().st_size // 1024
        if HAS_RICH:
            console.print(Panel(
                f"[green]✓[/green] [dim]{path.name}[/dim]  [dim]{size_kb} KB · {mime}[/dim]\n"
                f"[dim]Image queued — ask your question now[/dim]",
                border_style="dim",
                box=rich_box.ROUNDED,
                padding=(0, 1),
            ))
        else:
            print(f"Image loaded: {path.name} ({size_kb} KB) — send your question now")

    # ---- Config command ----

    def cmd_config(self, args: str):
        """Show or set CLI configuration."""
        parts = args.strip().split(maxsplit=1)
        if not parts or parts[0] == "show":
            # Show current config
            cfg = self.terminal.config
            if HAS_RICH:
                console.print()
                console.print("[bold]Configuration[/bold]")
                console.print()
                for key in ("api_url", "ollama_url", "model", "thinking_mode",
                            "command_policy", "write_policy", "auto_save_sessions"):
                    val = cfg.get(key, "-")
                    console.print(f"  [dim]{key:<24s}[/dim]{val}")
                console.print()
            else:
                for key in ("api_url", "ollama_url", "model", "thinking_mode",
                            "command_policy", "write_policy"):
                    print(f"  {key}: {cfg.get(key, '-')}")
        elif len(parts) == 2 and parts[0] == "set":
            # Parse key=value
            kv = parts[1].split("=", 1)
            if len(kv) == 2:
                key, val = kv[0].strip(), kv[1].strip()
                # Validate known config keys
                if key == "command_policy":
                    if val not in {"safe", "balanced", "full"}:
                        msg = "command_policy must be one of: safe | balanced | full"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "thinking_mode":
                    if val not in {"auto", "instant", "thinking"}:
                        msg = "thinking_mode must be one of: auto | instant | thinking"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "model":
                    resolved = MODEL_ALIASES.get(val) or (val if val in MODELS else None)
                    if not resolved:
                        valid = ", ".join(sorted(MODEL_ALIASES.keys()))
                        msg = f"Unknown model '{val}'. Valid: {valid}"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                    val = MODELS[resolved]["id"]
                elif key == "auto_save_sessions":
                    if val.lower() in {"true", "1", "yes", "on"}:
                        val = True
                    elif val.lower() in {"false", "0", "no", "off"}:
                        val = False
                    else:
                        msg = "auto_save_sessions must be: true | false"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "write_policy":
                    if val not in {"desktop_only", "confirm_outside", "always_confirm"}:
                        msg = "write_policy must be: desktop_only | confirm_outside | always_confirm"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "local_mode":
                    if val.lower() in {"true", "1", "yes", "on"}:
                        val = True
                    elif val.lower() in {"false", "0", "no", "off"}:
                        val = False
                    else:
                        msg = "local_mode must be: true | false"
                        console.print(f"[red]{msg}[/red]" if HAS_RICH else msg)
                        return
                elif key == "custom_endpoint":
                    # /config set custom_endpoint=http://my-litellm:4000/v1
                    # Automatically sets local_provider=custom
                    self.terminal.config["local_provider"] = "custom"
                    self.terminal.config["custom_endpoint"] = val
                    _sync_write_policy(self.terminal.config)
                    save_config(self.terminal.config)
                    msg = f"✓ 自定义 endpoint 设为 {val}  (local_provider=custom)"
                    console.print(f"[green]{msg}[/green]") if HAS_RICH else print(msg)
                    return
                elif key == "custom_model":
                    # /config set custom_model=gpt-4o
                    self.terminal.config["custom_model"] = val
                    if self.terminal.config.get("local_provider") == "custom":
                        self.terminal.config["model"] = val
                    _sync_write_policy(self.terminal.config)
                    save_config(self.terminal.config)
                    console.print(f"  [dim]custom_model[/dim] = {val}" if HAS_RICH else f"  custom_model = {val}")
                    return
                self.terminal.config[key] = val
                _sync_write_policy(self.terminal.config)
                save_config(self.terminal.config)
                console.print(f"  [dim]{key}[/dim] = {val}" if HAS_RICH else f"  {key} = {val}")
            else:
                console.print("[dim]Usage: /config set key=value[/dim]" if HAS_RICH
                              else "Usage: /config set key=value")
        elif parts[0] == "reload":
            fresh = load_config()
            self.terminal.config.update(fresh)
            msg = "Config reloaded from ~/.arthera/config.json"
            console.print(f"[dim]{msg}[/dim]" if HAS_RICH else msg)
        else:
            console.print("[dim]Usage: /config [show] | /config set key=value | /config reload[/dim]" if HAS_RICH
                          else "Usage: /config [show] | /config set key=value | /config reload")

    # ---- Context command ----

    def cmd_context(self, args: str):
        """Show current AI context: model, conversation length, token usage, project context."""
        cfg = self.terminal.config
        conv = self.terminal.conversation
        conv_len = len(conv)
        model_id = cfg.get("model", "qwen2.5:7b")
        thinking = cfg.get("thinking_mode", "auto")
        has_auth = bool(cfg.get("auth_token"))
        local_mode = cfg.get("local_mode", False)

        # Rough token estimate: ~3 chars per token
        total_chars = sum(len(m.get("content", "")) for m in conv)
        est_tokens = total_chars // 3
        max_ctx = get_model_cfg(model_id).get("num_ctx", 16384)
        ctx_pct = min(100, int(est_tokens / max_ctx * 100))
        ctx_color = "green" if ctx_pct < 60 else ("yellow" if ctx_pct < 85 else "red")

        if HAS_RICH:
            console.print()
            console.print("[bold]Current Context[/bold]")
            console.print()
            console.print(f"  [dim]{'Model':<20s}[/dim]{model_id}")
            console.print(f"  [dim]{'Provider':<20s}[/dim]{'[green]Local (Ollama)[/green]' if local_mode else 'AWS → Ollama fallback'}")
            console.print(f"  [dim]{'Thinking':<20s}[/dim]{thinking}")
            console.print(f"  [dim]{'Messages':<20s}[/dim]{conv_len}")
            console.print(f"  [dim]{'Est. tokens':<20s}[/dim][{ctx_color}]{est_tokens:,} / {max_ctx:,} ({ctx_pct}%)[/{ctx_color}]")
            console.print(f"  [dim]{'Authenticated':<20s}[/dim]{'yes' if has_auth else 'no'}")
            console.print(f"  [dim]{'Session':<20s}[/dim]{self.terminal.session_id}")
            console.print(f"  [dim]{'Project context':<20s}[/dim]{'loaded' if _PROJECT_CONTEXT else 'none'}")
            wl = cfg.get("watchlist", [])
            if wl:
                console.print(f"  [dim]{'Watchlist':<20s}[/dim]{', '.join(wl)}")
            if ctx_pct >= 80:
                console.print(f"\n  [yellow]⚠ Context {ctx_pct}% full — use /compact to free space[/yellow]")
            console.print()
        else:
            print(f"  Model: {model_id}  ({'local' if local_mode else 'aws'})")
            print(f"  Messages: {conv_len}  Tokens: ~{est_tokens:,}/{max_ctx:,} ({ctx_pct}%)")
            print(f"  Session: {self.terminal.session_id}")


# ── 经营权共创平台：Agent 输出辅助函数（模块级，SlashCommands 内外均可用）────────────


def _print_realty_result(result, agent_name: str):
    """格式化打印 realty Agent 结果"""
    _SIGNAL_LABELS = {
        "BUY": "[green]推荐/正常[/green]",
        "STRONG_BUY": "[bold green]强烈推荐[/bold green]",
        "HOLD": "[yellow]需观察[/yellow]",
        "SELL": "[red]警示[/red]",
        "STRONG_SELL": "[bold red]极高风险[/bold red]",
    }
    if not HAS_RICH:
        print(f"\n[{agent_name}] Signal: {result.signal}  Confidence: {result.confidence:.0%}")
        print(result.analysis)
        return

    console.print()
    console.print(f"  [bold]{agent_name.upper().replace('_',' ')}[/bold]"
                  f"  {_SIGNAL_LABELS.get(result.signal, result.signal)}"
                  f"  [dim]置信度 {result.confidence:.0%}[/dim]")
    console.print()
    for pt in (result.key_points or []):
        console.print(f"    • {pt}")
    if result.analysis:
        console.print()
        text = result.analysis[:1200] + ("…" if len(result.analysis) > 1200 else "")
        console.print(f"  [dim]{text}[/dim]")
    console.print()


def _print_risk_scan(data: dict):
    """格式化打印风险扫描结果"""
    if not HAS_RICH:
        print(f"Risk scan: {data.get('overall_level','?')} "
              f"(score={data.get('risk_score',0)})")
        for alert in data.get("alerts", []):
            print(f"  [{alert['level']}] {alert['desc']}")
        return

    level = data.get("overall_level", "未知")
    score = data.get("risk_score", 0)
    color = {"低": "green", "中": "yellow", "高": "red", "极高": "bold red"}.get(level, "white")
    console.print()
    console.print(f"  风险等级: [{color}]{level}[/{color}]  "
                  f"风险分值: {score}  "
                  f"预警项: {data.get('alert_count',0)}")
    console.print()
    for alert in data.get("alerts", []):
        ac = {"低": "dim", "中": "yellow", "高": "red", "极高": "bold red"}.get(
            alert["level"], "white")
        console.print(f"    [{ac}][{alert['level']}][/{ac}] {alert['desc']}")
    if data.get("suggestion"):
        console.print(f"\n  [dim]建议: {data['suggestion']}[/dim]")
    console.print()


def _p(msg: str, style: str = ""):
    """快速打印辅助（rich 可用时带样式）"""
    if HAS_RICH:
        tag = {"dim": "dim", "error": "red", "ok": "green"}.get(style, style)
        console.print(f"[{tag}]{msg}[/{tag}]" if tag else msg)
    else:
        print(msg)


# ============================================================================
# Main Terminal — Claude Code-like REPL
# ============================================================================

class ArtheraTerminal:
    """Interactive REPL inspired by Claude Code CLI."""

    def __init__(self, config: dict):
        self.config = config
        _sync_write_policy(config)  # ensure module-level policy matches loaded config
        self.api_url = config.get("api_url", DEFAULT_CONFIG["api_url"])
        self.conversation: List[dict] = []
        self.running = True
        self.session_id = config.get("last_session_id") or str(uuid.uuid4())[:8]
        self.session_mgr = SessionManager()
        self.pending_plan: List[str] = []
        self.last_plan_results: List[dict] = []
        self.cancel_event: Optional[asyncio.Event] = None
        self._streaming = False
        self._last_provider = ""   # last successful provider ("" = no message sent yet)
        self._actual_model: Optional[str] = None  # actual Ollama model in use (may differ from config)
        self._ollama_alive = False                # set by print_header / health check
        self._installed_models: set = set()       # installed Ollama models (from header detection)
        self._auto_healed_from: Optional[str] = None  # original model if auto-paired at startup

        # ── Session-level telemetry (like Claude Code's /cost) ──────────
        import time as _time_mod
        self._session_start: float = _time_mod.time()
        self._session_input_tokens: int = 0   # prompt tokens this session
        self._session_output_tokens: int = 0  # completion tokens this session
        self._session_thinking_tokens: int = 0
        self._session_turns: int = 0           # number of exchange pairs
        self._last_response: str = ""          # last assistant message text (for /copy)
        self._forks: List[dict] = []           # forked conversation snapshots
        self._pending_image: Optional[dict] = None  # pending vision content block

        # ── ariarc: project-level context injection ──────────────────────
        self.ariarc: Optional[Any] = None
        if _HAS_ARIARC:
            try:
                self.ariarc = get_ariarc()
                if self.ariarc.found:
                    logger.info("ariarc loaded from %s", self.ariarc.source_path)
            except Exception as _exc:
                logger.debug("ariarc load error: %s", _exc)

        # ── MCP registry placeholder (started async in run_interactive) ──
        self._mcp_registry: Optional[Any] = None
        self._mcp_started = False

        self.commands = SlashCommands(self)

        # Setup input — prefer prompt_toolkit, fallback to readline.
        # Skip interactive input setup entirely in non-interactive mode (-p flag)
        # to avoid prompt_toolkit emitting "Warning: Input is not a terminal".
        self._pt_session = None
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)

        _interactive = sys.stdin.isatty()
        if HAS_PT and _interactive:
            pt_completer = AriaPTCompleter(
                self.commands.commands, SKILLS, config.get("watchlist", []),
            )
            self._pt_session = PromptSession(
                history=FileHistory(str(HISTORY_FILE)),
                completer=pt_completer,
                complete_while_typing=True,
                style=ARIA_PT_STYLE,
                placeholder=HTML('<style fg="#606060">发送消息，或输入 / 查看命令…</style>'),
            )
        elif _interactive:
            try:
                if HISTORY_FILE.exists():
                    readline.read_history_file(str(HISTORY_FILE))
                readline.set_history_length(1000)
                completer = ArtheraCompleter(
                    list(self.commands.commands.keys()),
                    SKILLS,
                    config.get("watchlist", []),
                )
                readline.set_completer(completer.complete)
                readline.parse_and_bind("tab: complete")
                readline.set_completer_delims(" ")
            except Exception:
                pass

    def print_header(self):
        # Resolve current model info
        current_id  = self.config.get("model", "qwen2.5:7b")

        # ── 模型自动配对（现实优先）─────────────────────────────────────────
        # 检测本机已安装的 Ollama 模型；若配置模型未安装，自动配对到最优
        # 可用模型并持久化配置（与运行时 fallback 共用同一选择逻辑）。
        self._auto_healed_from: Optional[str] = None   # 原配置模型（仅本次显示用）
        self._ollama_alive = False
        self._installed_models: set = set()
        try:
            _rm, _ = detect_ollama_models_rich(
                self.config.get("ollama_url", "http://localhost:11434"))
            self._installed_models = {_x["name"] for _x in _rm}
            self._ollama_alive = bool(self._installed_models)
        except Exception:
            pass
        if self._installed_models and current_id not in self._installed_models:
            _resolved = _pick_best_installed_model(self._installed_models, current_id)
            if _resolved:
                self._auto_healed_from = current_id
                current_id = _resolved
                self.config["model"] = _resolved
                self._actual_model = None   # config now matches reality
                try:
                    save_config(self.config)
                except Exception:
                    pass

        current_key = next((k for k, v in MODELS.items() if v["id"] == current_id), None)
        _default_m  = MODELS.get("qwen7b") or MODELS.get("qwen-fast") or next(iter(MODELS.values()))
        m = MODELS.get(current_key, _default_m) if current_key else _default_m
        cwd = os.getcwd()
        # Shorten home directory to ~
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            cwd = "~" + cwd[len(home):]
        wl = self.config.get("watchlist", [])
        tool_count = len(ARIA_TOOLS) + len(LOCAL_TOOLS)
        skill_count = len(SKILLS)

        # Watchlist string
        wl_str = ""
        if wl:
            wl_str = ", ".join(wl[:5])
            if len(wl) > 5:
                wl_str += f" +{len(wl) - 5}"

        if HAS_RICH:
            console.print()

            # ASCII art logo in copper
            _LOGO = r"""    _         _
   / \  _ __ (_) __ _
  / _ \| '__|| |/ _` |
 / ___ \  |  | | (_| |
/_/   \_\_|  |_|\__,_|"""
            for line in _LOGO.splitlines():
                console.print(f"[#C08050]{line}[/#C08050]")
            console.print()

            # Info lines — vertical stack
            t1 = Text()
            t1.append("  Aria Code", style="bold")
            t1.append(" v3.0", style="dim")
            console.print(t1)

            # Model line — 现实优先：显示实际将使用的模型
            _badge = m.get("badge", "")
            if current_key:
                _model_label = f"{m['name']} {m['version']} · {m['tag']}"
            else:
                _model_label = current_id   # 不在注册表内的模型直接显示原始 ID
            if _badge == "Fast":
                console.print(f"  [dim]{_model_label}[/dim]  [yellow]⚡lite[/yellow]")
            elif _badge == "Cloud":
                console.print(f"  [dim]{_model_label}[/dim]  [cyan]☁ cloud[/cyan]")
            else:
                console.print(f"  [dim]{_model_label}[/dim]")
            # 自动配对附注（仅当本次启动发生了配对时显示，两行避免折行混乱）
            if self._auto_healed_from:
                console.print(
                    f"  [dim]⚙ 已自动配对：[/dim][yellow]{self._auto_healed_from}[/yellow]"
                    f"[dim] 未安装 → 配置已更新为 [/dim][bold]{current_id}[/bold]"
                )
                console.print(
                    f"  [dim]  恢复原模型: ollama pull {self._auto_healed_from}"
                    f" && /model {self._auto_healed_from}[/dim]"
                )
            console.print(f"  [dim]{cwd}[/dim]")
            if wl_str:
                console.print(f"  [dim]{wl_str}[/dim]")
            console.print(f"  [dim]{tool_count} tools · {skill_count} skills · /help[/dim]")

            # ── Recommend better model if only the tiny 1.5B is installed ─
            if _badge == "Fast" and self._installed_models:
                _best_id = (MODELS.get("qwen7b") or {}).get("id", "qwen2.5:7b")
                if _best_id not in self._installed_models:
                    console.print(
                        f"  [yellow]⚡ Tip:[/yellow] [dim]Running lite model. "
                        f"For best results: [bold]ollama pull {_best_id}[/bold][/dim]"
                    )

            # Thin separator
            try:
                tw = os.get_terminal_size().columns
            except OSError:
                tw = 80
            console.print(Text("─" * min(tw, 80), style="dim"))

            # First-run welcome tips
            if not self.config.get("first_run_seen"):
                console.print()
                tip1 = Text()
                tip1.append("  Try: ", style="dim")
                tip1.append("analyze AAPL", style="bold")
                tip1.append("  ·  ", style="dim")
                tip1.append("/morning-brief", style="bold")
                tip1.append("  ·  ", style="dim")
                tip1.append("/trade-idea TSLA", style="bold")
                console.print(tip1)
                tip2 = Text()
                tip2.append("  Type ", style="dim")
                tip2.append("/", style="bold")
                tip2.append(" for commands · ", style="dim")
                tip2.append("/help", style="bold")
                tip2.append(" for guide · ", style="dim")
                tip2.append("/login", style="bold")
                tip2.append(" to personalize", style="dim")
                console.print(tip2)
                self.config["first_run_seen"] = True
                save_config(self.config)
        else:
            print()
            print(f"  Aria Code v3.0")
            print(f"  {m['name']} {m['version']} · {m['tag']} · {tool_count} tools")
            print(f"  {cwd}")
            if wl_str:
                print(f"  {wl_str}")
            print("─" * 60)

    def _status_line(self) -> str:
        current_id = self.config.get("model", "qwen2.5:7b")
        # If Ollama switched to a different model, show the actual running model
        display_id = self._actual_model or current_id
        model_name = display_id  # fallback: raw model ID
        for k, v in MODELS.items():
            if v["id"] == display_id:
                model_name = v["name"].replace("Aria ", "")
                break
            # also match by actual model ID (e.g. gpt-oss:120b-cloud)
            if v["id"] == current_id and self._actual_model is None:
                model_name = v["name"].replace("Aria ", "")
                break
        # If actual_model differs from config, append a ⚑ warning marker
        _mismatch = (self._actual_model is not None and self._actual_model != current_id)
        if _mismatch:
            model_name = f"{self._actual_model} ⚑"
        thinking = THINKING_MODES.get(self.config.get("thinking_mode", "auto"), {}).get("label", "Auto")
        # Determine provider label based on last used provider AND selected model badge
        _lp = self._last_provider or ""
        _model_badge = next(
            (v.get("badge", "") for v in MODELS.values() if v["id"] == current_id), ""
        )
        if _lp == "ollama":
            provider_label = "Local"
        elif _lp in ("deepseek", "openai", "anthropic", "groq", "dashscope", "together"):
            provider_label = "Cloud"
        elif _model_badge == "Cloud" or "cloud" in current_id.lower():
            provider_label = "Cloud"
        elif not _lp:
            # 尚未发送消息 — 根据实际环境推断而非硬编码
            provider_label = "Local" if getattr(self, "_ollama_alive", False) else "—"
        else:
            provider_label = "AWS"
        return f"{model_name} · {thinking} · {provider_label}"

    async def send_message(self, message: str):
        """Send message to Aria AI with agentic tool loop, smart fallback, markdown."""
        # Fire prompt_submit hook (Claude Code: UserPromptSubmit)
        _run_event_hook("prompt_submit", {
            "ARIA_MESSAGE":  message[:500],
            "ARIA_SESSION":  self.session_id,
            "ARIA_PROVIDER": self._last_provider,
        })
        # Attach pending image block if /vision was used before this message
        if self._pending_image is not None:
            user_content = [
                {"type": "text", "text": message},
                self._pending_image,
            ]
            self._pending_image = None
        else:
            user_content = message
        self.conversation.append({"role": "user", "content": user_content})

        # ── 路由决策：支持工具调用的模型走 LLM+tool call，否则走确定性路由 ──
        # 支持 function calling 的模型（Claude / GPT-4 class / qwen-72b+）能自己
        # 识别公司名 → ticker 并调 get_market_data，不需要硬编码字典。
        # 本地小模型（<14B）工具调用不稳定，保留确定性路由作降级。
        _curr_model_id = self.config.get("model", "")
        _model_has_tools = False
        if _HAS_MODEL_CAP:
            try:
                _mc = get_model_capability(_curr_model_id)
                _model_has_tools = bool(_mc.tool_calls and _mc.context_window >= 8192)
            except Exception:
                pass

        deterministic: dict = {"success": False}
        if not _model_has_tools:
            # Deterministic path: only for models that can't reliably do function calling
            deterministic = _try_handle_market_snapshot_analysis(
                message, history=self.conversation[:-1])
        if not deterministic.get("success"):
            deterministic = _try_handle_stock_chart_analysis(message)
        if deterministic.get("success") or _is_stock_chart_analysis_request(message):
            final_text = deterministic.get("response", "")
            if not final_text:
                final_text = f"市场分析未完成：{deterministic.get('error', '未知错误')}"
            if HAS_RICH:
                console.print()
                console.print("[bold]Aria[/bold]")
                console.print()
                console.print(Markdown(_strip_latex(final_text)))
                # User-friendly footer: show data source(s) instead of internal routing label
                _tools = deterministic.get("tools_used", [])
                _tool_label = {
                    "market_snapshot": "市场快照",
                    "stock_chart":     "图表分析",
                }.get(_tools[0], _tools[0]) if _tools else "本地分析"
                _rate_limited = deterministic.get("rate_limited", False)
                _rl_note = "  [yellow]⚠ 数据源限流[/yellow]" if _rate_limited else ""
                console.print(f"\n[dim]{_tool_label} · 本内容不构成投资建议[/dim]{_rl_note}\n")
                console.print(Rule(style="dim"))
            else:
                print("\nAria\n")
                print(final_text)
                print(f"\n市场快照 · 本内容不构成投资建议\n")
            self.conversation.append({"role": "assistant", "content": final_text})
            return

        model = self.config.get("model", "qwen2.5:7b")
        thinking_mode = self.config.get("thinking_mode", "auto")
        auth_token = self.config.get("auth_token")
        user_context = _build_user_context(self.config)
        self.cancel_event = asyncio.Event()
        self._streaming = True
        _esc_watcher.start(self.cancel_event)

        if HAS_RICH:
            console.print()
        start_time = time.time()

        # --- Agentic loop: may run multiple rounds if AI requests tools ---
        max_rounds = 10
        current_message = message
        total_response = ""
        all_tools = []
        all_sources = []
        provider = "aws"
        token_count = 0
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0}
        thinking_tokens = 0
        tool_time_total = 0.0

        for round_num in range(max_rounds):
            response_text = ""
            thinking_shown = False
            thinking_start = None
            thinking_finished = False
            thinking_preview_buf: list = []  # accumulate preview chars
            streamed_any = False

            if round_num == 0:
                if HAS_RICH:
                    console.print("\n[bold]Aria[/bold]")
                else:
                    print("\nAria")

            # Progressive markdown rendering via Rich Live
            _live_display = [None]
            # Spinner shown before first token arrives
            _spinner = [None]
            _first_token_received = [False]
            _token_start_time = [None]
            # Throttle Live.update() to prevent terminal flooding (max once per 80ms)
            _last_live_update = [0.0]
            _LIVE_UPDATE_INTERVAL = 0.08  # seconds
            # Force plain-print mode for Ollama streams: Live.update() causes the
            # entire accumulated response to reprint on every token in embedded
            # terminals (Electron/Arthera) that report is_terminal=True but cannot
            # handle cursor-up ANSI sequences correctly.
            _use_plain_print  = [False]
            # Batch-render mode (Ollama only): accumulate tokens silently while
            # spinner runs, then render the COMPLETE response with Rich Markdown
            # after streaming ends.  Avoids per-token LaTeX-buffering issues where
            # "$$" is split across two single-"$" tokens (model-dependent tokenisation)
            # causing raw \frac, \sum, \; to leak into output.
            _use_batch_render = [False]
            # LaTeX streaming buffer: accumulate tokens between \( and \) or $$ and $$
            # so that _strip_latex gets the COMPLETE expression, not fragments.
            _latex_buf = [""]      # mutable for closure
            _in_latex  = [False]   # True while inside an open LaTeX block

            def _flush_latex_buf() -> str:
                """Process and return the accumulated LaTeX buffer, then clear it."""
                raw = _latex_buf[0]
                _latex_buf[0] = ""
                _in_latex[0]  = False
                return _strip_latex(raw) if raw.strip() else raw

            def _start_spinner():
                if HAS_RICH and _spinner[0] is None and not _first_token_received[0]:
                    _spinner[0] = console.status(
                        "[dim]思考中… [/dim][dim italic]esc 取消[/dim italic]",
                        spinner="dots", spinner_style="dim")
                    _spinner[0].__enter__()

            def _stop_spinner():
                if _spinner[0] is not None:
                    try:
                        _spinner[0].__exit__(None, None, None)
                    except Exception:
                        pass
                    _spinner[0] = None

            def _stop_live(discard: bool = False):
                """Stop Live display if active.

                discard=True: silently discard the current Live content without
                rendering it to the terminal. Use this before triggering a
                fallback stream so that the same content isn't printed twice
                (once by Live.stop() and once by the fallback's plain-print path).
                """
                _stop_spinner()
                if _live_display[0]:
                    try:
                        if discard:
                            # Replace renderable with empty text so Live.stop()
                            # does not paint stale content onto the terminal.
                            try:
                                from rich.text import Text as _RichText
                                _live_display[0].update(_RichText(""))
                                _live_display[0].refresh()
                            except Exception:
                                pass
                        _live_display[0].stop()
                    except Exception:
                        pass
                    _live_display[0] = None
                elif _first_token_received[0] and not discard:
                    # Plain-print mode: ensure cursor is on a new line.
                    # Skip for batch-render — no tokens were printed to stdout,
                    # so no newline is needed here.
                    if not _use_batch_render[0]:
                        print(flush=True)

            _start_spinner()

            def on_token(token):
                nonlocal response_text, streamed_any, thinking_shown, thinking_start, thinking_finished, token_count
                # Stop spinner on first token — UNLESS batch-render mode (Ollama),
                # where the spinner keeps running throughout generation so the user
                # knows work is in progress.
                if not _first_token_received[0]:
                    _first_token_received[0] = True
                    _token_start_time[0] = time.time()
                    if not _use_batch_render[0]:
                        _stop_spinner()
                # Filter out Ollama special tokens
                if "<|im_start|>" in token or "<|im_end|>" in token:
                    token = token.replace("<|im_start|>", "").replace("<|im_end|>", "")
                    if not token.strip():
                        return
                # Filter out model meta-annotation artifacts (small-model hallucinations)
                # e.g. "(注释：请使用实际注入的数据进行回答)" that the model should never output
                _META_ARTIFACTS = (
                    "(注释：", "（注释：", "(提示：", "（提示：",
                    "请使用实际注入的数据", "请使用实际数据", "实际注入的数据",
                    "[system]", "[/system]", "[INST]", "[/INST]",
                )
                if any(a in token for a in _META_ARTIFACTS):
                    # Strip the artifact from token; if nothing left, skip entirely
                    import re as _re_tok
                    token = _re_tok.sub(
                        r'\(注[释释]：[^)）]*[)）]|（注[释释]：[^)）]*[)）]'
                        r'|\(提示：[^)）]*[)）]|（提示：[^)）]*[)）]'
                        r'|请使用实际(?:注入的)?数据[^。\n]*'
                        r'|\[/?(?:system|INST)\]',
                        '', token
                    )
                    if not token.strip():
                        return
                # Finalize thinking display on first content token
                if thinking_shown and not thinking_finished:
                    thinking_finished = True
                    _stop_spinner()
                    elapsed_t = time.time() - thinking_start if thinking_start else 0
                    t_info = f"Thought for {elapsed_t:.1f}s"
                    if thinking_tokens > 0:
                        t_info += f" · {thinking_tokens:,} tokens"
                    if HAS_RICH:
                        console.print(f"  [dim]{t_info}[/dim]")
                        # Optional thinking preview (config: "thinking_preview": true)
                        if self.config.get("thinking_preview") and thinking_preview_buf:
                            preview_text = "".join(thinking_preview_buf)[:280].strip()
                            if len("".join(thinking_preview_buf)) > 280:
                                preview_text += "…"
                            console.print(f"  [dim italic]{preview_text}[/dim italic]")
                    else:
                        print(f"\r  {t_info}")
                # ── Batch-render mode (Ollama) ────────────────────────────────
                # Accumulate the raw token without any per-token processing.
                # The spinner keeps running; the COMPLETE response is rendered
                # with Rich Markdown + _strip_latex after the stream finishes.
                # This correctly handles "$$" split across two single-"$" tokens
                # (model-dependent tokenisation) that would otherwise bypass the
                # LaTeX buffer and leak raw \frac / \sum / \; into output.
                if _use_batch_render[0]:
                    response_text += token
                    streamed_any = True
                    token_count += 1
                    return
                # ── LaTeX buffering ───────────────────────────────────────────
                # Accumulate tokens between LaTeX delimiters (\(...\) or $$...$$)
                # so _strip_latex sees the COMPLETE expression, not fragments.
                # Inline `$...$` is NOT buffered to avoid false positives on dollar
                # signs in financial text ("price is $192").
                _OPEN_DELIMS  = (r"\(", r"\[", "$$")
                _CLOSE_DELIMS = (r"\)", r"\]", "$$")

                if not _in_latex[0]:
                    # Check if token OPENS a LaTeX block
                    _opens = any(d in token for d in _OPEN_DELIMS)
                    if _opens:
                        _in_latex[0] = True
                        _latex_buf[0] = token
                        # Check if it also CLOSES in the same token
                        _tail = token
                        for _od, _cd in zip(_OPEN_DELIMS, _CLOSE_DELIMS):
                            if _od in _tail:
                                _after = _tail[_tail.index(_od) + len(_od):]
                                if _cd in _after:
                                    # Complete block in one token — process immediately
                                    token = _flush_latex_buf()
                                    break
                        else:
                            # Block opened but not closed — keep buffering, don't print yet
                            response_text += _latex_buf[0]  # accumulate raw in response_text
                            streamed_any = True
                            token_count += 1
                            return
                    else:
                        # Normal token — strip and print
                        token = _strip_latex(token)
                else:
                    # Already inside a LaTeX block — keep buffering
                    _latex_buf[0] += token
                    _closes = any(d in token for d in _CLOSE_DELIMS)
                    if _closes:
                        # Block complete — process the whole accumulated buffer
                        token = _flush_latex_buf()
                    else:
                        # Still open — accumulate in response_text but don't print
                        response_text += token
                        streamed_any = True
                        token_count += 1
                        return
                # ─────────────────────────────────────────────────────────────

                response_text += token
                streamed_any = True
                token_count += 1
                # Streaming output: use Rich.Live ONLY when the terminal
                # supports ANSI cursor control (is_terminal=True and NOT dumb)
                # AND we are not in forced plain-print mode.
                # In dumb/pipe mode, or when streaming from a local Ollama model,
                # every Live.update() reprints the full block — producing the
                # cascading-echo bug — so fall back to incremental plain print.
                _can_live = (
                    HAS_RICH
                    and not _use_plain_print[0]
                    and getattr(console, "is_terminal", False)
                    and not getattr(console, "is_dumb_terminal", True)
                )
                if _can_live:
                    now = time.time()
                    _md = Markdown(_strip_latex(response_text))
                    if _live_display[0] is None:
                        _live_display[0] = Live(
                            _md, console=console,
                            refresh_per_second=12,
                            vertical_overflow="visible",
                        )
                        _live_display[0].start()
                        _last_live_update[0] = now
                    elif now - _last_live_update[0] >= _LIVE_UPDATE_INTERVAL:
                        _live_display[0].update(_md)
                        _last_live_update[0] = now
                else:
                    # Plain incremental output — works in all terminals / pipes
                    print(token, end="", flush=True)

            def on_thinking(content):
                nonlocal thinking_shown, thinking_start, thinking_tokens
                if not thinking_shown:
                    _stop_spinner()  # stop generic spinner, replace with thinking spinner
                    thinking_start = time.time()
                    if HAS_RICH:
                        _spinner[0] = console.status(
                            "[dim italic]Thinking[/dim italic]",
                            spinner="dots2", spinner_style="dim"
                        )
                        _spinner[0].__enter__()
                    else:
                        print("  (thinking...) ", end="", flush=True)
                    thinking_shown = True
                thinking_tokens += 1
                # Accumulate up to 300 chars for optional preview
                if len("".join(thinking_preview_buf)) < 300:
                    thinking_preview_buf.append(content)

            def on_tool_call(tool, params):
                nonlocal thinking_shown, thinking_start, thinking_finished, thinking_tokens
                # Finalize thinking display before tool call
                if thinking_shown and not thinking_finished:
                    thinking_finished = True
                    elapsed_t = time.time() - thinking_start if thinking_start else 0
                    t_info = f"Thought for {elapsed_t:.1f}s"
                    if thinking_tokens > 0:
                        t_info += f" · {thinking_tokens:,} tokens"
                    if HAS_RICH:
                        console.print(f"\r  [dim]{t_info}[/dim]")
                        if self.config.get("thinking_preview") and thinking_preview_buf:
                            preview_text = "".join(thinking_preview_buf)[:280].strip()
                            if len("".join(thinking_preview_buf)) > 280:
                                preview_text += "…"
                            console.print(f"  [dim italic]{preview_text}[/dim italic]")
                    else:
                        print(f"\r  {t_info}")
                _print_tool_call(tool, params if isinstance(params, dict) else {})

            def on_tool_result(tool, summary):
                pass  # Tool results are displayed by _print_tool_result

            def on_status(state, msg):
                if state == "fallback":
                    if HAS_RICH:
                        console.print(f"\n  [dim]{msg}[/dim]")
                    else:
                        print(f"\n  {msg}")

            # Route: local_mode → Ollama directly; otherwise AWS first → Ollama fallback
            local_mode = self.config.get("local_mode", False)
            if local_mode:
                _use_plain_print[0]  = True
                _use_batch_render[0] = True   # accumulate silently → Rich render at end
                result = await stream_ollama(
                    self.config.get("ollama_url", "http://localhost:11434"),
                    current_message, self.conversation,
                    model=model, on_token=on_token, on_thinking=on_thinking,
                    on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                    cancel_event=self.cancel_event,
                    enable_tools=True,
                )
                provider = "ollama"
                self._last_provider = "ollama"
            else:
                result = await stream_chat(
                    self.api_url, current_message, self.conversation,
                    model=model, thinking_mode=thinking_mode,
                    user_context=user_context, auth_token=auth_token,
                    on_token=on_token, on_thinking=on_thinking,
                    on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                    on_status=on_status, cancel_event=self.cancel_event,
                )
                # 响应质量检测：success=True 但返回占位符/空响应 → 同样 fallback
                def _is_placeholder_response(r: dict) -> bool:
                    resp = r.get("response", "")
                    if not resp or len(resp) < 20:
                        return True
                    # 后端已知占位模板
                    _placeholders = (
                        "欢迎使用 Aria AI 金融助手",
                        "这是一个需要详细解释的概念。请稍后重试",
                        "Welcome to Aria",
                        "请提供更具体的问题",
                        "I'm here to help with financial",
                    )
                    return any(p in resp for p in _placeholders)

                # If backend failed OR returned placeholder, fallback chain:
                # Ollama (if running) → DeepSeek cloud → OpenAI → error
                _should_fallback = (
                    (not result.get("success") and not result.get("cancelled"))
                    or _is_placeholder_response(result)
                )
                if _should_fallback:
                    # Discard any in-progress Live display without rendering it —
                    # the fallback will stream fresh content.  Rendering here would
                    # cause the same response to appear twice (once from the Live
                    # final-render and once from the fallback's plain-print path).
                    _stop_live(discard=True)
                    # Also reset streaming state so the fallback starts fresh
                    response_text = ""
                    streamed_any = False
                    _first_token_received[0] = False

                    # ── 1. 查询 Ollama 实际安装列表 ───────────────────────────
                    # NOTE: Use aiohttp with trust_env=False to bypass HTTP_PROXY
                    # environment variable — urllib.request can fail for localhost
                    # even when NO_PROXY=localhost,127.0.0.1 is set.
                    import json as _json
                    ollama_url      = self.config.get("ollama_url", "http://localhost:11434")
                    _ollama_up      = False
                    _ollama_models  = set()   # {"qwen2.5:7b", "gpt-oss:120b-cloud", ...}
                    try:
                        import aiohttp as _aiohttp
                        async with _aiohttp.ClientSession(
                            trust_env=False,  # ignore HTTP_PROXY / NO_PROXY
                            connector=_aiohttp.TCPConnector(ssl=False)
                        ) as _sess:
                            async with _sess.get(
                                f"{ollama_url}/api/tags",
                                timeout=_aiohttp.ClientTimeout(total=3)
                            ) as _resp:
                                if _resp.status == 200:
                                    _tags = await _resp.json()
                                    _ollama_up = True
                                    _ollama_models = {m["name"] for m in _tags.get("models", [])}
                    except Exception:
                        # Fallback: try urllib with explicit no-proxy
                        try:
                            import urllib.request as _ur
                            _proxy_handler = _ur.ProxyHandler({})  # bypass all proxies
                            _opener = _ur.build_opener(_proxy_handler)
                            _tags_resp = _opener.open(f"{ollama_url}/api/tags", timeout=3)
                            _tags = _json.loads(_tags_resp.read())
                            _ollama_up = True
                            _ollama_models = {m["name"] for m in _tags.get("models", [])}
                        except Exception:
                            pass

                    # 优先使用用户选定的模型；若未安装则按能力顺序降级
                    # （选择逻辑与启动预检共用 _pick_best_installed_model）
                    _ollama_model = None
                    if _ollama_up:
                        _ollama_model = _pick_best_installed_model(_ollama_models, model)

                    if _ollama_model:
                        _switched = _ollama_model != model
                        self._actual_model = _ollama_model  # record for header display
                        if _switched:
                            # 配置的模型未安装，已自动切换 — 用 Panel 明确告知用户
                            if HAS_RICH:
                                console.print(Panel(
                                    f"[yellow]⚠ 配置模型 [bold]{model}[/bold] 未安装\n"
                                    f"[/yellow][dim]已自动切换至 [bold]{_ollama_model}[/bold]（本地可用）\n"
                                    f"安装配置模型：[bold]ollama pull {model}[/bold][/dim]",
                                    border_style="yellow",
                                    box=rich_box.ROUNDED,
                                    padding=(0, 1),
                                ))
                            else:
                                print(f"  ⚠ 配置模型 {model} 未安装，已切换至 {_ollama_model}")
                        else:
                            # 正常使用配置的模型，仅在含 "cloud" 字样时说明是本地运行
                            if "cloud" in _ollama_model.lower() and HAS_RICH:
                                console.print(f"  [dim]本地运行: {_ollama_model}[/dim]")
                        _use_plain_print[0]  = True   # disable Live for Ollama
                        _use_batch_render[0] = True   # accumulate silently → Rich render at end
                        result = await stream_ollama(
                            ollama_url, current_message, self.conversation,
                            model=_ollama_model, on_token=on_token,
                            on_thinking=on_thinking,
                            on_tool_call=on_tool_call, on_tool_result=on_tool_result,
                            cancel_event=self.cancel_event, enable_tools=True,
                        )
                        provider = "ollama"
                        self._last_provider = "ollama"

                    else:
                        # ── 2. Ollama 无模型或未运行 → 尝试云端 provider ─────
                        if _ollama_up and not _ollama_models:
                            # Ollama 在但没有任何模型
                            _tip = "Ollama 已运行但未安装任何模型。运行: ollama pull qwen2.5:7b"
                            if HAS_RICH:
                                console.print(f"  [yellow]{_tip}[/yellow]")
                            else:
                                print(f"  {_tip}")
                        elif not _ollama_up:
                            if HAS_RICH:
                                console.print("  [dim]Ollama 未运行，尝试云端...[/dim]")
                            else:
                                print("  Ollama 未运行，尝试云端...")

                        try:
                            from providers.llm.registry import stream_cloud_fallback
                            _cloud_avail = True
                        except ImportError:
                            _cloud_avail = False

                        if _cloud_avail:
                            result = await stream_cloud_fallback(
                                current_message, self.conversation,
                                on_token=on_token,
                                cancel_event=self.cancel_event,
                            )
                            provider = result.get("provider", "cloud")
                            self._last_provider = provider
                        else:
                            # ── 3. 彻底无可用 provider ────────────────────────
                            _stop_live()
                            result = {"success": False, "error": "no_provider",
                                      "response": "", "cancelled": False}

            # Stop Live display before handling results
            _stop_live()

            if result.get("cancelled"):
                if HAS_RICH:
                    console.print("\n[dim]Cancelled[/dim]")
                else:
                    print("\n  (cancelled)")
                total_response += response_text
                break

            if not result.get("success"):
                error = result.get("error", "Unknown error")
                console.print() if HAS_RICH else print()
                # ── 用户友好错误提示 ──────────────────────────────────────────
                if error in ("no_cloud_provider", "no_provider"):
                    _hint_lines = [
                        "没有可用的 AI 模型",
                        "  Ollama 未运行，且未配置云端 API Key。",
                        "  解决方案（任选其一）：",
                        "    • 启动 Ollama:  ollama serve",
                        "    • 配置云端 Key: /apikey set deepseek <your-key>",
                        "    • 导出环境变量: export DEEPSEEK_API_KEY=sk-...",
                    ]
                    for ln in _hint_lines:
                        if HAS_RICH:
                            style = "bold yellow" if ln == _hint_lines[0] else "yellow"
                            console.print(f"  [{style}]{ln}[/{style}]")
                        else:
                            print(f"  {ln}")
                elif error == "all_providers_failed":
                    _msg = "所有云端 Provider 均请求失败，请检查网络或 API Key 是否有效。"
                    if HAS_RICH:
                        console.print(f"  [yellow]{_msg}[/yellow]")
                    else:
                        print(f"  {_msg}")
                else:
                    _print_error(f"Error: {error}")
                console.print() if HAS_RICH else print()
                break

            total_response += result.get("response", response_text)
            all_tools.extend(result.get("tools_used", []))
            all_sources.extend(result.get("sources", []))
            provider = result.get("provider", provider)

            # Accumulate usage stats from this round
            round_usage = result.get("usage", {})
            if round_usage:
                total_usage["prompt_tokens"] += round_usage.get("prompt_tokens", 0)
                total_usage["completion_tokens"] += round_usage.get("completion_tokens", 0)
                total_usage["thinking_tokens"] += round_usage.get("thinking_tokens", 0)
            self._last_provider = provider

            # --- Agentic tool loop ---
            pending = result.get("tool_calls_pending", [])
            if not pending:
                break  # No tools requested, done

            # ── Parallel tool dispatch ─────────────────────────────────────────
            # Read-only / remote tools run concurrently via asyncio.gather().
            # Write / edit / shell tools are serialised to avoid race conditions.
            _WRITE_TOOLS_PAR = {"write_file", "edit_file", "run_command"}
            _parallel_batch = [tc for tc in pending if tc["tool"] not in _WRITE_TOOLS_PAR]
            _serial_batch   = [tc for tc in pending if tc["tool"] in _WRITE_TOOLS_PAR]

            # Helper: execute one tool call and return (tc, result) pair
            async def _exec_one(tc_item: dict) -> tuple:
                _tn = tc_item["tool"]
                _tp = tc_item["params"]
                _run_hook("pre_tool", _tn, _tp)
                if _tn in LOCAL_TOOLS:
                    _tr = await asyncio.get_event_loop().run_in_executor(
                        None, execute_local_tool, _tn, _tp)
                else:
                    _tr = await execute_aria_tool(
                        self.api_url, _tn, _tp, auth_token=auth_token)
                _run_hook("post_tool", _tn, _tp, _tr)
                return tc_item, _tr

            # Run parallel batch first (read tools, remote tools)
            _par_results: list = []
            if _parallel_batch:
                _gathered = await asyncio.gather(
                    *[_exec_one(tc) for tc in _parallel_batch],
                    return_exceptions=True,
                )
                for _gr in _gathered:
                    if isinstance(_gr, Exception):
                        _par_results.append((None, {"success": False, "error": str(_gr)}))
                    else:
                        _par_results.append(_gr)

            # Build the ordered pending list for the sequential part of the loop
            # (serial_batch) + collect results from parallel_batch for summarising
            _parallel_done: Dict[int, dict] = {}
            for _orig_idx, tc in enumerate(pending):
                if tc["tool"] not in _WRITE_TOOLS_PAR:
                    for _ptc, _ptr in _par_results:
                        if _ptc is tc:
                            _parallel_done[_orig_idx] = _ptr
                            break

            # Execute pending tools: local tools first, then remote Aria tools
            tool_results = []
            cancelled_by_user = False
            for idx, tc in enumerate(pending):
                # Check if user cancelled (ESC / Ctrl+C) between tool executions
                if self.cancel_event and self.cancel_event.is_set():
                    cancelled_by_user = True
                    break

                tool_name = tc["tool"]
                tool_params = tc["params"]

                # Note: _print_tool_call already called by on_tool_call during streaming

                # If this tool was already executed in the parallel batch, reuse result
                if idx in _parallel_done:
                    tr = _parallel_done[idx]
                    tool_elapsed = 0.0
                    tool_time_total += tool_elapsed
                    # (fall through to result formatting below)
                    # Duplicate the summarisation block inline for clarity
                    _tool_summary = _format_tool_result(tool_name, tr)
                    tool_results.append({"tool": tool_name, "result": tr, "summary": _tool_summary})
                    _print_tool_result(tool_name, tr)
                    continue

                # Ask user confirmation for destructive local tools
                if tool_name in _CONFIRM_TOOLS:
                    _stop_live()
                    try:
                        _cfg_policy = self.config.get("command_policy", "safe")
                        if not _confirm_tool_execution(tool_name, tool_params,
                                                       config_policy=_cfg_policy):
                            cancelled_by_user = True
                            if HAS_RICH:
                                console.print("\n  [dim]Cancelled[/dim]")
                            break
                        # If user chose "Allow & set balanced", persist to config
                        if tool_params.pop("_upgrade_policy", False):
                            self.config["command_policy"] = "balanced"
                            try:
                                save_config(self.config)
                                if HAS_RICH:
                                    console.print("  [dim]策略已升级为 balanced 并保存[/dim]")
                            except Exception:
                                pass
                    except KeyboardInterrupt:
                        cancelled_by_user = True
                        break

                # Fire pre-tool hook (fire-and-forget, never blocks)
                _run_hook("pre_tool", tool_name, tool_params)

                try:
                    tool_start = time.time()
                    if tool_name in LOCAL_TOOLS:
                        # Inject current config policy for run_command
                        # (avoids double-blocking after user approval)
                        if tool_name == "run_command" and "policy" not in tool_params:
                            tool_params["policy"] = self.config.get("command_policy", "safe")
                        # Local tools: show spinner for slower ones
                        _slow_local = {"write_file", "edit_file", "run_command", "search_code"}
                        if tool_name in _slow_local and HAS_RICH:
                            with console.status("", spinner="dots", spinner_style="dim"):
                                tr = execute_local_tool(tool_name, tool_params)
                        else:
                            tr = execute_local_tool(tool_name, tool_params)
                    else:
                        # Spinner for remote tool calls
                        progress_label = f"  Running {tool_name}..."
                        if len(pending) > 1:
                            progress_label = f"  [{idx+1}/{len(pending)}] Running {tool_name}..."
                        if HAS_RICH:
                            with console.status(f"[dim]{progress_label}[/dim]", spinner="dots"):
                                tr = await execute_aria_tool(
                                    self.api_url, tool_name, tool_params,
                                    auth_token=auth_token)
                        else:
                            print(progress_label, end="", flush=True)
                            tr = await execute_aria_tool(
                                self.api_url, tool_name, tool_params,
                                auth_token=auth_token)
                    tool_elapsed = time.time() - tool_start
                    tool_time_total += tool_elapsed
                    # Fire post-tool hook
                    _run_hook("post_tool", tool_name, tool_params, tr)
                except KeyboardInterrupt:
                    cancelled_by_user = True
                    break

                _print_tool_result(tool_name, tr, tool_elapsed, params=tool_params)

                summary = _format_tool_summary(tool_name, tr)
                tool_results.append({"tool": tool_name, "result": summary})

            # User cancelled during tool execution
            if cancelled_by_user:
                _stop_live()
                if HAS_RICH:
                    console.print("\n[dim]Cancelled[/dim]")
                else:
                    print("\n  (cancelled)")
                result = {"success": True, "cancelled": True}
                break

            # Build follow-up message with tool results for next round
            followup = "Tool results:\n"
            for tr in tool_results:
                followup += f"\n[{tr['tool']}]: {tr['result']}\n"
            followup += "\nPlease continue your analysis using these results."

            self.conversation.append({"role": "assistant", "content": total_response})
            self.conversation.append({"role": "user", "content": followup})
            current_message = followup
            total_response = ""

        # --- End of agentic loop ---
        _esc_watcher.stop()
        self._streaming = False
        elapsed = time.time() - start_time

        if result.get("success") and not result.get("cancelled"):
            final_text = total_response or result.get("response", "")

            # Flush any unclosed LaTeX buffer (e.g. stream cut off mid-formula).
            # This only matters for the non-batch plain-print path; in batch-render
            # mode the full raw response is rendered below anyway.
            if _in_latex[0] and _latex_buf[0]:
                _leftover = _flush_latex_buf()
                final_text = (final_text or "") + _leftover
                if _use_plain_print[0] and not _use_batch_render[0]:
                    print(_leftover, end="", flush=True)

            # Stop progressive Live display (final state stays in terminal)
            _stop_live()

            # ── Render final response ──────────────────────────────────────
            if _use_batch_render[0] and final_text and HAS_RICH:
                # Ollama batch-render: spinner was kept running during generation.
                # Stop it and render the COMPLETE response through Rich Markdown +
                # _strip_latex in one pass.  This correctly handles:
                #   • "$$" split across two single-"$" tokens (tokeniser-dependent)
                #   • All LaTeX spacing commands (\; \, \quad etc.)
                #   • Markdown headings, bold, tables
                _stop_spinner()
                console.print(Markdown(_strip_latex(final_text)))
            elif token_count == 0 and final_text and HAS_RICH:
                # Non-streamed response (e.g. complete() API path): render markdown.
                console.print()
                console.print(Markdown(_strip_latex(final_text)))

            self.conversation.append({"role": "assistant", "content": final_text})

            # Metadata line — detailed stats
            meta_parts = [f"{elapsed:.1f}s"]

            # Token stats — prefer API usage, fallback to manual count
            prompt_t = total_usage.get("prompt_tokens", 0)
            completion_t = total_usage.get("completion_tokens", 0) or token_count
            think_t = total_usage.get("thinking_tokens", 0) or thinking_tokens
            total_t = prompt_t + completion_t + think_t

            if total_t > 0:
                parts = []
                if prompt_t > 0:
                    parts.append(f"in: {prompt_t:,}")
                if completion_t > 0:
                    parts.append(f"out: {completion_t:,}")
                if think_t > 0:
                    parts.append(f"think: {think_t:,}")
                meta_parts.append(f"{total_t:,} tokens ({', '.join(parts)})")
                # token/s speed based on output tokens and actual generation time
                gen_time = elapsed - tool_time_total
                if completion_t > 0 and gen_time > 0.5:
                    tps = completion_t / gen_time
                    meta_parts.append(f"{tps:.0f} t/s")
            elif token_count > 0:
                meta_parts.append(f"{token_count:,} tokens")
                gen_time = elapsed - tool_time_total
                if gen_time > 0.5:
                    meta_parts.append(f"{token_count / gen_time:.0f} t/s")

            if tool_time_total > 0:
                meta_parts.append(f"tools: {tool_time_total:.1f}s")
            if provider != "aws":
                meta_parts.append(provider)
            if all_tools:
                tool_names = list(dict.fromkeys(all_tools))  # dedupe preserving order
                meta_parts.append(" ".join(tool_names))

            if HAS_RICH:
                copy_hint = "  [dim]/copy[/dim]" if self._last_response else ""
                console.print(f"\n[dim]{' · '.join(meta_parts)}[/dim]{copy_hint}")
                # One-time warning if first response and input tokens are very high
                # (>2000 for a short message suggests a heavy system prompt)
                _is_first_turn = (self._session_turns == 0)
                if _is_first_turn and prompt_t > 2000:
                    _msg_len = len(message)
                    _sys_est = max(0, prompt_t - _msg_len // 3)
                    if _sys_est > 1500:
                        console.print(
                            f"[dim]  ℹ 系统提示词约 {_sys_est:,} tokens，"
                            f"较长的对话会较快占满上下文。"
                            f"可用 /compact 压缩历史，或用 /clear 重置。[/dim]"
                        )
                console.print(Rule(style="dim"))
            else:
                print(f"\n{' · '.join(meta_parts)}\n")

            # ── Accumulate session-level usage stats (for /cost) ──────────
            self._session_input_tokens  += prompt_t or 0
            self._session_output_tokens += completion_t or 0
            self._session_thinking_tokens += think_t or 0
            self._session_turns += 1
            self._last_response = final_text   # for /copy

            # Fire response_done lifecycle hook
            _run_event_hook("response_done", {
                "ARIA_RESPONSE":  (final_text or "")[:500],
                "ARIA_PROVIDER":  provider,
                "ARIA_TOKENS":    str((prompt_t or 0) + (completion_t or 0)),
                "ARIA_SESSION":   self.session_id,
            })

            # Trim conversation history to prevent unbounded growth
            if len(self.conversation) > 40:
                self.conversation = self.conversation[-40:]

            # Auto-warn when context approaching limit; auto-compact at 95%
            _est = sum(len(m.get("content", "")) for m in self.conversation) // 3
            _mkey = resolve_model_key(self.config.get("model", "qwen2.5:7b"))
            _default_m2 = MODELS.get("qwen7b") or MODELS.get("qwen-fast") or next(iter(MODELS.values()))
            _max = MODELS.get(_mkey, _default_m2).get("num_ctx", 16384)
            _pct = min(100, int(_est / _max * 100))
            if _pct >= 95:
                # Auto-compact: silently summarise and truncate
                try:
                    await self.commands._smart_compact_async(silent=True)
                except Exception:
                    # Fallback: hard trim
                    self.conversation = self.conversation[-8:]
                if HAS_RICH:
                    console.print("  [dim]↩ Auto-compacted context (was 95%+ full)[/dim]")
            elif _pct >= 75 and HAS_RICH:
                _color = "yellow" if _pct < 90 else "red"
                console.print(
                    f"  [{_color}]⚠ Context {_pct}% full "
                    f"({_est:,}/{_max:,} tokens) — /compact to free space[/{_color}]"
                )

            # Auto-save session
            if self.config.get("auto_save_sessions"):
                try:
                    self.session_mgr.save_session(self.session_id, self.conversation)
                except Exception:
                    pass

    def _bottom_toolbar(self):
        """Bottom toolbar content for prompt_toolkit."""
        # Context usage estimate
        conv = self.conversation
        est_tokens = sum(len(m.get("content", "")) for m in conv) // 3
        mkey = self.config.get("model", "qwen2.5:7b")
        max_ctx = get_model_cfg(mkey).get("num_ctx", 16384)
        ctx_pct = min(100, int(est_tokens / max_ctx * 100))
        ctx_color = "#606060" if ctx_pct < 60 else ("#aa8800" if ctx_pct < 85 else "#cc4444")
        ctx_str = f'<style fg="{ctx_color}">ctx {est_tokens:,} / {max_ctx:,}</style>' if conv else ""
        local_indicator = ' <style fg="#448844">⬡ local</style>' if self.config.get("local_mode") else ""
        return HTML(
            f'<style fg="#606060">'
            f'  {self._status_line()}'
            f'{local_indicator}'
            f'  ·  /help'
            f'  ·  esc cancel'
            f'{"  ·  " + ctx_str if ctx_str else ""}'
            f'</style>'
        )

    async def _startup_health_check(self):
        """Async Ollama + cloud connectivity probe displayed after the header."""
        if not HAS_RICH:
            return
        try:
            import aiohttp as _aio
            parts = []
            ollama_url = self.config.get("ollama_url", "http://localhost:11434")
            try:
                async with _aio.ClientSession() as s:
                    async with s.get(
                        f"{ollama_url}/api/tags",
                        timeout=_aio.ClientTimeout(total=2),
                    ) as r:
                        if r.status == 200:
                            _tags = await r.json()
                            _n = len(_tags.get("models", []))
                            self._ollama_alive = True
                            parts.append(
                                f"[green]● Ollama[/green][dim] · {_n} models[/dim]"
                                if _n else "[green]● Ollama[/green]"
                            )
                        else:
                            parts.append("[dim]○ Ollama[/dim]")
            except Exception:
                parts.append("[dim]○ Ollama[/dim]")

            # Cloud provider check (only if API key is set)
            if self.config.get("auth_token") or os.getenv("ANTHROPIC_API_KEY"):
                parts.append("[cyan]● Cloud[/cyan]")

            if parts:
                console.print("  " + "  ".join(parts))
        except ImportError:
            pass

    async def run_interactive(self):
        """Run the interactive REPL loop."""
        self.print_header()
        await self._startup_health_check()

        # ── Start MCP servers (non-blocking background task) ─────────────
        if _HAS_MCP and not self._mcp_started:
            self._mcp_started = True
            async def _start_mcp():
                global _mcp_registry
                try:
                    from mcp_client import MCPToolRegistry
                    self._mcp_registry = MCPToolRegistry()
                    results = await self._mcp_registry.start_all()
                    if results:
                        n = self._mcp_registry.register_into(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)
                        _mcp_registry = self._mcp_registry
                        if n and HAS_RICH:
                            console.print(f"  [dim]MCP: {n} tools from {len(results)} server(s)[/dim]")
                except Exception as _exc:
                    logger.debug("MCP startup error: %s", _exc)
            asyncio.create_task(_start_mcp())

        # ── Start plugin hot-reload watcher ───────────────────────────────
        if _HAS_PLUGIN:
            global _plugin_watcher
            if _plugin_watcher is None:
                try:
                    _plugin_watcher = PluginWatcher(LOCAL_TOOLS, LOCAL_TOOL_SCHEMAS)
                    asyncio.create_task(_plugin_watcher.start())
                except Exception:
                    pass

        while self.running:
            try:
                if self._pt_session:
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self._pt_session.prompt(
                            [("class:prompt", "❯ ")],
                            bottom_toolbar=self._bottom_toolbar,
                        ),
                    )
                    user_input = user_input.strip()
                elif HAS_RICH:
                    user_input = console.input("[bold #C08050]❯[/bold #C08050] ").strip()
                else:
                    user_input = input("> ").strip()

                if not user_input:
                    continue

                # Multi-line input: start with """ to enter multi-line mode
                if user_input == '"""' or user_input.startswith('"""'):
                    lines = []
                    if user_input != '"""':
                        lines.append(user_input[3:])  # Text after opening """
                    if HAS_RICH:
                        console.print("[dim]  Multi-line mode (type \"\"\" to end)[/dim]")
                    else:
                        print('  Multi-line mode (type """ to end)')
                    while True:
                        try:
                            line = input("  ... ")
                            if line.strip() == '"""':
                                break
                            lines.append(line)
                        except (EOFError, KeyboardInterrupt):
                            break
                    user_input = "\n".join(lines).strip()
                    if not user_input:
                        continue

                if user_input.lower() in ("exit", "quit", "q"):
                    if self.conversation and self.config.get("auto_save_sessions"):
                        self.session_mgr.save_session(self.session_id, self.conversation)
                        self.config["last_session_id"] = self.session_id
                    if HAS_RICH:
                        console.print("[dim]Goodbye[/dim]")
                    else:
                        print("Goodbye")
                    break

                if self.commands.is_command(user_input):
                    await self.commands.execute(user_input)
                    continue

                # Auto memory trigger: "记住 X" / "remember that X" → silent /note
                _mem_fact = _check_memory_trigger(user_input)
                if _mem_fact:
                    self.commands.cmd_note(_mem_fact)

                await self.send_message(user_input)

            except KeyboardInterrupt:
                _esc_watcher.stop()
                if self._streaming and self.cancel_event:
                    self.cancel_event.set()
                    self._streaming = False
                    if HAS_RICH:
                        console.print("\n[dim]Cancelled[/dim]")
                    else:
                        print("\n  (cancelled)")
                else:
                    if HAS_RICH:
                        console.print("\n[dim]Press ESC to cancel · Ctrl+D to exit[/dim]")
                    else:
                        print("\nESC to cancel · Ctrl+D to exit")
            except EOFError:
                break

        save_config(self.config)

    async def run_prompt(self, prompt: str, json_output: bool = False,
                         fmt: str = "table", output_file: str = None, quiet: bool = False):
        """Run a single prompt (non-interactive / pipe mode)."""
        model = self.config.get("model", "qwen2.5:7b")
        thinking_mode = self.config.get("thinking_mode", "auto")
        auth_token = self.config.get("auth_token")
        user_context = _build_user_context(self.config)

        local_mode = self.config.get("local_mode", False)

        # Dispatch slash commands in -p mode (same as interactive REPL loop).
        # Without this, /memory /note /init /review are sent to the LLM as plain text.
        _stripped_prompt = prompt.strip()
        if self.commands.is_command(_stripped_prompt):
            await self.commands.execute(_stripped_prompt)
            return

        # Auto-inject referenced local file contents before the LLM call (-p mode)
        _file_inject = _try_inject_file_paths(prompt)
        if _file_inject:
            prompt = _file_inject + prompt

        _curr_model_id_p = self.config.get("model", "")
        _model_has_tools_p = False
        if _HAS_MODEL_CAP:
            try:
                _mc_p = get_model_capability(_curr_model_id_p)
                _model_has_tools_p = bool(_mc_p.tool_calls and _mc_p.context_window >= 8192)
            except Exception:
                pass

        deterministic: dict = {"success": False}
        if not _model_has_tools_p:
            deterministic = _try_handle_market_snapshot_analysis(prompt)
        if not deterministic.get("success"):
            deterministic = _try_handle_stock_chart_analysis(prompt)
        if deterministic.get("success") or _is_stock_chart_analysis_request(prompt):
            result = deterministic
        else:
            # Spinner for terminal usage: gives visual feedback while the model generates.
            # Only starts when we actually need to call the LLM (not for deterministic responses).
            _prompt_spinner = None
            if HAS_RICH and sys.stdout.isatty():
                try:
                    _prompt_spinner = console.status("", spinner="dots", spinner_style="dim")
                    _prompt_spinner.__enter__()
                except Exception:
                    _prompt_spinner = None
            try:
                if local_mode:
                    result = await stream_ollama(
                        self.config.get("ollama_url", "http://localhost:11434"),
                        prompt, [], model=model,
                    )
                else:
                    # Try AWS, fallback to Ollama
                    result = await stream_chat(
                        self.api_url, prompt, [],
                        model=model, thinking_mode=thinking_mode,
                        user_context=user_context, auth_token=auth_token,
                    )
                    if not result.get("success"):
                        result = await stream_ollama(
                            self.config.get("ollama_url", "http://localhost:11434"),
                            prompt, [], model=model,
                        )
            finally:
                if _prompt_spinner is not None:
                    try:
                        _prompt_spinner.__exit__(None, None, None)
                    except Exception:
                        pass

        # Execute any pending tool calls (write_file / run_command) generated by
        # the code-block fallback in stream_ollama.  This makes -p mode behave
        # the same as interactive mode for code generation tasks.
        pending = result.get("tool_calls_pending", [])
        if pending and result.get("success"):
            for tc in pending:
                tool_name  = tc.get("tool", "")
                tool_params = tc.get("params", {})
                if tool_name in LOCAL_TOOLS:
                    fn = LOCAL_TOOLS[tool_name][0]
                    tr = fn(tool_params)
                    if not quiet:
                        if tool_name == "write_file":
                            _path = tool_params.get("path", "")
                            _status = "Created" if tr.get("success") else "Failed"
                            msg = f"{_status}: {_path}"
                            print(msg if not HAS_RICH else msg, file=sys.stderr)
                        elif tool_name == "run_command":
                            _out = tr.get("data", {}).get("stdout", "") or tr.get("error", "")
                            if _out:
                                print(_out[:2000])

        if json_output or fmt == "json":
            content = json.dumps(result, ensure_ascii=False, indent=2)
        elif fmt == "csv":
            content = f"role,content\nassistant,\"{result.get('response', '').replace(chr(34), chr(34)+chr(34))}\""
        elif fmt == "md":
            content = f"# Aria Code AI Response\n\n{result.get('response', '')}\n"
        else:
            content = result.get("response", "") if result.get("success") else f"Error: {result.get('error', 'Unknown')}"

        # Output routing
        if output_file:
            with open(output_file, "w") as f:
                f.write(content)
            if not quiet:
                console.print(f"[green]Saved to {output_file}[/green]" if HAS_RICH
                              else f"Saved: {output_file}")
        else:
            if not result.get("success") and fmt == "table":
                print(f"Error: {result.get('error', 'Unknown')}", file=sys.stderr)
                sys.exit(1)
            # In default (table) format: render with Rich Markdown when output is
            # a terminal.  This gives properly formatted headings, bold, and tables
            # in interactive use.  When piped/redirected, fall back to plain text
            # for scripting compatibility.
            if HAS_RICH and fmt == "table" and sys.stdout.isatty() and result.get("success"):
                console.print(Markdown(_strip_latex(content)))
            else:
                print(content)

    async def run_watch(self, command_fn, interval: int, cmd_args: str):
        """Run a command repeatedly with interval (like Unix watch)."""
        try:
            while True:
                if not self.config.get("_quiet"):
                    os.system("clear" if os.name == "posix" else "cls")
                    ts = datetime.now().strftime("%H:%M:%S")
                    if HAS_RICH:
                        console.print(f"[dim]Every {interval}s | {ts} | Ctrl+C to stop[/dim]\n")
                    else:
                        print(f"Every {interval}s | {ts} | Ctrl+C to stop\n")

                await command_fn(cmd_args)

                await asyncio.sleep(interval)
        except (KeyboardInterrupt, asyncio.CancelledError):
            if HAS_RICH:
                console.print("\n[dim]Watch stopped[/dim]")
            else:
                print("\nStopped")


# ============================================================================
# CLI Entry Point
# ============================================================================

async def main():
    parser = argparse.ArgumentParser(
        prog="aria-code",
        description="Aria Code — Quantitative Investment Terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s                                  # Interactive REPL
  %(prog)s --resume                         # Resume last session
  %(prog)s -p "Analyze AAPL technicals"     # Single query
  %(prog)s quote AAPL MSFT                  # Quick quote
  %(prog)s backtest momentum SPY            # Strategy backtest
  %(prog)s --thinking                       # Enable thinking mode
  %(prog)s -p "AAPL PE ratio" --json        # JSON output
  %(prog)s -p "分析AAPL" --output report.md  # Save to file
  %(prog)s -p "报价" --format csv --quiet    # CSV, data only
  %(prog)s quote AAPL --watch 30             # Refresh every 30s
  echo "AAPL MSFT" | %(prog)s -p "比较"      # Unix pipe
        """
    )

    parser.add_argument("--version", "-V", action="version", version=f"aria-code {__version__}")
    parser.add_argument("-p", "--prompt", help="Single prompt (non-interactive)")
    parser.add_argument("--model", help="AI model: sonata|prelude|sonata-thinking|prelude-thinking or full Ollama ID")
    parser.add_argument("--thinking", action="store_true", help="Enable thinking mode")
    parser.add_argument("--json", action="store_true", help="JSON output (with -p)")
    parser.add_argument("--format", choices=["table", "json", "csv", "md"], default="table",
                        help="Output format (default: table)")
    parser.add_argument("--output", "-o", help="Save output to file")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode (data only, no UI)")
    parser.add_argument("--watch", "-w", type=int, metavar="SECS", help="Refresh interval in seconds")
    parser.add_argument("--url", help="Backend API URL")
    parser.add_argument("--local", action="store_true", help="Local-only mode: skip AWS, use Ollama directly")
    parser.add_argument("--resume", action="store_true", help="Resume last session")
    parser.add_argument("--session", help="Load specific session ID")
    parser.add_argument("command", nargs="?", help="Direct command (quote, backtest, etc.)")
    parser.add_argument("args", nargs="*", help="Command arguments")

    args = parser.parse_args()

    config = load_config()

    # Apply syntax theme from config (P3)
    global _SYNTAX_THEME
    _SYNTAX_THEME = config.get("syntax_theme", "monokai")

    # Apply CLI overrides
    if args.model:
        mkey = resolve_model_key(args.model)
        config["model"] = MODELS[mkey]["id"] if mkey in MODELS else args.model
    if getattr(args, "local", False):
        config["local_mode"] = True
    if args.thinking:
        config["thinking_mode"] = "thinking"
    if args.url:
        config["api_url"] = args.url

    terminal = ArtheraTerminal(config)

    # Resume session
    if args.resume or args.session:
        sid = args.session or config.get("last_session_id")
        if sid:
            data = terminal.session_mgr.load_session(sid)
            if data:
                terminal.conversation = data.get("messages", [])
                terminal.session_id = data["id"]
                title = data.get("metadata", {}).get("title", "Untitled")
                n = len(terminal.conversation)
                if HAS_RICH:
                    console.print(f"[green]Resumed: {title} ({n} messages)[/green]")
                else:
                    print(f"Resumed: {title} ({n} msgs)")

    # Shared output flags
    fmt = args.format if hasattr(args, 'format') else "table"
    output_file = args.output if hasattr(args, 'output') else None
    quiet = args.quiet if hasattr(args, 'quiet') else False
    watch_interval = args.watch if hasattr(args, 'watch') else None

    # Store quiet flag for watch mode
    terminal.config["_quiet"] = quiet

    # Unix pipe: read stdin if not a TTY and prepend to prompt
    piped_input = ""
    if not sys.stdin.isatty():
        piped_input = sys.stdin.read().strip()
        if piped_input and args.prompt:
            args.prompt = f"Context data:\n{piped_input}\n\nUser request: {args.prompt}"
        elif piped_input and not args.prompt:
            args.prompt = piped_input

    # Mode 1: Single prompt
    if args.prompt:
        if watch_interval:
            await terminal.run_watch(
                lambda _: terminal.run_prompt(args.prompt, json_output=args.json, fmt=fmt, output_file=output_file, quiet=quiet),
                watch_interval, ""
            )
        else:
            await terminal.run_prompt(args.prompt, json_output=args.json, fmt=fmt, output_file=output_file, quiet=quiet)
        return

    # Mode 2: Direct command
    if args.command:
        cmd = args.command.lower()
        cmd_args = " ".join(args.args)

        # Build the command function for potential watch wrapping
        async def run_direct_cmd(_):
            if cmd == "quote":
                await terminal.commands.cmd_quote(cmd_args)
            elif cmd == "backtest":
                await terminal.commands.cmd_backtest(cmd_args)
            elif cmd == "health":
                await terminal.commands.cmd_health(cmd_args)
            elif cmd == "tools":
                terminal.commands.cmd_tools(cmd_args)
            elif cmd == "skills":
                terminal.commands.cmd_skills(cmd_args)
            elif cmd == "sessions":
                terminal.commands.cmd_sessions(cmd_args)
            elif cmd in ("watch", "watchlist"):
                terminal.commands.cmd_watch(cmd_args)
            elif cmd == "export":
                await terminal.commands.cmd_export(cmd_args)
            else:
                await terminal.run_prompt(f"{cmd} {cmd_args}".strip(),
                                          json_output=args.json, fmt=fmt,
                                          output_file=output_file, quiet=quiet)

        if watch_interval and cmd in ("quote", "health"):
            await terminal.run_watch(run_direct_cmd, watch_interval, cmd_args)
        else:
            await run_direct_cmd(None)
        return

    # Mode 3: Interactive REPL (default)
    await terminal.run_interactive()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        sys.exit(0)
