"""Aria robot mascot — animated terminal character.

States
------
  IDLE       ▣ ▣  blinking, waiting for input
  THINKING   ◌ ◌  spinner eyes, processing
  STREAMING  ▶ ▶  arrow eyes, generating output
  ERROR      ✕ ✕  X eyes
  DONE       ✓ ✓  check eyes, brief flash then back to IDLE

Robot shape (4 rows, copper #C08050 brand colour):

  ╭─ ▣  ▣ ─╮
  │  ─────  │
  ╰─────────╯
  ╌╌╌╌╌╌╌╌╌╌  ← status line (dim, 1 row)
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


# ── Animation frames ───────────────────────────────────────────────────────────
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

# Eye symbols per state — char that fills the eye socket
_EYES = {
    RobotState.IDLE:      ("▣", "▣"),
    RobotState.THINKING:  ("◌", "◌"),
    RobotState.STREAMING: ("▸", "▸"),
    RobotState.ERROR:     ("✕", "✕"),
    RobotState.DONE:      ("✓", "✓"),
}

# Mouth bar per state — 10 chars wide (fits ║  MMMMMMMMMM  ║)
_MOUTH = {
    RobotState.IDLE:      "──────────",
    RobotState.THINKING:  "──────────",
    RobotState.STREAMING: "══════════",
    RobotState.ERROR:     "╌╌╌╌╌╌╌╌╌╌",
    RobotState.DONE:      "──────────",
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

_SK = "#7A4E2A"   # eye socket lines (darker copper)


def _resolve_eyes(state: RobotState, tick: int) -> tuple[str, str]:
    el, er = _EYES[state]
    if state is RobotState.IDLE:
        if tick % 20 in (0, 1):
            el = er = " "
    elif state is RobotState.THINKING:
        ch = _SPIN[tick % len(_SPIN)]
        el = er = ch
    elif state is RobotState.STREAMING:
        el = er = "▸" if (tick % 4) < 2 else "▹"
    return el, er


def get_robot_row(tick: int, row: int) -> list:
    """Return FormattedText fragments for a single robot row (new 6-row design).

    Rows:
      0 → ╔══════════════╗   outer top frame
      1 → ║  ┌──┐  ┌──┐  ║  eye socket top
      2 → ║  │EL│  │ER│  ║  animated eyes
      3 → ║  └──┘  └──┘  ║  eye socket bottom
      4 → ║  MMMMMMMMMM  ║  state mouth
      5 → ╚══════════════╝   outer bottom frame
    """
    state = get_robot_state()
    col   = _COLOUR[state]
    bf    = f"bold {col}"
    mt    = f"dim {col}"
    el, er = _resolve_eyes(state, tick)
    mouth  = _MOUTH[state]

    if row == 0:
        return [(bf, "╔══════════════╗")]
    if row == 1:
        return [(bf, "║  "), (_SK, "┌──┐"), (bf, "  "), (_SK, "┌──┐"), (bf, "  ║")]
    if row == 2:
        return [
            (bf, "║  "), (_SK, "│"), (bf, el + " "), (_SK, "│"),
            (bf, "  "), (_SK, "│"), (bf, er + " "), (_SK, "│"),
            (bf, "  ║"),
        ]
    if row == 3:
        return [(bf, "║  "), (_SK, "└──┘"), (bf, "  "), (_SK, "└──┘"), (bf, "  ║")]
    if row == 4:
        return [(mt, f"║  {mouth}  ║")]
    # row == 5
    return [(bf, "╚══════════════╝")]


def get_robot_frame(tick: int) -> list:
    """Legacy single-fragment list (not split by row). Use get_robot_row() instead."""
    out = []
    for r in range(3):
        out += get_robot_row(tick, r)
    return out


def get_status_dot(tick: int) -> list:
    """Compact inline indicator for the status bar — one animated glyph + state label.

    IDLE:      ▣               (copper, slow blink)
    THINKING:  ⠹  thinking…   (yellow spinner)
    STREAMING: ▶  generating… (green pulse)
    ERROR:     ✕  error        (red)
    DONE:      ✓  done         (green, brief)
    """
    state = get_robot_state()
    col   = _COLOUR[state]
    el, _ = _resolve_eyes(state, tick)
    label = _STATUS[state]

    frags: list = [(f"bold {col}", el)]
    if label:
        frags.append((col, f" {label}"))
    return frags
