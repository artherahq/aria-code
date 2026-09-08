"""Short-lived response cache and offline greeting fallbacks.

Extracted from aria_cli.py, which had grown past 7,000 lines.  This group is
self-contained — it touches no CLI session state — so it moves out with plain
imports and needs none of the globals-rebinding shim the rest of that module
relies on.

The cache exists to avoid re-sending an identical stateless query (a greeting,
a repeated market question, a tab-completion probe) to the model seconds apart.
"""

from __future__ import annotations

import hashlib
import time

# key → (response_text, expire_ts)
_RESPONSE_CACHE: dict[str, tuple[str, float]] = {}
RESPONSE_CACHE_TTL = 60.0  # seconds
RESPONSE_CACHE_MAX_ENTRIES = 200

GREETINGS = frozenset({
    "hi", "hello", "hey", "你好", "您好", "嗨", "哈喽", "在吗",
    "早上好", "下午好", "晚上好",
})


def cache_get(key: str) -> str | None:
    """Return cached response text if still valid, else None."""
    entry = _RESPONSE_CACHE.get(key)
    if entry and time.time() < entry[1]:
        return entry[0]
    return None


def cache_set(key: str, value: str) -> None:
    """Store response in cache with TTL expiry."""
    _RESPONSE_CACHE[key] = (value, time.time() + RESPONSE_CACHE_TTL)
    # Keep cache small — evict expired entries when it grows large
    if len(_RESPONSE_CACHE) > RESPONSE_CACHE_MAX_ENTRIES:
        now = time.time()
        for k in list(_RESPONSE_CACHE.keys()):
            if _RESPONSE_CACHE[k][1] < now:
                del _RESPONSE_CACHE[k]


def cache_clear() -> None:
    """Drop every cached response (tests, and /clear)."""
    _RESPONSE_CACHE.clear()


def cache_key(model: str, message: str) -> str:
    raw = f"{model}::{(message or '').strip().lower()}"
    return hashlib.md5(raw.encode()).hexdigest()


def is_simple_greeting(message: str) -> bool:
    text = (message or "").strip().lower()
    return text in GREETINGS or (len(text) <= 8 and any(g in text for g in GREETINGS))


def offline_greeting_response() -> dict:
    return {
        "success": True,
        "response": (
            "你好，我是 Aria Code。\n\n"
            "当前云端模型不可用，且本地 Ollama 服务没有启动；简单问候可以直接响应。"
            "如果要进行代码修改、市场分析或长文本推理，请先启动本地模型：\n\n"
            "```bash\n"
            "ollama serve\n"
            "```\n\n"
            "然后可用 `ollama list` 检查已安装模型，或运行 `/health` 查看 Aria Code 状态。"
        ),
        "provider": "builtin",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "thinking_tokens": 0},
    }


def ollama_unavailable_result(ollama_url: str, err: str = "") -> dict:
    host = ollama_url or "http://localhost:11434"
    detail = f"\n\nDetail: {err}" if err else ""
    return {
        "success": False,
        "provider": "ollama",
        "error": (
            "Local Ollama is not reachable.\n\n"
            f"Host: {host}\n"
            "Start it in another terminal:\n\n"
            "  ollama serve\n\n"
            "Then verify:\n\n"
            "  curl http://127.0.0.1:11434/api/tags\n"
            "  ollama list\n\n"
            "If you do not want local fallback, use a working cloud/API provider or disable local mode."
            f"{detail}"
        ),
    }


__all__ = [
    "GREETINGS",
    "RESPONSE_CACHE_MAX_ENTRIES",
    "RESPONSE_CACHE_TTL",
    "cache_clear",
    "cache_get",
    "cache_key",
    "cache_set",
    "is_simple_greeting",
    "offline_greeting_response",
    "ollama_unavailable_result",
]
