"""Provider selection for the public Aria SDK."""

from __future__ import annotations

from dataclasses import dataclass

from apps.cli.providers.base import AriaSSEProvider, LLMProvider, OllamaProvider

from .types import AriaAgentOptions


@dataclass(frozen=True)
class ProviderSelection:
    """Resolved provider instance and normalized provider name."""

    name: str
    provider: LLMProvider


def normalize_provider_name(name: str) -> str:
    key = (name or "auto").strip().lower().replace("-", "_")
    aliases = {
        "local": "ollama",
        "ollama_local": "ollama",
        "cloud": "aria_sse",
        "remote": "aria_sse",
        "sse": "aria_sse",
        "aria": "aria_sse",
    }
    return aliases.get(key, key)


def build_llm_provider(options: AriaAgentOptions) -> ProviderSelection:
    """Build the configured LLM provider for SDK turns."""

    requested = normalize_provider_name(options.provider)
    if requested == "auto":
        requested = "ollama" if options.local_mode else "aria_sse"

    if requested == "ollama":
        return ProviderSelection(
            name="ollama",
            provider=OllamaProvider(
                options.ollama_url,
                options.model,
                system_override=options.system_prompt or None,
            ),
        )

    if requested == "aria_sse":
        return ProviderSelection(
            name="aria_sse",
            provider=AriaSSEProvider(
                options.api_url,
                options.model,
                auth_token=options.auth_token or None,
                thinking_mode=options.thinking_mode,
                user_context=dict(options.user_context),
                system_override=options.system_prompt or None,
            ),
        )

    raise ValueError(f"Unsupported SDK provider: {options.provider}")


__all__ = [
    "ProviderSelection",
    "build_llm_provider",
    "normalize_provider_name",
]
