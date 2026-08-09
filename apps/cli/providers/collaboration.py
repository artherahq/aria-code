"""Small, explicit multi-provider consultation helpers for the terminal.

This module deliberately talks to provider *APIs*, not consumer desktop apps.
ChatGPT Plus and Claude Pro sessions cannot safely be reused as API credentials.
Keeping this boundary explicit prevents a CLI command from accidentally scraping
browser sessions or consuming a provider that the user did not configure.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Iterable


@dataclass(frozen=True)
class Collaborator:
    alias: str
    provider: str
    default_model: str
    label: str


COLLABORATORS: dict[str, Collaborator] = {
    "chatgpt": Collaborator("chatgpt", "openai", "gpt-4o-mini", "ChatGPT / OpenAI"),
    "openai": Collaborator("chatgpt", "openai", "gpt-4o-mini", "ChatGPT / OpenAI"),
    "claude": Collaborator("claude", "anthropic", "claude-3-5-haiku-latest", "Claude / Anthropic"),
    "anthropic": Collaborator("claude", "anthropic", "claude-3-5-haiku-latest", "Claude / Anthropic"),
}


def resolve_collaborator(name: str, config: dict | None = None) -> Collaborator | None:
    """Resolve user-friendly names and apply an optional configured model."""
    target = COLLABORATORS.get(str(name or "").strip().lower())
    if not target:
        return None
    configured_model = (config or {}).get(f"collab_{target.alias}_model")
    if configured_model and str(configured_model).strip():
        return Collaborator(target.alias, target.provider, str(configured_model).strip(), target.label)
    return target


def collaboration_readiness(config: dict, key_available: Callable[[str], bool]) -> list[dict[str, Any]]:
    """Return deterministic readiness rows for the UI and tests."""
    rows = []
    for alias in ("chatgpt", "claude"):
        target = resolve_collaborator(alias, config)
        assert target is not None
        rows.append({
            "alias": target.alias,
            "provider": target.provider,
            "label": target.label,
            "model": target.default_model,
            "configured": bool(key_available(target.provider)),
        })
    return rows


async def consult(
    prompt: str,
    targets: Iterable[Collaborator],
    *,
    get_provider: Callable[[str], Any],
    message_factory: Callable[..., Any],
    timeout: float = 60.0,
) -> list[dict[str, str | bool]]:
    """Ask configured collaborators concurrently without tools or chat history.

    The caller decides which providers are configured.  Each response is kept
    separate: this command is a transparent second-opinion panel, not a hidden
    synthesis that could blur source attribution.
    """
    safe_prompt = str(prompt or "").strip()
    if not safe_prompt:
        return []

    async def _one(target: Collaborator) -> dict[str, str | bool]:
        try:
            provider = get_provider(f"{target.provider}/{target.default_model}")
            result = await asyncio.wait_for(
                provider.complete([message_factory(role="user", content=safe_prompt)]),
                timeout=timeout,
            )
            return {
                "alias": target.alias,
                "label": target.label,
                "model": target.default_model,
                "success": bool(result.get("success")),
                "response": str(result.get("response") or ""),
                "error": str(result.get("error") or ""),
            }
        except asyncio.TimeoutError:
            return {"alias": target.alias, "label": target.label, "model": target.default_model,
                    "success": False, "response": "", "error": f"timed out after {timeout:g}s"}
        except Exception as exc:
            return {"alias": target.alias, "label": target.label, "model": target.default_model,
                    "success": False, "response": "", "error": str(exc)}

    return await asyncio.gather(*(_one(target) for target in targets))
