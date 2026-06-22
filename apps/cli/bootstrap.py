"""CLI bootstrap utilities shared by legacy and packaged entrypoints."""

from __future__ import annotations

import os
import socket
import urllib.parse
from pathlib import Path

from apps.cli.config_paths import resolve_paths


def load_aria_env(env_file: Path | None = None) -> None:
    """Load persisted CLI environment values without overriding the process."""
    target = env_file or (Path.home() / ".aria" / ".env")
    if not target.exists():
        return
    try:
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key and value:
                os.environ.setdefault(key, value)
    except Exception:
        return


def disable_broken_proxy(timeout: float = 1.5) -> None:
    """Unset proxy variables when the configured proxy cannot be reached."""
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )
    if not proxy:
        return
    try:
        parsed = urllib.parse.urlparse(proxy if "://" in proxy else f"http://{proxy}")
        host = parsed.hostname
        port = parsed.port or 80
        if not host:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        alive = sock.connect_ex((host, port)) == 0
        sock.close()
    except Exception:
        alive = False
    if not alive:
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
            os.environ.pop(name, None)


def initialize_cli_environment() -> None:
    os.environ.setdefault("TQDM_DISABLE", "1")
    load_aria_env()
    disable_broken_proxy()


def default_config() -> dict:
    return {
        "api_url": os.getenv(
            "ARTHERA_API_URL",
            "http://localhost:8000",
        ),
        "local_url": "http://localhost:8000",
        "ollama_url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
        "model": "qwen2.5-coder:1.5b",
        "thinking_mode": "auto",
        "watchlist": ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA"],
        "auth_token": None,
        "user_id": None,
        "last_session_id": None,
        "auto_save_sessions": True,
        "auto_compact_context": True,
        "auto_compact_threshold": 0.78,
        "command_policy": "safe",
        "permission_mode": "workspace-write",
        "network_enabled": True,
        "data_sharing": False,
        "feedback_upload": False,
        "write_policy": "desktop_only",
        "lsp_autocheck": False,
        "input_style": "panel",
        "input_theme": "auto",
        "response_footer": "compact",
        "local_mode": False,
        "conversation_history": [],
        "ui_lang": "",
    }


def runtime_paths():
    return resolve_paths()
