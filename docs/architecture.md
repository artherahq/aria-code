# Architecture

Aria Code follows a product architecture similar to modern coding agents:
a small launcher, terminal UI adapters, a reusable runtime, typed tools,
service packages, plugin boundaries, and explicit safety controls.

## Repository Layers

```text
apps/
  cli/          terminal adapter, command parsing, direct command routing
  daemon/       background/server entrypoints
  channels/     chat or webhook adapters

runtime/        agent loop, tool execution, approvals, traces, events
packages/
  aria_sdk/     public SDK surface
  aria_core/    architecture manifest and contracts
  aria_services/data, provider health, usage, registry
  aria_mcp/     MCP bridge and tool manifests
  quant_engine/ quantitative engines

ui/             terminal rendering, input, banner, image/glyph rendering
plugins/        shareable workflow bundles
docs/           architecture, operations, and release guidance
```

## CLI Rule

`aria_cli.py` remains the legacy compatibility adapter while the migration is in
progress. New reusable behavior should move to:

- `apps/cli/*` for CLI parsing and adapters;
- `runtime/*` for agent/tool/approval/streaming behavior;
- `packages/aria_services/*` for business services;
- `packages/aria_sdk/*` for public programmable APIs;
- `ui/*` for terminal rendering only.

## Runtime Rule

Tool execution, permissions, retries, result events, streaming callbacks, and
approval decisions should flow through runtime events. Terminal code consumes
events; it should not own core execution semantics.

## Plugin Rule

Product workflows that are not core platform behavior should be packaged under
`plugins/` first. If a plugin needs a reusable primitive, promote that primitive
into `runtime/` or `packages/` with tests.
