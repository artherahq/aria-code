# Market Analysis Plugin

Reference plugin for market snapshot, technical-analysis, and multi-timeframe
support/resistance workflows.

This plugin is intentionally small. Its purpose is to document how product
workflows should be packaged outside `aria_cli.py` while the runtime/plugin
loader matures.

## Capabilities

- market snapshot commands;
- technical-analysis prompts;
- short, swing, and long-term level interpretation;
- artifact generation hooks.

## Required Services

- `market_data`
- `technical_indicators`
- `artifacts`
