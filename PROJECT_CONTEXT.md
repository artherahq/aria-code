# Project Context

This file keeps durable notes that should not depend on long chat history.

## 2026-06-18 SPCX Chart Cleanup

- `SPCX` is treated by this project as SpaceX / Space Exploration Technologies Corp. per the existing prompt rules.
- Do not generate placeholder scripts for market analysis. Prefer deterministic chart commands and real data providers.
- Chart generation should fetch real OHLCV data, write a local artifact, and report clear errors when all providers fail.
- For new IPOs with short history, chart generation may succeed with warnings because MA/RSI/MACD can be unreliable with too few bars.
- Long sessions should be compacted early. The CLI now warns at about 70% context and auto-compacts at about 90%.
- One user instruction should be completed end-to-end where feasible. Tool loops use soft limits that auto-extend before stopping, so the CLI does not exit immediately after the final tool result without a synthesis.
- User-requested strategies, scripts, generated code, and scaffolds should default to the user's local Aria workspace, not the source checkout: single files under `~/Documents/Aria Code/generated`, projects under `~/Documents/Aria Code/projects`. Users can override with an explicit absolute path or `ARIA_USER_OUTPUT_ROOT`.
