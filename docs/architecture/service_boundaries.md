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

## Agent Architecture Contract

Aria should follow the same product shape users expect from Claude Code and
Codex-style coding agents: a thin launcher and UI, a separate agent runtime, a
typed tool/service layer, explicit safety policy, MCP/app connectors, and
observable task state. This is not a claim about private internal
implementations; it is the Aria product contract.

The source of truth is `packages/aria_core/architecture.py`.

| Layer | Responsibility | Current status |
| --- | --- | --- |
| `launcher` | Stable executable, runtime selection, dependency bootstrap | partial |
| `settings` | Config, secrets, model profiles, permission policy | planned |
| `ui` | Terminal rendering, input UX, progress, artifact links | partial |
| `context` | Memory, automatic compaction, resume checkpoints | partial |
| `runtime` | Agent loop, planning, tools, retries, streaming | partial |
| `tools` | Tool registry, schemas, permissions, local/MCP adapters | partial |
| `services` | Data, reports, brokers, skills, channels, gateway | partial |
| `mcp` | External tool/package integration and health | partial |
| `safety` | Filesystem, shell, network, broker, privacy guardrails | partial |
| `channels` | Daemon, webhooks, TradingView alerts, chat apps | planned |
| `observability` | Doctor checks, traces, provider health, audits | partial |

Architectural rules:

1. CLI commands are adapters. They may parse user input and render output, but
   business behavior belongs behind tools or services.
2. Tool calls are typed manifests with permissions and deterministic result
   shapes before the LLM consumes them.
3. Runtime state, context compaction, and trace artifacts must be separate from
   terminal rendering.
4. MCP, TradingView webhooks, daemon tasks, and future apps should enter through
   gateway/channel services, not call `aria_cli.py` internals.
5. Live broker execution must keep preview/confirm/audit boundaries even when
   requests originate from MCP or webhooks.

## Migration Rules

1. Do not add new business logic directly to `aria_cli.py` unless it is a small
   temporary adapter.
2. New market-data behavior belongs behind the `data` service contract.
3. New report or chart output belongs behind the `reports` service contract.
4. New broker behavior belongs behind the `brokers` service contract and must
   default to read-only unless it is the local paper broker.
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

## Broker Execution Contract

Broker execution is a two-stage service flow. The CLI and MCP tool are adapters;
they should not directly place orders.

```text
user intent
  -> symbol/account resolution
  -> broker snapshot
  -> order preview
  -> risk checks
  -> preview_id
  -> explicit confirmation with preview_id
  -> paper/live adapter
  -> audit log
```

Required boundaries:

- `read_only` is the default for all real broker configs.
- `paper` uses `brokers.paper_broker.PaperBroker` and writes only to the local
  paper ledger.
- `live` execution is blocked unless the broker config explicitly sets
  `allow_live_trade=true`.
- Every executable order must be created by `brokers.trading.build_order_preview`
  and confirmed through `brokers.trading.execute_order_preview`.
- Confirmation must use the exact `preview_id`; symbol/side/quantity from a new
  user message are not enough to execute.
- Risk checks run before execution and include cash, cash reserve, single-order
  size, sell availability, and projected single-symbol position weight.
- Every preview, rejection, and execution writes an audit event.

User-facing commands:

- `/paper start 100000 USD`: create/reset a local paper account.
- `/paper account|positions|orders`: inspect the paper ledger.
- `/trade mode`: inspect the active account's execution policy.
- `/trade preview AAPL buy 10 190`: create a guarded preview.
- `/trade confirm <preview_id>`: execute only the exact saved preview.
