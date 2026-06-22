# Claude Code Parity Gap Analysis

This document tracks the practical gaps between Aria Code's current architecture
and the Claude Code/Codex-style product shape described in
`docs/architecture/service_boundaries.md`.

This is not a claim about private Claude Code internals. It is an implementation
target for Aria Code: a thin terminal adapter, a reusable agent runtime, typed
tools, explicit safety policy, service facades, MCP/channel adapters, and
observable task state.

## Reference Branch

Local reference branch:

- `aria/agent-output-brokers-doc-cleanup`

Current finding: `aria-code` already contains the reference branch's architectural
direction and additional commits. The reference branch remains useful as a naming
and migration checkpoint, but should not be merged blindly into `aria-code`.

## Current Strengths

- Runtime primitives exist in `runtime/`: agent loop, parallel/serial tool
  execution, approval decisions, loop guard, events, and traces.
- SDK facade exists in `packages/aria_sdk`, with event streaming independent of
  terminal rendering.
- Terminal runtime consumer exists in `apps/cli/runtime_consumer.py`, keeping UI
  rendering out of the core runtime.
- Data facade exists in `data_service.py` and is exported through
  `packages/aria_services.data`, including provider provenance, stale flags,
  missing fields, warnings, errors, and provider health.
- Dependency preflight exists in `apps/cli/preflight.py`, with `plan`, `auto`,
  `required`, `optional`, `custom`, and `skip` selection semantics.
- Architecture contract exists in `packages/aria_core/architecture.py` and is
  surfaced through `/architecture`.

## P0 Gaps

1. Runtime cutover is still opt-in.
   - Current: `use_runtime_loop` can route chat through `run_agent`, but legacy
     inline `send_message` remains the fallback path.
   - Needed: make `run_agent` the default path after live REPL validation, then
     retire duplicate inline tool-loop semantics.

2. Settings are not a single service.
   - Current: config, provider keys, model profiles, permissions, and runtime
     flags are spread across CLI config, env vars, and feature-specific helpers.
   - Needed: `SettingsService` with one read/write API and redacted export shape.

3. Safety is not unified.
   - Current: command safety, runtime approvals, broker preview/confirm, and
     privacy controls exist separately.
   - Needed: `SafetyService` that classifies all risky actions, returns a
     permission decision, and writes audit records.

4. Service facades are incomplete.
   - Current: `data` and context have real facades; `reports`, `brokers`,
     `gateway`, `settings`, `tools`, `mcp`, `safety`, and `observability` are
     partly manifests or legacy module boundaries.
   - Needed: concrete service classes for each required service, with CLI and
     MCP using those classes instead of direct legacy calls.

## P1 Gaps

1. Tool manifest coverage is incomplete.
   - Needed: every local and MCP tool should expose input schema, output schema,
     permission level, owner service, deterministic result shape, and provenance.

2. Report and artifact generation need a service boundary.
   - Needed: `ReportService` for markdown/html/chart/backtest artifacts, with
     storage policy, generated-file links, and stable metadata.

3. Gateway and channels are still planned.
   - Needed: daemon, TradingView webhook, Feishu, Telegram, and future channels
     should submit structured tasks to a gateway rather than call CLI internals.

4. Context checkpoints are not durable enough.
   - Needed: compact/resume checkpoints stored as artifacts with schema version,
     task state, modified files, tool trace IDs, and last user intent.

## P2 Gaps

1. Observability needs support bundles.
   - Needed: `/doctor` should export provider health, architecture gaps,
     service manifests, tool manifests, selected settings, and recent trace
     metadata without secrets.

2. MCP lifecycle needs isolation.
   - Needed: reconnect/reload policy, per-server cooldown, tool provenance, and
     failure isolation so one bad server cannot degrade all tool use.

3. UI output needs a dedicated artifact/render service.
   - Needed: generated files, chart links, terminal tables, and rich/plain
     fallbacks should be rendered through a UI/report adapter, not per command.

## Recommended Service Completion Order

1. `SettingsService`
   - Unblock: runtime defaults, provider selection, permission policy, daemon,
     MCP, and reproducible `/doctor` output.

2. `SafetyService`
   - Unblock: shell/network/file/broker/privacy approvals through one policy.

3. `ToolRegistry` full cutover
   - Unblock: model-visible tools, MCP tools, permission checks, and traces.

4. `ReportService`
   - Unblock: consistent market reports, charts, backtests, and artifact links.

5. `GatewayService` + `ChannelRegistry`
   - Unblock: daemon, TradingView alerts, Feishu/Telegram, and app connectors.

6. `ObservabilityService`
   - Unblock: support bundles, trace export, provider health, and architecture
     coverage in one place.

## Acceptance Criteria

- `/architecture --gaps` lists only real remaining migration work.
- `/doctor` reports service coverage, provider health, MCP health, permission
  policy, and runtime mode.
- A normal chat turn uses `runtime.run_agent` by default.
- A missing dependency produces a selector with `plan`, `auto`, `required`,
  `optional`, `custom`, and `skip`.
- A tool call always has a manifest, owner service, permission level, trace
  record, and deterministic result envelope.
- Reports and charts carry source, timestamp, stale/partial status, missing
  fields, and artifact metadata.
- Broker execution always follows preview -> confirm -> execute -> audit.
