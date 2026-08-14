# ADK research integration

`adk_apps/aria_research` is the Google ADK layer for Aria Code. It is an
optional, read-only research surface: it can request a normalized market
snapshot and inspect data-provider health, but it cannot call broker, trade,
filesystem, shell, scheduling, or credential tools.

## Install and run locally

Use Python 3.10–3.13 and install the optional extra:

```bash
cd "$(git rev-parse --show-toplevel)"
uv venv --python 3.13 .venv-adk
uv pip install --python .venv-adk/bin/python -e '.[adk]'
source .venv-adk/bin/activate
export GOOGLE_API_KEY='your Gemini API key'
adk web adk_apps --port 8000
```

Open `http://localhost:8000` and select `aria_research`. Set `ARIA_ADK_MODEL`
to override the default Gemini model.

## Tool contracts

`get_market_snapshot(symbol)` returns a normalized, read-only quote/fundamental/
technical snapshot. The agent must disclose its `as_of`, `quality`, warnings,
and errors before describing any market implication.

`get_market_data_health()` is a product-safe diagnostic endpoint. It returns:

- `status`: a user-facing availability state and retry guidance;
- `health`: aggregate counts for available, degraded, unavailable, and
  cooling-down providers.

It deliberately does **not** return provider names, endpoint URLs, raw
exceptions, request headers, or credentials. Provider-specific diagnostics stay
inside the service's protected logs and operator tooling.

## Architecture and boundaries

```text
Aria Code CLI / web surfaces
        |                         \-- existing runtime + approvals
        v
ADK aria_research (planning, conversation, tool selection)
        v
packages.adk_bridge (bounded read-only tool contracts)
        v
DataService / MarketDataClient -> provider-health telemetry
```

Arthera should remain the authoritative API and compute layer. A subsequent
production step can replace the bridge's local `DataService` factory with an
authenticated, read-only Arthera API client; the ADK function signatures stay
the same. Do not publish the development `adk web` UI, and do not add trading
or credential-bearing tools to this agent.

## Highest-value rollout

1. Keep this agent in research mode and record every tool call, data timestamp,
   aggregate health state, and user-visible warning with the existing run store.
2. Add evidence-producing Arthera endpoints for quotes, filings/news, backtest
   results, and portfolio risk. Return structured records, not prose.
3. Bind `aria-skills` policies to agent intent: research and risk skills may
   enrich analysis, while execution-position remains paper-only.
4. Require explicit UI approval outside ADK for exports, write operations, and
   any broker workflow. A trade must be a separate, signed intent processed by
   Arthera's existing broker boundary—not an ADK tool call.
5. Evaluate with fixed market-data fixtures: factual accuracy, source/timestamp
   disclosure, tool-call precision, refusal of execution requests, and no
   unsupported performance claims.
