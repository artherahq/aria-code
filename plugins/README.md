# Aria Code Plugins

Plugins package repeatable Aria workflows without adding product-specific logic
to `aria_cli.py`. They are the public extension layer for commands, agents,
skills, hooks, and MCP bindings.

## Shape

```text
plugins/<plugin-name>/
├── .aria-plugin/
│   └── plugin.json
├── commands/
├── agents/
├── skills/
├── hooks/
├── mcp.json
└── README.md
```

Only `.aria-plugin/plugin.json` and `README.md` are required for a published
plugin. Other folders are optional.

## Manifest

```json
{
  "name": "market-analysis",
  "displayName": "Market Analysis",
  "version": "0.1.0",
  "description": "Opinionated market snapshot and support/resistance workflows.",
  "capabilities": ["commands", "skills"],
  "permissions": ["market_data", "write_artifacts"]
}
```

## Rules

- Keep broker execution read-only unless the core broker safety contract is used.
- Declare external data dependencies and required API keys.
- Prefer SDK/runtime events over direct terminal rendering.
- Keep generated artifacts outside the plugin directory by default.
