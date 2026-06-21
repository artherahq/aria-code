"""Aria robot mascot — animated terminal character.

States
------
  The mascot stays visually stable at startup. Runtime state is shown by the
  compact status dot so the banner keeps the same low-noise feel as Claude Code.

Robot shape (9 terminal rows, rendered from half-block pixels):

       ██████
     ████████
   ████████████
 ████        ████
 ████  █  ██  ████
 ████        ████
   ████████████
   ██        ██
     ██  ██  ██  ██
"""

from __future__ import annotations

import threading
import time
from enum import Enum


class RobotState(Enum):
    IDLE      = "idle"
    THINKING  = "thinking"
    STREAMING = "streaming"
    ERROR     = "error"
    DONE      = "done"


# ── Shared mutable state (written by aria_cli, read by input_box) ─────────────
_state      = RobotState.IDLE
_state_lock = threading.Lock()
_done_at: float | None = None  # timestamp when DONE state was set


def set_robot_state(state: RobotState) -> None:
    global _state, _done_at
    with _state_lock:
        _state = state
        _done_at = time.monotonic() if state is RobotState.DONE else None


def get_robot_state() -> RobotState:
    with _state_lock:
        # Auto-revert DONE → IDLE after 1.5 s
        if _state is RobotState.DONE and _done_at is not None:
            if time.monotonic() - _done_at > 1.5:
                return RobotState.IDLE
        return _state


# Eye symbols per state. IDLE mirrors the mascot art: one light square eye and
# one copper dash eye.
_EYES = {
    RobotState.IDLE:      ("■", "▬"),
    RobotState.THINKING:  ("◐", "◑"),
    RobotState.STREAMING: ("▸", "▸"),
    RobotState.ERROR:     ("×", "×"),
    RobotState.DONE:      ("✓", "✓"),
}

# Accent colour per state
_COLOUR = {
    RobotState.IDLE:      "#C08050",
    RobotState.THINKING:  "#d29922",
    RobotState.STREAMING: "#3fb950",
    RobotState.ERROR:     "#f85149",
    RobotState.DONE:      "#3fb950",
}

# Status text per state (used by status bar dot)
_STATUS = {
    RobotState.IDLE:      "",
    RobotState.THINKING:  "thinking…",
    RobotState.STREAMING: "generating…",
    RobotState.ERROR:     "error",
    RobotState.DONE:      "done",
}

_SHELL = "#f2eadc"
_SCREEN = "#0d1117"
_SHADOW = "#b8b2a8"
_LEG = "#c7c3ba"
_EYE_LIGHT = "#fffaf0"
_ACCENT_STYLE = "#ffb35c"

_PIXEL_ROWS = [
    "...SSSSSSSSSSSS...",
    "..SSSSSSSSSSSSSS..",
    "..SSSSSSSSSSSSSS..",
    "..SSDDDDDDDDDDSS..",
    "..SSDDDDDDDDDDSS..",
    "GGSSDDDDDDDDDDSSGG",
    "GASSDDDDDDDDDDSSAG",
    "GGSSDDWWDDAADDSSGG",
    "GGSSDDDDDDDDDDSSGG",
    "..SSDDDDDDDDDDSS..",
    "..SSDDDDDDDDDDSS..",
    "..SSSSSSSSSSSSSS..",
    "..SSSSSSSSSSSSSS..",
    "..SSAAAAAAAAAASS..",
    "..GGLLLLLLLLLLGG..",
    "...LL..LL..LL..LL.",
    "...LL..LL..LL..LL.",
    "..................",
]

_COLOUR_BY_PIXEL = {
    "S": _SHELL,
    "D": _SCREEN,
    "G": _SHADOW,
    "L": _LEG,
    "A": _ACCENT_STYLE,
    "W": _EYE_LIGHT,
}

ROBOT_ROW_COUNT = len(_PIXEL_ROWS) // 2


def _halfblock(top: str, bottom: str) -> tuple[str, str]:
    if top == "." and bottom == ".":
        return "", " "
    if top == ".":
        return _COLOUR_BY_PIXEL[bottom], "▄"
    if bottom == ".":
        return _COLOUR_BY_PIXEL[top], "▀"
    return f"{_COLOUR_BY_PIXEL[top]} on {_COLOUR_BY_PIXEL[bottom]}", "▀"


def _row_from_halfblocks(top: str, bottom: str) -> list:
    fragments: list = []
    current_style: str | None = None
    current_text = ""
    for top_pixel, bottom_pixel in zip(top, bottom):
        style, text = _halfblock(top_pixel, bottom_pixel)
        if style == current_style:
            current_text += text
            continue
        if current_text:
            fragments.append((current_style or "", current_text))
        current_style = style
        current_text = text
    if current_text:
        fragments.append((current_style or "", current_text))
    return fragments


def _resolve_eyes(state: RobotState, tick: int) -> tuple[str, str]:
    el, er = _EYES[state]
    if state is RobotState.IDLE:
        if tick % 24 in (0, 1):
            el, er = "·", "·"
    elif state is RobotState.THINKING:
        frames = (("◐", "◑"), ("◓", "◒"), ("◑", "◐"), ("◒", "◓"))
        el, er = frames[tick % len(frames)]
    elif state is RobotState.STREAMING:
        el = er = "▸" if (tick % 4) < 2 else "▹"
    return el, er


def get_robot_row(tick: int, row: int) -> list:
    """Return FormattedText fragments for a single robot row.

    Rows:
      0 → top cap
      1 → body top + screen top
      2 → screen + ears
      3 → side LEDs + eyes
      4 → screen bottom
      5 → shell bottom
      6 → copper underline
      7 → legs top
      8 → legs bottom
    """
    del tick
    return _row_from_halfblocks(_PIXEL_ROWS[row * 2], _PIXEL_ROWS[row * 2 + 1])


def get_robot_frame(tick: int) -> list:
    """Legacy single-fragment list (not split by row). Use get_robot_row() instead."""
    out = []
    for r in range(ROBOT_ROW_COUNT):
        out += get_robot_row(tick, r)
    return out


def get_status_dot(tick: int) -> list:
    """Compact inline indicator for the status bar — one animated glyph + state label.

    IDLE:      •               (copper, slow blink)
    THINKING:  ◐  thinking…   (yellow spinner)
    STREAMING: ▶  generating… (green pulse)
    ERROR:     ✕  error        (red)
    DONE:      ✓  done         (green, brief)
    """
    state = get_robot_state()
    col   = _COLOUR[state]
    el, _ = _resolve_eyes(state, tick)
    if state is RobotState.IDLE:
        el = "•"
    label = _STATUS[state]

    frags: list = [(f"bold {col}", el)]
    if label:
        frags.append((col, f" {label}"))
    return frags
