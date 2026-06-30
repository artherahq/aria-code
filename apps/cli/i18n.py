"""i18n.py — System language detection and UI string translations for Aria Code.

Priority order for UI language:
  1. config["ui_lang"]  (user explicitly set via /config set ui_lang=zh)
  2. OS locale at first-run  (written into config automatically)
  3. Fallback: "en"

Supported languages: "zh" (Simplified Chinese), "en" (English)
Future: "ja", "ko" — extend STRINGS dict below.
"""
from __future__ import annotations

import os
from typing import Optional


# ---------------------------------------------------------------------------
# String catalogue
# ---------------------------------------------------------------------------

STRINGS: dict[str, dict[str, str]] = {
    # Banner labels
    "local_first_agent": {
        "zh": "本地优先智能体",
        "en": "local-first agent",
    },
    "model": {
        "zh": "模型",
        "en": "model",
    },
    "workspace": {
        "zh": "工作区",
        "en": "workspace",
    },
    "mode": {
        "zh": "模式",
        "en": "mode",
    },
    "status": {
        "zh": "状态",
        "en": "status",
    },
    "tools": {
        "zh": "工具",
        "en": "tools",
    },
    "skills": {
        "zh": "技能",
        "en": "skills",
    },
    "quant": {
        "zh": "量化",
        "en": "quant",
    },
    # Status labels
    "sharing_on": {
        "zh": "数据共享",
        "en": "sharing on",
    },
    "local_only": {
        "zh": "纯本地",
        "en": "local-only",
    },
    "local_retention": {
        "zh": "本地留存",
        "en": "local retention",
    },
    "cloud": {
        "zh": "云端",
        "en": "cloud",
    },
    "local": {
        "zh": "本地",
        "en": "local",
    },
    "lite": {
        "zh": "轻量",
        "en": "lite",
    },
    "ollama_online": {
        "zh": "Ollama 在线",
        "en": "Ollama online",
    },
    "ollama_offline": {
        "zh": "Ollama 离线",
        "en": "Ollama offline",
    },
    "cloud_connected": {
        "zh": "云端已连接",
        "en": "cloud ✓",
    },
    "model_singular": {
        "zh": "个模型",
        "en": "model",
    },
    "model_plural": {
        "zh": "个模型",
        "en": "models",
    },
    # Hint line
    "try": {
        "zh": "试试",
        "en": "try",
    },
    # Model picker
    "select_model": {
        "zh": "选择模型",
        "en": "Select Model",
    },
    "installed": {
        "zh": "已安装",
        "en": "installed",
    },
    "not_installed": {
        "zh": "未安装",
        "en": "not installed",
    },
    "cancelled": {
        "zh": "已取消",
        "en": "Cancelled",
    },
    "no_change": {
        "zh": "未更改",
        "en": "No change",
    },
    "community_models": {
        "zh": "社区模型 (Ollama)",
        "en": "Community (Ollama)",
    },
    "ollama_unreachable": {
        "zh": "Ollama 无法连接",
        "en": "Ollama unreachable",
    },
    "switch_model_hint": {
        "zh": "使用 /model <id> 切换模型",
        "en": "Use /model <id> to switch",
    },
    "current": {
        "zh": "当前",
        "en": "current",
    },
    # Permission / mode labels
    "network_on": {
        "zh": "网络开",
        "en": "network on",
    },
    "network_off": {
        "zh": "网络关",
        "en": "network off",
    },
    "privacy": {
        "zh": "隐私",
        "en": "privacy",
    },
    # Setup wizard
    "detecting_lang": {
        "zh": "检测到系统语言：中文",
        "en": "Detected system language: English",
    },
    "first_run_welcome": {
        "zh": "欢迎使用 Aria Code！首次运行，正在自动配置...",
        "en": "Welcome to Aria Code! First run — auto-configuring...",
    },
    "auto_model_selected": {
        "zh": "已自动选择本地模型：",
        "en": "Auto-selected local model: ",
    },
    "no_ollama_models": {
        "zh": "未发现本地模型，使用默认配置",
        "en": "No local models found, using default config",
    },
    # Thinking mode
    "thinking_mode": {
        "zh": "思考模式",
        "en": "Thinking Mode",
    },
    # Misc
    "tip": {
        "zh": "提示",
        "en": "tip",
    },
    "auto_matched": {
        "zh": "自动匹配",
        "en": "auto-matched",
    },
}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def detect_system_lang() -> str:
    """Detect OS language. Returns 'zh' for Chinese systems, 'en' for everything else.

    Checks (in order): $LANGUAGE, $LANG, $LC_ALL, $LC_MESSAGES, Python locale module.
    Treats zh_CN, zh_TW, zh_HK, zh_SG as 'zh'.
    """
    for var in ("LANGUAGE", "LANG", "LC_ALL", "LC_MESSAGES"):
        raw = os.environ.get(var, "").strip()
        if not raw or raw in ("C", "POSIX", "C.UTF-8"):
            continue
        # LANGUAGE may be colon-separated list: zh_CN:en
        code = raw.split(":")[0].split(".")[0].split("_")[0].lower()
        if code == "zh":
            return "zh"
        if code in ("en", "fr", "de", "es", "pt", "ja", "ko", "ru", "ar"):
            return code if code in ("ja", "ko") else "en"

    # Fallback: Python locale module (works on macOS/Windows when env vars absent)
    try:
        import locale as _locale
        lang_code, _ = _locale.getdefaultlocale()
        if lang_code:
            prefix = lang_code.split("_")[0].lower()
            if prefix == "zh":
                return "zh"
            if prefix in ("ja", "ko"):
                return prefix
    except Exception:
        pass

    return "en"


def get_ui_lang(config: Optional[dict] = None) -> str:
    """Return effective UI language.

    1. config["ui_lang"] if explicitly set
    2. detect_system_lang()
    """
    if config:
        saved = config.get("ui_lang", "")
        if saved in STRINGS.get("model", {}):  # valid lang code
            return saved
    return detect_system_lang()


def t(key: str, lang: Optional[str] = None, config: Optional[dict] = None) -> str:
    """Translate a UI key to the given language (or auto-detect).

    Falls back to 'en' if key/lang not found.
    """
    if lang is None:
        lang = get_ui_lang(config)
    entry = STRINGS.get(key, {})
    return entry.get(lang) or entry.get("en") or key


# ---------------------------------------------------------------------------
# Ollama model helpers (used by load_config on first run)
# ---------------------------------------------------------------------------

_MODEL_PRIORITY = [
    "qwen2.5:7b",
    "qwen2.5-coder:7b",
    "deepseek-r1:7b",
    "qwen2.5:3b",
    "qwen2.5-coder:3b",
    "llama3.2:3b",
    "mistral:7b",
    "qwen2.5-coder:1.5b",
    "qwen2.5:1.5b",
    "llama3.2:1b",
]


def auto_select_model(ollama_url: str = "http://localhost:11434",
                      fallback: str = "qwen2.5-coder:1.5b") -> str:
    """Query Ollama and return the best locally available model.

    Returns *fallback* when Ollama is unreachable or no models are installed.
    """
    try:
        import urllib.request as _ur
        import json as _json
        with _ur.urlopen(f"{ollama_url}/api/tags", timeout=3) as resp:
            data = _json.loads(resp.read())
        installed = {m["name"] for m in data.get("models", [])}
        if not installed:
            return fallback
        for pref in _MODEL_PRIORITY:
            if pref in installed:
                return pref
        # Return alphabetically first installed model
        return sorted(installed)[0]
    except Exception:
        return fallback
