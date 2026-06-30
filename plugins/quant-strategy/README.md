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

### `point-in-time-research`

Enforces point-in-time data discipline whenever a factor or strategy is
researched or backtested, so a simulation never trades on information no real
investor could have had. It catches the three silent leaks (period-end dating,
latest-value overwrite, same-session execution), quantifies the distortion with
the four-variant A–D information-set comparison, runs the validation gauntlet
(market- and sector-neutral, transaction/borrow cost ladder, multiple-testing),
and reports honestly which alpha is real versus a beta/cost/look-ahead artefact.

- `skills/point-in-time-research/SKILL.md` — the workflow and triggering.
- `references/methodology.md` — formal time semantics, the admissibility rule,
  and the exact statistics (Newey–West, stationary bootstrap, Benjamini–Hochberg).
- `references/audit_checklist.md` — the pre-report red-flag checklist.
- `scripts/information_set_compare.py` — the A–D comparison harness (pandas +
  numpy, no statsmodels). Self-test with `python information_set_compare.py --demo`,
  which embeds a deliberate look-ahead edge and shows variant A's earnings alpha
  (≈ +69%, t≈14) collapsing to ≈ 0 under the strict point-in-time variant D.
