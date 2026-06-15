# Aria Code Packages Integration

Aria Code is moving from a single CLI module toward reusable package facades.
The first step is intentionally non-destructive: existing modules remain in
place, while `packages/` provides stable manifests and registry adapters.

## Current Boundaries

- `packages.aria_core`: shared manifest contracts.
- `packages.aria_services`: product service boundary manifests.
- `packages.aria_tools`: typed facade over the legacy `LOCAL_TOOLS` registry.
- `packages.aria_agents`: manifests generated from the existing `agents.registry`.
- `packages.aria_skills`: workflow-level skill manifests that bind tools and agents.
- `packages.aria_mcp`: stable MCP exposure names for a future Aria MCP server.
- `packages.aria_infra`: infrastructure helpers, including optional Arthera package discovery.

## Arthera Packages Relationship

`<ARTHERA_ROOT>/packages` is treated as a sibling company monorepo,
not copied into Aria Code. Aria should connect to it in two ways:

1. MCP: use `packages/quant_engine/mcp_server.py` as an external MCP server.
2. Optional Python adapter: discover package entrypoints, then import only when
   explicitly configured by the user.

Recommended MCP config (replace `<ARTHERA_ROOT>` with your local checkout path):

```json
{
  "servers": [{
    "name": "arthera_quant_engine",
    "command": "python3",
    "args": ["<ARTHERA_ROOT>/packages/quant_engine/mcp_server.py"],
    "env": {"PYTHONPATH": "<ARTHERA_ROOT>"}
  }]
}
```

## Target Connection

```text
CLI / MCP server / daemon
        |
        v
Gateway / Runtime / Safety service boundaries
        |
        v
ToolRegistry / AgentRegistry / SkillRegistry
        |
        v
DataService / BrokerRegistry / ArtifactStore
        |
        v
Aria local modules + optional Arthera packages via MCP
```
