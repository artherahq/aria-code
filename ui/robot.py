"""Aria robot mascot — animated terminal character.

States
------
  IDLE       • •  blinking, waiting for input
  THINKING   ◐ ◑  spinner eyes, processing
  STREAMING  ▶ ▶  arrow eyes, generating output
  ERROR      × ×  X eyes
  DONE       ✓ ✓  check eyes, brief flash then back to IDLE

Robot shape (8 rows, terminal pixel mark based on the app mascot):

    ███████
   █████████
  ███████████
███████████████
██████■██▬█████
  ███████████
  ██▀▀▀▀▀▀▀██
   ██ ██ ██ ██
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

_SHELL = "bold #f2eadc"
_SCREEN = "bold #0d1117"
_SHADOW = "#b8b2a8"
_LEG = "bold #c7c3ba"
_EYE_LIGHT = "bold #fffaf0"
_ACCENT_STYLE = "bold #ffb35c"
ROBOT_ROW_COUNT = 8


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
      0 →     ███████
      1 →    █████████
      2 →   ██SSSSSSS██
      3 → EEE█SSSSSSS█EEE
      4 → EAE█SS EL SS ER S█EAE
      5 →   ██SSSSSSS██
      6 →   ██AAAAAAA██
      7 →    LL LL LL LL
    """
    state = get_robot_state()
    col   = _COLOUR[state]
    eye   = f"bold {col}"
    el, er = _resolve_eyes(state, tick)
    left_eye_style = _EYE_LIGHT if state is RobotState.IDLE else eye

    if row == 0:
        return [("", "    "), (_SHELL, "███████"), ("", "    ")]
    if row == 1:
        return [("", "   "), (_SHELL, "█████████"), ("", "   ")]
    if row == 2:
        return [("", "  "), (_SHELL, "██"), (_SCREEN, "███████"), (_SHELL, "██"), ("", "  ")]
    if row == 3:
        return [(_SHADOW, "███"), (_SHELL, "█"), (_SCREEN, "███████"), (_SHELL, "█"), (_SHADOW, "███")]
    if row == 4:
        return [
            (_SHADOW, "█"),
            (_ACCENT_STYLE, "█"),
            (_SHADOW, "█"),
            (_SHELL, "█"),
            (_SCREEN, "██"),
            (left_eye_style, el),
            (_SCREEN, "██"),
            (eye, er),
            (_SCREEN, "█"),
            (_SHELL, "█"),
            (_SHADOW, "█"),
            (_ACCENT_STYLE, "█"),
            (_SHADOW, "█"),
        ]
    if row == 5:
        return [("", "  "), (_SHELL, "██"), (_SCREEN, "███████"), (_SHELL, "██"), ("", "  ")]
    if row == 6:
        return [("", "  "), (_SHELL, "██"), (_ACCENT_STYLE, "▀▀▀▀▀▀▀"), (_SHELL, "██"), ("", "  ")]
    return [("", "   "), (_LEG, "██"), ("", " "), (_LEG, "██"), ("", " "), (_LEG, "██"), ("", " "), (_LEG, "██"), ("", " ")]


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
