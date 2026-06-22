# Quant Strategy Plugin

Reference plugin for strategy research, backtesting, and report generation.

The plugin boundary is meant to keep generated strategy templates and research
flows separate from the core CLI. Reusable execution should target the SDK and
runtime event layer, not terminal callbacks.

## Capabilities

- strategy idea scaffolding;
- backtest command presets;
- report artifact generation;
- risk and benchmark summaries.

## Required Services

- `market_data`
- `backtest_engine`
- `artifacts`
