"""Aria robot mascot — animated terminal character.

States
------
  The mascot stays visually stable at startup. Runtime state is shown by the
  compact status dot so the banner keeps the same low-noise feel as Claude Code.

Robot shape (7 terminal rows, hand-tuned for light and dark terminals):

    ░░░░░░░░░░
  ░░          ░░
▓░  ██████████  ░▓
▓▮  ██▌   ▬▬██  ▮▓
▓░  ██████████  ░▓
  ░░▄▄▄▄▄▄▄▄▄▄░░
    ▓▓  ▓▓  ▓▓  ▓▓
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

_SHELL_STYLE = "bold #d9cbb7"
_SCREEN_STYLE = "bold #0d1117"
_SHADOW_STYLE = "dim #9f9a90"
_LEG_STYLE = "dim #bdb7ad"
_EYE_LIGHT_STYLE = "bold #f8f4ec"
_ACCENT_STYLE = "bold #ffb35c"

_MASCOT_ROWS = [
    [("", "    "), (_SHELL_STYLE, "░░░░░░░░░░"), ("", "    ")],
    [("", "  "), (_SHELL_STYLE, "░░          ░░"), ("", "  ")],
    [(_SHADOW_STYLE, "▓"), (_SHELL_STYLE, "░  "), (_SCREEN_STYLE, "██████████"), (_SHELL_STYLE, "  ░"), (_SHADOW_STYLE, "▓")],
    [
        (_SHADOW_STYLE, "▓"),
        (_ACCENT_STYLE, "▮"),
        (_SHELL_STYLE, "  "),
        (_SCREEN_STYLE, "██"),
        (_EYE_LIGHT_STYLE, "▌"),
        (_SCREEN_STYLE, "   "),
        (_ACCENT_STYLE, "▬▬"),
        (_SCREEN_STYLE, "██"),
        (_SHELL_STYLE, "  "),
        (_ACCENT_STYLE, "▮"),
        (_SHADOW_STYLE, "▓"),
    ],
    [(_SHADOW_STYLE, "▓"), (_SHELL_STYLE, "░  "), (_SCREEN_STYLE, "██████████"), (_SHELL_STYLE, "  ░"), (_SHADOW_STYLE, "▓")],
    [("", "  "), (_SHELL_STYLE, "░░"), (_ACCENT_STYLE, "▄▄▄▄▄▄▄▄▄▄"), (_SHELL_STYLE, "░░"), ("", "  ")],
    [("", "    "), (_LEG_STYLE, "▓▓"), ("", "  "), (_LEG_STYLE, "▓▓"), ("", "  "), (_LEG_STYLE, "▓▓"), ("", "  "), (_LEG_STYLE, "▓▓")],
]

ROBOT_ROW_COUNT = len(_MASCOT_ROWS)


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
      0 → shell cap
      1 → shell shoulders
      2 → screen top
      3 → side LEDs + eyes
      4 → screen bottom
      5 → copper underline
      6 → legs
    """
    del tick
    return _MASCOT_ROWS[row]


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
