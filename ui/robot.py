"""Aria robot mascot — animated terminal character.

States
------
  IDLE       • •  blinking, waiting for input
  THINKING   ◐ ◑  spinner eyes, processing
  STREAMING  ▶ ▶  arrow eyes, generating output
  ERROR      × ×  X eyes
  DONE       ✓ ✓  check eyes, brief flash then back to IDLE

Robot shape (4 rows, flat pixel mark with copper state accents):

   ▄▄▄▄▄
  ▐ • • ▌
  ▐  ─  ▌
   ▀▀▀▀▀
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


# Eye symbols per state.
_EYES = {
    RobotState.IDLE:      ("•", "•"),
    RobotState.THINKING:  ("◐", "◑"),
    RobotState.STREAMING: ("▸", "▸"),
    RobotState.ERROR:     ("×", "×"),
    RobotState.DONE:      ("✓", "✓"),
}

# Mouth bar per state — two chars wide.
_MOUTH = {
    RobotState.IDLE:      "─",
    RobotState.THINKING:  "·",
    RobotState.STREAMING: "━",
    RobotState.ERROR:     "!",
    RobotState.DONE:      "─",
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

_BODY = "dim #a8b0b8"
_MOUTH_STYLE = "dim #C08050"
ROBOT_ROW_COUNT = 4


def _resolve_eyes(state: RobotState, tick: int) -> tuple[str, str]:
    el, er = _EYES[state]
    if state is RobotState.IDLE:
        if tick % 24 in (0, 1):
            el = er = "·"
    elif state is RobotState.THINKING:
        frames = (("◐", "◑"), ("◓", "◒"), ("◑", "◐"), ("◒", "◓"))
        el, er = frames[tick % len(frames)]
    elif state is RobotState.STREAMING:
        el = er = "▸" if (tick % 4) < 2 else "▹"
    return el, er


def get_robot_row(tick: int, row: int) -> list:
    """Return FormattedText fragments for a single robot row.

    Rows:
      0 →   ▄▄▄▄▄
      1 →  ▐ EL ER ▌
      2 →  ▐  MM  ▌
      3 →   ▀▀▀▀▀
    """
    state = get_robot_state()
    col   = _COLOUR[state]
    eye   = f"bold {col}"
    el, er = _resolve_eyes(state, tick)
    mouth  = _MOUTH[state]

    if row == 0:
        return [(_BODY, "  ▄▄▄▄▄  ")]
    if row == 1:
        return [(_BODY, " ▐ "), (eye, el), (_BODY, " "), (eye, er), (_BODY, " ▌ ")]
    if row == 2:
        return [(_BODY, " ▐  "), (_MOUTH_STYLE, mouth), (_BODY, "  ▌ ")]
    return [(_BODY, "  ▀▀▀▀▀  ")]


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
