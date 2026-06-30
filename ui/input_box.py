"""Prompt-toolkit input panel — lightweight Claude Code-style input block.

Layout:
  ──────────────────────────────────────────────  ← subtle top rule
   › cursor_                                      ← padded input row
  ──────────────────────────────────────────────  ← subtle bottom rule
  model  ·  ~/workspace                          ← dim status bar
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Callable, Optional

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout import Float, FloatContainer, HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.layout.processors import Processor, Transformation
    from prompt_toolkit.styles import Style
    from prompt_toolkit.widgets import TextArea
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

    class Processor:  # type: ignore[no-redef]
        pass

    class Transformation:  # type: ignore[no-redef]
        def __init__(self, fragments, source_to_display=None, display_to_source=None):
            self.fragments = fragments
            self.source_to_display = source_to_display or (lambda i: i)
            self.display_to_source = display_to_source or (lambda i: i)

    class Style:  # type: ignore[no-redef]
        @staticmethod
        def from_dict(values):
            return values

    Application = Buffer = KeyBindings = Dimension = None  # type: ignore
    Float = FloatContainer = HSplit = Layout = VSplit = Window = None  # type: ignore
    FormattedTextControl = CompletionsMenu = TextArea = None  # type: ignore


# ── Theme detection ────────────────────────────────────────────────────────────

def detect_terminal_theme() -> str:
    explicit = os.getenv("ARIA_INPUT_THEME", "").strip().lower()
    if explicit in {"dark", "light"}:
        return explicit
    colorfgbg = os.getenv("COLORFGBG", "")
    if colorfgbg:
        try:
            return "dark" if int(colorfgbg.split(";")[-1]) < 8 else "light"
        except ValueError:
            pass
    if os.uname().sysname == "Darwin":
        try:
            r = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=0.2, check=False,
            )
            return "dark" if (r.returncode == 0 and "dark" in r.stdout.lower()) else "light"
        except Exception:
            pass
    return "dark"


# ── Config ─────────────────────────────────────────────────────────────────────

@dataclass
class PanelInputConfig:
    prompt: str = "› "
    placeholder: str = ""
    theme: str = "auto"
    lang: str = "en"

    est_tokens: int = 0
    max_tokens: int = 131072

    # Status bar display
    model_label: str = ""
    cwd: str = ""

    # Robot mascot — show animated dot in status bar
    show_robot: bool = True

    # Legacy fields kept for call-site compatibility
    privacy: str = "local-only"
    tools_count: int = 0
    skills_count: int = 0
    ollama_status: str = ""
    pending_file: str = ""
    permission_mode: str = "workspace-write"
    git_branch: str = ""
    git_dirty: bool = False
    mcp_running: int = 0
    mcp_total: int = 0
    mcp_tool_count: int = 0

    # Resolved by .resolved()
    fg: str = ""
    accent: str = ""
    accent_y: str = ""
    accent_b: str = ""
    muted: str = ""
    dim: str = ""
    sep: str = ""
    input_bg: str = ""   # intentional input-area background
    ph_color: str = ""   # very dim placeholder
    box: str = ""        # rounded border color

    # Completion menu palette (copper, theme-aware)
    menu_bg: str = ""
    menu_fg: str = ""
    menu_sel_bg: str = ""
    menu_sel_fg: str = ""
    menu_meta: str = ""
    menu_meta_cur: str = ""
    scroll_bg: str = ""
    scroll_btn: str = ""
    hi: str = ""         # fuzzy-match highlight (copper)

    def resolved(self) -> "PanelInputConfig":
        theme = self.theme if self.theme != "auto" else detect_terminal_theme()
        is_zh = self.lang.lower().startswith("zh")
        placeholder = self.placeholder or (
            "问 Aria、编辑文件或运行命令…  / 命令  @ 上下文  ! shell"
            if is_zh
            else "Ask Aria, edit files, or run commands…  / commands  @ context  ! shell"
        )
        if theme == "dark":
            return replace(self, theme=theme, placeholder=placeholder,
                fg="#c9d1d9",
                accent="#3fb950", accent_y="#d29922", accent_b="#79c0ff",
                muted="#6e7781", dim="#484f58", sep="#2d333b",
                input_bg="default",   # transparent — box border defines the zone
                ph_color="#6e7781",   # secondary text, readable on dark terminals
                box="#C08050",        # copper — Aria's brand accent on the frame
                menu_bg="#161b22", menu_fg="#c9d1d9",
                menu_sel_bg="#3a2e20", menu_sel_fg="#e8c9a6",
                menu_meta="#6e7681", menu_meta_cur="#c0a585",
                scroll_bg="#161b22", scroll_btn="#C08050",
                hi="#C08050",
            )
        return replace(self, theme="light", placeholder=placeholder,
            fg="#24292f",
            accent="#1a7f37", accent_y="#9a6700", accent_b="#0969da",
            muted="#57606a", dim="#8c959f", sep="#d0d7de",
            input_bg="default",
            ph_color="#6e7781",
            box="#9a6700",
            menu_bg="#f2eee4", menu_fg="#24292f",
            menu_sel_bg="#e7e1d3", menu_sel_fg="#8a5a00",
            menu_meta="#6e7781", menu_meta_cur="#8a5a00",
            scroll_bg="#e7e1d3", scroll_btn="#9a6700",
            hi="#9a6700",
        )


# ── Processor (mode badge + placeholder) ──────────────────────────────────────

class PromptAndPlaceholderProcessor(Processor):
    def __init__(self, get_prefix: Callable[[], list], placeholder: str,
                 is_empty: Callable[[], bool]) -> None:
        self.get_prefix = get_prefix
        self.placeholder = placeholder
        self.is_empty = is_empty

    def apply_transformation(self, ti) -> Transformation:
        if ti.lineno == 0:
            empty = self.is_empty()
            prefix = self.get_prefix()
            pw = sum(len(t) for _, t in prefix)
            frags = list(prefix)
            if empty:
                frags.append(("class:ph", self.placeholder))
            frags.extend(ti.fragments)
            return Transformation(
                frags,
                source_to_display=lambda i: pw + i,
                display_to_source=lambda i: 0 if (i <= pw or empty) else max(0, i - pw),
            )
        return Transformation(ti.fragments)


PlaceholderProcessor = PromptAndPlaceholderProcessor


INPUT_MAX_HEIGHT = 6


# ── Style ──────────────────────────────────────────────────────────────────────

def _build_style(cfg: PanelInputConfig) -> Style:
    return Style.from_dict({
        # Input row: transparent bg — the rounded box border defines the zone
        "input-bg":  cfg.fg if cfg.input_bg == "default" else f"{cfg.fg} bg:{cfg.input_bg}",
        "ph":        cfg.ph_color,
        # Rounded box border (Claude Code style)
        "box":       cfg.box,
        # Mode prompt glyph — always copper (brand). 5-color discipline:
        # red/green are reserved for 涨跌 semantics, never for chrome.
        "mode-chat": f"bold {cfg.box}",
        "mode-cmd":  f"bold {cfg.box}",
        "mode-file": f"bold {cfg.box}",
        "prompt":    cfg.muted,
        # Divider (transparent bg — terminal bg shows through)
        "divider":   cfg.sep,
        # Status bar (transparent bg)
        "st-model":  cfg.muted,
        "st-sep":    cfg.dim,
        "st-cwd":    cfg.dim,
        "tok-warn":  cfg.box,            # copper — context-pressure caution
        "tok-crit":  "#f85149",          # red — critical only
        # Completion menu — theme-aware copper palette (matches terminal theme)
        "completion-menu":                    f"bg:{cfg.menu_bg} {cfg.menu_fg}",
        "completion-menu.completion":         f"bg:{cfg.menu_bg} {cfg.menu_fg}",
        "completion-menu.completion.current": f"bg:{cfg.menu_sel_bg} {cfg.menu_sel_fg} bold",
        "completion-menu.meta.completion":         f"bg:{cfg.menu_bg} {cfg.menu_meta}",
        "completion-menu.meta.completion.current": f"bg:{cfg.menu_sel_bg} {cfg.menu_meta_cur}",
        "completion-menu.multi-column-meta":       f"bg:{cfg.menu_bg} {cfg.menu_meta}",
        "scrollbar.background":               f"bg:{cfg.scroll_bg}",
        "scrollbar.button":                   f"bg:{cfg.scroll_btn}",
        # Fuzzy-match highlight classes (shared with ui/completer.py) — copper
        "fz-hi":                              f"bold {cfg.hi}",
        "fz-cat":                             cfg.dim,
    })


# ── Row builders ───────────────────────────────────────────────────────────────

def _input_rule(cfg: PanelInputConfig) -> list:
    w = shutil.get_terminal_size((80, 24)).columns
    return [("class:divider", "─" * w)]


def _input_pad() -> list:
    return [("", " ")]


def _mode_prefix(cfg: PanelInputConfig, text_getter: Callable[[], str]) -> list:
    """Claude Code-style ›  glyph — color shifts by detected input mode."""
    txt = text_getter().lstrip()
    if txt.startswith("/"):
        return [("class:mode-cmd",  "› ")]
    if txt.startswith("@") or txt.startswith("!"):
        return [("class:mode-file", "› ")]
    return     [("class:mode-chat", "› ")]


def _status_bar(cfg: PanelInputConfig) -> list:
    """Compact runtime row: model · workspace · MCP · permissions · context."""
    from .robot import get_status_dot

    tick = int(time.monotonic() * 4)  # 4 fps tick without a background thread
    parts: list = []

    if cfg.show_robot:
        dot_frags = get_status_dot(tick)
        parts.extend(dot_frags)
        parts.append(("class:st-sep", "  "))

    width = shutil.get_terminal_size((80, 24)).columns
    model_label = cfg.model_label
    model_limit = 24 if width >= 90 else 19
    if len(model_label) > model_limit:
        model_label = model_label[: model_limit - 1] + "…"
    if model_label:
        parts.append(("class:st-model", model_label))

    workspace = cfg.git_branch or os.path.basename(cfg.cwd.rstrip(os.sep))
    if workspace:
        if model_label:
            parts.append(("class:st-sep", "  ·  "))
        if cfg.git_branch and cfg.git_dirty:
            workspace += "*"
        parts.append(("class:st-cwd", workspace))

    if cfg.mcp_total:
        mcp_label = (
            f"MCP {cfg.mcp_tool_count}"
            if cfg.mcp_running == cfg.mcp_total and cfg.mcp_tool_count
            else f"MCP {cfg.mcp_running}/{cfg.mcp_total}"
        )
        parts += [("class:st-sep", "  ·  "), ("class:st-cwd", mcp_label)]

    permission = {
        "read-only": "ro",
        "workspace-write": "rw",
        "full-access": "full",
    }.get(cfg.permission_mode, cfg.permission_mode)
    if permission:
        parts += [("class:st-sep", "  ·  "), ("class:st-cwd", permission)]

    ratio = cfg.est_tokens / max(cfg.max_tokens, 1)
    tc = "tok-crit" if ratio >= 0.85 else ("tok-warn" if ratio >= 0.60 else "st-cwd")
    context_label = "ctx <1%" if cfg.est_tokens > 0 and ratio < 0.01 else f"ctx {ratio:.0%}"
    parts += [("class:st-sep", "  ·  "), (f"class:{tc}", context_label)]

    shortcuts = (
        "? 快捷键 · / 命令 · @ 上下文 · ! shell"
        if cfg.lang.lower().startswith("zh")
        else "? shortcuts · / commands · @ context · ! shell"
    )
    used = sum(len(text) for _, text in parts)
    if width >= 110 and used + len(shortcuts) + 4 <= width:
        parts += [("class:st-sep", "    "), ("class:st-cwd", shortcuts)]

    return parts


# ── Main ───────────────────────────────────────────────────────────────────────

def _build_panel_input_application(
    *,
    completer=None,
    history=None,
    status_text: Callable[[], str] | str = "",   # kept for compat
    config: Optional[PanelInputConfig] = None,
) -> Optional[Application]:
    cfg = (config or PanelInputConfig()).resolved()
    if not HAS_PROMPT_TOOLKIT:
        return None

    def _accept(buf: Buffer) -> bool:
        app.exit(result=buf.text)
        return True

    def _get_text() -> str:
        try:
            return text_area.text
        except Exception:
            return ""

    text_area = TextArea(
        height=Dimension(min=1, max=INPUT_MAX_HEIGHT),
        multiline=True,
        wrap_lines=True,
        dont_extend_height=True,
        completer=completer,
        complete_while_typing=True,
        history=history,
        prompt="",
        input_processors=[
            PromptAndPlaceholderProcessor(
                lambda: _mode_prefix(cfg, _get_text),
                cfg.placeholder,
                lambda: _get_text() == "",
            ),
        ],
        accept_handler=_accept,
        style="class:input-bg",   # only the input row gets the subtle bg
    )

    kb = KeyBindings()

    @kb.add("escape")
    def _cancel(event) -> None:
        event.app.exit(result="")

    @kb.add("enter", eager=True)
    def _submit(event) -> None:
        event.app.exit(result=text_area.text)

    @kb.add("s-tab")
    def _shift_tab(event) -> None:
        event.app.current_buffer.complete_previous()

    root = FloatContainer(
        content=HSplit([
            # Lightweight terminal-native input section.
            Window(height=1,
                   content=FormattedTextControl(lambda: _input_rule(cfg), focusable=False)),
            VSplit([
                Window(width=1, content=FormattedTextControl(_input_pad, focusable=False)),
                text_area,
                Window(width=1, content=FormattedTextControl(_input_pad, focusable=False)),
            ]),
            Window(height=1,
                   content=FormattedTextControl(lambda: _input_rule(cfg), focusable=False)),
            # Status bar: transparent bg, dim model · cwd
            Window(height=1,
                   content=FormattedTextControl(lambda: _status_bar(cfg), focusable=False)),
        ]),
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=12, scroll_offset=2),
            )
        ],
    )

    app: Application = Application(
        layout=Layout(root, focused_element=text_area),
        key_bindings=kb,
        style=_build_style(cfg),
        full_screen=False,
        erase_when_done=False,
        mouse_support=False,
        refresh_interval=0.25,  # drives robot dot animation at 4 fps
    )
    return app


@contextmanager
def _without_cpr_probe():
    """Disable CPR for this inline panel, which owns a fixed-height layout."""
    key = "PROMPT_TOOLKIT_NO_CPR"
    previous = os.environ.get(key)
    os.environ[key] = "1"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = previous


def run_panel_input(
    *,
    completer=None,
    history=None,
    status_text: Callable[[], str] | str = "",
    config: Optional[PanelInputConfig] = None,
) -> str:
    """Run the panel synchronously for compatibility with non-async callers."""
    app = _build_panel_input_application(
        completer=completer,
        history=history,
        status_text=status_text,
        config=config,
    )
    if app is None:
        try:
            return input((config or PanelInputConfig()).prompt)
        except EOFError:
            return ""
    with _without_cpr_probe():
        return app.run() or ""


async def run_panel_input_async(
    *,
    completer=None,
    history=None,
    status_text: Callable[[], str] | str = "",
    config: Optional[PanelInputConfig] = None,
) -> str:
    """Run the panel on the active event loop to preserve IME input state."""
    app = _build_panel_input_application(
        completer=completer,
        history=history,
        status_text=status_text,
        config=config,
    )
    if app is None:
        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, input, (config or PanelInputConfig()).prompt)
        except EOFError:
            return ""
    with _without_cpr_probe():
        return (await app.run_async()) or ""
