"""Arrow-key selector and async wrapper used by model/skill picker dialogs.

    from ui.picker import arrow_select, run_picker_in_thread
"""

from __future__ import annotations

import asyncio
import os
import sys

from ui.console import _HAS_TERMIOS, _esc_watcher

if _HAS_TERMIOS:
    import termios
    import tty
    import select as _select


def arrow_select(options: list, selected: int = 0, title: str = "",
                 max_visible: int = 10,
                 controls_hint: str = "↑↓  Enter  Esc/q Cancel") -> int:
    """Interactive arrow-key selector with scrolling.

    Args:
        options:     list of ``(label, description)`` tuples or plain strings
        selected:    initially highlighted index
        title:       optional header line
        max_visible: max rows shown at once; scrolls when list is larger
    Returns:
        index of chosen option, or -1 if cancelled
    """
    if not options:
        return -1

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

    n       = len(options)
    visible = min(max_visible, n)
    scroll  = max(0, selected - visible + 1)

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    try:
        _tcols = os.get_terminal_size(fd).columns
    except Exception:
        _tcols = 80

    _rule = "─" * min(_tcols, 72)

    def _display_width(s: str) -> int:
        import re as _re
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
        dw = _display_width(text)
        return max(1, (dw + _tcols - 1) // _tcols)

    def _raw(s: str):
        os.write(1, s.encode())

    def _render():
        nonlocal scroll
        if selected < scroll:
            scroll = selected
        elif selected >= scroll + visible:
            scroll = selected - visible + 1

        buf = ""
        if _render.drawn:
            buf += f"\033[{_render.last_phys_height}A"

        phys_height = 0
        for row in range(visible):
            idx   = scroll + row
            opt   = options[idx]
            label = opt[0] if isinstance(opt, tuple) else opt
            desc  = opt[1] if isinstance(opt, tuple) and len(opt) > 1 else ""
            if idx == selected:
                _accent = os.getenv("ARIA_ACCENT_COLOR", "192;128;80")
                line = f"  \033[1m\033[38;2;{_accent}m❯\033[0m \033[1m{label}\033[0m"
                if desc:
                    line += f"  \033[2m{desc}\033[0m"
            else:
                line = f"    {label}"
                if desc:
                    line += f"  \033[2m{desc}\033[0m"
            buf += f"\033[2K{line}\n"
            phys_height += _physical_lines(line)

        if n > visible:
            hint = f"  \033[2m{selected + 1}/{n}\033[0m"
            buf += f"\033[2K{hint}\n"
        else:
            buf += f"\033[2K\n"
        phys_height += 1

        _render.last_phys_height = phys_height
        _raw(buf)
        _render.drawn = True

    _render.drawn = False
    _render.last_phys_height = visible + 1

    try:
        _esc_watcher.pause()
        tty.setcbreak(fd)
        sys.stdout.flush()

        # Top boundary
        _raw(f"\n  \033[2m{_rule}\033[0m\n")
        if title:
            _raw(f"  \033[1m{title}\033[0m  \033[2m{controls_hint}\033[0m\n\n")
        else:
            _raw(f"  \033[2m{controls_hint}\033[0m\n\n")

        _render()

        while True:
            ch = os.read(fd, 1)
            if ch == b'\x1b':
                seq = b''
                if _select.select([fd], [], [], 0.05)[0]:
                    seq = os.read(fd, 2)
                if seq == b'[A':
                    selected = (selected - 1) % n; _render()
                elif seq == b'[B':
                    selected = (selected + 1) % n; _render()
                elif seq == b'[5~':
                    selected = max(0, selected - visible); _render()
                elif seq == b'[6~':
                    selected = min(n - 1, selected + visible); _render()
                elif not seq:
                    return -1
            elif ch in (b'\r', b'\n'):
                return selected
            elif ch == b'q':
                return -1
            elif ch == b'k':
                selected = (selected - 1) % n; _render()
            elif ch == b'j':
                selected = (selected + 1) % n; _render()
            elif ch == b'g':
                selected = 0; _render()
            elif ch == b'G':
                selected = n - 1; _render()
            elif ch in (b'\x03', b'\x04'):
                return -1
    except (EOFError, KeyboardInterrupt, OSError):
        return -1
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        _esc_watcher.resume()
        _raw(f"  \033[2m{_rule}\033[0m\n\n")


async def run_picker_in_thread(options: list, current_idx: int,
                               title: str, max_visible: int = 14,
                               controls_hint: str = "↑↓  Enter  Esc/q Cancel") -> int:
    """Run arrow_select in an executor thread to avoid kqueue conflicts on macOS."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: arrow_select(
            options, current_idx, title, max_visible, controls_hint
        ),
    )


# Back-compat aliases used by aria_cli.py
_arrow_select = arrow_select
_run_picker_in_thread = run_picker_in_thread
