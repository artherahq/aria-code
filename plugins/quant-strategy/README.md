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

## Skills

Research-discipline skills for this plugin live in the standalone
[`artherahq/skills`](https://github.com/artherahq/skills) catalog so they can be
reused outside Aria. Install them as a marketplace:

```
/plugin marketplace add artherahq/skills
/plugin install quant-research-skills@aria-skills
```

- `point-in-time-research` — enforces point-in-time data discipline when
  backtesting a factor or strategy; catches the three silent leaks (period-end
  dating, latest-value overwrite, same-session execution), runs the four-variant
  A–D information-set comparison and the validation gauntlet, and ships a runnable
  harness (`information_set_compare.py --demo`).
- `equity-research-report` — builds comprehensive stock reports from normalized
  evidence, specialist agents, deterministic fallbacks, a critic pass, and an
  executable completion gate before a report can claim `complete`.
