"""Background version checker for the active Aria Code install channel.

Checks GitHub, scoped npm, or PyPI once per 24 hours in a daemon thread so startup is
never blocked.  The result is cached to ~/.arthera/update_check.json and read
at banner render time.

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
import sys
import threading
import time
from pathlib import Path
from typing import Optional
from aria_code.packages.aria_core.paths import aria_home

_RELEASE_URL   = "https://api.github.com/repos/artheras/aria-code/releases/latest"
_NPM_URL       = "https://registry.npmjs.org/@artheras%2Faria-code/latest"
_PYPI_URL      = "https://pypi.org/pypi/aria-code/json"
_CACHE_FILE    = aria_home() / "update_check.json"
_CACHE_TTL_S   = 86_400      # 24 hours
_FETCH_TIMEOUT = 4           # seconds — fail cleanly on slow networks

_notice: Optional[str] = None
_lock   = threading.Lock()


# ── Version comparison ────────────────────────────────────────────────────────

def _parse(v: str) -> tuple[int, int, int] | None:
    """Accept only stable project release tags, never another package's version."""
    import re
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", v.strip())
    return tuple(map(int, match.groups())) if match else None


def _newer(latest: str, current: str) -> bool:
    parsed_latest, parsed_current = _parse(latest), _parse(current)
    return parsed_latest is not None and parsed_current is not None and parsed_latest > parsed_current


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

def _install_channel() -> str:
    """Infer which update channel owns the running executable."""
    executable = str(getattr(sys, "executable", "") or "").lower()
    if "node_modules" in executable and "aria" in executable:
        return "npm"
    return "native" if getattr(sys, "frozen", False) else "pip"


def _update_command(channel: str) -> str:
    if channel == "npm":
        return "npm install -g @artheras/aria-code@latest"
    if channel == "pip":
        return "python3 -m pip install --upgrade aria-code"
    if sys.platform == "win32":
        return "irm https://raw.githubusercontent.com/artheras/aria-code/main/scripts/install.ps1 | iex"
    return "curl -fsSL https://raw.githubusercontent.com/artheras/aria-code/main/scripts/install.sh | sh"


def _build_notice(latest: str, current: str, lang: str, channel: str = "native") -> str:
    latest = latest.removeprefix("v")
    current = current.removeprefix("v")
    cmd = _update_command(channel)
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


# ── Background worker ─────────────────────────────────────────────────────────

def _worker(current: str, lang: str, channel: str = "native") -> None:
    global _notice
    sources = {
        "native": (_RELEASE_URL, "tag_name"),
        "npm": (_NPM_URL, "version"),
        "pip": (_PYPI_URL, "info"),
    }
    source_url, version_field = sources[channel]

    # 1. Serve from cache if still fresh
    cache = _read_cache()
    now   = time.time()
    if cache.get("source") == source_url and cache.get("checked_at", 0) + _CACHE_TTL_S > now:
        latest = cache.get("latest", "")
        if latest and _newer(latest, current):
            with _lock:
                _notice = _build_notice(latest, current, lang, channel)
        return

    # 2. Fetch metadata for this installation channel, never a similarly named package.
    try:
        import urllib.request
        req = urllib.request.Request(
            source_url,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "aria-code-update-check"},
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            data   = json.loads(resp.read())
            latest = data[version_field]
            if channel == "pip":
                latest = latest["version"]
    except Exception:
        return   # network error → silently skip, try again next day

    # 3. Persist to cache
    if _parse(latest) is None:
        return
    _write_cache({"source": source_url, "checked_at": now, "latest": latest})

    # 4. Set notice
    if _newer(latest, current):
        with _lock:
            _notice = _build_notice(latest, current, lang, channel)


# ── Public API ────────────────────────────────────────────────────────────────

def start_update_check(current_version: str, lang: str = "en") -> None:
    """Start background version check. Call once, early in startup."""
    global _notice
    with _lock:
        _notice = None
    t = threading.Thread(
        target=_worker,
        args=(current_version, lang, _install_channel()),
        daemon=True,
        name="aria-update-check",
    )
    t.start()


def get_update_notice(wait_ms: int = 1200) -> Optional[str]:
    """Return Rich-markup update notice, or None if up to date / not yet known.

    Waits up to *wait_ms* ms for the background thread so the notice can appear
    on the same run (not just next run).  Startup already takes >1s so this
    almost never adds real delay.
    """
    deadline = time.monotonic() + wait_ms / 1000
    while time.monotonic() < deadline:
        with _lock:
            if _notice is not None:
                return _notice
        alive = any(t.name == "aria-update-check" for t in threading.enumerate())
        if not alive:
            break
        time.sleep(0.05)
    with _lock:
        return _notice
