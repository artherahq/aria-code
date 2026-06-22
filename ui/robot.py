"""Aria robot mascot — animated terminal character.

States
------
  The mascot stays visually stable at startup. Runtime state is shown by the
  compact status dot so the banner keeps the same low-noise feel as Claude Code.

Robot shape (7 rows, 13 columns, hand-placed). It is intentionally drawn as a
terminal icon, not a raster-image conversion: light shell, dark screen, one
white eye, one copper eye, a copper status strip, and four small legs.
"""

from __future__ import annotations

import os
import sys
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

# ── Theme-aware palette ───────────────────────────────────────────────────────
# Every region is a BACKGROUND fill — a space painted with an `on <colour>` style.
# The terminal paints a cell background across the whole line height (including the
# inter-row gap), so fills join into a solid shape with no horizontal striping.
# Two palettes (light/dark) are swapped from the OS/terminal theme so the robot
# inverts (white-on-dark ↔ dark-on-light) and never disappears into the background.
# The copper accent reads on both, so it is shared (just darkened a touch on light).
_PALETTES = {
    "dark": {
        "shelltop": "#e8e2d4",             # thin top cap (▄): fg only → transparent above
        "shell":    "on #e8e2d4",          # light shell body
        "screen":   "on #0d1117",          # dark screen
        "eye":      "#f6f2ea on #0d1117",  # light square eye (▀)
        "dash":     "#C08050 on #0d1117",  # copper dash (▬)
        "ear":      "#C08050 on #9d9488",  # copper dot on a gray ear nub (▪)
        "strip":    "#C08050 on #e8e2d4",  # copper strip on the body bottom (▬)
        "leg":      "#8a8176",             # gray legs (▀)
    },
    "light": {
        "shelltop": "#E7E1D3",             # warm cap, matching the light shell
        "shell":    "on #E7E1D3",          # warm shell, distinct from white terminal bg
        "screen":   "on #0D1117",          # same dark screen as dark mode
        "eye":      "#F6F2EA on #0D1117",  # light eye on dark screen
        "dash":     "#9A6700 on #0D1117",  # dark copper on dark screen
        "ear":      "#9A6700 on #6E7781",  # copper dot on gray side nub
        "strip":    "#9A6700 on #E7E1D3",  # copper strip on warm shell
        "leg":      "#6E7781",             # medium gray legs
    },
}

_theme_cache: str | None = None


def _resolve_theme() -> str:
    pref = os.environ.get("ARIA_THEME", "").strip().lower()
    if pref in ("light", "dark"):
        return pref
    if sys.platform == "darwin":
        try:
            import subprocess
            r = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True, text=True, timeout=1,
            )
            # `defaults` prints "Dark" in dark mode and errors (non-zero) in light.
            return "dark" if (r.returncode == 0 and "dark" in r.stdout.lower()) else "light"
        except Exception:
            pass
    # xterm-style COLORFGBG ("fg;bg"): bg 0-6/8 → dark, 7/9-15 → light.
    cfb = os.environ.get("COLORFGBG", "")
    if ";" in cfb:
        try:
            bg = int(cfb.split(";")[-1])
            return "light" if (bg == 7 or bg >= 9) else "dark"
        except Exception:
            pass
    return "dark"


def detect_theme() -> str:
    """Return 'light' or 'dark' for the mascot, auto-detected once and cached.

    Order: ``ARIA_THEME`` env override → macOS system appearance → ``COLORFGBG``
    → default dark.
    """
    global _theme_cache
    if _theme_cache is None:
        _theme_cache = _resolve_theme()
    return _theme_cache


# 11 cols × 5 rows — a SOLID body with a "monitor" screen cut into it, modelled on
# the minimal robot icon and kept short/flat to sit beside the three-line banner
# text. Each cell is ``(palette-role, text)``; get_robot_row() resolves the role
# to a themed style. Layout: ▄ top cap · screen · eye(▀) + dash(▬) + ear dots(▪)
# · copper strip(▬) · legs(▀).
_MASCOT_TEMPLATE = [
    [("", " "), ("shelltop", "▄▄▄▄▄▄▄▄▄"), ("", " ")],
    [("", " "), ("shell", " "), ("screen", "       "), ("shell", " "), ("", " ")],
    [
        ("ear", "▪"), ("shell", " "),
        ("screen", " "), ("eye", "▀"), ("screen", "   "),
        ("dash", "▬"), ("screen", " "),
        ("shell", " "), ("ear", "▪"),
    ],
    [("", " "), ("strip", "▬▬▬▬▬▬▬▬▬"), ("", " ")],
    [
        ("", "  "), ("leg", "▀"), ("", " "), ("leg", "▀"), ("", " "),
        ("leg", "▀"), ("", " "), ("leg", "▀"), ("", "  "),
    ],
]

ROBOT_ROW_COUNT = len(_MASCOT_TEMPLATE)


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
    """Return FormattedText fragments for a single robot row, themed.

    Rows: 0 shell top · 1 screen · 2 ear dots + eyes (square · dash) · 3 copper
    strip · 4 legs. Each role is resolved to a colour for the active light/dark
    theme (see detect_theme()).
    """
    del tick
    pal = _PALETTES[detect_theme()]
    return [(pal[key] if key else "", text) for key, text in _MASCOT_TEMPLATE[row]]


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
    if detect_theme() == "light":
        col = {
            RobotState.IDLE:      "#9A6700",
            RobotState.THINKING:  "#9A6700",
            RobotState.STREAMING: "#1A7F37",
            RobotState.ERROR:     "#CF222E",
            RobotState.DONE:      "#1A7F37",
        }[state]
    el, _ = _resolve_eyes(state, tick)
    if state is RobotState.IDLE:
        el = "•"
    label = _STATUS[state]

    frags: list = [(f"bold {col}", el)]
    if label:
        frags.append((col, f" {label}"))
    return frags
