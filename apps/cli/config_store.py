"""Persistent CLI configuration loading and saving."""

from __future__ import annotations

import json
from collections.abc import Callable

from apps.cli.config_paths import AriaConfigPaths


STALE_ARIA_MODEL_PREFIXES = ("aria-opus", "aria-prelude", "aria-sonata:3", "aria-sonata:4")


def load_cli_config(
    paths: AriaConfigPaths,
    defaults: dict,
    *,
    sync_policy: Callable[[dict], None] | None = None,
) -> dict:
    """Load config.json and merge with defaults."""
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    paths.sessions_dir.mkdir(parents=True, exist_ok=True)
    if paths.config_file.exists():
        try:
            saved = json.loads(paths.config_file.read_text(encoding="utf-8"))
            merged = {**defaults, **saved}
            saved_model = merged.get("model", "")
            if "local_provider" not in saved:
                merged["local_provider"] = (
                    saved_model.split("/", 1)[0].lower()
                    if "/" in saved_model else "ollama"
                )
            try:
                from apps.cli.providers.chat_routing import normalize_provider_name

                merged["local_provider"] = (
                    normalize_provider_name(merged.get("local_provider")) or "ollama"
                )
            except Exception:
                pass
            if any(saved_model.startswith(prefix) for prefix in STALE_ARIA_MODEL_PREFIXES):
                merged["model"] = defaults["model"]
            if not merged.get("ui_lang"):
                try:
                    from apps.cli.i18n import detect_system_lang

                    merged["ui_lang"] = detect_system_lang()
                except Exception:
                    merged["ui_lang"] = "en"
            if sync_policy:
                sync_policy(merged)
            return merged
        except Exception:
            pass

    cfg = dict(defaults)
    try:
        from apps.cli.i18n import auto_select_model, detect_system_lang

        cfg["ui_lang"] = detect_system_lang()
        ollama_url = cfg.get("ollama_url", "http://localhost:11434")
        cfg["model"] = auto_select_model(ollama_url, fallback=defaults["model"])
    except Exception:
        cfg["ui_lang"] = "en"
    if sync_policy:
        sync_policy(cfg)
    return cfg


def save_cli_config(paths: AriaConfigPaths, cfg: dict) -> None:
    paths.config_dir.mkdir(parents=True, exist_ok=True)
    exclude = {"conversation_history"}
    payload = {key: value for key, value in cfg.items() if key not in exclude}
    paths.config_file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
