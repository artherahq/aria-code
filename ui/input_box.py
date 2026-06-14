"""Prompt-toolkit input panel — Codex-style minimal layout.

Three-row layout:
  ──────────────────────────────────────────────  ← dim divider
  CHAT › cursor_                                  ← input (intentional bg fill)
  model  ·  ~/workspace                          ← dim status bar
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, replace
from typing import Callable, Optional

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import ConditionalCompleter
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea


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
    placeholder: str = "问 Aria、编辑文件、运行命令… 或 / 快速命令"
    theme: str = "auto"

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

    def resolved(self) -> "PanelInputConfig":
        theme = self.theme if self.theme != "auto" else detect_terminal_theme()
        if theme == "dark":
            return replace(self, theme=theme,
                fg="#c9d1d9",
                accent="#3fb950", accent_y="#d29922", accent_b="#79c0ff",
                muted="#6e7781", dim="#484f58", sep="#2d333b",
                input_bg="#1a1a1a",   # slightly lighter than terminal black (Codex approach)
                ph_color="#3a3a3a",   # very dim placeholder
            )
        return replace(self, theme="light",
            fg="#24292f",
            accent="#1a7f37", accent_y="#9a6700", accent_b="#0969da",
            muted="#57606a", dim="#8c959f", sep="#d0d7de",
            input_bg="#f2f2f2",
            ph_color="#c8c8c8",
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


# ── Style ──────────────────────────────────────────────────────────────────────

def _build_style(cfg: PanelInputConfig) -> Style:
    return Style.from_dict({
        # Input row: intentional slightly-lighter background (defines input zone, Codex style)
        "input-bg":  f"{cfg.fg} bg:{cfg.input_bg}",
        "ph":        cfg.ph_color,
        # Mode badge
        "mode-chat": f"bold {cfg.accent}",
        "mode-cmd":  f"bold {cfg.accent_y}",
        "mode-file": f"bold {cfg.accent_b}",
        "prompt":    cfg.muted,
        # Divider (transparent bg — terminal bg shows through)
        "divider":   cfg.sep,
        # Status bar (transparent bg)
        "st-model":  cfg.muted,
        "st-sep":    cfg.dim,
        "st-cwd":    cfg.dim,
        "tok-warn":  cfg.accent_y,
        "tok-crit":  "#f85149",
        # Completion menu — GitHub dark palette, stands out from terminal bg
        "completion-menu":                    "bg:#161b22 #c9d1d9",
        "completion-menu.completion":         "bg:#161b22 #c9d1d9",
        "completion-menu.completion.current": "bg:#1f2937 #e6edf3 bold",
        "completion-menu.meta":               "bg:#161b22 #484f58",
        "completion-menu.meta.current":       "bg:#1f2937 #8b949e",
        "scrollbar.background":               "bg:#161b22",
        "scrollbar.button":                   "bg:#30363d",
        # Fuzzy-match highlight classes (shared with ui/completer.py)
        "fz-hi":                              "bold #f0883e",
        "fz-cat":                             "#484f58",
    })


# ── Row builders ───────────────────────────────────────────────────────────────

def _divider(cfg: PanelInputConfig) -> list:
    w = shutil.get_terminal_size((80, 24)).columns
    return [("class:divider", "─" * w)]


def _mode_prefix(cfg: PanelInputConfig, text_getter: Callable[[], str]) -> list:
    txt = text_getter().lstrip()
    if txt.startswith("/"):
        return [("class:mode-cmd",  "CMD "), ("class:prompt", cfg.prompt)]
    if txt.startswith("@") or txt.startswith("!"):
        return [("class:mode-file", "FILE"), ("class:prompt", cfg.prompt)]
    return     [("class:mode-chat", "CHAT"), ("class:prompt", cfg.prompt)]


def _status_bar(cfg: PanelInputConfig) -> list:
    """Dim status row: [robot dot]  model · cwd [· ctx warning when >60%]"""
    from .robot import get_status_dot, get_robot_state, RobotState

    tick = int(time.monotonic() * 4)  # 4 fps tick without a background thread
    parts: list = []

    if cfg.show_robot:
        dot_frags = get_status_dot(tick)
        parts.extend(dot_frags)
        parts.append(("class:st-sep", "  "))

    if cfg.model_label:
        parts.append(("class:st-model", cfg.model_label))

    if cfg.cwd:
        if cfg.model_label:
            parts.append(("class:st-sep", "  ·  "))
        parts.append(("class:st-cwd", cfg.cwd))

    # Token warning only above 60% — silent below that
    if cfg.est_tokens > 0:
        ratio = cfg.est_tokens / max(cfg.max_tokens, 1)
        if ratio >= 0.60:
            tc = "tok-crit" if ratio >= 0.85 else "tok-warn"
            def _k(n: int) -> str:
                return f"{n // 1000}K" if n >= 1000 else str(n)
            parts += [
                ("class:st-sep", "  ·  "),
                (f"class:{tc}", f"ctx {_k(cfg.est_tokens)}/{_k(cfg.max_tokens)}"),
            ]

    return parts


# ── Main ───────────────────────────────────────────────────────────────────────

def run_panel_input(
    *,
    completer=None,
    history=None,
    status_text: Callable[[], str] | str = "",   # kept for compat
    config: Optional[PanelInputConfig] = None,
) -> str:
    cfg = (config or PanelInputConfig()).resolved()

    def _accept(buf: Buffer) -> bool:
        app.exit(result=buf.text)
        return True

    def _get_text() -> str:
        try:
            return text_area.text
        except Exception:
            return ""

    # Only auto-complete when the buffer starts with "/" — avoids triggering
    # the completion menu on every chat message.
    _slash_completer = None
    if completer is not None:
        @Condition
        def _is_slash_input() -> bool:
            try:
                return text_area.text.lstrip().startswith("/")
            except Exception:
                return False
        _slash_completer = ConditionalCompleter(completer, _is_slash_input)

    text_area = TextArea(
        height=1,
        multiline=False,
        wrap_lines=False,
        completer=_slash_completer,
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

    @kb.add("s-tab")
    def _shift_tab(event) -> None:
        event.app.current_buffer.complete_previous()

    root = HSplit([
        # Divider: transparent bg, dim ─── line
        Window(height=1,
               content=FormattedTextControl(lambda: _divider(cfg), focusable=False)),
        # Input: intentional #1a1a1a bg (Codex-style zone definition)
        text_area,
        # Status bar: transparent bg, dim model · cwd
        Window(height=1,
               content=FormattedTextControl(lambda: _status_bar(cfg), focusable=False)),
    ])

    app: Application = Application(
        layout=Layout(root, focused_element=text_area),
        key_bindings=kb,
        style=_build_style(cfg),
        full_screen=False,
        erase_when_done=False,
        mouse_support=False,
        refresh_interval=0.25,  # drives robot dot animation at 4 fps
    )
    return app.run() or ""
