"""Channel intake policy + gateway submission (the daemon-facing half).

Completes the channels contract loop: the daemon's webhook endpoint hands
verified channel tasks to ``runtime.run_turn`` instead of poking CLI
internals, and refuses unauthenticated ("open mode") intake from anywhere
but localhost.

Two pieces, both dependency-light so the daemon can import them without
dragging in aria_cli:

  • should_refuse_open_intake() — pure policy: when neither an intake token
    nor ARIA_WEBHOOK_SECRET is configured, only loopback clients may submit.
  • analyze_alert_via_gateway() — submits a task prompt through the shared
    runtime gateway with an EMPTY tool set: channel-triggered turns get
    model analysis but can never execute tools from the daemon context.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def should_refuse_open_intake(
    client_host: Optional[str],
    *,
    token_configured: bool,
    secret_configured: bool,
) -> bool:
    """True when an unauthenticated intake must be rejected.

    With any credential configured (endpoint token or webhook passphrase),
    authentication handles trust and remote clients are fine. With neither
    configured the endpoint is running "open", which is only acceptable from
    the loopback interface — an open endpoint reachable off-host would let
    anyone on the network inject buy/sell alerts.
    """
    if token_configured or secret_configured:
        return False
    host = str(client_host or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):  # bracketed IPv6
        host = host[1:-1]
    return host not in _LOOPBACK_HOSTS


async def analyze_alert_via_gateway(
    prompt: str,
    config: Dict[str, Any],
    *,
    run_turn_fn: Optional[Callable] = None,
    max_rounds: int = 4,
) -> str:
    """Run one tool-less analysis turn for a channel task; returns the text.

    ``run_turn_fn`` is injectable for tests; the default builds the same
    provider selection the CLI uses (chat_routing via runtime_bridge) and
    drives ``runtime.gateway.run_turn``. Raises on failure — the caller
    decides its own fallback (the daemon keeps its legacy quick summary).
    """
    if run_turn_fn is None:
        # stream_ollama borrows 47 bare names from aria_cli's module globals
        # (verified by AST audit) and only works after aria_cli's import-time
        # rebinding. Until that module is untangled, any out-of-CLI consumer
        # of the default Ollama path must import aria_cli first — a one-time
        # ~1s cost the long-lived daemon amortizes.
        import aria_cli  # noqa: F401  (side effect: binds stream_ollama globals)
        from apps.cli.providers.runtime_bridge import build_tool_executor, make_provider_fn
        from runtime.gateway import run_turn as run_turn_fn_impl

        provider_fn = make_provider_fn(
            model=str(config.get("model", "qwen2.5:7b")),
            config=config,
            api_url=config.get("api_url"),
            ollama_url=str(config.get("ollama_url", "http://localhost:11434")),
            tool_schemas=[],
        )
        executor = build_tool_executor({}, config)

        async def run_turn_fn(prompt_, history_, **kw):
            return await run_turn_fn_impl(
                prompt_, history_,
                provider_fn=provider_fn, tool_executor=executor,
                tool_schemas=[], max_rounds=max_rounds,
            )

    result = await run_turn_fn(prompt, [])
    text = (getattr(result, "text", "") or "").strip()
    error = getattr(result, "error", None)
    if error or not text:
        raise RuntimeError(f"gateway turn failed: {error or 'empty response'}")
    return text
