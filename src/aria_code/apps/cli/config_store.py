"""Persistent CLI configuration loading and saving.

CLI adapter for :class:`packages.aria_services.settings.SettingsService` —
the service owns the merge/normalize/persist mechanics; this module supplies
the CLI-specific hooks (provider-name normalization, i18n detection, model
auto-selection) and keeps the historical ``load_cli_config``/``save_cli_config``
API so existing call sites are untouched.
"""

from __future__ import annotations

from collections.abc import Callable

from aria_code.apps.cli.config_paths import AriaConfigPaths
from aria_code.packages.aria_services.settings import (  # noqa: F401  (re-exported)
    STALE_ARIA_MODEL_PREFIXES,
    SettingsService,
)


def _normalize_provider(name: str) -> str:
    from apps.cli.providers.chat_routing import normalize_provider_name
    return normalize_provider_name(name)


def _detect_lang() -> str:
    from apps.cli.i18n import detect_system_lang
    return detect_system_lang()


def _auto_select_model(ollama_url: str, fallback: str) -> str:
    from apps.cli.i18n import auto_select_model
    return auto_select_model(ollama_url, fallback=fallback)


def build_settings_service(
    paths: AriaConfigPaths,
    defaults: dict,
    *,
    sync_policy: Callable[[dict], None] | None = None,
) -> SettingsService:
    """Construct the SettingsService with the CLI's environment hooks."""
    return SettingsService(
        config_dir=paths.config_dir,
        config_file=paths.config_file,
        sessions_dir=paths.sessions_dir,
        defaults=defaults,
        normalize_provider=_normalize_provider,
        detect_lang=_detect_lang,
        auto_select_model=_auto_select_model,
        on_loaded=sync_policy,
    )


def load_cli_config(
    paths: AriaConfigPaths,
    defaults: dict,
    *,
    sync_policy: Callable[[dict], None] | None = None,
) -> dict:
    """Load config.json and merge with defaults (SettingsService-backed)."""
    return build_settings_service(paths, defaults, sync_policy=sync_policy).load()


def save_cli_config(paths: AriaConfigPaths, cfg: dict) -> None:
    build_settings_service(paths, {}).save(cfg)
