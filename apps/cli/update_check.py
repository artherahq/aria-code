"""Background PyPI version checker for Aria Code.

Checks PyPI once per 24 hours in a daemon thread so startup is never blocked.
The result is cached to ~/.arthera/update_check.json and read at banner time.

Public API
----------
    start_update_check(current_version: str) -> None
        Call once, early in startup. Spawns daemon thread; returns immediately.

    get_update_notice() -> str | None
        Call at banner render time. Returns a Rich-markup string if a newer
        version is available, otherwise None.  Thread-safe — safe to call
        before the background thread finishes (returns cached result then).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

_PYPI_URL      = "https://pypi.org/pypi/aria-code/json"
_CACHE_FILE    = Path.home() / ".arthera" / "update_check.json"
_CACHE_TTL_S   = 86_400          # 24 hours
_FETCH_TIMEOUT = 4               # seconds — give up cleanly on slow networks

_notice: Optional[str] = None    # populated by background thread
_lock   = threading.Lock()


# ── Version comparison ────────────────────────────────────────────────────────

def _parse(v: str) -> tuple[int, ...]:
    """'4.1.2' → (4, 1, 2).  Tolerates 'v' prefix and non-numeric segments."""
    parts = []
    for seg in v.lstrip("v").split("."):
        try:
            parts.append(int(seg))
        except ValueError:
            break
    return tuple(parts) or (0,)


def _newer(latest: str, current: str) -> bool:
    return _parse(latest) > _parse(current)


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _read_cache() -> dict:
    try:
        return json.loads(_CACHE_FILE.read_text())
    except Exception:
        return {}


def _write_cache(data: dict) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(data))
    except Exception:
        pass


# ── Notice builder ────────────────────────────────────────────────────────────

def _build_notice(latest: str, current: str, lang: str) -> str:
    cmd = f"pip install --upgrade aria-code"
    if lang == "zh":
        return (
            f"[yellow]⬆  新版本可用[/yellow] "
            f"[dim]v{current}[/dim] [dim]→[/dim] [bold]v{latest}[/bold]"
            f"  [dim]{cmd}[/dim]"
        )
    return (
        f"[yellow]⬆  Update available[/yellow] "
        f"[dim]v{current}[/dim] [dim]→[/dim] [bold]v{latest}[/bold]"
        f"  [dim]{cmd}[/dim]"
    )


# ── Background thread ─────────────────────────────────────────────────────────

def _worker(current: str, lang: str) -> None:
    global _notice

    # 1. Check cache first — avoid network if still fresh
    cache = _read_cache()
    now   = time.time()
    if cache.get("checked_at", 0) + _CACHE_TTL_S > now:
        latest = cache.get("latest", "")
        if latest and _newer(latest, current):
            with _lock:
                _notice = _build_notice(latest, current, lang)
        return   # cache is fresh, no network call needed

    # 2. Fetch PyPI
    try:
        import urllib.request
        with urllib.request.urlopen(_PYPI_URL, timeout=_FETCH_TIMEOUT) as resp:
            data   = json.loads(resp.read())
            latest = data["info"]["version"]
    except Exception:
        return   # network failure → silently skip, try again next day

    # 3. Write cache
    _write_cache({"checked_at": now, "latest": latest})

    # 4. Set notice if update available
    if _newer(latest, current):
        with _lock:
            _notice = _build_notice(latest, current, lang)


# ── Public API ────────────────────────────────────────────────────────────────

def start_update_check(current_version: str, lang: str = "en") -> None:
    """Start the background version check.  Call once, early in startup."""
    t = threading.Thread(
        target=_worker,
        args=(current_version, lang),
        daemon=True,
        name="aria-update-check",
    )
    t.start()


def get_update_notice(wait_ms: int = 1200) -> Optional[str]:
    """Return Rich-markup update notice, or None if up-to-date / not yet known.

    Waits up to *wait_ms* milliseconds for the background thread so the notice
    can appear on the very first run after an update (not just the second run).
    In practice startup takes >1s anyway, so this almost never blocks.
    """
    deadline = time.monotonic() + wait_ms / 1000
    while time.monotonic() < deadline:
        with _lock:
            if _notice is not None:
                return _notice
        # Check if thread is still alive — if it finished with no notice, done
        alive = any(
            t.name == "aria-update-check"
            for t in threading.enumerate()
        )
        if not alive:
            break
        time.sleep(0.05)
    with _lock:
        return _notice
