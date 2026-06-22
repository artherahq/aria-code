"""Provider routing + fallback DECISIONS for the chat loop (pure, testable).

Extracted from ``aria_cli.send_message`` as the keystone for the documented
runtime next step ("route the whole CLI tool loop through run_agent"). The
*decision* of which provider a round uses, and whether to fall back, is pure
logic; pulling it out of the streaming machinery lets it be unit-tested and
reused as a ``provider_fn`` selector without touching the live REPL path.

Routing rules (mirrors send_message):
  • local_mode                       → always local Ollama
  • cloud-named model ("provider/x") → cloud backend (AriaSSE)
  • ollama-named model ("x:y")       → cloud backend ONLY if backend_chat forces
                                       it; otherwise skip the backend stub and go
                                       straight to the local/cloud fallback chain
"""

from __future__ import annotations

from typing import Callable, Optional


def is_cloud_model(model: str) -> bool:
    """Cloud models are provider-prefixed, e.g. ``openai/gpt-4.5``, ``anthropic/…``."""
    return "/" in (model or "")


def is_ollama_model(model: str) -> bool:
    """Ollama models have no ``/`` (``gpt-oss:120b-cloud``, ``deepseek-r1:14b``)."""
    return "/" not in (model or "")


def force_backend(config: dict, api_url: Optional[str]) -> bool:
    """backend_chat=True routes ALL chat through the self-hosted backend (which
    proxies to its own Ollama + collects training data), requiring an api_url."""
    return bool(config.get("backend_chat")) and bool(api_url)


def first_round_route(model: str, config: dict, api_url: Optional[str]) -> str:
    """Return where the first round's generation goes: ``ollama`` | ``cloud`` | ``skip``.

    ``skip`` means the backend would only return a stub for this (ollama-named)
    model, so the round is skipped and the fallback chain runs directly.
    """
    if config.get("local_mode", False):
        return "ollama"
    if is_cloud_model(model) or force_backend(config, api_url):
        return "cloud"
    return "skip"


def is_placeholder_response(
    response: str,
    token_count: int,
    stub_detector: Optional[Callable[[str], bool]] = None,
) -> bool:
    """A 'successful' result that is actually empty / canned / a backend stub."""
    resp = response or ""
    if len(resp) < 20:
        return True
    if stub_detector is not None and stub_detector(resp):
        return True
    # Long "response" with ~no streamed tokens ⇒ canned backend reply, not a generation.
    if token_count <= 2 and len(resp) > 80:
        return True
    return False


def should_fallback(route: str, result: dict, *, is_placeholder: bool) -> bool:
    """Whether to run the local/cloud fallback chain after the primary round.

    Keyed on the *route* (not the model name), so a forced-backend round that
    genuinely succeeded does NOT fall back — which is the bug-free version of the
    old ``_should_fallback`` that keyed on ``is_ollama_model`` and could discard a
    good backend answer (causing a re-run / hang).
    """
    if route == "skip":
        return True
    if not result.get("success") and not result.get("cancelled"):
        return True
    return is_placeholder
