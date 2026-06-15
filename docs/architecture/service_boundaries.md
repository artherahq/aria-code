# Aria Code Service Boundaries

Aria Code is an Arthera product. It should stay in its own repository while
connecting to shared Arthera packages through MCP or explicit Python adapters.

## Target Shape

```text
apps/cli, apps/daemon, apps/channels
        |
        v
packages/aria_services
        |
        +-- gateway   session routing, task queues, channel routing
        +-- runtime   agent loop, tool execution, approvals, traces
        +-- data      quotes, history, indicators, provider attribution
        +-- reports   markdown/html/chart artifacts and storage policies
        +-- brokers   account, positions, orders, guarded execution
        +-- skills    workflow bindings over tools and agents
        +-- safety    sandbox, command policy, privacy, audit
        +-- channels  optional external entrypoints
```

## Migration Rules

1. Do not add new business logic directly to `aria_cli.py` unless it is a small
   temporary adapter.
2. New market-data behavior belongs behind the `data` service contract.
3. New report or chart output belongs behind the `reports` service contract.
4. New broker behavior belongs behind the `brokers` service contract and must
   default to read-only.
5. New external app integrations should call the `gateway` service, not the CLI.
6. Every service-facing capability should have a manifest with permissions and
   capabilities.

## Arthera Relationship

`<ARTHERA_ROOT>/packages` is the company platform package tree (set via `ARTHERA_ROOT` env var or `~/.aria/config.json`).
Aria Code should not copy those packages. It should connect to them through:

- MCP first, especially for QuantEngine tools;
- optional explicit Python adapters when local imports are configured;
- shared contracts only when both repositories agree on stable schemas.

## First Extraction Candidates

- `data_service.py`, `market_data_client.py`, and quote/TA paths -> `data`.
- `report_generator.py`, `backtest_report.py`, chart output -> `reports`.
- `brokers/` and broker slash commands -> `brokers`.
- `aria_daemon.py`, relay, Feishu, Telegram -> `gateway` and `channels`.
- command policy, workspace safety, privacy -> `safety`.

## Data Service Contract

All market data paths should eventually return or consume the package facade:

```python
from packages.aria_services.data import DataService

bundle = DataService().bundle("AAPL")
```

Required provenance and quality fields:

- `provider_chain`: ordered providers used or attempted.
- `source`: winning provider for a result.
- `timestamp`: provider timestamp when available.
- `stale`: whether quote data is older than the configured freshness window.
- `quality.status`: `ok`, `partial`, `stale`, or `unavailable`.
- `missing_fields`: required fields that could not be populated.
- `warnings` and `errors`: provider failures without raw noisy tracebacks.
- `provider_health`: provider status snapshots, including cooldown state.

Provider errors are normalized into these categories:

- `rate_limited`: provider throttled the request; retry after cooldown.
- `timeout`: request timed out; retryable.
- `network`: DNS, connection, proxy, or remote disconnect failure.
- `no_data`: provider was reachable but returned no usable market data.
- `auth`: missing/invalid key or unauthorized response; not automatically retryable.
- `error`: other provider failure.

Report, team-agent, and strategy outputs should surface stale/partial data
instead of silently producing confident conclusions from incomplete inputs.
